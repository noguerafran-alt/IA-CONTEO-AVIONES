"""Fine-tune a YOLO detector on the auto-labeled runway dataset.

Usage:
  python train.py --data dataset/data.yaml --epochs 30

The resulting weights land in runs/detect/<name>/weights/best.pt and can be
passed straight to detect_track_count.py via --model. Note the fine-tuned
model is single-class, so also pass --class-id 0.
"""

import argparse
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default="dataset/data.yaml", help="Dataset YAML from build_dataset.py")
    parser.add_argument("--base-model", default="yolov8n.pt", help="Starting weights")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--name", default="runway", help="Run name under runs/detect/")
    parser.add_argument("--device", default="cpu", help="'cpu', or a CUDA index like '0'")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"Dataset config not found: {data_path}. Run build_dataset.py first.")

    model = YOLO(args.base_model)
    model.train(
        data=str(data_path),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        name=args.name,
        device=args.device,
        patience=10,
    )

    metrics = model.val()
    print(f"mAP50:    {metrics.box.map50:.4f}")
    print(f"mAP50-95: {metrics.box.map:.4f}")
    print(f"Weights:  runs/detect/{args.name}/weights/best.pt")


if __name__ == "__main__":
    main()
