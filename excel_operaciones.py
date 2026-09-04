"""Bajar a Excel la tabla de operaciones, con las MISMAS columnas que se ven.

POR QUE NO ES adsb_decode_full.exportar_excel(). Ese vuelca MENSAJES crudos y
esta atado a ese dataset: arma su hoja de diccionario con adsb_catalogo, que
conoce campos de radio como nuc_p o sil pero no sabe nada de `pista` ni de
`confirmada`. Esto vuelca OPERACIONES ya clasificadas, que son otra tabla con
otras columnas. Compartir el escritor obligaria a parametrizar aquel hasta que
no explique nada.

POR QUE LAS COLUMNAS LAS MANDA EL NAVEGADOR. La tabla de /aeropuerto se arma
desde COLUMNAS, una lista que vive en la plantilla y de la que ya salen el
encabezado, el orden, la celda, los grupos y el glosario. Escribir aca una
segunda lista seria la misma duplicacion que este repo ya pago dos veces: los
dos acumuladores del cilindro, y los colspan 7/7/7 escritos a mano que se
desalineaban sin que nada fallara. Asi que el cliente manda las columnas que
ESTA mostrando, y el Excel no puede salir con otras.

Eso ademas hace que la descarga respete el filtro, la pestana y el orden
activos: lo que se ve es lo que se baja.

LOS VALORES VIAJAN CON SU TIPO. Se toman del JSON de la API y no del texto ya
formateado de la tabla, asi que una altitud es el numero 950 y no la cadena
"950 ft". En Excel esa es la diferencia entre poder ordenar y sumar una columna
o no. Es tambien lo que evita el problema que CLAUDE.md documenta para el CSV:
-34.6635 se ve como -34.663.541.114.936.400 en un Excel en castellano, porque el
punto es separador de miles. Un .xlsx guarda el numero y no su representacion.

La UNIDAD no se pierde: va en el encabezado, que es donde una planilla la
espera. Repetirla en cada celda convertiria la columna en texto.
"""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

AZUL = "1F3864"
GRIS = "3B3838"
BLANCO = "FFFFFF"
_BORDE = Border(*[Side(style="thin", color="BFBFBF")] * 4)

# Columnas cuyo valor es un instante en segundos Unix. Se convierten a fecha
# real de Excel para poder ordenarlas y restarlas; como texto no se puede.
FECHAS = {"timestamp", "last_seen"}

UNIDADES = {
    "min_altitude_ft": "ft", "min_altitude_ft_total": "ft", "max_altitude_ft": "ft",
    "min_distance_km": "km", "max_distance_km": "km",
    "min_distance_receptor_km": "km", "max_distance_receptor_km": "km",
    "max_speed_kt": "kt", "track_deg": "grados",
}

TITULOS_GRUPO = {
    "aeronave": "LA AERONAVE",
    "operacion": "LA OPERACIÓN (dentro del cilindro de este aeropuerto)",
    "historia": "TODO LO ESCUCHADO DE ELLA, EN CUALQUIER LUGAR",
}


def _encabezado(hoja, fila: int, columnas: list[dict]) -> None:
    """Dos filas -- los grupos y los titulos -- igual que la tabla de la pagina.

    Los grupos se cuentan de las columnas recibidas y no se escriben: es el
    mismo motivo por el que la pagina dejo de tener los colspan a mano.
    """
    grupos: list[list] = []
    for c in columnas:
        g = c.get("g") or ""
        if grupos and grupos[-1][0] == g:
            grupos[-1][1] += 1
        else:
            grupos.append([g, 1])

    col = 1
    for clave, cuantas in grupos:
        celda = hoja.cell(row=fila, column=col, value=TITULOS_GRUPO.get(clave, clave))
        celda.font = Font(bold=True, color=BLANCO, size=9)
        celda.fill = PatternFill("solid", fgColor=GRIS)
        celda.alignment = Alignment(horizontal="center", vertical="center")
        for i in range(cuantas):
            hoja.cell(row=fila, column=col + i).border = _BORDE
            hoja.cell(row=fila, column=col + i).fill = PatternFill("solid", fgColor=GRIS)
        if cuantas > 1:
            hoja.merge_cells(start_row=fila, start_column=col,
                             end_row=fila, end_column=col + cuantas - 1)
        col += cuantas

    for i, c in enumerate(columnas, 1):
        unidad = UNIDADES.get(c.get("k"))
        celda = hoja.cell(row=fila + 1, column=i,
                          value=f"{c.get('t')} ({unidad})" if unidad else c.get("t"))
        celda.font = Font(bold=True, color=BLANCO, size=9.5)
        celda.fill = PatternFill("solid", fgColor=AZUL)
        celda.alignment = Alignment(vertical="center", wrap_text=True)
        celda.border = _BORDE
        # La ayuda de cada columna -- la misma que el glosario y el tooltip de
        # la pagina -- va como comentario de celda: quien abre el Excel tiene el
        # mismo texto que quien mira la tabla, sin tener que volver a la pagina.
        ayuda = c.get("ayuda")
        if ayuda:
            celda.comment = Comment(ayuda, "RADAR YPF", width=400, height=120)


def construir(columnas: list[dict], filas: list[dict], meta: dict | None = None) -> bytes:
    """El .xlsx en memoria. `columnas` son las que el navegador esta mostrando."""
    meta = meta or {}
    wb = Workbook()
    hoja = wb.active
    hoja.title = "Operaciones"

    hoja.cell(row=1, column=1,
              value=meta.get("titulo") or "Operaciones del aeropuerto").font = Font(
                  bold=True, size=14, color=AZUL)

    # De donde salen estos numeros, DENTRO del archivo. Un Excel se manda por
    # mail y se abre tres semanas despues en otra maquina: una distancia no
    # significa nada sin saber desde donde se midio. Es el mismo motivo por el
    # que las cinco paginas llevan la franja del receptor.
    partes = []
    if meta.get("receptor"):
        partes.append(f"medido desde {meta['receptor']}")
    if meta.get("filtro"):
        partes.append(f"filtro: {meta['filtro']}")
    partes.append(f"{len(filas)} operaciones")
    partes.append(f"bajado el {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    hoja.cell(row=2, column=1, value=" · ".join(partes)).font = Font(
        italic=True, size=9.5, color="595959")

    _encabezado(hoja, 4, columnas)

    fila = 6
    for f in filas:
        for i, c in enumerate(columnas, 1):
            valor = f.get(c.get("k"))
            if isinstance(valor, bool):
                # ANTES que el caso numerico: en Python un bool ES un int, y sin
                # esto "confirmada" saldria como 1 y 0 en vez de si y no.
                valor = "sí" if valor else "no"
            elif c.get("k") in FECHAS and isinstance(valor, (int, float)):
                valor = datetime.fromtimestamp(valor)
            celda = hoja.cell(row=fila, column=i, value=valor)
            celda.font = Font(size=9.5)
            celda.border = _BORDE
            if isinstance(valor, datetime):
                celda.number_format = "DD/MM/YYYY HH:MM"
            elif isinstance(valor, float):
                celda.number_format = "0.00"
        fila += 1

    for i, c in enumerate(columnas, 1):
        ancho = max([len(str(c.get("t") or "")) + 4]
                    + [len(str(f.get(c.get("k")) or "")) for f in filas[:200]])
        hoja.column_dimensions[get_column_letter(i)].width = min(max(ancho + 2, 10), 46)
    hoja.freeze_panes = "A6"
    hoja.auto_filter.ref = f"A5:{get_column_letter(len(columnas))}{max(fila - 1, 5)}"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
