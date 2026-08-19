"""Build a YOLO-format dataset by auto-labeling video frames with the COCO model.

No manual annotation: the stock COCO detector runs over sampled frames and its
'airplane' boxes become pseudo-labels. Only frames where the model is confident
are kept, so the fine-tuned model learns from its teacher's reliable cases.

Usage:
  python build_dataset.py --source data/aeroparque_full.mp4 --stride 15 --min-confidence 0.5
"""

import argparse
import random
import shutil
from pathlib import Path

import cv2
import supervision as sv
from ultralytics import YOLO

AIRPLANE_CLASS_ID = 4


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="Video to sample frames from")
    parser.add_argument("--dataset-dir", default="dataset", help="Output dataset root")
    parser.add_argument("--model", default="yolov8n.pt", help="Teacher model for pseudo-labeling")
    parser.add_argument("--stride", type=int, default=15, help="Keep 1 of every N frames")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="Only keep detections above this")
    parser.add_argument("--val-split", type=float, default=0.2, help="Fraction of frames held out for validation")
    parser.add_argument("--seed", type=int, default=0, help="Split shuffling seed")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise FileNotFoundError(f"Video not found: {source_path}")

    dataset_dir = Path(args.dataset_dir)
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    for split in ("train", "val"):
        (dataset_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    video_info = sv.VideoInfo.from_video_path(str(source_path))
    width, height = video_info.width, video_info.height
    model = YOLO(args.model)

    samples = []
    frame_generator = sv.get_video_frames_generator(str(source_path), stride=args.stride)
    for sampled_idx, frame in enumerate(frame_generator):
        result = model(frame, conf=args.min_confidence, verbose=False)[0]
        detections = sv.Detections.from_ultralytics(result)
        detections = detections[detections.class_id == AIRPLANE_CLASS_ID]
        if len(detections) == 0:
            continue

        lines = []
        for x1, y1, x2, y2 in detections.xyxy:
            cx = ((x1 + x2) / 2) / width
            cy = ((y1 + y2) / 2) / height
            bw = (x2 - x1) / width
            bh = (y2 - y1) / height
            # Single-class dataset: 'airplane' is class 0 in the fine-tuned model.
            lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        samples.append((sampled_idx, frame, lines))

    if not samples:
        raise SystemExit("No confident airplane detections found -- lower --min-confidence or check the video.")

    random.Random(args.seed).shuffle(samples)
    split_at = int(len(samples) * (1 - args.val_split))

    for position, (sampled_idx, frame, lines) in enumerate(samples):
        split = "train" if position < split_at else "val"
        stem = f"{source_path.stem}_{sampled_idx:06d}"
        cv2.imwrite(str(dataset_dir / "images" / split / f"{stem}.jpg"), frame)
        (dataset_dir / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n")

    data_yaml = dataset_dir / "data.yaml"
    data_yaml.write_text(
        f"path: {dataset_dir.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "nc: 1\n"
        "names: [airplane]\n"
    )

    n_train = split_at
    n_val = len(samples) - split_at
    print(f"Dataset written to {dataset_dir}")
    print(f"  train: {n_train} images")
    print(f"  val:   {n_val} images")
    print(f"  config: {data_yaml}")


if __name__ == "__main__":
    main()
