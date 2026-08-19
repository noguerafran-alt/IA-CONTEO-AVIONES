"""Transcode annotated videos to browser-playable H.264.

supervision's VideoSink writes mp4v (MPEG-4 Part 2), which browsers refuse to
play. This re-encodes to H.264 + faststart so the dashboard can stream them.

Usage:
  python transcode_web.py                      # all output/annotated*.mp4
  python transcode_web.py --input output/annotated_clip2.mp4
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

WEB_DIR = Path(__file__).parent / "output" / "web"

# winget installs ffmpeg here but doesn't always put it on PATH for this shell.
FALLBACK_FFMPEG = Path(
    os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
        r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
        r"\ffmpeg-9.0-full_build\bin\ffmpeg.exe"
    )
)


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    if FALLBACK_FFMPEG.exists():
        return str(FALLBACK_FFMPEG)
    sys.exit("ffmpeg not found. Install it (winget install Gyan.FFmpeg) or add it to PATH.")


def transcode(ffmpeg: str, src: Path, dest: Path, crf: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", help="Single video to convert (default: every output/annotated*.mp4)")
    parser.add_argument("--crf", type=int, default=26, help="H.264 quality, lower is better/bigger")
    args = parser.parse_args()

    ffmpeg = find_ffmpeg()
    output_dir = Path(__file__).parent / "output"

    sources = [Path(args.input)] if args.input else sorted(output_dir.glob("annotated*.mp4"))
    if not sources:
        sys.exit("No annotated videos found in output/. Run detect_track_count.py first.")

    for src in sources:
        dest = WEB_DIR / f"{src.stem}.mp4"
        print(f"Transcoding {src.name} -> output/web/{dest.name} ...", flush=True)
        transcode(ffmpeg, src, dest, args.crf)
        size_mb = dest.stat().st_size / 1_000_000
        print(f"  done ({size_mb:.1f} MB)")

    print(f"\nWeb-ready videos in {WEB_DIR}")


if __name__ == "__main__":
    main()
