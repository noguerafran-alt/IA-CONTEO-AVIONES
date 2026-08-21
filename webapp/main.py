"""Local dashboard showing landing/takeoff events from the SQLite DB."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

import db
from adsb_service import service as adsb_service

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

@app.get("/adsb")
def adsb_page(request: Request):
    return templates.TemplateResponse(request, "adsb.html", {})


@app.get("/api/adsb/status")
def api_adsb_status():
    return JSONResponse(adsb_service.status())


@app.post("/api/adsb/start")
async def api_adsb_start(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    result = adsb_service.start(mode=body.get("source", "auto"))
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
