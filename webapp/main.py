"""Local dashboard showing landing/takeoff events from the SQLite DB."""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import db
# La ruta de la base la define adsb_record (y la puede mover ADSB_DB). Se
# importa y no se rearma como ROOT/"adsb_log.db" en cinco lugares: dos
# definiciones de la misma ruta es como se termina con la webapp leyendo un
# archivo y el grabador escribiendo otro.
from adsb_record import DB_PATH as ADSB_DB_PATH
from adsb_service import service as adsb_service
from adsb_service import uptime_24h as _uptime_24h

ROOT = Path(__file__).resolve().parent.parent
THUMBNAILS_DIR = ROOT / "output" / "thumbnails"
THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
# Browser-playable (H.264) copies produced by transcode_web.py. The raw
# annotated videos are mp4v, which browsers refuse to play.
WEB_VIDEO_DIR = ROOT / "output" / "web"
WEB_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

DATASET_DIR = ROOT / "dataset"

app = FastAPI(title="Runway Video Analytics")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Plotly se sirve desde el disco y no desde un CDN: el sistema tiene que
# funcionar sin internet, y una pagina que se queda en blanco porque no llego un
# script de un tercero no es aceptable para algo que corre al lado de una pista.
# El archivo se baja una vez con "python webapp/bajar_plotly.py" y esta en
# .gitignore, igual que rtl_adsb.exe y la base de OpenSky.
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/thumbnails", StaticFiles(directory=str(THUMBNAILS_DIR)), name="thumbnails")
app.mount("/videos", StaticFiles(directory=str(WEB_VIDEO_DIR)), name="videos")
if (DATASET_DIR / "images").exists():
    app.mount("/dataset-images", StaticFiles(directory=str(DATASET_DIR / "images")), name="dataset_images")


def available_videos() -> list[str]:
    return sorted(p.name for p in WEB_VIDEO_DIR.glob("*.mp4"))


def web_video_for_event(row, videos: list[str]) -> str | None:
    """Web-ready render for an event, using the annotated video it was produced from."""
    annotated = row["annotated_video"]
    if annotated and annotated in videos:
        return annotated
    # Rows written before annotated_video existed: fall back to name matching.
    stem = Path(row["source"]).stem
    for name in videos:
        if stem in Path(name).stem or Path(name).stem.replace("annotated_", "") in stem:
            return name
    return None


def thumbnail_url(path: str | None) -> str | None:
    if not path:
        return None
    return f"/thumbnails/{Path(path).name}"


@app.get("/")
def index(request: Request):
    conn = db.get_connection()
    events = db.list_events(conn, limit=200)
    counts = db.counts_by_type(conn)
    conn.close()
    videos = available_videos()
    rows = [
        {
            **dict(row),
            "thumbnail_url": thumbnail_url(row["thumbnail_path"]),
            "video": web_video_for_event(row, videos),
        }
        for row in events
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": rows,
            "counts": counts,
            "total": counts["landing"] + counts["takeoff"],
            "videos": videos,
        },
    )


@app.get("/api/events")
def api_events(limit: int = 200):
    conn = db.get_connection()
    events = db.list_events(conn, limit=limit)
    counts = db.counts_by_type(conn)
    conn.close()
    rows = [
        {**dict(row), "thumbnail_url": thumbnail_url(row["thumbnail_path"])}
        for row in events
    ]
    return JSONResponse({"events": rows, "counts": counts})


# --- Label review -------------------------------------------------------
# The dataset is auto-labeled, so every box is a guess by the teacher model.
# These endpoints let a human fix them, which is the only way the fine-tuned
# model can learn anything the teacher got wrong.

def label_path(split: str, stem: str) -> Path:
    return DATASET_DIR / "labels" / split / f"{stem}.txt"


def image_relpath(split: str, stem: str) -> str:
    return f"/dataset-images/{split}/{stem}.jpg"


def read_boxes(split: str, stem: str) -> list[list[float]]:
    path = label_path(split, stem)
    if not path.exists():
        return []
    boxes = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 5:
            boxes.append([float(p) for p in parts[1:]])
    return boxes


def dataset_items() -> list[dict]:
    items = []
    for split in ("train", "val"):
        images_dir = DATASET_DIR / "images" / split
        if not images_dir.exists():
            continue
        for image in sorted(images_dir.glob("*.jpg")):
            items.append({"split": split, "stem": image.stem})
    return items


@app.get("/label")
def label_page(request: Request):
    items = dataset_items()
    return templates.TemplateResponse(
        request, "label.html", {"total": len(items), "has_dataset": bool(items)}
    )


@app.get("/api/label/items")
def api_label_items():
    return JSONResponse({"items": dataset_items()})


@app.get("/api/label/item/{split}/{stem}")
def api_label_item(split: str, stem: str):
    if split not in ("train", "val"):
        return JSONResponse({"error": "bad split"}, status_code=400)
    return JSONResponse({
        "split": split,
        "stem": stem,
        "image_url": image_relpath(split, stem),
        "boxes": read_boxes(split, stem),
    })


@app.post("/api/label/item/{split}/{stem}")
async def api_save_label(split: str, stem: str, request: Request):
    if split not in ("train", "val"):
        return JSONResponse({"error": "bad split"}, status_code=400)
    payload = await request.json()
    boxes = payload.get("boxes", [])

    lines = []
    for box in boxes:
        if len(box) != 4:
            continue
        cx, cy, bw, bh = (max(0.0, min(1.0, float(v))) for v in box)
        if bw <= 0 or bh <= 0:
            continue
        lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

    path = label_path(split, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""))
    return JSONResponse({"saved": len(lines)})


# --- ADS-B ---------------------------------------------------------------
# Recording runs in a background thread inside this same process (see
# adsb_service.py) so the page can show live counts and a downloadable CSV
# instead of a terminal window scrolling text.

# --- mapas incrementales -------------------------------------------------
# Los dos mapas pollean con un CURSOR en vez de volver a pedir todo. El numero
# que lo justifica: /api/adsb/mapa mandaba 208 028 B cada 10 s = 74.9 MB/h, y
# simulando 360 refrescos sobre la ultima hora real el delta equivalente tiene
# mediana 40 B y maximo 701 B. 279 de esos 360 refrescos (78 %) no traen ni una
# posicion nueva, y hoy cada uno de esos cuesta releer la base entera: 153 ms
# que crecen linealmente con el historico (40x filas = 40.4x tiempo).

_LECTORES: dict[str, object] = {}
_LECTORES_LOCK = __import__("threading").Lock()

# Cuanto historico viaja en la carga completa. NO recorta los agregados: la
# mediana, el p95 y el maximo siguen siendo de toda la grabacion, y lo que la
# ventana deja afuera viaja como numero (trazas_fuera_de_ventana).
VENTANA_H_DEFECTO = 3.0


def _resolver_ventana(ventana_h) -> tuple[float | None, float | None, str | None]:
    """La ventana de dibujo en segundos, y el motivo si el valor pedido no valia.

    `ventana_h=0` pide la grabacion ENTERA y es el boton de las dos paginas.
    Cualquier otro valor que no sea finito y >= 0 se rechaza DICIENDOLO, en vez
    de reinterpretarse: medido, `ventana_h=1e400` llegaba como inf y reventaba
    con HTTP 500 al serializar ("Out of range float values are not JSON
    compliant", main.py, campo ventana_h), y `nan` o `-5` caian por
    `ventana_s > 0` = False y devolvian la grabacion entera sin avisar que
    habian interpretado un nan. Un 500 mudo es justo lo contrario de lo que hace
    el resto de este endpoint, que ante un cursor invalido contesta 200 y
    explica la decision en `forzada`.
    """
    import math

    nota = None
    if ventana_h is None:
        horas = VENTANA_H_DEFECTO
    elif not math.isfinite(ventana_h) or ventana_h < 0:
        horas = VENTANA_H_DEFECTO
        nota = (f"ventana_h={ventana_h} no es una cantidad de horas valida "
                f"(hace falta un numero finito >= 0); se dibuja con "
                f"{VENTANA_H_DEFECTO} h")
    else:
        horas = ventana_h
    limite = horas * 3600.0 if horas > 0 else None
    return limite, (limite / 3600.0 if limite else None), nota


def lector_mapa():
    """Un LectorIncremental por proceso, compartido por los dos mapas.

    Uno solo y no uno por endpoint: el PositionGate tiene estado (la ultima
    posicion ACEPTADA de cada aeronave es la referencia de la regla de
    continuidad), asi que dos lectores sobre la misma base gastarian el doble
    de memoria para llegar exactamente al mismo resultado -- y si algun dia no
    llegaran al mismo, las dos paginas contarian distinto sobre los mismos
    datos.
    """
    from adsb_events import LectorIncremental
    import aeropuerto

    clave = str(ADSB_DB_PATH)
    with _LECTORES_LOCK:
        lector = _LECTORES.get(clave)
        if lector is None:
            lector = _LECTORES[clave] = LectorIncremental(
                clave, cilindro=aeropuerto.geometria_cilindro())
    return lector


def _estaticos_adsb() -> dict:
    """Receptor, costa/pistas y aeropuertos: 6 001 B que NO cambian.

    Se calculan una vez por proceso. Hoy viajaban en cada refresco, 360 veces
    por hora = 2.16 MB/h de puro desperdicio. Ahora van con la carga completa y
    en el delta viaja solo su hash (`estatico_v`, ~10 B): si cambia, el cliente
    pide completa. Asi un ADSB_SURFACE_REF movido a mitad de corrida se sigue
    delatando, que es la unica razon por la que estos datos viajaban seguido.
    """
    global _ESTATICOS
    if _ESTATICOS is not None:
        return _ESTATICOS

    from receiver import (ANTENA_M, RECEIVER_ES_DEFAULT, RECEIVER_LAT, RECEIVER_LON,
                          RECEIVER_NAME, distance_km, horizonte_km, nearest_airport,
                          surface_ref_default)

    referencia = surface_ref_default()
    codigo, km_cercano = nearest_airport(RECEIVER_LAT, RECEIVER_LON)
    # El horizonte a un blanco EN EL SUELO es el numero que decide si se pueden
    # ver aviones en pista, y depende de la ALTURA de la antena mas que de la
    # cercania: 13.0 km a 10 m contra 52.2 km a 160 m.
    horizonte_suelo = horizonte_km(0)
    receptor = {
        "lat": RECEIVER_LAT, "lon": RECEIVER_LON,
        "name": RECEIVER_NAME,
        "is_default": RECEIVER_ES_DEFAULT,
        "antenna_m": ANTENA_M,
        "surface_horizon_km": round(horizonte_suelo, 1),
        "nearest_airport": codigo, "nearest_airport_km": round(km_cercano, 1),
        # Que referencia esta REALMENTE activa, no la que por defecto estaria:
        # si alguien exporto ADSB_SURFACE_REF, el mapa tiene que delatarlo o
        # muestra un receptor en un lugar y decodifica desde otro.
        "surface_ref": (list(referencia) if isinstance(referencia, tuple) else referencia),
        "surface_ref_is_default": referencia == (RECEIVER_LAT, RECEIVER_LON),
    }

    # La costa y las pistas van por el mismo endpoint y no en el template: son
    # datos, con fuente y fecha, no decoracion.
    import geografia
    geo = geografia.como_json()

    aeropuertos = []
    try:
        from pyModeS.position._airports import AIRPORTS
        for code, nombre in (("SADF", "San Fernando"), ("SABE", "Aeroparque"),
                             ("SAEZ", "Ezeiza")):
            if code in AIRPORTS:
                lat, lon = AIRPORTS[code]
                km = distance_km(lat, lon)
                aeropuertos.append({
                    "code": code, "name": nombre, "lat": lat, "lon": lon,
                    "km": (round(km, 1) if km is not None else None),
                    # Si la PISTA de este aeropuerto entra en el horizonte de
                    # superficie desde donde esta la antena. Es lo que separa
                    # "cuento sus operaciones" de "solo lo veo pasar por arriba".
                    "surface_visible": (km is not None and km <= horizonte_suelo),
                })
    except Exception:
        pass

    import hashlib
    import json as _json
    crudo = _json.dumps([receptor, geo, aeropuertos], sort_keys=True, default=str)
    _ESTATICOS = {"receiver": receptor, "geo": geo, "airports": aeropuertos,
                  "estatico_v": hashlib.sha1(crudo.encode()).hexdigest()[:10]}
    return _ESTATICOS


_ESTATICOS = None


def _receptor_publicado() -> dict:
    """Desde donde se esta midiendo, para la franja que va en las cinco paginas.

    Un solo lugar que lo arme y un solo endpoint que lo sirva. Antes /adsb/mapa
    era la UNICA pagina que nombraba al receptor, y encima al reves: el cartel
    se pintaba solo `if (rx.is_default === false)`, o sea que avisaba cuando
    alguien habia elegido la ubicacion a proposito y se callaba justo cuando
    nadie la eligio -- que es el modo de falla. La portada no lo decia nunca,
    /adsb -donde se APRIETA grabar- tampoco, y /aeropuerto/mapa rearmaba un dict
    mas chico sin is_default ni antenna_m, asi que no podia avisar ni queriendo.
    """
    import receiver

    est = _estaticos_adsb()
    rx = dict(est["receiver"])
    codigo = __import__("aeropuerto").objetivo()
    rx["origen"] = receiver.origen_configuracion()
    rx["resumen"] = receiver.resumen_configuracion(codigo)
    objetivo = None
    if codigo:
        km = None
        try:
            from pyModeS.position._airports import AIRPORTS
            if codigo in AIRPORTS:
                lat, lon = AIRPORTS[codigo]
                km = receiver.distance_km(lat, lon)
        except Exception:
            pass
        objetivo = {
            "codigo": codigo,
            "km": (round(km, 1) if km is not None else None),
            # Lo que la CONFIGURACION afirma. Los datos pueden contradecirlo, y
            # cuando lo hacen es la config la que esta mal: ver
            # coverage.receiver_misplaced y aeropuerto.Informe.advertencia.
            "surface_visible": (km is not None and km <= receiver.horizonte_km(0)),
        }
    rx["objetivo"] = objetivo
    return rx


def _proceso_publicado() -> dict:
    """Quien esta contestando: PID e interprete.

    El 23/08 habia DOS procesos servidor vivos, arrancados el mismo segundo --
    PID 12040 con el Python del Store de Windows y PID 22728 con el del venv --
    y ninguna pagina permitia saber cual tenia el puerto. La configuracion de
    ubicacion vive solo en el entorno del proceso, asi que sin esto "desde donde
    se mide" no se puede atribuir a nadie.
    """
    return {"pid": os.getpid(), "ejecutable": sys.executable,
            "arrancado_en": _ARRANCADO_EN}


_ARRANCADO_EN = __import__("time").time()


@app.get("/api/receptor")
def api_receptor():
    """La franja de receptor que pintan las cinco paginas, de un solo lugar.

    Endpoint propio y barato (todo sale de _estaticos_adsb, que se calcula una
    vez por proceso y no toca la base) para que hasta la portada pueda pintarlo
    sin cargar como la pagina de analisis.
    """
    return JSONResponse({"receptor": _receptor_publicado(),
                         "proceso": _proceso_publicado()})


@app.get("/api/adsb/uptime")
def api_adsb_uptime():
    """Cuanto estuvo ARRIBA el grabador en las ultimas 24 h, para la franja.

    Endpoint propio y no un campo de /api/receptor: ese se sirve de _ESTATICOS,
    se calcula una vez por proceso y NO TOCA LA BASE, y esto cambia cada 30 s.
    Meterlo ahi convertiria el endpoint barato que pinta hasta la portada en uno
    que consulta SQLite en cada carga.

    Tampoco se reusa /api/adsb/status, que trae la foto entera del grabador
    -historico, senal, contadores del filtro-: seria pedir todo eso para mostrar
    un porcentaje. Lo unico compartido es la lectura, adsb_service.uptime_24h()
    -- importada como _uptime_24h porque el nombre `adsb_service` en este archivo
    es la INSTANCIA del servicio, no el modulo.

    Va aparte tambien para que FALLE APARTE: si el registro no se puede leer, la
    franja del receptor se pinta igual y solo la banda de uptime dice que no se
    pudo. Son dos afirmaciones distintas y ninguna deberia tapar a la otra.
    """
    return JSONResponse({"uptime": _uptime_24h()})


def _coverage_mapa(lector) -> dict:
    """coverage sin rejected_detail: los rechazos ya viajan en `rejected`.

    Hoy la misma lista salia DOS veces en la respuesta, entera en las dos, y
    ninguna pagina leia la segunda. Con un solo rechazo son 400 B; con la
    grabacion de un mes que se estropeo son megabytes duplicados en cada
    refresco.
    """
    cov = lector.coverage()
    cov.pop("rejected_detail", None)
    return cov


def _resolver_base(lector, desde) -> tuple[bool, int, str | None]:
    """Decide completa o delta. Lo decide el SERVIDOR, siempre.

    Un solo endpoint con parametro y no dos, porque el unico que sabe si el
    proceso se reinicio -y por lo tanto si su cursor volvio a cero- es el
    servidor. Con dos endpoints el cliente adivina, y adivinar mal deja una
    pantalla desincronizada que nadie nota.
    """
    if desde is None:
        return True, 0, None
    try:
        desde = int(desde)
    except (TypeError, ValueError):
        return True, 0, "cursor invalido"
    if desde < 0:
        return True, 0, "cursor invalido"
    if desde > lector.cursor:
        # El servidor se reinicio (o el cliente venia de otro proceso): su
        # cursor esta mas adelante que el nuestro y no tenemos con que
        # completarlo sin inventar.
        return True, desde, "el servidor se reinicio y su cursor quedo atras"
    return False, desde, None


@app.get("/adsb")
def adsb_page(request: Request):
    # La fuente por defecto se marca en el HTML y no en el JS: el selector
    # manda SIEMPRE su value, asi que si la pagina abriera en "Automatico"
    # taparia a ADSB_SOURCE y la variable no serviria para nada.
    return templates.TemplateResponse(
        request, "adsb.html",
        {"fuente_default": os.environ.get("ADSB_SOURCE") or "auto"})


@app.get("/aeropuerto/mapa")
def aeropuerto_map_page(request: Request):
    return templates.TemplateResponse(request, "aeropuerto_mapa.html", {})


@app.get("/api/aeropuerto/mapa")
def api_aeropuerto_map(desde: int | None = None, ventana_h: float | None = None):
    """El mapa de UN aeropuerto: solo las trayectorias que operaron ahi.

    Distinto de /api/adsb/mapa en tres cosas, y por eso es otro endpoint y no un
    parametro: esta centrado en el aeropuerto y no en la antena, manda SOLO las
    trazas que entraron al cilindro de operaciones -no las 55 que la antena
    escucho de paso-, y cada traza viene con el tipo de operacion que se le
    atribuyo, que es lo que el mapa colorea.

    El mismo protocolo de cursor que el otro mapa. `airport` viaja SIEMPRE,
    tambien en el delta, y es a proposito: un punto nuevo puede convertir un
    sobrevuelo en aterrizaje (_clasificar mira `sube = alturas[-1] -
    alturas[i_min]`, aeropuerto.py), asi que el delta no puede ser solo "puntos
    nuevos". Mandando el informe entero -- son 8 operaciones, no 55 trazas -- el
    cliente puede comparar el tipo que tenia contra el que llego y repintar la
    traza, el <li> y las tarjetas.
    """
    import time as _t

    import aeropuerto

    ahora = _t.time()
    db_path = ADSB_DB_PATH
    if not db_path.exists():
        return JSONResponse({"airport": None, "tracks": [], "base": "completa",
                             "cursor": 0, "nuevos": 0, "nuevas_posiciones": 0,
                             "lag_s": None,
                             "empty_reason": "todavia no se grabo nada"})

    lector = lector_mapa()
    with lector._lock:
        completa, desde_ok, forzada = _resolver_base(lector, desde)
        avance = lector.avanzar()
        # avanzar() puede haber reconstruido el lector porque la base
        # retrocedio (archivo borrado y recreado, o filas borradas). Ahi el
        # cursor del cliente no significa nada -- apunta a ids de otra base --
        # asi que la respuesta pasa a completa y el motivo viaja en `forzada`,
        # el mismo campo que ya usa el reinicio del servidor.
        if avance["rebobinado"] is not None:
            completa, forzada = True, avance["rebobinado"]["motivo"]
        inf = lector.informe_aeropuerto()
        if inf is None:
            return JSONResponse({"airport": None, "tracks": [], "base": "completa",
                                 "cursor": lector.cursor, "nuevos": avance["filas"],
                                 "nuevas_posiciones": avance["posiciones"],
                                 "lag_s": None,
                                 "empty_reason": "no hay aeropuerto configurado"})

        # El filtro por operaciones va ANTES de construir las trazas y no
        # despues: antes se armaban las 59 trazas completas para tirar 51 y
        # quedarse con 8. Se pagaban enteras -- puntos, rumbo, matricula -- para
        # descartarlas en la linea siguiente.
        por_icao = {o.icao24: o for o in inf.operaciones}
        solo = set(por_icao)
        limite, ventana_publicada, ventana_nota = _resolver_ventana(ventana_h)
        fuera_t, fuera_p = (lector.fuera_de_ventana(limite, ahora, solo) if limite
                            else (0, 0))

        def adornar(trazas):
            for t in trazas:
                op = por_icao.get(t["icao24"])
                if op is None:
                    continue
                t["operacion"] = op.tipo
                t["confirmada"] = op.confirmada
                t["pista"] = op.pista
                t["min_altitude_ft"] = op.min_altitude_ft
                t["min_distance_km"] = op.min_distance_km
            return trazas

        import geografia
        comun = {
            "cursor": lector.cursor,
            "airport": aeropuerto.como_json(inf),
            "lag_s": (round(lector.lag_s(ahora), 1)
                      if lector.lag_s(ahora) is not None else None),
            "ventana_h": ventana_publicada,
            # Si el valor pedido no valia, se dice cual se uso y por que. Un
            # parametro reinterpretado en silencio es un descarte en silencio.
            "ventana_nota": ventana_nota,
            "trazas_fuera_de_ventana": fuera_t,
            "puntos_fuera_de_ventana": fuera_p,
            "servidor_ms": avance["ms"],
            # Cuantas veces este lector tuvo que reconstruirse porque la base
            # retrocedio, con el detalle de la ultima. Cero explicito: "nunca
            # paso" y "no se mira" son cosas distintas.
            "rebobinados": lector.rebobinados,
            "rebobinado": avance["rebobinado"],
        }

        if completa:
            trazas, ft, fp = lector.trazas(ventana_s=limite, ahora=ahora, solo=solo)
            comun["trazas_fuera_de_ventana"] = ft
            comun["puntos_fuera_de_ventana"] = fp
            return JSONResponse({
                **comun,
                "base": "completa",
                "forzada": forzada,
                "desde": desde_ok,
                "nuevos": avance["filas"], "nuevas_posiciones": avance["posiciones"],
                # La traza se manda ENTERA y no recortada al cilindro: ver de
                # donde venia el avion es la mitad de lo que hace entendible una
                # aproximacion.
                "tracks": adornar(trazas),
                "pistas": [p for p in geografia.como_json()["pistas"]
                           if p["apt"] == inf.codigo],
                # El MISMO dict que las otras paginas, no uno mas chico: el
                # de antes traia solo lat/lon/name, sin is_default ni antenna_m
                # ni surface_horizon_km, asi que este mapa -el que dibuja las
                # trazas de los aterrizajes- no podia avisar que la ubicacion
                # era la de por defecto ni queriendo. Dos definiciones del mismo
                # dato es como se termina con una pagina que avisa y otra que no.
                "receiver": _receptor_publicado(),
                "reconstruido_en_ms": lector.ms_reconstruccion,
            })

        nuevos_desde = max(0, lector.cursor - desde_ok)
        trazas = adornar(lector.delta_trazas(desde_ok, solo))
        return JSONResponse({
            **comun,
            "base": "delta",
            "desde": desde_ok,
            "nuevos": nuevos_desde,
            "nuevas_posiciones": sum(len(t["points"]) for t in trazas),
            "tracks": trazas,
        })


@app.get("/api/aeropuerto")
def api_aeropuerto():
    """Solo los conteos del aeropuerto objetivo, para el dashboard.

    Endpoint aparte y no un campo de /api/adsb/analisis porque el dashboard no
    necesita las 300 aeronaves ni la cobertura por campo: pedir todo eso para
    mostrar cuatro numeros haria que la portada cargue como la pagina completa.
    """
    import aeropuerto
    from adsb_events import load_db

    db_path = ADSB_DB_PATH
    if not db_path.exists():
        return JSONResponse({"airport": None})
    observaciones, _ = load_db(str(db_path))
    inf = aeropuerto.informe(observaciones)
    datos = aeropuerto.como_json(inf)
    if datos:
        # La lista completa de operaciones no viaja: el dashboard muestra el
        # resumen y el detalle esta en /adsb/analisis.
        datos.pop("operaciones", None)
    # La portada pinta los conteos y la advertencia y no nombraba en ningun
    # momento desde donde se mide. Es la primera pantalla que alguien abre.
    return JSONResponse({"airport": datos, "receptor": _receptor_publicado()})


@app.get("/aeropuerto")
def aeropuerto_operaciones_page(request: Request):
    return templates.TemplateResponse(request, "aeropuerto_operaciones.html", {})


@app.get("/api/aeropuerto/operaciones")
def api_aeropuerto_operaciones():
    """El registro COMPLETO de cada aeronave que ATERRIZO O DESPEGO en el objetivo.

    Es la pregunta que el proyecto existe para contestar, y hasta hoy no habia
    ninguna pantalla que la contestara con todos los datos: /adsb/analisis tiene
    el registro completo por aeronave pero de TODO lo que la antena escucha
    (aviones de paso incluidos), y el apartado del aeropuerto tiene la
    clasificacion pero con media docena de columnas.

    Aca se juntan las dos, y NO se escribe un tercer acumulador -- el repo ya
    tuvo el problema de dos que divergen (ver resumir_cilindro). Las dos mitades
    se calculan sobre LA MISMA lectura de la base:

      aeropuerto.informe()      QUE hizo cada aeronave y donde (cilindro, pista)
      adsb_report.summarize()   todo lo que se sabe de ella (alt, vel, mensajes)

    El filtro es tipo in (aterrizaje, despegue): no van las aproximaciones sin
    resolver, ni las salidas sin resolver, ni los sobrevuelos, ni las que
    quedaron en tierra. Lo que se filtra igual se CUENTA y viaja en `excluidas`:
    una tabla de 23 filas al lado de "43 aeronaves en el cilindro" tiene que
    poder explicar las otras 20.
    """
    import adsb_report
    import aeropuerto
    from adsb_events import coverage_report, load_db

    db_path = ADSB_DB_PATH
    if not db_path.exists():
        return JSONResponse({"airport": None, "operaciones": [],
                             "receptor": _receptor_publicado(),
                             "empty_reason": "todavia no se grabo nada"})

    observaciones, gate = load_db(str(db_path))
    inf = aeropuerto.informe(observaciones)
    if inf is None:
        return JSONResponse({"airport": None, "operaciones": [],
                             "receptor": _receptor_publicado(),
                             "empty_reason": "no hay aeropuerto configurado "
                                             "(ADSB_AIRPORT)"})
    datos = aeropuerto.como_json(inf)
    # El registro por aeronave, indexado por ICAO24 para pegarlo a la operacion.
    # summarize() ve TODOS los mensajes de esa aeronave, no solo los del
    # cilindro: la altitud maxima, la velocidad maxima y la primera vez que se
    # la escucho pasan casi siempre afuera.
    resumenes = {s.icao24: s for s in adsb_report.summarize(observaciones)}

    filas = []
    for o in datos["operaciones"]:
        if o["tipo"] not in ("aterrizaje", "despegue"):
            continue
        s = resumenes.get(o["icao24"])
        filas.append({
            **o,
            # Del registro por aeronave. Los nombres se mantienen iguales a los
            # de /api/adsb/analisis a proposito: es la misma columna y tiene que
            # poder compararse fila a fila entre las dos paginas.
            "messages": (s.messages if s else None),
            "first_seen": (s.first_seen if s else None),
            "last_seen": (s.last_seen if s else None),
            "duration_s": (s.duration_s if s else None),
            # OJO: min/max_altitude_ft de aca son de TODA la historia de la
            # aeronave; min_altitude_ft de la operacion es solo lo de adentro
            # del cilindro. Son dos preguntas distintas y por eso van las dos.
            "min_altitude_ft_total": (s.min_altitude_ft if s else None),
            "max_altitude_ft": (s.max_altitude_ft if s else None),
            "max_speed_kt": (s.max_speed_kt if s else None),
            "max_climb_fpm": (s.max_climb_fpm if s else None),
            "max_descent_fpm": (s.max_descent_fpm if s else None),
            # Distancia AL RECEPTOR, mientras min_distance_km de la operacion es
            # AL AEROPUERTO. Dos origenes distintos: se publican con nombres
            # distintos para que no se comparen sin querer.
            "min_distance_receptor_km": (s.min_distance_km if s else None),
            "max_distance_receptor_km": (s.max_distance_km if s else None),
            "signal_dbfs": (s.signal_dbfs if s else None),
            "phase": (s.phase if s else None),
            "latitude": (s.last_latitude if s else None),
            "longitude": (s.last_longitude if s else None),
        })

    return JSONResponse({
        "airport": {k: v for k, v in datos.items() if k != "operaciones"},
        "operaciones": filas,
        # Lo que la tabla NO muestra, contado. Nada se descarta en silencio: con
        # esto la suma de la pantalla cierra contra aeronaves_en_cilindro.
        "excluidas": {
            "aproximaciones": datos["aproximaciones"],
            "salidas": datos["salidas"],
            "frustradas": datos["frustradas"],
            "en_tierra": datos["en_tierra"],
            "sobrevuelos": datos["sobrevuelos"],
        },
        "receptor": _receptor_publicado(),
        "proceso": _proceso_publicado(),
        # El detector de antena mal ubicada, del lado referenciado al RECEPTOR.
        # Va en esta pagina porque es la que afirma mas fuerte: "estos aviones
        # aterrizaron aca". Si la antena no esta donde dice la config, la
        # atribucion sigue siendo correcta (el cilindro se define alrededor del
        # aeropuerto) pero todo lo que se diga de la ANTENA no lo es.
        "coverage": coverage_report(observaciones, gate),
    })


@app.get("/api/adsb/status")
def api_adsb_status():
    """El estado del grabador MAS desde donde se esta midiendo.

    El receptor viaja aca porque /adsb es la pagina donde se aprieta el boton de
    grabar y hasta hoy no podia decir desde donde se iba a grabar: su subtitulo
    era "Aeronaves recibidas por el receptor RTL-SDR" y esta respuesta no traia
    ni lat/lon ni nombre ni is_default. La decision de empezar a grabar se tomaba
    sin ver la configuracion con la que se iba a grabar.
    """
    return JSONResponse({**adsb_service.status(),
                         "receptor": _receptor_publicado(),
                         "proceso": _proceso_publicado()})


@app.post("/api/adsb/start")
async def api_adsb_start(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    # El default sale de ADSB_SOURCE y no de "auto" fijo. "auto" nunca elige IQ
    # -resuelve a rtl_adsb si el .exe esta, y lo dice el propio title del
    # selector-, asi que el camino recomendado en ESTADO.md era el unico que
    # habia que pedir a mano. Eso convertia "llegar y prender" en "llegar,
    # prender y acordarse", y olvidarse no falla: graba igual, peor y en
    # silencio. Peor por dos cosas medidas: rtl_adsb no mide dBFS -sin eso no
    # se puede juzgar la ganancia, que es LA decision cuando la antena esta
    # pegada a la pista- y no corrige errores de un bit, que son el 4,5% de las
    # direcciones debiles (450x sobre el azar); 141 de los 143 fantasmas del
    # historico entraron por ese camino.
    result = adsb_service.start(
        mode=body.get("source") or os.environ.get("ADSB_SOURCE") or "auto")
    return JSONResponse(result)


@app.post("/api/adsb/stop")
def api_adsb_stop():
    return JSONResponse(adsb_service.stop())


@app.get("/adsb/download")
def adsb_download():
    from adsb_record import CSV_DIR
    files = sorted(CSV_DIR.glob("adsb_*.csv")) if CSV_DIR.exists() else []
    if not files:
        return JSONResponse({"error": "todavia no hay ningun CSV grabado"}, status_code=404)
    latest = files[-1]
    return FileResponse(latest, media_type="text/csv", filename=latest.name)


@app.get("/api/adsb/downloads")
def api_adsb_downloads():
    from adsb_record import CSV_DIR
    files = sorted(CSV_DIR.glob("adsb_*.csv")) if CSV_DIR.exists() else []
    return JSONResponse({
        "files": [{"name": f.name, "size": f.stat().st_size} for f in files]
    })


@app.get("/adsb/download/{filename}")
def adsb_download_one(filename: str):
    from adsb_record import CSV_DIR
    # Reject anything that isn't a plain filename inside CSV_DIR: filename
    # comes straight from the URL, and letting a path-traversal value like
    # "../../something" through would serve files outside the CSV directory.
    if "/" in filename or "\\" in filename or not filename.startswith("adsb_"):
        return JSONResponse({"error": "nombre invalido"}, status_code=400)
    path = CSV_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "no encontrado"}, status_code=404)
    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/adsb/analisis")
def adsb_analysis_page(request: Request):
    return templates.TemplateResponse(request, "adsb_analisis.html", {})


@app.get("/api/adsb/analisis")
def api_adsb_analysis():
    """Per-aircraft summary of everything recorded so far.

    Reads the SQLite log rather than the live recorder's rolling window: the
    window only keeps the last 15 minutes, and the point of this page is the
    whole history, including sessions recorded days ago.
    """
    import adsb_report
    from adsb_events import load_db

    db_path = ADSB_DB_PATH
    if not db_path.exists():
        return JSONResponse({"aircraft": [], "observations": 0, "fields": [],
                             "empty_reason": "todavia no se grabo nada"})

    # load_db devuelve tambien el filtro que corrio: sin el, coverage_report no
    # puede distinguir "no hubo rechazos" de "nadie evaluo nada".
    observaciones, gate = load_db(str(db_path))
    info = adsb_report.overview(observaciones, gate)
    return JSONResponse({
        "observations": info["observations"],
        "identified": info["identified"],
        "with_registration": info["with_registration"],
        "registry_available": info["registry_available"],
        # El recorte de alcance y el ruido descartado viajan al navegador para
        # que la pagina pueda decir DE QUE esta hablando. Un titular de "46
        # aeronaves" sin estos numeros al lado no se puede interpretar: no se
        # sabe si la antena recibio poco, si se recorto el radio, o si se
        # filtro ruido.
        "receiver_name": __import__("receiver").RECEIVER_NAME,
        "receptor": _receptor_publicado(),
        # El apartado de UN aeropuerto. Se calcula sobre las mismas
        # observaciones que el resto del informe -no se vuelve a leer la base-
        # para que las dos mitades de la pagina hablen del mismo conjunto.
        "airport": __import__("aeropuerto").como_json(
            __import__("aeropuerto").informe(observaciones)),
        "analysis_radius_km": info["analysis_radius_km"],
        "within_radius": info["within_radius"],
        "outside_radius": info["outside_radius"],
        "outside_radius_max_km": info["outside_radius_max_km"],
        "without_position": info["without_position"],
        "unconfirmed": info["unconfirmed"],
        "unconfirmed_messages": info["unconfirmed_messages"],
        "confirmed": info["confirmed"],
        "coverage": info["coverage"],
        "fields": info["fields"],
        "events": [
            {"icao24": e.icao24, "type": e.event_type, "timestamp": e.timestamp,
             "callsign": e.callsign, "reason": e.reason}
            for e in info["events"]
        ],
        "aircraft": [
            {"icao24": s.icao24, "callsign": s.callsign, "registration": s.registration,
             "aircraft_type": s.aircraft_type, "operator": s.operator,
             "messages": s.messages, "first_seen": s.first_seen, "last_seen": s.last_seen,
             "duration_s": s.duration_s, "min_altitude_ft": s.min_altitude_ft,
             "max_altitude_ft": s.max_altitude_ft, "max_speed_kt": s.max_speed_kt,
             "max_climb_fpm": s.max_climb_fpm, "max_descent_fpm": s.max_descent_fpm,
             # La posicion y la distancia se serializan explicitamente porque
             # este dict se arma campo por campo: agregar el campo al
             # AircraftSummary no alcanza para que llegue al navegador.
             "latitude": s.last_latitude, "longitude": s.last_longitude,
             "min_distance_km": s.min_distance_km,
             "max_distance_km": s.max_distance_km,
             "phase": s.phase, "events": len(s.events)}
            for s in info["aircraft"]
        ],
    })


@app.get("/adsb/mapa")
def adsb_map_page(request: Request):
    return templates.TemplateResponse(request, "adsb_mapa.html", {})


@app.get("/api/adsb/mapa")
def api_adsb_map(desde: int | None = None, ventana_h: float | None = None):
    """Trayectorias decodificadas, el receptor y los aeropuertos de la zona.

    Manda coordenadas crudas y deja proyectar al navegador: la escala del mapa
    depende de hasta donde llegaron los datos, y eso recien se sabe con todos
    los puntos juntos.

    Sin `desde` responde la carga completa. Con `desde=<id>` responde SOLO lo
    que entro despues de esa fila: `nuevos` filas, `nuevas_posiciones`
    posiciones y unicamente las aeronaves tocadas. Los ceros viajan EXPLICITOS
    porque 279 de 360 refrescos medidos son exactamente eso, y sin el campo el
    cliente no puede distinguir "no hay nada nuevo" de "la respuesta vino vacia
    por un error".
    """
    import time as _t

    ahora = _t.time()
    est = _estaticos_adsb()
    db_path = ADSB_DB_PATH
    if not db_path.exists():
        return JSONResponse({"base": "completa", "tracks": [], "cursor": 0,
                             "nuevos": 0, "nuevas_posiciones": 0,
                             "receiver": est["receiver"], "airports": est["airports"],
                             "geo": est["geo"], "estatico_v": est["estatico_v"],
                             "coverage": {}, "rejected": [], "lag_s": None,
                             "trazas_fuera_de_ventana": 0, "puntos_fuera_de_ventana": 0,
                             "empty_reason": "todavia no se grabo nada"})

    lector = lector_mapa()
    with lector._lock:
        completa, desde_ok, forzada = _resolver_base(lector, desde)
        avance = lector.avanzar()
        # avanzar() puede haber reconstruido el lector porque la base
        # retrocedio (archivo borrado y recreado, o filas borradas). Ahi el
        # cursor del cliente no significa nada -- apunta a ids de otra base --
        # asi que la respuesta pasa a completa y el motivo viaja en `forzada`,
        # el mismo campo que ya usa el reinicio del servidor.
        if avance["rebobinado"] is not None:
            completa, forzada = True, avance["rebobinado"]["motivo"]
        # ventana_h=0 pide la grabacion ENTERA. Es el boton de la pagina: la
        # ventana tiene que ser reversible sin tocar codigo, porque las 3 h son
        # un numero elegido para que la carga completa quede acotada y no una
        # medicion de cuanto tiempo permanece una aeronave en alcance.
        limite, ventana_publicada, ventana_nota = _resolver_ventana(ventana_h)
        fuera_t, fuera_p = (lector.fuera_de_ventana(limite, ahora) if limite
                            else (0, 0))
        comun = {
            "cursor": lector.cursor,
            "estatico_v": est["estatico_v"],
            # El unico numero que impide que la pagina mienta. Un poll exitoso
            # cada 5 s sobre una base atrasada 905 s es la forma mas convincente
            # de decir "en vivo" sin estarlo.
            "lag_s": (round(lector.lag_s(ahora), 1)
                      if lector.lag_s(ahora) is not None else None),
            "ventana_h": ventana_publicada,
            # Si el valor pedido no valia, se dice cual se uso y por que. Un
            # parametro reinterpretado en silencio es un descarte en silencio.
            "ventana_nota": ventana_nota,
            "trazas_fuera_de_ventana": fuera_t,
            "puntos_fuera_de_ventana": fuera_p,
            "servidor_ms": avance["ms"],
            # Cuantas veces este lector tuvo que reconstruirse porque la base
            # retrocedio, con el detalle de la ultima. Cero explicito: "nunca
            # paso" y "no se mira" son cosas distintas.
            "rebobinados": lector.rebobinados,
            "rebobinado": avance["rebobinado"],
        }

        if completa:
            trazas, ft, fp = lector.trazas(ventana_s=limite, ahora=ahora)
            comun["trazas_fuera_de_ventana"] = ft
            comun["puntos_fuera_de_ventana"] = fp
            return JSONResponse({
                **comun,
                "base": "completa",
                "forzada": forzada,
                "desde": desde_ok,
                "nuevos": avance["filas"], "nuevas_posiciones": avance["posiciones"],
                "tracks": trazas,
                "receiver": est["receiver"], "geo": est["geo"],
                "airports": est["airports"],
                "coverage": _coverage_mapa(lector),
                # Las descartadas viajan APARTE de las trazas y con sus
                # coordenadas intactas: el mapa las dibuja como cruz gris, sin
                # unirlas a nada y fuera del encuadre automatico. Tirarlas del
                # dibujo tambien seria descartarlas en silencio.
                #
                # Y viajan UNA vez: antes iban aca y otra vez completas dentro
                # de coverage.rejected_detail.
                "rejected": lector.rechazos_desde(0),
                # A la vista y no disimulado: despues de un reinicio el primer
                # navegador que entra paga la reconstruccion del historico
                # entero. 76 ms hoy sobre 11 423 filas, pero 3.64 s a 29 dias.
                "reconstruido_en_ms": lector.ms_reconstruccion,
            })

        nuevos_desde = max(0, lector.cursor - desde_ok)
        trazas = lector.delta_trazas(desde_ok)
        return JSONResponse({
            **comun,
            "base": "delta",
            "desde": desde_ok,
            "nuevos": nuevos_desde,
            "nuevas_posiciones": sum(len(t["points"]) for t in trazas),
            "tracks": trazas,
            "rejected_nuevos": lector.rechazos_desde(desde_ok),
            # coverage viaja SOLO si cambio. Cambia exactamente cuando entro al
            # menos una fila -- `observations` la cuenta -- asi que la condicion
            # es exacta y no una heuristica. Cuando no viaja, el cliente se
            # queda con el que tenia y la bandera lo dice.
            "coverage": _coverage_mapa(lector) if nuevos_desde else None,
            "coverage_sin_cambios": not nuevos_desde,
        })


# El bloque de arranque va AL FINAL del archivo, no en el medio. Estaba antes de
# los decoradores de /adsb/analisis, /api/adsb/analisis, /adsb/mapa y
# /api/adsb/mapa, y funcionaba de casualidad: uvicorn.run("main:app") vuelve a
# importar este modulo como "main" y esa segunda copia si registra todo, mientras
# la copia __main__ registraba esas cuatro rutas despues de que el servidor
# paraba. Importa porque `python main.py` es el camino de arranque documentado y
# el que efectivamente se uso el 23/08, y porque cualquier ruta nueva agregada
# abajo heredaba la misma fragilidad.
if __name__ == "__main__":
    import uvicorn

    # El unico rastro que queda de con que configuracion arranco este proceso.
    # La ubicacion vive solo en el entorno: nada del repo la escribe en un
    # archivo, y hasta hoy no se imprimia en ningun lado -- el resumen del .bat
    # se muestra en una ventana que se cierra a los 8 s por su propio
    # `timeout /t 8`, y el servidor arranca con `start /min`.
    import receiver as _receiver
    print(f"[receptor] {_receiver.resumen_configuracion(__import__('aeropuerto').objetivo())}"
          f" | PID {os.getpid()} | {sys.executable}", flush=True)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)

@app.post("/api/aeropuerto/excel")
async def api_aeropuerto_excel(request: Request):
    """Bajar la tabla de /aeropuerto a Excel, con las columnas que se ven.

    LAS COLUMNAS LLEGAN DEL NAVEGADOR y no se definen aca. La lista COLUMNAS
    vive en la plantilla -- de ahi salen el encabezado, el orden, la celda, los
    grupos y el glosario -- y escribir una segunda copia del lado del servidor
    es la duplicacion que este repo ya pago dos veces. Ver excel_operaciones.py.

    Como efecto util, la descarga respeta el filtro, la pestana y el orden
    activos: lo que se ve es lo que se baja.
    """
    import excel_operaciones

    cuerpo = await request.json()
    columnas = cuerpo.get("columnas") or []
    filas = cuerpo.get("filas") or []
    if not columnas:
        return JSONResponse({"error": "no llegaron columnas"}, status_code=400)

    contenido = excel_operaciones.construir(columnas, filas, cuerpo.get("meta"))
    nombre = f"operaciones-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})
