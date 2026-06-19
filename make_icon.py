"""Generate the app icon (icon.ico) — a rupee growth motif on a blue tile.

Run once at build time:  python make_icon.py
"""

from PIL import Image, ImageDraw, ImageFont


def _font(size: int):
    for name in ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(size: int) -> Image.Image:
    s = size * 4  # supersample for crisp edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded background tile with a subtle vertical gradient.
    radius = int(s * 0.22)
    top, bot = (59, 130, 246), (29, 78, 216)  # blue gradient
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / s
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        gd.line([(0, y), (s, y)], fill=(r, g, b, 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # Upward trend line with arrow head.
    pts = [(int(s * 0.20), int(s * 0.70)),
           (int(s * 0.40), int(s * 0.55)),
           (int(s * 0.55), int(s * 0.62)),
           (int(s * 0.80), int(s * 0.30))]
    lw = max(2, int(s * 0.035))
    d.line(pts, fill=(255, 255, 255, 235), width=lw, joint="curve")
    ax, ay = pts[-1]
    ah = int(s * 0.10)
    d.polygon([(ax + int(ah * 0.1), ay - int(ah * 0.1)),
               (ax - ah, ay), (ax, ay + ah)], fill=(255, 255, 255, 235))

    # Rupee glyph.
    f = _font(int(s * 0.42))
    text = "₹"
    try:
        bbox = d.textbbox((0, 0), text, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((int(s * 0.16) - bbox[0], int(s * 0.12) - bbox[1]),
               text, font=f, fill=(255, 255, 255, 255))
    except Exception:
        pass

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [render(sz) for sz in sizes]
    imgs[-1].save("icon.ico", sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    imgs[-1].save("icon.png")
    print("Wrote icon.ico and icon.png")


if __name__ == "__main__":
    main()
