"""
MVP: airplane detection + tracking + landing/takeoff line-crossing count.

Pipeline:
  YOLO (COCO 'airplane' class) -> supervision.ByteTrack -> supervision.LineZone

Usage:
  python detect_track_count.py --source data/runway.mp4 --output output/annotated.mp4 \
      --line-start 0,540 --line-end 1920,540

If --line-start/--line-end are omitted, a horizontal line across the middle
of the frame is used. The crossing direction (in vs out) depends on which
side of the line the line's normal vector points to -- run once, look at the
output video, then flip --line-start/--line-end if landing/takeoff read backwards.
"""

import argparse
import csv
import math
import time
from collections import defaultdict, deque
from pathlib import Path

import cv2
import numpy as np
import supervision as sv
from ultralytics import YOLO

import db
import ocr
from motion import MotionGate

# COCO class id for "airplane" in the stock pretrained YOLO weights.
AIRPLANE_CLASS_ID = 4


def parse_point(s: str) -> tuple[int, int]:
    x, y = s.split(",")
    return int(x), int(y)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="Path to input video file")
    parser.add_argument("--output", default="output/annotated.mp4", help="Path to write annotated video")
    parser.add_argument("--events-csv", default="output/events.csv", help="Path to write crossing events log")
    parser.add_argument("--model", default="yolov8n.pt", help="Ultralytics weights (COCO-pretrained by default)")
    parser.add_argument("--device", default=None,
                        help="Inference device. 'intel:gpu' runs on the Intel iGPU via an OpenVINO model "
                             "(export one with export_openvino.py) and is ~6x faster than CPU here.")
    parser.add_argument("--confidence", type=float, default=0.25, help="Detection confidence threshold")
    parser.add_argument("--iou", type=float, default=0.5, help="NMS IoU threshold")
    parser.add_argument("--line-start", type=parse_point, default=None, help="x,y pixel coords of line start")
    parser.add_argument("--line-end", type=parse_point, default=None, help="x,y pixel coords of line end")
    parser.add_argument("--class-id", type=int, default=AIRPLANE_CLASS_ID, help="COCO class id to keep (default: airplane)")
    parser.add_argument("--no-display", action="store_true", help="Skip cv2.imshow preview while processing")
    parser.add_argument("--output-scale", type=float, default=1.0,
                        help="Scale of the annotated video relative to the source (e.g. 0.5). Detection and "
                             "measurement stay at full resolution; only the written video shrinks. Encoding "
                             "dominates runtime on long clips, so this is the main speed/disk lever.")
    parser.add_argument("--thumbnails-dir", default="output/thumbnails", help="Directory to save per-event airplane crops")
    parser.add_argument("--no-db", action="store_true", help="Skip writing events to the SQLite database")
    parser.add_argument("--ocr", action="store_true", help="Run OCR on each event crop to read registration/airline (slow, needs --no-db off)")
    parser.add_argument("--motion-threshold", type=float, default=0.0,
                        help="Skip detection when less than this fraction of pixels changed (e.g. 0.002). "
                             "0 disables the motion gate. Big savings on a mostly-idle 24/7 camera.")
    parser.add_argument("--detect-every", type=int, default=1,
                        help="Run detection only every Nth frame (1 = every frame)")
    parser.add_argument("--min-speed", type=float, default=40.0,
                        help="Ignore crossings by tracks slower than this (pixels/second). Parked aircraft "
                             "whose boxes jitter over the line would otherwise be counted. 0 disables.")
    parser.add_argument("--speed-window", type=float, default=0.5,
                        help="Seconds of track history used to measure speed")
    parser.add_argument("--track-cooldown", type=float, default=5.0,
                        help="Seconds a track must wait before it can register another crossing. One "
                             "aircraft cannot land and take off seconds apart, so a quick reversal is "
                             "box jitter, not a real operation. 0 disables.")
    parser.add_argument("--scene-cut", type=float, default=0.5,
                        help="Fraction of the frame that must change to call it a scene cut, which resets "
                             "the tracker. Needed for edited/compilation footage; harmless on a fixed "
                             "camera, where cuts never happen. 0 disables.")
    parser.add_argument("--line-margin", type=float, default=20.0,
                        help="Hysteresis band, in pixels, on each side of the line. A track must clear it "
                             "to register a side change, which stops a jittering box from firing repeated "
                             "crossings while it rides along the line.")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise FileNotFoundError(f"Video not found: {source_path}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    events_path = Path(args.events_csv)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnails_dir = Path(args.thumbnails_dir)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)

    conn = None if args.no_db else db.get_connection()

    video_info = sv.VideoInfo.from_video_path(str(source_path))
    width, height = video_info.width, video_info.height
    out_scale = max(0.05, min(1.0, args.output_scale))
    sink_info = sv.VideoInfo(
        width=int(width * out_scale), height=int(height * out_scale),
        fps=video_info.fps, total_frames=video_info.total_frames,
    )

    if args.line_start is None or args.line_end is None:
        line_start = sv.Point(0, height // 2)
        line_end = sv.Point(width, height // 2)
    else:
        line_start = sv.Point(*args.line_start)
        line_end = sv.Point(*args.line_end)

    model = YOLO(args.model)
    predict_kwargs = {"device": args.device} if args.device else {}
    tracker = sv.ByteTrack(frame_rate=video_info.fps)
    # Airplanes span most of the frame, so requiring all 4 bbox corners to
    # cross (the LineZone default) rarely fires. A single center anchor is
    # far more reliable for large objects.
    line_zone = sv.LineZone(start=line_start, end=line_end, triggering_anchors=(sv.Position.CENTER,))

    box_annotator = sv.BoxAnnotator()
    label_annotator = sv.LabelAnnotator()
    trace_annotator = sv.TraceAnnotator(trace_length=60)
    # Counts come from our own hysteresis logic, not LineZone's, so let the
    # annotator draw only the line and we render the totals ourselves.
    line_annotator = sv.LineZoneAnnotator(
        thickness=2, text_thickness=1, text_scale=0.6,
        display_in_count=False, display_out_count=False,
    )

    events = []
    seen_in_ids = set()
    seen_out_ids = set()

    # Recent box centers per track, used to tell a moving aircraft from a
    # parked one whose box jitters across the line.
    speed_window_frames = max(2, int(video_info.fps * args.speed_window))
    track_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=speed_window_frames))
    skipped_slow = 0

    def track_speed(tracker_id: int) -> float:
        """Pixels/second the track's centre moved across its recorded history."""
        history = track_history[tracker_id]
        if len(history) < 2:
            return 0.0
        first_frame, x0, y0 = history[0]
        last_frame, x1, y1 = history[-1]
        seconds = (last_frame - first_frame) / video_info.fps
        if seconds <= 0:
            return 0.0
        return math.hypot(x1 - x0, y1 - y0) / seconds

    # Which side of the line each track was last confidently on (+1 / -1).
    track_side: dict[int, int] = {}
    # Frame at which each track last registered a crossing, for the cooldown.
    last_crossing_frame: dict[int, int] = {}
    skipped_cooldown = 0

    # Two crops are kept per aircraft, because reading text and recognising the
    # model want opposite framings:
    #
    #   "text"       the largest crop overall. Usually a close-up of the nose or
    #                fuselage, which is what makes small lettering (the tail
    #                number) legible.
    #   "silhouette" the largest crop still fully inside the frame. Identifying a
    #                model needs the whole airframe -- nose, nacelles, wingtips,
    #                tail -- and a close-up that spills past the frame edge shows
    #                none of it.
    #
    # Measured while identifying these aircraft by eye: every confident model
    # call came from a frame showing the complete aircraft, and none from a
    # close-up, even though the close-ups had far more pixels.
    best_crop: dict[int, dict] = {}
    # How far a box must stay from the frame edge to count as fully visible.
    edge_margin = max(4, int(min(width, height) * 0.01))
    line_dx = line_end.x - line_start.x
    line_dy = line_end.y - line_start.y
    line_length = math.hypot(line_dx, line_dy) or 1.0

    def signed_distance(px: float, py: float) -> float:
        """Perpendicular distance to the line; sign tells which side."""
        return (line_dx * (py - line_start.y) - line_dy * (px - line_start.x)) / line_length

    # A scene-cut detector needs a frame differ regardless of whether the
    # motion gate is on, so build one whenever either feature is enabled.
    motion_gate = (MotionGate(threshold=args.motion_threshold)
                   if args.motion_threshold > 0 or args.scene_cut > 0 else None)
    gate_enabled = args.motion_threshold > 0
    scene_cuts = 0
    frames_total = 0
    frames_detected = 0
    # Reused for annotation on skipped frames so the output video stays smooth.
    last_detections = sv.Detections.empty()

    frame_generator = sv.get_video_frames_generator(str(source_path))

    t0 = time.time()
    with sv.VideoSink(str(output_path), sink_info) as sink:
        for frame_idx, frame in enumerate(frame_generator):
            frames_total += 1

            on_schedule = (frame_idx % args.detect_every) == 0
            # Evaluate the differ every frame so its comparison stays
            # frame-to-frame, then reuse the same number for both features.
            changed = motion_gate.changed_fraction(frame) if motion_gate else 1.0

            if args.scene_cut > 0 and changed >= args.scene_cut:
                # The whole picture changed: this is a different shot, so any
                # track carried across it would be a bogus identity. Keeping
                # them produces phantom crossings when an id lands on a
                # different aircraft on the far side of the line.
                scene_cuts += 1
                tracker.reset()
                track_side.clear()
                track_history.clear()
                last_detections = sv.Detections.empty()

            moving = (changed >= args.motion_threshold) if gate_enabled else True
            # Never gate away frames while an aircraft is being tracked: dropping
            # updates mid-pass breaks track continuity and loses line crossings.
            # The gate only earns its keep on a genuinely empty scene.
            tracking_active = len(last_detections) > 0
            run_detection = on_schedule and (moving or tracking_active)

            if run_detection:
                frames_detected += 1
                result = model(frame, conf=args.confidence, iou=args.iou, verbose=False, **predict_kwargs)[0]
                detections = sv.Detections.from_ultralytics(result)
                detections = detections[detections.class_id == args.class_id]
                detections = tracker.update_with_detections(detections)
                last_detections = detections
                crossings = []
                for tid, (bx1, by1, bx2, by2) in zip(detections.tracker_id, detections.xyxy):
                    tid = int(tid)
                    cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
                    track_history[tid].append((frame_idx, cx, cy))

                    # Hysteresis: a track's side only updates once it is clear of
                    # the margin band. Without this, a box jittering on the line
                    # (very common for an aircraft rolling parallel to it) fires
                    # a crossing every couple of frames, in alternating directions.
                    distance = signed_distance(cx, cy)
                    if abs(distance) < args.line_margin:
                        continue
                    side = 1 if distance > 0 else -1
                    previous_side = track_side.get(tid)
                    track_side[tid] = side
                    if previous_side is not None and previous_side != side:
                        crossings.append((tid, "landing" if side > 0 else "takeoff",
                                          np.array([bx1, by1, bx2, by2])))

                # Grow both crops for tracks that already produced an event.
                for tid, (bx1, by1, bx2, by2) in zip(detections.tracker_id, detections.xyxy):
                    entry = best_crop.get(int(tid))
                    if entry is None:
                        continue
                    area = (bx2 - bx1) * (by2 - by1)
                    cx1, cy1 = max(int(bx1), 0), max(int(by1), 0)
                    cx2, cy2 = min(int(bx2), width), min(int(by2), height)
                    crop = frame[cy1:cy2, cx1:cx2]
                    if not crop.size:
                        continue

                    if area > entry["area"]:
                        entry["area"] = area
                        entry["crop"] = crop.copy()

                    fully_visible = (bx1 > edge_margin and by1 > edge_margin
                                     and bx2 < width - edge_margin
                                     and by2 < height - edge_margin)
                    if fully_visible and area > entry["silhouette_area"]:
                        entry["silhouette_area"] = area
                        entry["silhouette"] = crop.copy()
            else:
                # Skipping means no tracker/line update: an idle or unsampled
                # frame carries no new crossing information anyway.
                detections = last_detections
                crossings = []

            def save_event(tracker_id: int, event_type: str, xyxy: np.ndarray) -> None:
                time_s = round(frame_idx / video_info.fps, 2)
                x1, y1, x2, y2 = xyxy.astype(int)
                x1, y1 = max(x1, 0), max(y1, 0)
                x2, y2 = min(x2, width), min(y2, height)
                crop = frame[y1:y2, x1:x2]
                thumb_path = thumbnails_dir / f"{source_path.stem}_{event_type}_{tracker_id}_{frame_idx}.jpg"
                if crop.size > 0:
                    cv2.imwrite(str(thumb_path), crop)
                else:
                    thumb_path = None
                events.append({
                    "frame": frame_idx,
                    "time_s": time_s,
                    "tracker_id": int(tracker_id),
                    "event": event_type,
                })
                if conn is not None:
                    event_id = db.insert_event(
                        conn,
                        source=source_path.name,
                        video_time_s=time_s,
                        tracker_id=int(tracker_id),
                        event_type=event_type,
                        thumbnail_path=str(thumb_path) if thumb_path else None,
                        annotated_video=output_path.name,
                    )
                    if args.ocr:
                        # Start collecting the largest view of this aircraft;
                        # the actual reading happens once the video is done.
                        entry = best_crop.setdefault(
                            int(tracker_id),
                            {"area": 0, "crop": None, "silhouette_area": 0,
                             "silhouette": None, "events": []},
                        )
                        entry["events"].append(event_id)
                        area = (x2 - x1) * (y2 - y1)
                        if crop.size and area > entry["area"]:
                            entry["area"] = area
                            entry["crop"] = crop.copy()

            def is_moving(tracker_id: int) -> bool:
                nonlocal skipped_slow
                if args.min_speed <= 0:
                    return True
                if track_speed(int(tracker_id)) >= args.min_speed:
                    return True
                skipped_slow += 1
                return False

            cooldown_frames = args.track_cooldown * video_info.fps
            for tracker_id, event_type, xyxy in crossings:
                seen = seen_in_ids if event_type == "landing" else seen_out_ids
                if tracker_id in seen or not is_moving(tracker_id):
                    continue
                previous_frame = last_crossing_frame.get(tracker_id)
                if previous_frame is not None and (frame_idx - previous_frame) < cooldown_frames:
                    skipped_cooldown += 1
                    continue
                last_crossing_frame[tracker_id] = frame_idx
                seen.add(tracker_id)
                save_event(tracker_id, event_type, xyxy)


            labels = [
                f"#{tid} {model.names[cid]} {conf:0.2f}"
                for tid, cid, conf in zip(detections.tracker_id, detections.class_id, detections.confidence)
            ]

            annotated = frame.copy()
            annotated = trace_annotator.annotate(annotated, detections)
            annotated = box_annotator.annotate(annotated, detections)
            annotated = label_annotator.annotate(annotated, detections, labels=labels)
            annotated = line_annotator.annotate(annotated, line_zone)
            cv2.putText(annotated, f"landings: {len(seen_in_ids)}", (12, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (74, 222, 128), 2)
            cv2.putText(annotated, f"takeoffs: {len(seen_out_ids)}", (12, 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (250, 162, 96), 2)

            if out_scale != 1.0:
                annotated = cv2.resize(annotated, (sink_info.width, sink_info.height),
                                       interpolation=cv2.INTER_AREA)
            sink.write_frame(annotated)

            if not args.no_display:
                cv2.imshow("runway-video-analytics", annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    if not args.no_display:
        cv2.destroyAllWindows()

    with open(events_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["frame", "time_s", "tracker_id", "event"])
        writer.writeheader()
        writer.writerows(events)

    identified = 0
    if conn is not None and args.ocr and best_crop:
        print(f"Reading registration/airline from {len(best_crop)} aircraft ...")
        for tracker_id, entry in best_crop.items():
            crop = entry["crop"]
            if crop is None or not crop.size:
                continue
            # Upscale: at this resolution the lettering is only a few pixels
            # tall, and the recognizer does better on an enlarged crop.
            enlarged = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
            found = ocr.identify(enlarged)
            if found["registration"] or found["airline"]:
                identified += 1
                print(f"  track #{tracker_id}: airline={found['airline']} "
                      f"registration={found['registration']}")
            for event_id in entry["events"]:
                db.update_event_identification(
                    conn, event_id,
                    registration=found["registration"],
                    airline=found["airline"],
                )
            # Point the events at the clearest view: it is the best image for a
            # human checking the event, and the one a re-run of the OCR should
            # use (see backfill_ocr.py).
            best_path = thumbnails_dir / f"{source_path.stem}_best_{tracker_id}.jpg"
            cv2.imwrite(str(best_path), crop)
            for event_id in entry["events"]:
                db.update_event_thumbnail(conn, event_id, str(best_path))

            silhouette = entry.get("silhouette")
            if silhouette is not None and silhouette.size:
                sil_path = thumbnails_dir / f"{source_path.stem}_silhouette_{tracker_id}.jpg"
                cv2.imwrite(str(sil_path), silhouette)
                for event_id in entry["events"]:
                    db.update_event_silhouette(conn, event_id, str(sil_path))

    if conn is not None:
        conn.close()

    elapsed = time.time() - t0
    print(f"Done in {elapsed:0.1f}s -> {output_path}")
    print(f"Landings (line-in):  {len(seen_in_ids)}")
    print(f"Takeoffs (line-out): {len(seen_out_ids)}")
    print(f"Events log: {events_path}")

    if frames_total:
        skipped = frames_total - frames_detected
        print(f"Detector ran on {frames_detected}/{frames_total} frames "
              f"({skipped} skipped, {skipped / frames_total * 100:.1f}% of inference saved)")
    if skipped_slow:
        print(f"Ignored {skipped_slow} crossing(s) from tracks below {args.min_speed:.0f} px/s "
              f"(parked/idle aircraft)")
    if skipped_cooldown:
        print(f"Ignored {skipped_cooldown} crossing(s) that reversed within {args.track_cooldown:.0f}s "
              f"(box jitter, not a real operation)")
    if scene_cuts:
        print(f"Detected {scene_cuts} scene cut(s); tracker reset at each one")
    if args.ocr and best_crop:
        print(f"Identified airline/registration on {identified}/{len(best_crop)} aircraft")


if __name__ == "__main__":
    main()
