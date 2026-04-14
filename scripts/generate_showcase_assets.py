from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "screenshots"
OUT = ROOT / "docs" / "showcase"
OUT.mkdir(parents=True, exist_ok=True)


def load_font(size: int, bold: bool = False):
    candidates = []
    if bold:
        candidates.extend(
            [
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
            ]
        )
    else:
        candidates.extend(
            [
                r"C:\Windows\Fonts\segoeui.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
        )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


TITLE_FONT = load_font(54, bold=True)
SUB_FONT = load_font(24, bold=False)
LABEL_FONT = load_font(22, bold=True)


def rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    return mask


def add_shadow(base, card, xy, radius=24, offset=(0, 16), shadow_alpha=88):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_card = Image.new("RGBA", card.size, (0, 0, 0, shadow_alpha))
    shadow_mask = rounded_mask(card.size, radius)
    shadow.alpha_composite(shadow_card, (xy[0] + offset[0], xy[1] + offset[1]))
    shadow.putalpha(Image.new("L", base.size, 0))
    base.alpha_composite(shadow)


def paste_card(base, card, xy, radius=24):
    mask = rounded_mask(card.size, radius)
    base.paste(card, xy, mask)


def cover(img: Image.Image, size):
    src_ratio = img.width / img.height
    dst_ratio = size[0] / size[1]
    if src_ratio > dst_ratio:
        new_h = size[1]
        new_w = int(new_h * src_ratio)
    else:
        new_w = size[0]
        new_h = int(new_w / src_ratio)
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left = (new_w - size[0]) // 2
    top = (new_h - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def make_card(img, size, label):
    card = cover(img, size).convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((18, size[1] - 76, 210, size[1] - 24), radius=18, fill=(9, 20, 33, 200))
    draw.text((40, size[1] - 65), label, fill=(255, 255, 255), font=LABEL_FONT)
    card.alpha_composite(overlay)
    return card


def gradient_bg(size):
    bg = Image.new("RGBA", size, (0, 0, 0, 255))
    px = bg.load()
    for y in range(size[1]):
        for x in range(size[0]):
            t = y / max(1, size[1] - 1)
            r = int(238 - 28 * t + 14 * (x / size[0]))
            g = int(245 - 48 * t)
            b = int(248 - 70 * t)
            px[x, y] = (r, g, b, 255)
    draw = ImageDraw.Draw(bg)
    draw.ellipse((-120, -80, 420, 360), fill=(255, 255, 255, 90))
    draw.ellipse((900, -40, 1500, 420), fill=(198, 226, 255, 120))
    draw.ellipse((980, 360, 1520, 900), fill=(224, 240, 252, 110))
    return bg


def build_hero(desktop, mobile):
    canvas = gradient_bg((1400, 880))
    draw = ImageDraw.Draw(canvas)
    draw.text((90, 84), "Frontend Showcase", fill=(10, 22, 34), font=TITLE_FONT)
    draw.text(
        (92, 158),
        "Desktop dashboard and mobile control panel for smart home operations.",
        fill=(61, 79, 96),
        font=SUB_FONT,
    )

    desktop_card = make_card(desktop, (900, 520), "Desktop")
    mobile_card = make_card(mobile, (280, 560), "Mobile")

    canvas.alpha_composite(desktop_card, (88, 250))
    canvas.alpha_composite(mobile_card, (1020, 210))

    out = OUT / "showcase-hero.png"
    canvas.convert("RGB").save(out, quality=95)


def crop_focus(img, target_size, progress, mode):
    scale = 1.10 - 0.08 * progress
    src_w = int(target_size[0] / scale)
    src_h = int(target_size[1] / scale)
    src_w = min(src_w, img.width)
    src_h = min(src_h, img.height)
    if mode == "desktop":
        left = int((img.width - src_w) * (0.10 + 0.20 * progress))
        top = int((img.height - src_h) * 0.05)
    else:
        left = int((img.width - src_w) * 0.08)
        top = int((img.height - src_h) * (0.04 + 0.18 * progress))
    cropped = img.crop((left, top, left + src_w, top + src_h))
    return cropped.resize(target_size, Image.Resampling.LANCZOS)


def frame_base():
    bg = gradient_bg((960, 540))
    draw = ImageDraw.Draw(bg)
    draw.rounded_rectangle((30, 26, 930, 514), radius=28, outline=(255, 255, 255, 170), width=2)
    return bg


def add_caption(frame, title, subtitle):
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle((44, 388, 916, 498), radius=22, fill=(8, 18, 30, 182))
    draw.text((72, 410), title, fill=(255, 255, 255), font=load_font(30, bold=True))
    draw.text((72, 450), subtitle, fill=(222, 232, 241), font=load_font(18))
    frame.alpha_composite(overlay)
    return frame


def build_demo_gif(desktop, mobile):
    frames = []

    for i in range(10):
        p = i / 9
        frame = frame_base()
        shot = crop_focus(desktop, (820, 320), p, "desktop")
        frame.alpha_composite(shot.convert("RGBA"), (70, 54))
        add_caption(frame, "Desktop Dashboard", "Live overview for rooms, events, analysis, and device control.")
        frames.append(frame.convert("P", palette=Image.Palette.ADAPTIVE))

    for i in range(8):
        p = i / 7
        frame = frame_base()
        desk = crop_focus(desktop, (560, 250), 0.55, "desktop")
        mob = crop_focus(mobile, (220, 360), p, "mobile")
        frame.alpha_composite(desk.convert("RGBA"), (60, 72))
        frame.alpha_composite(mob.convert("RGBA"), (670, 54))
        add_caption(frame, "Desktop + Mobile", "The project includes both a full dashboard and a phone-friendly control page.")
        frames.append(frame.convert("P", palette=Image.Palette.ADAPTIVE))

    durations = [120] * 9 + [900] + [120] * 7 + [1200]
    frames[0].save(
        OUT / "showcase-demo.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )


def main():
    desktop = Image.open(SRC / "frontend-desktop.png").convert("RGB")
    mobile = Image.open(SRC / "frontend-mobile.png").convert("RGB")
    build_hero(desktop, mobile)
    build_demo_gif(desktop, mobile)


if __name__ == "__main__":
    main()
