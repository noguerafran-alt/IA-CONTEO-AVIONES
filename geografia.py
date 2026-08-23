"""Geografia fija de la zona: la costa del Rio de la Plata y las pistas.

Son datos de terceros bajados una vez y horneados aca como coordenadas, no
consultados en vivo. El sistema tiene que funcionar sin internet, asi que un
mapa que pide tiles o una API cada vez que se abre esta descartado; y una costa
o una pista puesta a ojo se ve igual de convincente que una real, que es peor
que no dibujarla.

FUENTES

  Costa: Natural Earth, ne_10m_coastline (dominio publico).
  https://github.com/nvkelso/natural-earth-vector
  Recortada al recuadro lat -36.2..-33.2, lon -59.8..-55.8, que es el estuario
  entero. Son dos lineas separadas y no un poligono: la orilla argentina y la
  uruguaya. Dibujarlas como un poligono cerrado inventaria una costa donde el
  rio se abre al Atlantico.

  Pistas: OurAirports, runways.csv (dominio publico).
  https://davidmegginson.github.io/ourairports-data/
  Coordenadas de los DOS umbrales de cada pista, asi que la orientacion y el
  largo salen del dato y no de la designacion. Importa: la designacion es rumbo
  MAGNETICO redondeado a diez grados, y en Buenos Aires la declinacion ronda los
  -8 grados, asi que deducir la orientacion de "13/31" la deja varios grados
  torcida. Con los dos umbrales no hace falta suponer nada.
"""
from __future__ import annotations

# Orilla argentina EN EL ORDEN DEL TRAZO de Natural Earth: del delta del Parana,
# pasando por Buenos Aires, hasta la costa atlantica.
#
# El orden se conserva tal como viene y NO se reordena por latitud. Ordenar una
# costa por latitud parece inofensivo y la destruye: una costa no es monotona en
# latitud -tiene bahias, el delta, la vuelta de Punta del Este- asi que ordenar
# hace que el trazo salte de un lado al otro. Medido: la orilla uruguaya pasaba
# de 394 km a 1346 km de largo, 3.4 veces mas, y en el mapa se veia como rayas
# horizontales cruzando el rio.
COSTA_AR: list[tuple[float, float]] = [
    (-33.2131, -58.432), (-33.2324, -58.4332), (-33.2755, -58.4171),
    (-33.2983, -58.4123), (-33.308, -58.4224), (-33.3161, -58.4401),
    (-33.3347, -58.4599), (-33.3696, -58.4879), (-33.4059, -58.5065),
    (-33.4493, -58.5217), (-33.4939, -58.5318), (-33.5339, -58.5356),
    (-33.579, -58.5288), (-33.589, -58.531), (-33.609, -58.5408), (-33.683, -58.5494),
    (-33.7053, -58.5492), (-33.7452, -58.5407), (-33.7778, -58.5243),
    (-33.8425, -58.4694), (-33.8595, -58.461), (-33.879, -58.4556),
    (-33.9246, -58.4521), (-33.9797, -58.4394), (-33.9899, -58.4417),
    (-34.0014, -58.4466), (-34.0069, -58.447), (-34.0105, -58.4472),
    (-34.0144, -58.4363), (-34.0162, -58.427), (-34.026, -58.4027),
    (-34.0312, -58.3954), (-34.0465, -58.3878), (-34.0663, -58.3844),
    (-34.11, -58.3854), (-34.142, -58.3913), (-34.1541, -58.3922),
    (-34.1596, -58.3901), (-34.1772, -58.3807), (-34.1882, -58.3786),
    (-34.2187, -58.4121), (-34.226, -58.4163), (-34.2399, -58.4215),
    (-34.2467, -58.4258), (-34.2612, -58.4411), (-34.2729, -58.4602),
    (-34.2774, -58.4801), (-34.2708, -58.4984), (-34.269, -58.5064),
    (-34.2677, -58.5431), (-34.2726, -58.5493), (-34.2795, -58.5528),
    (-34.2854, -58.5583), (-34.2882, -58.5704), (-34.3157, -58.5123),
    (-34.3285, -58.4946), (-34.348, -58.4666), (-34.3662, -58.4546),
    (-34.3982, -58.4732), (-34.4071, -58.4855), (-34.418, -58.4966),
    (-34.4359, -58.5115), (-34.449, -58.5104), (-34.4631, -58.5032),
    (-34.4833, -58.4773), (-34.4918, -58.4737), (-34.512, -58.475),
    (-34.5216, -58.4742), (-34.5338, -58.4516), (-34.542, -58.4334),
    (-34.5552, -58.4128), (-34.5725, -58.3788), (-34.5861, -58.3634),
    (-34.6079, -58.345), (-34.632, -58.3318), (-34.6572, -58.315),
    (-34.7008, -58.2362), (-34.7352, -58.1901), (-34.7504, -58.1549),
    (-34.7536, -58.1183), (-34.7577, -58.0939), (-34.7729, -58.0635),
    (-34.7791, -58.0306), (-34.7873, -58.0002), (-34.8245, -57.965),
    (-34.8337, -57.9382), (-34.8289, -57.882), (-34.8299, -57.871),
    (-34.8481, -57.8516), (-34.8723, -57.8064), (-34.9097, -57.7588),
    (-34.9339, -57.6868), (-34.9877, -57.6036), (-35.0132, -57.522),
    (-35.0444, -57.4875), (-35.0705, -57.4467), (-35.1499, -57.3496),
    (-35.1891, -57.3139), (-35.2297, -57.2703), (-35.2488, -57.2488),
    (-35.3102, -57.2016), (-35.3717, -57.1622), (-35.4167, -57.1336),
    (-35.4413, -57.1281), (-35.4842, -57.1449), (-35.5221, -57.1804),
    (-35.5693, -57.2238), (-35.6964, -57.3236), (-35.7275, -57.3526),
    (-35.7638, -57.3656), (-35.79, -57.3714), (-35.8314, -57.3812),
    (-35.863, -57.3933), (-35.8996, -57.3835), (-35.9395, -57.3736),
    (-35.9684, -57.3725), (-35.977, -57.3706), (-35.9879, -57.3664),
    (-36.0011, -57.348), (-36.0112, -57.3439), (-36.0139, -57.342),
    (-36.0695, -57.316), (-36.0908, -57.3127), (-36.0999, -57.3088),
    (-36.1072, -57.2911), (-36.1236, -57.2719), (-36.1279, -57.2682),
    (-36.1502, -57.2623), (-36.1703, -57.248),
]

# Orilla uruguaya, la que cierra el estuario del otro lado. Sin esta el rio no
# se lee como un rio: queda una linea suelta cruzando el mapa.
#
# Viene en sentido contrario a la argentina -de Punta del Este hacia el delta-,
# asi que las dos concatenadas TAL CUAL ya forman un anillo cerrado: la
# argentina baja por la orilla oeste y la uruguaya vuelve por la este. No hay
# que invertir ninguna, y hacerlo cruzaria el poligono en diagonal.
COSTA_UY: list[tuple[float, float]] = [
    (-34.7931, -55.8588), (-34.8035, -55.8891), (-34.8394, -55.9544),
    (-34.8691, -56.0041), (-34.901, -56.0696), (-34.8984, -56.1052),
    (-34.9128, -56.1356), (-34.9203, -56.1499), (-34.9412, -56.1561),
    (-34.9163, -56.1773), (-34.9135, -56.1945), (-34.9077, -56.213),
    (-34.8987, -56.2047), (-34.8875, -56.2035), (-34.8796, -56.2146),
    (-34.8777, -56.2306), (-34.8929, -56.2365), (-34.9062, -56.2563),
    (-34.9063, -56.2658), (-34.9035, -56.288), (-34.9062, -56.3113),
    (-34.884, -56.3428), (-34.8548, -56.4029), (-34.843, -56.4184),
    (-34.8282, -56.4208), (-34.8161, -56.4038), (-34.8019, -56.3868),
    (-34.7966, -56.3613), (-34.7827, -56.3761), (-34.7759, -56.3957),
    (-34.7583, -56.4484), (-34.7535, -56.4851), (-34.7548, -56.5204),
    (-34.767, -56.5419), (-34.7651, -56.558), (-34.759, -56.5736), (-34.7394, -56.611),
    (-34.7326, -56.6285), (-34.7226, -56.6464), (-34.707, -56.7135),
    (-34.6985, -56.7953), (-34.6702, -56.8739), (-34.6601, -56.8907),
    (-34.6355, -56.9042), (-34.6232, -56.9197), (-34.6034, -56.9529),
    (-34.581, -56.9766), (-34.5641, -56.9974), (-34.5521, -57.0158),
    (-34.5381, -57.0427), (-34.5271, -57.0574), (-34.5134, -57.0559),
    (-34.5065, -57.0624), (-34.4981, -57.077), (-34.4936, -57.0832),
    (-34.4758, -57.102), (-34.463, -57.12), (-34.4509, -57.152), (-34.4421, -57.2032),
    (-34.4403, -57.2922), (-34.4435, -57.3469), (-34.4304, -57.3652),
    (-34.4315, -57.4114), (-34.4446, -57.437), (-34.4315, -57.4491),
    (-34.4295, -57.4747), (-34.4406, -57.5026), (-34.4536, -57.5379),
    (-34.4265, -57.5695), (-34.4255, -57.595), (-34.4305, -57.6242),
    (-34.4454, -57.6643), (-34.4634, -57.7203), (-34.4613, -57.7385),
    (-34.4743, -57.7641), (-34.4722, -57.7933), (-34.4751, -57.8225),
    (-34.471, -57.8554), (-34.463, -57.8456), (-34.4479, -57.854), (-34.4407, -57.882),
    (-34.4165, -57.9002), (-34.3763, -57.8976), (-34.3537, -57.9228),
    (-34.3263, -57.9427), (-34.2531, -58.0169), (-34.24, -58.0498),
    (-34.2247, -58.0614), (-34.192, -58.077), (-34.1778, -58.0946),
    (-34.1692, -58.1194), (-34.1651, -58.1459), (-34.164, -58.1691),
    (-34.1594, -58.1962), (-34.1476, -58.2087), (-34.11, -58.2203),
    (-34.0888, -58.2318), (-33.9865, -58.3165), (-33.9733, -58.3348),
    (-33.9544, -58.3724), (-33.9421, -58.3888), (-33.9228, -58.4027),
    (-33.9023, -58.41), (-33.7849, -58.4346), (-33.6985, -58.4394),
    (-33.6129, -58.4258), (-33.5921, -58.429), (-33.5724, -58.435),
    (-33.5514, -58.4383), (-33.5278, -58.4332), (-33.5082, -58.4196),
    (-33.4703, -58.3854), (-33.4487, -58.3786), (-33.4291, -58.3849),
    (-33.4096, -58.4126), (-33.3899, -58.4189), (-33.3717, -58.4126),
    (-33.3556, -58.3972), (-33.3258, -58.3612), (-33.3058, -58.3507),
    (-33.2828, -58.3492),
]

# Una entrada por pista, con los dos umbrales. El largo y el rumbo verdadero
# vienen del mismo registro y se conservan para poder mostrarlos.
PISTAS: list[dict] = [
    {"apt": "SABE", "nombre": "13/31", "le": (-34.553902, -58.425098), "he": (-34.564499, -58.406101), "largo_m": 2350, "rumbo": 124.0, "elev_ft": 16},
    {"apt": "SADF", "nombre": "5/23", "le": (-34.459801, -58.597099), "he": (-34.447399, -58.582802), "largo_m": 1801, "rumbo": 44.0, "elev_ft": 10},
    {"apt": "SAEZ", "nombre": "11/29", "le": (-34.819099, -58.553501), "he": (-34.825401, -58.5182), "largo_m": 3300, "rumbo": 102.3, "elev_ft": 64},
    {"apt": "SAEZ", "nombre": "17/35", "le": (-34.8083, -58.533901), "he": (-34.835201, -58.524601), "largo_m": 3105, "rumbo": 164.0, "elev_ft": 64},
]


# Elevacion media del campo, en pies. Hace falta para saber que altitud
# significa "en el suelo": la altitud ADS-B es barometrica sobre el nivel del
# mar, no sobre la pista. En Aeroparque son 18 ft y da casi igual, pero en
# Ezeiza son 64 y en un aeropuerto de altura la diferencia decide si un
# aterrizaje se cuenta o no.
ELEVACION_FT: dict[str, float] = {
    "SABE": 16,
    "SADF": 10,
    "SAEZ": 64,
}


def como_json() -> dict:
    """Lo que el mapa del navegador necesita, en un solo objeto."""
    return {
        "costa": [[list(p) for p in COSTA_AR], [list(p) for p in COSTA_UY]],
        "pistas": [
            {"apt": p["apt"], "nombre": p["nombre"],
             "le": list(p["le"]), "he": list(p["he"]),
             "largo_m": p["largo_m"], "rumbo": p["rumbo"],
             "elev_ft": p.get("elev_ft", 0)}
            for p in PISTAS
        ],
    }
