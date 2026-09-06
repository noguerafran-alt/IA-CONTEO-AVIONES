"""Cuando estuvo ARRIBA el grabador, para poder afirmar cobertura por escrito.

POR QUE EXISTE ESTE MODULO. Hasta ahora la unica senal de que el grabador
estuvo caido era el SILENCIO en adsb_log, y el silencio no distingue dos cosas
que significan lo contrario:

    la antena estaba APAGADA          -> esa operacion no es una perdida
    estaba PRENDIDA Y SORDA           -> esa operacion SI es una perdida

Sin esa distincion no se puede publicar ningun porcentaje de cobertura, y de
hecho se publicaron dos equivocados. El primero comparaba contra todas las
operaciones de la ventana 10:09-16:42 del 03/09 y daba 49%/22%. El segundo
descontaba los 56 min sin mensajes y daba 56-58% de partidas -- y tampoco media
la antena, porque los huecos se reconstruian DESDE EL SILENCIO con un corte de
3-5 min, asi que toda parada mas corta que el corte se quedaba en el
denominador. El 03/09 fue un dia de prender y apagar probando ganancias.

Verificado a mano contra los listados de AA2000: con el grabador prendido, las
partidas entran todas. Este modulo es lo que hace que el sistema pueda PROBAR
eso solo, en vez de depender de que alguien lo haya mirado.

EL LATIDO LO MANEJA EL RELOJ, NO LOS DATOS. Es la decision central y es facil
de arruinar: si el latido se escribiera desde record(), un cielo vacio -o una
antena sorda- no dejaria latidos, y el registro diria "apagada" justo en el
caso que este modulo existe para detectar. Por eso latir() se llama desde el
bucle que corre cada segundo pase lo que pase, y NO desde el camino de la
observacion.

COMO SE LEE UNA FILA. Tres estados, y hay que poder distinguirlos:

    cierre NOT NULL              cerro ordenado; el final es exacto
    cierre NULL + latido fresco  esta corriendo AHORA
    cierre NULL + latido viejo   se cayo sin cerrar (corte de luz, cuelgue,
                                 taskkill). El final no se sabe exacto: esta
                                 entre el ultimo latido y latido + intervalo

El intervalo del latido se guarda EN LA FILA (latido_cada_s) y no se asume
desde la constante de hoy: si manana se cambia, las filas viejas tienen que
seguir siendo interpretables. Una cota que se lee de una constante que cambio
es una cota inventada.

VIVE EN LA MISMA BASE a proposito. publicar_datos.py copia la base entera con
la API de backup, asi que la tabla viaja a Torre sola, sin un segundo archivo
que sincronizar ni que se pueda desparejar del .db.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone

# 30 s: la cota de incertidumbre sobre el instante de una caida queda muy por
# debajo del corte de 3-5 min con el que se reconstruian los huecos, que es el
# error que este modulo viene a cerrar. El costo es un UPDATE + commit cada
# 30 s -- 0.45 ms en WAL, medido en adsb_record.py -- o sea 1.3 ms por hora.
LATIDO_CADA_S = float(os.environ.get("ADSB_LATIDO_S") or 30.0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS grabador_sesion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arranque REAL NOT NULL,
    arranque_utc TEXT NOT NULL,
    latido REAL NOT NULL,
    latido_utc TEXT NOT NULL,
    cierre REAL,
    cierre_utc TEXT,
    motivo TEXT,
    receptor TEXT,
    fuente TEXT,
    latido_cada_s REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sesion_arranque ON grabador_sesion(arranque);
"""


def _utc(marca: float) -> str:
    return datetime.fromtimestamp(marca, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crear_esquema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def abrir_sesion(conn: sqlite3.Connection, receptor: str = "", fuente: str = "",
                 latido_cada_s: float = LATIDO_CADA_S,
                 ahora: float | None = None) -> int:
    """Anotar que el grabador arranco. Devuelve el id de la sesion.

    El primer latido se escribe igual al arranque y no NULL: asi una sesion que
    se muere en el primer segundo -el caso del dongle tomado por otro proceso-
    igual deja un intervalo, corto pero real, en vez de una fila que no se
    puede interpretar.
    """
    ahora = time.time() if ahora is None else ahora
    cursor = conn.execute(
        "INSERT INTO grabador_sesion (arranque, arranque_utc, latido, latido_utc,"
        " receptor, fuente, latido_cada_s) VALUES (?,?,?,?,?,?,?)",
        (ahora, _utc(ahora), ahora, _utc(ahora), receptor, fuente, latido_cada_s))
    conn.commit()
    return int(cursor.lastrowid)


def latir(conn: sqlite3.Connection, sesion_id: int, ahora: float | None = None) -> None:
    """Mover el latido de esta sesion. Commitea SIEMPRE.

    El commit no es opcional ni se puede diferir al commit por tiempo de las
    observaciones: lo unico que este dato tiene que sobrevivir es exactamente
    el corte de luz que impide cerrar la sesion. Un latido que quedo sin
    confirmar no existe cuando mas se lo necesita.
    """
    ahora = time.time() if ahora is None else ahora
    conn.execute("UPDATE grabador_sesion SET latido=?, latido_utc=? WHERE id=?",
                 (ahora, _utc(ahora), sesion_id))
    conn.commit()


def cerrar_sesion(conn: sqlite3.Connection, sesion_id: int, motivo: str = "detenido",
                  ahora: float | None = None) -> None:
    """Anotar que el grabador se detuvo, y por que.

    El motivo se guarda porque no es lo mismo que lo hayan parado a que se haya
    caido con una excepcion: lo primero es operacion normal y lo segundo hay
    que ir a mirarlo. Sin el motivo, las dos se ven igual en la tabla.
    """
    ahora = time.time() if ahora is None else ahora
    conn.execute(
        "UPDATE grabador_sesion SET cierre=?, cierre_utc=?, motivo=?, latido=?,"
        " latido_utc=? WHERE id=?",
        (ahora, _utc(ahora), motivo, ahora, _utc(ahora), sesion_id))
    conn.commit()


def _fin_efectivo(fila) -> tuple[float, bool]:
    """(cuando termino, si el final es exacto).

    Con cierre, el final es exacto. Sin cierre, lo unico que se sabe es que
    llego hasta el ultimo latido; se toma ESE valor y no latido + intervalo,
    porque dar el beneficio de la duda al sistema es como se infla una
    cobertura. Mas corto y honesto antes que mas largo y favorable.
    """
    if fila["cierre"] is not None:
        return float(fila["cierre"]), True
    return float(fila["latido"]), False


def sesiones(conn: sqlite3.Connection, desde: float | None = None,
             hasta: float | None = None) -> list[dict]:
    """Las sesiones que TOCAN la ventana, con lo que se sabe de cada una."""
    # El row_factory se pone en el CURSOR y no en la conexion: la conexion
    # puede ser la del Recorder, que esta grabando, y cambiarle el row_factory
    # de prepo le cambia el tipo de fila a todo lo demas que la use. Un efecto
    # de ese estilo se paga tarde y lejos de aca.
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    filas = cursor.execute("SELECT * FROM grabador_sesion ORDER BY arranque").fetchall()
    salida = []
    for fila in filas:
        arranque = float(fila["arranque"])
        fin, exacto = _fin_efectivo(fila)
        if desde is not None and fin < desde:
            continue
        if hasta is not None and arranque > hasta:
            continue
        salida.append({
            "id": int(fila["id"]),
            "arranque": arranque,
            "arranque_utc": fila["arranque_utc"],
            "fin": fin,
            "fin_utc": fila["cierre_utc"] or fila["latido_utc"],
            "fin_exacto": exacto,
            "cerro_ordenado": fila["cierre"] is not None,
            "motivo": fila["motivo"],
            "receptor": fila["receptor"],
            "fuente": fila["fuente"],
            "latido_cada_s": float(fila["latido_cada_s"]),
        })
    return salida


def resumen(conn: sqlite3.Connection, desde: float | None = None,
            hasta: float | None = None, ahora: float | None = None) -> dict:
    """Cuanto tiempo estuvo arriba el grabador dentro de la ventana.

    `cobertura` es None y no 0.0 cuando no hay con que calcularla -- sin
    ventana, o con la tabla vacia porque la grabacion es anterior a que este
    modulo existiera--. Un cero ahi se leeria como "no grabo nada", que es una
    afirmacion, y no tenemos con que hacerla.

    Los intervalos se UNEN antes de sumar. No deberia haber dos sesiones
    solapadas -el dongle es exclusivo de un proceso- pero si las hay, sumar por
    separado contaria el mismo segundo dos veces y podria dar cobertura > 100%,
    que es como se publica un numero imposible sin que nadie lo note. Cuando
    pasa, sale en `solapamientos` en vez de quedar tapado.
    """
    ahora = time.time() if ahora is None else ahora
    lista = sesiones(conn, desde, hasta)

    crudos = sorted((s["arranque"], s["fin"]) for s in lista)
    unidos: list[list[float]] = []
    solapamientos = 0
    for inicio, fin in crudos:
        if unidos and inicio <= unidos[-1][1]:
            solapamientos += 1
            unidos[-1][1] = max(unidos[-1][1], fin)
        else:
            unidos.append([inicio, fin])

    arriba = 0.0
    for inicio, fin in unidos:
        if desde is not None:
            inicio = max(inicio, desde)
        if hasta is not None:
            fin = min(fin, hasta)
        if fin > inicio:
            arriba += fin - inicio

    ventana = None
    if desde is not None and hasta is not None and hasta > desde:
        ventana = hasta - desde

    # Una caida es una sesion que no cerro ordenado Y cuyo ultimo latido ya
    # quedo viejo. Sin la segunda condicion, la sesion que esta corriendo AHORA
    # -que tambien tiene cierre NULL- se contaria como caida siempre.
    caidas = [s for s in lista
              if not s["cerro_ordenado"]
              and (ahora - s["fin"]) > s["latido_cada_s"] * 2]
    corriendo = [s for s in lista
                 if not s["cerro_ordenado"]
                 and (ahora - s["fin"]) <= s["latido_cada_s"] * 2]

    return {
        "sesiones": len(lista),
        "segundos_arriba": round(arriba, 1),
        "segundos_ventana": round(ventana, 1) if ventana else None,
        "cobertura": round(arriba / ventana, 4) if ventana and lista else None,
        "caidas": len(caidas),
        "corriendo": bool(corriendo),
        "solapamientos": solapamientos,
        # El hueco mas largo DENTRO de la ventana en que el grabador no estuvo
        # arriba. Es el numero que decide si un share por franja horaria se
        # puede publicar: 20 min sueltos repartidos no es lo mismo que 20 min
        # seguidos sobre el pico de una aerolinea.
        "hueco_max_s": round(_hueco_maximo(unidos, desde, hasta), 1) if ventana else None,
        "intervalos": [{"desde": _utc(a), "hasta": _utc(b)} for a, b in unidos],
    }


def _hueco_maximo(unidos: list[list[float]], desde: float | None,
                  hasta: float | None) -> float:
    if desde is None or hasta is None:
        return 0.0
    if not unidos:
        return hasta - desde
    peor = max(0.0, unidos[0][0] - desde)
    for anterior, siguiente in zip(unidos, unidos[1:]):
        peor = max(peor, siguiente[0] - anterior[1])
    return max(peor, hasta - unidos[-1][1])
