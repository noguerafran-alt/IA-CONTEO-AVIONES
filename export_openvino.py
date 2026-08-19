"""Export YOLO weights to OpenVINO IR so inference can run on the Intel iGPU.

There is no CUDA here (the GPU is an Intel Iris Xe), so PyTorch cannot use it.
OpenVINO can: on this machine it takes detection from ~7 FPS (CPU) to ~40 FPS
(iGPU). Inference only -- training still runs on CPU.

Usage:
  python export_openvino.py --weights yolov8n.pt
  python export_openvino.py --weights runs/detect/runway/weights/best.pt

Then run the pipeline against the exported directory:
  python detect_track_count.py --model yolov8n_openvino_model/ --device intel:gpu ...
"""

import argparse
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weights", default="yolov8n.pt", help="Weights to export")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--half", action="store_true", help="Export FP16 (usually faster on the iGPU)")
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.exists():
        raise FileNotFoundError(f"Weights not found: {weights}")

    model = YOLO(str(weights))
    exported = model.export(format="openvino", imgsz=args.imgsz, half=args.half)
    print(f"\nExported to: {exported}")
    print("Run with:  --model <that directory> --device intel:gpu")


if __name__ == "__main__":
    main()


