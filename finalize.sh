#!/bin/bash
# Pasos finales: transcodificar para el navegador, liberar los mp4v pesados,
# y leer matriculas/aerolineas con consenso de dos modelos de vision.
cd "$(dirname "$0")"
PY=./.venv/Scripts/python.exe

echo "=== 1. Transcodificando a H.264 para el dashboard"
for f in output/proc_*.mp4; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  if [ -f "output/web/$base" ]; then echo "   ya estaba: $base"; continue; fi
  $PY transcode_web.py --input "$f" --crf 30 2>&1 | grep -E "Transcoding|done"
done

echo ""
echo "=== 2. Espacio antes de limpiar"; df -h C: | tail -1
# Los mp4v son intermedios: el dashboard sirve las copias H.264 de output/web/.
for f in output/proc_*.mp4; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  if [ -f "output/web/$base" ]; then rm -f "$f"; echo "   liberado: $base"; fi
done
echo "=== espacio despues"; df -h C: | tail -1

echo ""
echo "=== 3. Leyendo matriculas y aerolineas (consenso de 2 modelos)"
$PY backfill_ocr.py --all --vlm 2>&1 | grep -viE "Warning|warn|torch|super\(\)|w_ih|pin_memory"

echo ""
echo "=== 4. Resumen final"
$PY - <<'PYEOF'
import db
conn = db.get_connection()
c = db.counts_by_type(conn)
tot = conn.execute("SELECT COUNT(*) n FROM events").fetchone()["n"]
conf = conn.execute("SELECT COUNT(*) n FROM events WHERE registration IS NOT NULL").fetchone()["n"]
unc = conn.execute("SELECT COUNT(*) n FROM events WHERE registration_unconfirmed IS NOT NULL").fetchone()["n"]
air = conn.execute("SELECT COUNT(*) n FROM events WHERE airline IS NOT NULL").fetchone()["n"]
print(f"eventos: {tot} (landings {c['landing']}, takeoffs {c['takeoff']})")
print(f"aerolinea identificada: {air}/{tot}")
print(f"matricula confirmada por 2 modelos: {conf}/{tot}")
print(f"matricula de un solo modelo (dudosa): {unc}/{tot}")
print("\nmatriculas confirmadas:")
for r in conn.execute("SELECT DISTINCT registration, airline FROM events WHERE registration IS NOT NULL ORDER BY registration"):
    print(f"   {r['registration']:9s} {r['airline'] or '-'}")
conn.close()
PYEOF
echo ""
echo "=== LISTO"
