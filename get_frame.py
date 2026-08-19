"""Dump a single frame from a video, to eyeball pixel coords for --line-start/--line-end."""
import argparse
from pathlib import Path

import cv2
import supervision as sv

parser = argparse.ArgumentParser()
parser.add_argument("--source", required=True)
parser.add_argument("--frame", type=int, default=0)
parser.add_argument("--output", default="output/sample_frame.png")
args = parser.parse_args()

Path(args.output).parent.mkdir(parents=True, exist_ok=True)
generator = sv.get_video_frames_generator(args.source, start=args.frame, end=args.frame + 1)
frame = next(generator)

h, w = frame.shape[:2]
for x in range(0, w, 100):
    cv2.line(frame, (x, 0), (x, h), (60, 60, 60), 1)
    cv2.putText(frame, str(x), (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
for y in range(0, h, 100):
    cv2.line(frame, (0, y), (w, y), (60, 60, 60), 1)
    cv2.putText(frame, str(y), (2, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

cv2.imwrite(args.output, frame)
print(f"Saved {args.output} ({w}x{h}) with a 100px grid to help pick line-start/line-end coords.")
