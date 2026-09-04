"""Generate the TransLens application icon using Pillow only."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


SCALE = 4
CANVAS_SIZE = 256
WORK_SIZE = CANVAS_SIZE * SCALE
TEAL = (0, 184, 169, 255)
WHITE = (244, 255, 253, 255)
ICO_SIZES = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]


def find_font() -> tuple[str | None, int]:
    """Return the preferred CJK font, or the first usable installed font."""
    font_candidates = [
        (Path("C:/Windows/Fonts/msjhbd.ttc"), 0),
        (Path("C:/Windows/Fonts/msjh.ttc"), 0),
        (Path("C:/Windows/Fonts/malgun.ttf"), 0),
        (Path("C:/Windows/Fonts/arial.ttf"), 0),
        (Path("C:/Windows/Fonts/segoeui.ttf"), 0),
    ]
    windows_fonts = Path("C:/Windows/Fonts")
    if windows_fonts.is_dir():
        for path in sorted(windows_fonts.glob("*.tt[fc]")):
            font_candidates.append((path, 0))
        for path in sorted(windows_fonts.glob("*.otf")):
            font_candidates.append((path, 0))

    seen: set[Path] = set()
    for path, index in font_candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            ImageFont.truetype(str(path), 10, index=index)
        except (OSError, IndexError):
            continue
        return str(path), index
    return None, 0


def load_character_font(size: int) -> tuple[ImageFont.ImageFont, str]:
    """Load the requested bold font and use 中 if 譯 has no measurable glyph."""
    font_path, font_index = find_font()
    if font_path is None:
        return ImageFont.load_default(), "中"

    try:
        font = ImageFont.truetype(font_path, size, index=font_index)
    except (OSError, IndexError):
        return ImageFont.load_default(), "中"

    character = "譯"
    if font.getbbox(character) is None:
        character = "中"
    return font, character


def rounded_rect(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_icon() -> Image.Image:
    """Build the icon at 1024px, then downsample for clean edges."""
    canvas = Image.new("RGBA", (WORK_SIZE, WORK_SIZE), (0, 0, 0, 0))

    # A restrained background keeps the teal frame legible on light and dark shells.
    background = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    background_draw = ImageDraw.Draw(background)
    rounded_rect(background_draw, (38, 38, 986, 986), 220, (10, 24, 34, 242))
    rounded_rect(background_draw, (57, 57, 967, 967), 198, (20, 39, 49, 175))
    canvas.alpha_composite(background)

    # A soft shadow separates the lens from the dark tile without making it glow.
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    rounded_rect(shadow_draw, (164, 139, 786, 761), 172, (0, 0, 0, 145))
    shadow_draw.line((705, 685, 869, 849), fill=(0, 0, 0, 145), width=104, joint="curve")
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    canvas.alpha_composite(shadow)

    lens = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    lens_draw = ImageDraw.Draw(lens)

    # Rounded square lens frame and its translucent viewing area.
    rounded_rect(lens_draw, (150, 125, 770, 745), 180, TEAL)
    rounded_rect(lens_draw, (198, 173, 722, 697), 132, (8, 35, 42, 165))
    rounded_rect(lens_draw, (211, 186, 709, 684), 119, (16, 51, 57, 145))

    # Magnifier handle, tucked behind the frame.
    lens_draw.line((700, 695, 865, 860), fill=TEAL, width=94, joint="curve")
    lens_draw.ellipse((818, 813, 912, 907), fill=TEAL)
    canvas.alpha_composite(lens)

    # The glyph is deliberately centered as a strong, readable mark at small sizes.
    glyph_font, character = load_character_font(390)
    glyph_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph_layer)
    glyph_draw.text(
        (460, 415),
        character,
        font=glyph_font,
        anchor="mm",
        fill=WHITE,
        stroke_width=4,
        stroke_fill=(0, 105, 101, 255),
    )
    canvas.alpha_composite(glyph_layer)

    return canvas.resize((CANVAS_SIZE, CANVAS_SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    output_dir = Path(__file__).resolve().parent
    output_dir.mkdir(parents=True, exist_ok=True)
    icon = make_icon()
    icon.save(output_dir / "translens.png", format="PNG", optimize=True)
    icon.save(output_dir / "translens.ico", format="ICO", sizes=ICO_SIZES)


if __name__ == "__main__":
    main()
