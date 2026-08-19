#!/bin/bash
# Procesa todos los videos 1080p de data/ con la configuracion completa.
cd "$(dirname "$0")"
PY=./.venv/Scripts/python.exe
COMMON="--model yolov8n_openvino_model/ --device intel:gpu \
 --line-start 960,0 --line-end 960,1080 --line-margin 60 --min-speed 120 \
 --confidence 0.3 --motion-threshold 0.002 --output-scale 0.5 --ocr --no-display"

i=0
for v in data/YTDown*.mp4; do
  case "$v" in *"(1)"*) echo "== omito duplicado: $v"; continue;; esac
  i=$((i+1))
  name=$(basename "$v" .mp4 | cut -c1-40 | tr -c 'A-Za-z0-9_-' '_')
  echo ""
  echo "############ [$i] $(basename "$v")"
  date +"inicio %H:%M:%S"
  $PY detect_track_count.py --source "$v" --output "output/proc_${i}.mp4" \
      --events-csv "output/events_${i}.csv" $COMMON 2>&1 \
    | grep -viE "Warning|warn|torch|super\(\)|w_ih|pin_memory|Loading|LATENCY"
  date +"fin %H:%M:%S"
done
echo ""
echo "############ TODO PROCESADO"
