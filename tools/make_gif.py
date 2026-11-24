"""Builds docs/demo.gif from the screenshots saved by `python -m portal_robot demo --frames DIR`.

Usage: python tools/make_gif.py FRAMES_DIR docs/demo.gif
Requires Pillow (pip install pillow), which the robot itself does not need.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 820
CAPTION_HEIGHT = 34


def caption_for(name: str) -> str:
    words = name.split("_", 1)[1].rsplit(".", 1)[0].replace("-", " ")
    return words[:1].upper() + words[1:]


def main(frames_dir: str, out: str, every: int = 1) -> None:
    files = sorted(Path(frames_dir).glob("*.png"))[::every]
    if not files:
        raise SystemExit(f"no frames in {frames_dir}")
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except OSError:
        font = ImageFont.load_default()
    frames = []
    for path in files:
        img = Image.open(path).convert("RGB")
        img = img.resize((WIDTH, round(img.height * WIDTH / img.width)), Image.LANCZOS)
        canvas = Image.new("RGB", (WIDTH, img.height + CAPTION_HEIGHT), "#111827")
        canvas.paste(img, (0, CAPTION_HEIGHT))
        ImageDraw.Draw(canvas).text((12, 8), f"Robot: {caption_for(path.name)}", fill="#f9fafb", font=font)
        frames.append(canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    durations = [1100] * len(frames)
    durations[-1] = 2500
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"{out}: {len(frames)} frames, {Path(out).stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1)
