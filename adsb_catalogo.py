"""Que significa cada campo que sale del decodificador, y de que mensaje viene.

Existe separado del decodificador porque cumple otra funcion: el decodificador
produce columnas, esto explica que es cada una. Un Excel de 60 columnas con
nombres como `nuc_p` o `sil_supplement` es ilegible sin esta tabla, y la
alternativa -- que cada analisis empiece por adivinar que significa cada
columna -- es como se fabrican conclusiones equivocadas.

`ORDEN` decide el orden de las columnas exportadas. No es cosmetico: agrupa por
FAMILIA DE MENSAJE, y esa agrupacion es la que hace visible el hecho central de
ADS-B -- que cada fila viene de UN mensaje y por lo tanto trae datos de UN solo
grupo, con el resto vacio. Ordenadas alfabeticamente los grupos se entreveran y
el patron desaparece.
"""
from __future__ import annotations

# campo -> (que es, unidad, de que mensaje sale)
CATALOGO: dict[str, tuple[str, str, str]] = {
    # --- derivados (los calcula este proyecto, no vienen en el aire) ---
    "utc": ("Momento de recepcion", "ISO 8601", "derivado"),
    "epoch": ("Momento de recepcion", "segundos Unix", "derivado"),
    "hex": ("El mensaje crudo tal como llego", "hex", "derivado"),
    "dbfs": ("Nivel de senal del mensaje (0 = saturacion). Solo lo mide el "
             "camino de IQ crudo; vacio significa que esa fuente no lo mide",
             "dBFS", "derivado: adsb_iq"),
    "registration": ("Matricula", "-", "derivado: base OpenSky por icao24"),
    "aircraft_type": ("Modelo", "-", "derivado: base OpenSky por icao24"),
    "operator": ("Explotador", "-", "derivado: base OpenSky por icao24"),
    "df_nombre": ("Tipo de mensaje en castellano", "-", "derivado"),
    "bds_nombre": ("Registro BDS en castellano", "-", "derivado"),
    "distancia_km": ("Distancia a la antena", "km", "derivado: haversine"),
    "sobre_horizonte": ("Si llego de mas lejos que el horizonte de radio para "
                        "su altitud (sospechoso)", "bool", "derivado"),

    # --- cabecera: en todos los mensajes ---
    "df": ("Downlink Format: que clase de mensaje es", "0-24", "cabecera"),
    "icao": ("Direccion ICAO24 del transpondedor", "hex", "cabecera"),
    "crc_valid": ("True verificado, False corrupto, VACIO no verificable "
                  "(DF0/4/5/11/16 llevan la paridad XOR-eada con la direccion)",
                  "bool", "cabecera"),
    "typecode": ("Type Code: que trae el ADS-B", "1-31", "DF17/18"),
    "icao_verified": ("Si la direccion ICAO24 quedo confirmada por CRC. En "
                      "los DF sin CRC verificable la direccion puede ser ruido",
                      "bool", "cabecera"),
    "bds": ("Registro Comm-B equivalente", "-", "DF17/18/20/21"),

    # --- BDS 0,5 posicion en vuelo (TC 9-18, 20-22) ---
    "altitude": ("Altitud barometrica", "ft", "BDS 0,5 / 0,6 / DF0/4/16/20"),
    "latitude": ("Latitud resuelta", "grados", "BDS 0,5 / 0,6 (CPR)"),
    "longitude": ("Longitud resuelta", "grados", "BDS 0,5 / 0,6 (CPR)"),
    "cpr_format": ("Trama CPR par (0) o impar (1)", "0/1", "BDS 0,5 / 0,6"),
    "cpr_lat": ("Latitud CPR sin resolver", "cuentas", "BDS 0,5 / 0,6"),
    "cpr_lon": ("Longitud CPR sin resolver", "cuentas", "BDS 0,5 / 0,6"),
    "surveillance_status": ("0 ninguno, 1 alerta permanente, 2 alerta "
                            "transitoria, 3 SPI", "0-3", "BDS 0,5"),
    "nic_b": ("Suplemento B de integridad de posicion", "0/1", "BDS 0,5"),
    "nuc_p": ("Categoria de incertidumbre de posicion: cuanto error declara "
              "el avion", "0-9", "BDS 0,5"),
    "geo_minus_baro": ("Altitud GNSS menos barometrica: mide directamente el "
                       "error del altimetro", "ft", "BDS 0,9"),

    # --- BDS 0,6 posicion en superficie (TC 5-8) ---
    "movement": ("Campo crudo de movimiento en tierra", "0-127", "BDS 0,6"),
    "track_status": ("Si el rumbo en tierra es valido", "0/1", "BDS 0,6"),

    # --- BDS 0,8 identificacion (TC 1-4) ---
    "callsign": ("Indicativo del VUELO (no del avion)", "-", "BDS 0,8 / 2,0"),
    "category": ("Categoria del emisor dentro de su TC", "0-7", "BDS 0,8"),
    "wake_vortex": ("Categoria de estela: liviano, pesado, helicoptero...",
                    "-", "BDS 0,8"),

    # --- BDS 0,9 velocidad en vuelo (TC 19) ---
    "groundspeed": ("Velocidad respecto al SUELO", "kt", "BDS 0,9 / 0,6 / 5,0"),
    "track": ("Rumbo sobre el suelo: hacia donde se mueve", "grados",
              "BDS 0,9 / 0,6"),
    "heading": ("Rumbo de la NARIZ: hacia donde apunta. La resta contra track "
                "es la deriva por viento", "grados", "BDS 0,9"),
    "vertical_rate": ("Regimen vertical", "ft/min", "BDS 0,9"),
    "vr_source": ("Si el regimen vertical es barometrico o GNSS", "-", "BDS 0,9"),
    "airspeed": ("Velocidad AEREA (no respecto al suelo)", "kt", "BDS 0,9"),
    "airspeed_type": ("Si airspeed es indicada (IAS) o verdadera (TAS)", "-",
                      "BDS 0,9"),
    "nac_v": ("Categoria de exactitud de la velocidad", "0-4", "BDS 0,9"),
    "subtype": ("Subtipo dentro del Type Code", "-",
                "BDS 0,9 / 6,1 / 6,2 / 6,5"),

    # --- BDS 6,1 estado (TC 28) ---
    "squawk": ("Codigo transpondedor. 7500 secuestro, 7600 falla de radio, "
               "7700 emergencia", "octal", "BDS 6,1 / DF5 / DF21"),
    "emergency_state": ("Emergencia declarada", "0-7", "BDS 6,1"),

    # --- BDS 6,2 estado y objetivo (TC 29) ---
    "selected_altitude": ("La altitud que el piloto MARCO en el piloto "
                          "automatico", "ft", "BDS 6,2 / 4,0"),
    "selected_altitude_mcp": ("Altitud marcada en el MCP/FCU (el tablero del "
                              "piloto automatico)", "ft", "BDS 4,0"),
    "selected_altitude_fms": ("Altitud cargada en el FMS (el plan de vuelo)",
                              "ft", "BDS 4,0"),
    "target_altitude_source": ("Si el objetivo de altitud sale del MCP, del "
                               "FMS o del control", "-", "BDS 4,0"),
    "selected_altitude_source": ("De donde sale selected_altitude", "-",
                                 "BDS 6,2"),
    "selected_heading": ("Rumbo seleccionado en el piloto automatico",
                         "grados", "BDS 6,2"),
    "baro_pressure_setting": ("EL QNH QUE TIENE PUESTO EL AVION. Con esto el "
                              "offset barometrico se mide en vez de estimarse",
                              "hPa", "BDS 6,2 / 4,0"),
    "autopilot": ("Piloto automatico conectado", "bool", "BDS 6,2"),
    "vnav_mode": ("Modo de navegacion vertical activo", "bool", "BDS 6,2 / 4,0"),
    "lnav_mode": ("Modo de navegacion lateral activo", "bool", "BDS 6,2"),
    "altitude_hold_mode": ("Manteniendo altitud", "bool", "BDS 6,2 / 4,0"),
    "approach_mode": ("Modo aproximacion armado", "bool", "BDS 6,2 / 4,0"),
    "tcas_operational": ("TCAS operativo", "bool", "BDS 6,2"),
    "nic_baro": ("Integridad de la altitud barometrica", "0/1", "BDS 6,2 / 6,5"),

    # --- BDS 6,5 estado operacional (TC 31) ---
    "version": ("Version de ADS-B del equipo (0, 1 o 2)", "-", "BDS 6,5"),
    "nic_supplement_a": ("Suplemento A de integridad", "0/1", "BDS 6,5"),
    "nac_p": ("Categoria de exactitud de la posicion", "0-11", "BDS 6,5 / 6,2"),
    "sil": ("Nivel de integridad de la fuente", "0-3", "BDS 6,5 / 6,2"),
    "sil_supplement": ("Si SIL es por hora o por muestra", "0/1", "BDS 6,5"),
    "hrd": ("Si los rumbos son al norte magnetico o verdadero", "0/1", "BDS 6,5"),
    "capability_class": ("Capacidades declaradas del equipo", "bits", "BDS 6,5"),
    "operational_mode": ("Modo operacional declarado", "bits", "BDS 6,5"),

    # --- DF0/16 ACAS ---
    "vertical_status": ("EN TIERRA O EN VUELO, dicho por el avion", "-",
                        "DF0/16"),
    "cross_link_capability": ("Capacidad de cross-link", "0/1", "DF0/16"),
    "sensitivity_level": ("Nivel de sensibilidad del ACAS", "0-7", "DF0/16"),
    "reply_information": ("Capacidad de respuesta / velocidad maxima", "0-15",
                          "DF0/16"),
    "mv": ("Campo MV crudo (contiene el aviso de resolucion TCAS)", "hex",
           "DF16"),

    # --- DF4/5 vigilancia ---
    "flight_status": ("Estado de vuelo: incluye en tierra/en vuelo y alerta",
                      "0-7", "DF4/5/20/21"),
    "flight_status_text": ("flight_status en palabras", "-", "DF4/5/20/21"),
    "downlink_request": ("Pedido de enlace descendente", "0-31", "DF4/5/20/21"),
    "utility_message": ("Mensaje de utilidad", "0-63", "DF4/5/20/21"),

    # --- DF11 ---
    "capability": ("Capacidad del transpondedor", "0-7", "DF11"),
    "capability_text": ("capability en palabras", "-", "DF11"),

    # --- BDS 5,0 track and turn (Comm-B) ---
    "roll": ("Angulo de alabeo: cuanto esta inclinado", "grados", "BDS 5,0"),
    "true_track": ("Rumbo verdadero", "grados", "BDS 5,0"),
    "track_rate": ("Regimen de viraje", "grados/s", "BDS 5,0"),
    "true_airspeed": ("Velocidad aerea verdadera", "kt", "BDS 5,0"),

    # --- BDS 6,0 heading and speed (Comm-B) ---
    "magnetic_heading": ("Rumbo magnetico", "grados", "BDS 6,0"),
    "indicated_airspeed": ("Velocidad indicada", "kt", "BDS 6,0"),
    "mach": ("Numero de Mach", "-", "BDS 6,0"),
    "baro_vertical_rate": ("Regimen vertical barometrico", "ft/min", "BDS 6,0"),
    "inertial_vertical_rate": ("Regimen vertical inercial", "ft/min", "BDS 6,0"),

    # --- BDS 4,4 / 4,5 meteorologia (Comm-B, poco frecuentes) ---
    "wind_speed": ("Viento medido por el avion", "kt", "BDS 4,4"),
    "wind_direction": ("Direccion del viento", "grados", "BDS 4,4"),
    "static_air_temperature": ("Temperatura del aire", "C", "BDS 4,4 / 4,5"),
    "static_pressure": ("Presion estatica", "hPa", "BDS 4,4 / 4,5"),
    "humidity": ("Humedad relativa", "%", "BDS 4,4"),
    "turbulence": ("Turbulencia informada", "0-3", "BDS 4,4 / 4,5"),
    "icing": ("Engelamiento informado", "0-3", "BDS 4,5"),
    "wind_shear": ("Cortante de viento informada", "0-3", "BDS 4,5"),
    "microburst": ("Microrrafaga informada", "0-3", "BDS 4,5"),
    "radio_height": ("Altura por radioaltimetro", "ft", "BDS 4,5"),
    "figure_of_merit": ("Calidad del dato meteorologico", "-", "BDS 4,4"),
    "supported_bds": ("Que registros BDS declara soportar", "lista", "BDS 1,7"),
}

# El orden de las columnas en el Excel. Agrupado por familia de mensaje.
ORDEN = [
    "utc", "epoch", "dbfs", "icao", "registration", "aircraft_type", "operator",
    "callsign", "df", "df_nombre", "typecode", "bds", "bds_nombre", "crc_valid",
    "icao_verified",
    "altitude", "latitude", "longitude", "distancia_km", "sobre_horizonte",
    "groundspeed", "track", "heading", "vertical_rate", "vr_source",
    "airspeed", "airspeed_type", "geo_minus_baro",
    "vertical_status", "flight_status", "flight_status_text",
    "squawk", "emergency_state", "surveillance_status",
    "category", "wake_vortex",
    "selected_altitude", "selected_altitude_mcp", "selected_altitude_fms",
    "selected_altitude_source", "target_altitude_source", "baro_pressure_setting",
    "selected_heading", "autopilot", "vnav_mode", "lnav_mode",
    "altitude_hold_mode", "approach_mode", "tcas_operational",
    "roll", "true_track", "track_rate", "true_airspeed",
    "magnetic_heading", "indicated_airspeed", "mach",
    "baro_vertical_rate", "inertial_vertical_rate",
    "wind_speed", "wind_direction", "static_air_temperature", "static_pressure",
    "humidity", "turbulence", "icing", "wind_shear", "microburst",
    "radio_height", "figure_of_merit",
    "version", "nac_p", "nac_v", "nuc_p", "nic_b", "nic_baro",
    "nic_supplement_a", "sil", "sil_supplement", "hrd",
    "capability", "capability_text", "capability_class", "operational_mode",
    "cross_link_capability", "sensitivity_level", "reply_information",
    "downlink_request", "utility_message", "supported_bds", "mv",
    "movement", "track_status", "subtype",
    "cpr_format", "cpr_lat", "cpr_lon",
    "hex",
]

DF_NOMBRE = {
    0: "DF0 vigilancia aire-aire (ACAS)",
    4: "DF4 respuesta de altitud a radar",
    5: "DF5 respuesta de identidad a radar",
    11: "DF11 respuesta all-call",
    16: "DF16 vigilancia aire-aire larga (ACAS)",
    17: "DF17 ADS-B (extended squitter)",
    18: "DF18 ADS-B de equipo no transpondedor",
    19: "DF19 militar",
    20: "DF20 Comm-B con altitud",
    21: "DF21 Comm-B con identidad",
    24: "DF24 Comm-D",
}

BDS_NOMBRE = {
    "0,5": "posicion en vuelo",
    "0,6": "posicion en superficie",
    "0,7": "estado operacional (version 0)",
    "0,8": "identificacion y categoria",
    "0,9": "velocidad en vuelo",
    "1,0": "capacidad de enlace",
    "1,7": "registros BDS soportados",
    "2,0": "identificacion (Comm-B)",
    "3,0": "aviso de resolucion ACAS",
    "4,0": "intencion vertical seleccionada",
    "4,4": "informe meteorologico (MRAR)",
    "4,5": "informe de peligros meteorologicos (MHR)",
    "5,0": "rumbo y viraje (track and turn)",
    "6,0": "rumbo y velocidad",
    "6,1": "estado de la aeronave / emergencia",
    "6,2": "estado y objetivo seleccionado",
    "6,5": "estado operacional",
}


def describir(campo: str) -> tuple[str, str, str]:
    """Que es un campo. Los no catalogados se marcan, no se inventan."""
    return CATALOGO.get(
        campo, ("(sin catalogar: lo emitio pyModeS y este catalogo no lo "
                "describe todavia)", "-", "-"))
