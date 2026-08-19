"""Measure whether a vision model actually reads tail numbers, against known answers.

The point is not to see the model produce a registration -- it is to see whether
the registration it produces is RIGHT, and whether it admits defeat when the
crop is illegible. A model that invents a plausible tail number is worse than
one that returns nothing, because nothing is honestly missing while an invented
one silently corrupts the record.

Ground truth here was read by eye from zoomed crops of the source videos.

  python validate_vlm.py                      # default model
  python validate_vlm.py --compare            # every free NVIDIA vision model
"""
import argparse
import glob
import json

import cv2
import supervision as sv

import vlm_ocr

MODELS = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

# video index, frame, aircraft box, tight registration box, truth
CASES = [
    {"name": "austral-E190",  "video": 0, "frac": 3, "airline": "Austral",
     "aircraft": (150, 380, 1750, 760), "reg": (1230, 570, 1420, 630), "truth": "LV-CEV"},
    {"name": "jetsmart-A320", "video": 3, "frac": 3, "airline": "JetSMART",
     "aircraft": (60, 300, 1900, 800), "reg": (650, 580, 850, 640), "truth": "LV-KDP"},
]


def videos() -> list[str]:
    return [v for v in sorted(glob.glob("data/YTDown*.mp4")) if "(1)" not in v]


def build_crops() -> list[dict]:
    files = videos()
    crops = []
    for case in CASES:
        info = sv.VideoInfo.from_video_path(files[case["video"]])
        index = info.total_frames // case["frac"]
        frame = next(sv.get_video_frames_generator(files[case["video"]], start=index, end=index + 1))
        for kind in ("aircraft", "reg"):
            x1, y1, x2, y2 = case[kind]
            crops.append({**case, "kind": kind, "image": frame[y1:y2, x1:x2]})

    # The Flybondi 737 from the already-processed clip, whose answer we also know.
    frame = next(sv.get_video_frames_generator("data/aeroparque_1080.mp4", start=1500, end=1501))
    crops.append({"name": "flybondi-737", "kind": "aircraft", "airline": "Flybondi",
                  "truth": "LV-HKN", "image": frame[440:700, 250:1800]})
    crops.append({"name": "flybondi-737", "kind": "reg", "airline": "Flybondi",
                  "truth": "LV-HKN", "image": frame[615:660, 1195:1345]})
    return crops


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true", help="Run every model in MODELS")
    parser.add_argument("--model", default=vlm_ocr.DEFAULT_MODEL)
    args = parser.parse_args()

    if not vlm_ocr.load_api_key():
        raise SystemExit("Falta OPENROUTER_API_KEY. Ponela en .env")

    crops = build_crops()
    for model in (MODELS if args.compare else [args.model]):
        print(f"\n=== {model}")
        correct = invented = missed = 0
        for crop in crops:
            result = vlm_ocr.identify(crop["image"], model=model)
            if result.get("error"):
                print(f"  {crop['name']:14s} {crop['kind']:8s} ERROR {result['error'][:70]}")
                continue

            got, truth = result.get("registration"), crop["truth"]
            if got == truth:
                verdict, _ = "CORRECTA", correct
                correct += 1
            elif got is None:
                verdict = "no leida"
                missed += 1
            else:
                verdict = "INVENTADA"
                invented += 1
            print(f"  {crop['name']:14s} {crop['kind']:8s} esperado={truth:7s} "
                  f"leido={str(got):8s} {verdict:10s} aerolinea={result.get('airline')}")

        total = correct + invented + missed
        if total:
            print(f"  -> correctas {correct}/{total}, no leidas {missed}, INVENTADAS {invented}")
            if invented:
                print("     Una matricula inventada descalifica el metodo: no hay forma de "
                      "distinguirla de una correcta despues.")


if __name__ == "__main__":
    main()
