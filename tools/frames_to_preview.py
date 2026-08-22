"""Turn a rendered frame folder into something you can actually look at.

    python tools/frames_to_preview.py <folder> --gif out.gif --sheet sheet.png

Renders come out as a PNG sequence, which is right for an edit but useless for a
quick "did it work" glance. This builds an animated GIF and a contact sheet from
the same frames, no ffmpeg required.
"""

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def load(folder, step=1, limit=0):
    files = sorted(p for p in Path(folder).iterdir()
                   if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
    files = files[::max(1, step)]
    return files[:limit] if limit else files


def make_gif(files, out, width=640, fps=12):
    frames = []
    for f in files:
        im = Image.open(f).convert("RGB")
        h = int(im.height * width / im.width)
        frames.append(im.resize((width, h), Image.LANCZOS))
    if not frames:
        raise SystemExit("no frames")
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=int(1000 / fps), loop=0, optimize=True)
    return out


def make_sheet(files, out, cols=4, width=460):
    picks = [files[round(i * (len(files) - 1) / 7)] for i in range(8)] if len(files) > 8 \
        else files
    ims = []
    for f in picks:
        im = Image.open(f).convert("RGB")
        h = int(im.height * width / im.width)
        ims.append((im.resize((width, h), Image.LANCZOS), f.stem.split(".")[-1]))
    w, h = ims[0][0].size
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), "#101418")
    draw = ImageDraw.Draw(sheet)
    for i, (im, label) in enumerate(ims):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(im, (x, y))
        draw.text((x + 8, y + 6), f"кадр {label}", fill="#ffe680")
    sheet.save(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--gif", default="")
    ap.add_argument("--sheet", default="")
    ap.add_argument("--step", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fps", type=int, default=12)
    a = ap.parse_args()
    files = load(a.folder, a.step, a.limit)
    print(f"кадров: {len(files)}")
    if a.gif:
        print("gif:  ", make_gif(files, a.gif, fps=a.fps))
    if a.sheet:
        print("лист: ", make_sheet(files, a.sheet))


if __name__ == "__main__":
    main()
