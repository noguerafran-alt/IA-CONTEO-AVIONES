# IA-CONTEO-AVIONES

Detección, seguimiento y conteo de aterrizajes y despegues sobre video de pista, con identificación de aerolínea, matrícula y tipo de avión.

Usa [supervision](https://github.com/roboflow/supervision) + Ultralytics YOLO para la visión, y un modelo de visión-lenguaje para leer matrículas.

---

## Instalación desde cero en otra PC

```bash
git clone https://github.com/noguerafran-alt/IA-CONTEO-AVIONES.git
cd IA-CONTEO-AVIONES

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt        # Windows
# source .venv/bin/activate && pip install -r requirements.txt      # Linux/Mac
```

Eso es todo lo obligatorio. Los pesos del detector (`yolov8n.pt`) los descarga Ultralytics solo la primera vez que corrés el pipeline.

### Opcional: leer matrícula y tipo de avión

Requiere una clave gratuita de [OpenRouter](https://openrouter.ai/keys):

```bash
cp .env.example .env      # y editá .env con tu clave
```

Sin esto el sistema funciona igual: detecta, sigue y cuenta aviones, y lee la aerolínea con OCR local. Lo único que no hace es leer la matrícula.

### Opcional: acelerar con GPU Intel

```bash
.venv\Scripts\python.exe export_openvino.py --weights yolov8n.pt
```

Medido en una Intel Iris Xe: la detección pasa de 6,9 a 40,2 fps.

### Probar que quedó bien

```bash
.venv\Scripts\python.exe detect_track_count.py \
  --source tu_video.mp4 \
  --line-start 960,0 --line-end 960,1080 --no-display
```

Y para ver el dashboard, doble clic en `dashboard.bat` (Windows) o:

```bash
.venv\Scripts\python.exe -m uvicorn main:app --app-dir webapp --port 8000
```

### Qué NO está en el repositorio

Por tamaño o por derechos de terceros, y cómo obtenerlo:

| Falta | Por qué | Cómo se consigue |
|---|---|---|
| `data/` (videos) | Contenido de terceros, GB de peso | Poné tus propios videos ahí |
| `DATOS AVIONES/` (PDFs) | Material de un sitio de terceros | Los datos ya extraídos están en `aircraft_specs.json` |
| `.venv/` | Se recrea | `pip install -r requirements.txt` |
| `output/`, `dataset/`, `runs/` | Se generan | Corriendo el pipeline |
| `yolov8n.pt` | Se descarga solo | Automático en el primer uso |
| `.env` | **Contiene tu clave privada** | Copiá `.env.example` |

---

## 1. Calibrar la línea virtual

Sacá un frame de referencia con grilla de píxeles para elegir los puntos de la línea:

```bash
./.venv/Scripts/python.exe get_frame.py --source data/tu_video.mp4 --output output/sample_frame.png
```

Abrí `output/sample_frame.png`, elegí dos puntos que crucen la pista (perpendicular a la dirección de rodaje).

## 2. Correr el pipeline

```bash
./.venv/Scripts/python.exe detect_track_count.py \
  --source data/tu_video.mp4 \
  --line-start 0,540 --line-end 1920,540 \
  --output output/annotated.mp4 \
  --no-display
```

Salida:
- `output/annotated.mp4`: video anotado (boxes, tracker id, trace, línea con contadores in/out).
- `output/events.csv`: log de eventos (`frame`, `time_s`, `tracker_id`, `event` = landing/takeoff), un evento por track (sin duplicados).

## Nota sobre landing vs takeoff

`LineZone` de supervision distingue cruces "in" vs "out" según el vector normal de la línea (definido por el orden de `--line-start`/`--line-end`), no según semántica de aterrizaje/despegue. Corré una vez, mirá el video anotado, y si landing/takeoff salen invertidos, invertí start/end (o mirá qué lado corresponde a qué maniobra en la cámara de tu pista) y volvé a correr — no requiere tocar el código.

`LineZone` usa por defecto las 4 esquinas del bbox para decidir si un objeto cruzó — con un avión, que ocupa casi todo el cuadro, eso casi nunca dispara. `detect_track_count.py` ya lo configura con `triggering_anchors=(sv.Position.CENTER,)` para evitar ese problema.

## Persistencia + dashboard

Cada evento de cruce se guarda automáticamente en `runway_events.db` (SQLite, ver [db.py](db.py)) junto con un recorte del avión en `output/thumbnails/`. Para desactivar esto: `--no-db`.

Para ver el dashboard local:

```bash
./.venv/Scripts/python.exe -m uvicorn main:app --app-dir webapp --host 127.0.0.1 --port 8000
```

Abrí `http://localhost:8000`. La página muestra:

- **Contadores** de landings / takeoffs / total.
- **Reproductor del video con el etiquetado** (recuadros, track id, trace, línea y contadores dibujados).
- **Tabla de eventos** con thumbnail, tipo, hora, track id, fuente, matrícula y aerolínea. **Clic en cualquier fila salta el video a ese evento** (arranca 2s antes para que se vea el cruce).

Para que el video se vea en el navegador hay que convertirlo primero: supervision escribe `mp4v`, que los navegadores no reproducen.

```bash
./.venv/Scripts/python.exe transcode_web.py
```

Eso deja copias H.264 en `output/web/`, que es de donde las sirve el dashboard.

## Acelerar con la GPU (Intel iGPU vía OpenVINO)

La GPU de esta máquina es una **Intel Iris Xe integrada**, así que CUDA/PyTorch-GPU no aplica. OpenVINO sí la usa, para **inferencia**:

```bash
./.venv/Scripts/python.exe export_openvino.py --weights yolov8n.pt

./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --model yolov8n_openvino_model/ --device intel:gpu \
  --line-start 320,0 --line-end 320,360 --no-display
```

Medido en esta máquina, solo la detección: **6.9 FPS en CPU → 40.2 FPS en la iGPU (~6x)**. Sobre el pipeline completo la mejora es bastante menor (121s → 105s en un clip de 90s), porque una vez acelerada la inferencia el cuello de botella pasa a ser el decode/encode de video y la anotación, que siguen en CPU.

**El entrenamiento sigue en CPU:** OpenVINO es solo inferencia, y PyTorch no puede entrenar en esta iGPU. Para entrenar rápido hace falta una GPU NVIDIA con el torch de CUDA.

## Ahorrar cómputo: gate de movimiento

En una cámara 24/7 la enorme mayoría de los frames no tienen nada pasando. El gate ([motion.py](motion.py)) compara el frame con el anterior (en gris y reducido, cuesta fracciones de milisegundo) y **saltea el detector cuando la escena está quieta**:

```bash
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --motion-threshold 0.002 --line-start 250,100 --line-end 250,360 --no-display
```

También está `--detect-every N` para correr el detector 1 de cada N frames.

**Detalle de diseño importante:** el gate **nunca saltea frames mientras hay un avión trackeado**. La primera versión sí lo hacía y perdía eventos (8 landings/5 takeoffs → 7/3), porque cortar las actualizaciones del tracker en pleno paso rompe la continuidad del track y se pierden cruces. Con el guard de tracks activos, los conteos quedan **idénticos al baseline** y aun así se ahorra cómputo.

Medido en el clip de prueba (compilado con cámara en movimiento casi todo el tiempo, o sea el peor caso para el gate):

| Configuración | Tiempo | Frames con detector | Conteo |
|---|---|---|---|
| CPU, sin gate | 121s | 2701/2701 | 8 / 5 |
| iGPU, sin gate | 105s | 2701/2701 | 8 / 5 |
| iGPU + gate | 98s | 1744/2701 (**-35%**) | 8 / 5 |

En una cámara fija apuntando a una pista real, con largos períodos sin actividad, el ahorro va a ser **mucho mayor** que ese 35% — este video no tiene un solo momento verdaderamente quieto.

## Filtros anti-falsos-positivos

Solo cuentan los aviones **en movimiento cruzando la línea**. Un avión estacionado, o uno que rueda paralelo a la línea, no debe contar — pero el detector hace temblar su bounding box y eso disparaba cruces falsos. Cuatro filtros lo resuelven:

| Flag | Default | Qué descarta |
|---|---|---|
| `--min-speed` | 40 px/s | Aviones quietos (estacionados en plataforma) |
| `--line-margin` | 20 px | Banda de histéresis: el track tiene que despegarse de la línea para que cuente el cambio de lado |
| `--track-cooldown` | 5 s | Cruces del mismo track que se revierten en segundos — físicamente imposible |
| `--scene-cut` | 0.5 | Cortes de cámara: resetea el tracker para que un ID no salte a otro avión |

Medido sobre el clip de prueba (compilado de spotting, el peor caso):

```
sin filtros:   17 eventos  (incluía pares takeoff+landing del mismo track a 0.03s)
con filtros:    8 eventos  (ningún par imposible)
```

En una cámara fija de pista, `--scene-cut` no va a activarse nunca (no hay cortes) y los otros tres siguen siendo útiles contra el temblor del detector.

**Nota:** los conteos de este video de prueba no son verdad absoluta ni siquiera después de filtrar — es un compilado editado con saltos de cámara. Los filtros están validados contra artefactos identificables (pares imposibles), no contra un conteo real verificado a mano.

## Identificación de matrícula y aerolínea (OCR)

Pasá `--ocr` a `detect_track_count.py` para que, en cada evento, corra OCR sobre el recorte del avión y complete las columnas `registration`/`airline` de la DB:

```bash
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --line-start 320,0 --line-end 320,360 --ocr --no-display
```

Cómo funciona ([ocr.py](ocr.py)): EasyOCR lee el texto del recorte; la matrícula sale por patrón regex (formato argentino `LV-XXX` / `LQ-XXX`) y la aerolínea por coincidencia contra una lista de nombres conocidos. No hay modelo entrenado acá — es lectura de texto + matching.

### La resolución es el factor decisivo

Comparación directa, **mismo avión, mismo frame**, una versión a 640x360 y otra a 1920x1080:

| | 640x360 | 1920x1080 |
|---|---|---|
| Texto detectado | `{de`, `"9 J`, `7` (basura) | `libertad de volar`, `LV-LKK` |
| Aerolínea | ninguna | **Flybondi** ✅ |
| Matrícula | ninguna | detectada pero **mal leída** |

A 360p no hay nada que hacer: las letras miden pocos píxeles y el reconocedor devuelve ruido. A 1080p la aerolínea sale bien.

### La matrícula: EasyOCR no puede, un modelo de visión sí

A 1080p el OCR clásico encuentra el patrón de matrícula pero **confunde caracteres**: leyó `LV-LKK` y `LV-HKM` en un avión que es `LV-HKN`. Probé escalas 6x y 10x, CLAHE, Otsu, lista blanca de caracteres y votación entre 20 cuadros. Ninguna acertó, y la votación **empeoró** las cosas: el error no es aleatorio sino un sesgo constante (`N` leída como `M` en la mayoría de los cuadros), y promediar no corrige un sesgo.

**La resolución nunca fue el problema.** Un recorte de 150x45 px ampliado se lee sin esfuerzo a simple vista (ver `output/matricula_zoom8.png`). El limitante era el reconocedor.

La solución fue [vlm_ocr.py](vlm_ocr.py): un modelo de visión-lenguaje lee con contexto en vez de carácter por carácter. Medido contra respuestas verificadas a ojo ([validate_vlm.py](validate_vlm.py)):

| Método | Matrículas correctas |
|---|---|
| EasyOCR | 0 / 3 |
| Modelo de visión (NVIDIA Nemotron, gratis en OpenRouter) | **6 / 6** |

### Por qué hay tres redes de seguridad y no una

Un modelo de lenguaje puede devolver una matrícula plausible con total seguridad para una imagen ilegible, y no trae score de confianza para filtrarla. Sobre los recortes reales del pipeline eso ocurrió: leyó `LV-4KN` donde decía `LV-HKN`. Por eso:

1. **Consenso entre dos modelos.** Solo se marca como confirmada si ambos leen lo mismo; si difieren, se muestra en gris con `?`.
2. **Validación de formato.** Las matrículas argentinas son `LV`/`LQ` más **tres letras**. Esto atrapó un `LV-600` en el que *ambos modelos coincidieron* — el consenso reduce errores, no los elimina.
3. **Normalización de aerolíneas.** El modelo devuelve texto libre (`Aerolineas Argentinas`, `Aeroline Argentinas`), que se mapea a un nombre canónico antes de guardar.

Una cuarta red posible, no implementada: contrastar contra el registro real de matrículas argentinas.

**Límite de uso:** los modelos gratuitos tienen cupo diario (`free-models-per-day`). Al agotarse, `backfill_ocr.py --vlm` retoma los pendientes al día siguiente sin repetir lo hecho.

### Otras limitaciones

- Los logos muy estilizados no se leen (el wordmark cursivo de flybondi nunca sale). Por eso la lista de [ocr.py](ocr.py) incluye **eslóganes** además de nombres: "libertad de volar" está pintado mucho más grande que el logo y sobrevive a distancias donde el logo no.
- Los aviones lejanos en aproximación no dan ningún texto, a ninguna resolución.
- El OCR corre sobre el **mejor recorte** de cada avión (el frame donde se ve más grande), no sobre el del momento del cruce, que suele ser el peor.
- **Lo que más influye no es la resolución sino cuánto dura el avión en cuadro.** Sobre 83 minutos de material procesado: el video con 2 cortes de cámara identificó el 59% de los aviones; el de 142 cortes, apenas el 12%. Mismo sistema, misma resolución. Una cámara fija no tiene cortes, así que ese 59% es el piso esperable, no el techo.

## Arranque rápido (Windows)

Doble clic en **`dashboard.bat`**: levanta el servidor y abre el navegador solo. El servidor queda en una ventana minimizada; cerrala para detenerlo.

## Revisar y corregir etiquetas

En `http://localhost:8000/label` hay una herramienta para revisar el dataset auto-etiquetado:

- **Arrastrar** sobre la imagen dibuja una caja nueva.
- **Clic dentro de una caja** la borra.
- Flechas ←/→ para navegar, `S` guardar, `Z` deshacer.

Guarda directo sobre los `.txt` YOLO del dataset, así que después entrenás normal con `train.py`. Esta es la única forma de que el modelo aprenda lo que el modelo base hace mal: mientras las etiquetas las genere el propio modelo, no puede superarse a sí mismo.

## Entrenar un detector propio

El detector COCO base confunde/pierde aviones en tomas difíciles. Para afinarlo con tu propio footage, sin etiquetar a mano, el flujo es auto-labeling + fine-tune:

```bash
# 1. Genera dataset YOLO auto-etiquetado (el modelo COCO actúa de "profesor")
./.venv/Scripts/python.exe build_dataset.py --source data/aeroparque_full.mp4 --stride 15 --min-confidence 0.5

# 2. Fine-tune
./.venv/Scripts/python.exe train.py --data dataset/data.yaml --epochs 30

# 3. Usar los pesos entrenados (ojo: modelo de 1 clase, va con --class-id 0)
./.venv/Scripts/python.exe detect_track_count.py --source data/tu_video.mp4 \
  --model runs/detect/runway/weights/best.pt --class-id 0 --line-start 320,0 --line-end 320,360 --no-display
```

**Qué esperar de esto:** el auto-labeling solo conserva detecciones de alta confianza, así que el modelo aprende de los casos donde el profesor ya acertaba. Eso lo hace más rápido y más especializado en *esta* pista/ángulo, pero **no le enseña a detectar los aviones que el modelo base ya no veía** (no puede superar a su profesor en esos casos). Para ganar ahí hace falta etiquetado manual real de los frames difíciles — Roboflow sirve para eso.

Sobre el video de Aeroparque completo (23:31, stride 15, conf 0.5) el dataset generado fue de **1846 imágenes de train + 462 de val**.

### Resultado del entrenamiento

20 épocas a 416px en CPU (~85 min). Pesos en `runs/detect/runway/weights/best.pt`:

| Métrica | Valor |
|---|---|
| Precision | 0.953 |
| Recall | 0.931 |
| mAP50 | 0.978 |
| mAP50-95 | 0.831 |

**Cuidado al interpretar estos números.** El conjunto de validación son las *mismas pseudo-etiquetas* generadas por el modelo profesor, así que un mAP50 de 0.978 significa **"coincide 97.8% con el modelo base"**, no "acierta el 97.8% de los aviones reales". Un modelo que replicara perfectamente los errores del profesor sacaría 1.000 acá. Para medir precisión real hace falta un conjunto de validación etiquetado a mano — para eso está la herramienta en `/label`.

**Nota de hardware:** el `torch` instalado es build CPU-only, así que el entrenamiento corre en CPU y es lento (por eso los defaults de arriba usan `--imgsz 416` y pocas épocas). Si tenés GPU NVIDIA, instalá el torch con CUDA y pasá `--device 0` para acelerarlo un orden de magnitud.

## Estado actual

Esta versión procesa **archivos de video**: le pasás un `.mp4`, lo procesa entero, y los resultados quedan en la base y el dashboard.

Hay dos fases que conviene no confundir:

- **Entrenar** (una vez, opcional): `build_dataset.py` → corregir en `/label` → `train.py`. No se repite por cada video.
- **Operar** (cada vez): `detect_track_count.py` sobre un video → eventos en SQLite → dashboard.

## Objetivo final: cámara en vivo 24/7

El destino del proyecto es una **cámara fija transmitiendo 24/7**, procesada en vivo, mostrando el video etiquetado en una pantalla y registrando cada aterrizaje/despegue con aerolínea y matrícula.

Lo que falta para llegar ahí:

1. **Entrada en vivo:** hoy `--source` es un archivo. Falta soportar RTSP/USB con reconexión automática ante cortes.
2. **Streaming al navegador:** el dashboard hoy reproduce un archivo ya procesado. Para vivo hace falta transmitir los frames anotados (MJPEG o WebRTC) y que los eventos aparezcan solos, sin recargar.
3. **Proceso permanente:** correr como servicio de Windows en vez de un comando puntual, con rotación de video/thumbnails para no llenar el disco.
4. **OCR asincrónico:** leer matrícula es lento; en vivo tiene que correr fuera del loop de tiempo real, sobre los recortes ya guardados (ver [backfill_ocr.py](backfill_ocr.py), que ya hace exactamente eso en diferido).

**Viabilidad medida:** el pipeline completo corrió a 27.6 FPS sobre video de 30 FPS con la iGPU, *incluyendo* escribir el video anotado a disco (que en vivo no haría falta). Sumado al gate de movimiento, una cámara debería entrar cómoda en tiempo real. Falta medirlo con un stream real.

## Otros pendientes

- Reducir el "churn" de tracker IDs cerca de la línea: cuando un avión se ocluye o pasa cerca de otros, ByteTrack le cambia el ID y eso infla el conteo. Se ataca ajustando `track_activation_threshold`/`lost_track_buffer`, o usando una zona (polígono) en vez de una línea.
- Clasificar aerolínea por librea/logo con un modelo de clasificación, para los casos donde el OCR no puede (logos cursivos como el de flybondi).
- Leer matrícula de verdad: necesita más resolución que 640x360, o zoom óptico sobre la zona de cola.
