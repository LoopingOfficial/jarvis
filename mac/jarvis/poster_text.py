"""Compose poster typography after generation, with PIL rather than diffusion.

Measured reason this exists: asked for a giveaway poster with room for a title,
Z-Image stamped "GIVAWAY" — misspelled — twice, in the very zones that had been
reserved for typography.  The generative model is not a typesetter, so the
pipeline now tells it to draw no lettering at all and the wording is composited
here, where it is pixel-exact and spelled correctly.

The layout is deliberately simple and predictable: named bands (top / bottom /
centre) measured as fractions of the image, text auto-fitted inside its band.
Nothing here tries to be clever about where the subject is; the POSTER pipeline
already asks the model to keep those bands clear.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:  # Pillow is optional; the caller degrades gracefully without it.
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on installs without PIL
    PIL_AVAILABLE = False


class PosterTextError(RuntimeError):
    pass


# Bands are (top, bottom) as fractions of image height.
BANDS: dict[str, tuple[float, float]] = {
    "top": (0.04, 0.26),
    "upper": (0.10, 0.34),
    "center": (0.38, 0.62),
    "lower": (0.66, 0.88),
    "bottom": (0.78, 0.96),
}

# Ordered by preference; the first family present on the machine wins.
_FONT_CANDIDATES = (
    "Montserrat-ExtraBold.ttf", "Poppins-Bold.ttf", "BebasNeue-Regular.ttf",
    "impact.ttf", "arialbd.ttf", "segoeuib.ttf", "calibrib.ttf", "arial.ttf",
)
_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
    Path("/usr/share/fonts"),
)


def find_font(preferred: str = "") -> Path | None:
    """Locate a usable bold font, or ``None`` if the system has none."""
    names = ([preferred] if preferred else []) + list(_FONT_CANDIDATES)
    for name in names:
        candidate = Path(name)
        if candidate.is_file():
            return candidate
        for directory in _FONT_DIRS:
            if not directory.is_dir():
                continue
            hit = directory / name
            if hit.is_file():
                return hit
            matches = list(directory.rglob(name))
            if matches:
                return matches[0]
    return None


@dataclass
class TextBlock:
    """One line (or wrapped paragraph) of poster copy."""

    text: str
    band: str = "top"
    align: str = "center"
    color: tuple[int, int, int] = (255, 255, 255)
    max_fraction: float = 0.86      # of image width
    uppercase: bool = False
    shadow: bool = True
    outline: int = 0
    font: str = ""
    # "auto" repaints the band with the surrounding background colour before
    # drawing.  This is not cosmetic: at CFG 1.0 Z-Image has no negative
    # guidance, so asking it to draw no lettering works only sometimes -- a
    # test poster still stamped "GIVAWAY" across the bottom band after the
    # instruction was added.  Repainting makes the result deterministic
    # instead of dependent on the model obeying.
    band_fill: str = "auto"         # "auto" | "none"

    def resolved(self) -> str:
        value = re.sub(r"\s+", " ", str(self.text or "")).strip()
        return value.upper() if self.uppercase else value


@dataclass
class PosterLayout:
    blocks: list[TextBlock] = field(default_factory=list)
    scrim: float = 0.0   # 0..1 darkening behind text bands, for legibility

    def as_dict(self) -> dict[str, Any]:
        return {"blocks": [b.__dict__ for b in self.blocks], "scrim": self.scrim}


def _repaint_band(image: "Image.Image", top: int, bottom: int) -> None:
    """Flood a horizontal band with the background colour just outside it.

    Samples thin strips above and below the band and fills with their median
    colour, so residual lettering disappears into the poster's own background
    rather than under a black bar.  Modifies ``image`` in place.
    """
    width, height = image.size
    top, bottom = max(0, top), min(height, bottom)
    if bottom <= top:
        return
    samples: list[tuple[int, int, int]] = []
    for y in (max(0, top - 6), min(height - 1, bottom + 5)):
        strip = image.crop((0, y, width, y + 1)).convert("RGB")
        # get_flattened_data() is the Pillow 11+ name; getdata() is deprecated.
        reader = getattr(strip, "get_flattened_data", None) or strip.getdata
        samples.extend(reader())
    if not samples:
        return
    channels = [sorted(c[i] for c in samples) for i in range(3)]
    median = tuple(channels[i][len(channels[i]) // 2] for i in range(3))
    image.paste(Image.new("RGB", (width, bottom - top), median), (0, top))


def _fit_font(draw: "ImageDraw.ImageDraw", text: str, font_path: Path,
              max_width: int, max_height: int) -> "ImageFont.FreeTypeFont":
    """Largest size that fits the band, found by bisection rather than a loop."""
    low, high = 8, max(10, max_height)
    best = ImageFont.truetype(str(font_path), low)
    while low <= high:
        mid = (low + high) // 2
        font = ImageFont.truetype(str(font_path), mid)
        box = draw.textbbox((0, 0), text, font=font)
        if (box[2] - box[0]) <= max_width and (box[3] - box[1]) <= max_height:
            best, low = font, mid + 1
        else:
            high = mid - 1
    return best


def compose_poster_text(image_path: str | Path, layout: PosterLayout,
                        output_path: str | Path | None = None) -> Path:
    """Draw ``layout`` onto the generated poster and save it.

    Raises :class:`PosterTextError` rather than returning an unlabelled poster:
    silently skipping the text would look like success.
    """
    if not PIL_AVAILABLE:
        raise PosterTextError(
            "Pillow est requis pour composer le texte des affiches (pip install Pillow).")
    source = Path(image_path)
    if not source.is_file():
        raise PosterTextError(f"Image introuvable : {source}")

    image = Image.open(source).convert("RGBA")
    width, height = image.size
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for block in layout.blocks:
        text = block.resolved()
        if not text:
            continue
        font_path = find_font(block.font)
        if font_path is None:
            raise PosterTextError(
                "Aucune police utilisable trouvée sur cette machine.")
        top_f, bottom_f = BANDS.get(block.band, BANDS["top"])
        band_top, band_bottom = int(height * top_f), int(height * bottom_f)
        max_width = int(width * block.max_fraction)
        max_height = max(12, band_bottom - band_top)

        if block.band_fill == "auto":
            _repaint_band(image, band_top, band_bottom)
        if layout.scrim > 0:
            alpha = int(max(0.0, min(1.0, layout.scrim)) * 255)
            draw.rectangle([0, band_top, width, band_bottom], fill=(0, 0, 0, alpha))

        font = _fit_font(draw, text, font_path, max_width, max_height)
        box = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = box[2] - box[0], box[3] - box[1]
        if block.align == "left":
            x = int(width * (1 - block.max_fraction) / 2)
        elif block.align == "right":
            x = width - int(width * (1 - block.max_fraction) / 2) - text_w
        else:
            x = (width - text_w) // 2
        y = band_top + (max_height - text_h) // 2 - box[1]

        if block.shadow:
            offset = max(2, font.size // 22)
            draw.text((x + offset, y + offset), text, font=font, fill=(0, 0, 0, 150))
        if block.outline:
            draw.text((x, y), text, font=font, fill=(*block.color, 255),
                      stroke_width=block.outline, stroke_fill=(0, 0, 0, 220))
        else:
            draw.text((x, y), text, font=font, fill=(*block.color, 255))

    result = Image.alpha_composite(image, overlay).convert("RGB")
    destination = Path(output_path) if output_path else source.with_name(
        source.stem + "_text.png")
    result.save(destination)
    return destination


def layout_from_lines(lines: Iterable[str], *, scrim: float = 0.0) -> PosterLayout:
    """Build a sensible default layout: title on top, details at the bottom."""
    items = [re.sub(r"\s+", " ", str(x)).strip() for x in lines]
    items = [x for x in items if x]
    if not items:
        raise PosterTextError("Aucun texte à composer.")
    blocks = [TextBlock(items[0], band="top", uppercase=True, outline=3)]
    if len(items) > 1:
        blocks.append(TextBlock(items[1], band="upper", max_fraction=0.7))
    for extra in items[2:3]:
        blocks.append(TextBlock(extra, band="bottom", max_fraction=0.8, outline=2))
    return PosterLayout(blocks=blocks, scrim=scrim)
