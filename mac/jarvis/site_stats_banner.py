"""Bannière GIF animée résumant les inscriptions du site.

Pourquoi un GIF et pas une image fixe : Discord anime les GIF dans un embed,
sans webhook ni service externe. C'est le seul visuel animé qu'un bot peut
publier tel quel.

Pillow est une dépendance OPTIONNELLE ici : sans elle, `build()` lève une
erreur explicite et l'appelant publie l'embed sans bannière plutôt que d'échouer.
Les polices sont résolues avec repli sur la police par défaut : un poste sans
Segoe UI produit une bannière moins jolie, pas une exception.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - dépend de l'environnement
    from PIL import Image, ImageDraw, ImageFont

    HAVE_PIL = True
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = None  # type: ignore
    HAVE_PIL = False

WIDTH, HEIGHT, FRAMES = 960, 320, 44
BG_TOP, BG_BOTTOM = (14, 17, 28), (26, 18, 46)
ACCENT, ACCENT_2, WARN = (124, 92, 255), (0, 214, 180), (255, 176, 46)
WHITE, GREY = (238, 240, 250), (146, 152, 178)

_FONTS = ("C:/Windows/Fonts/segoeui{}.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf")


def _font(size: int, bold: bool = True):
    for template in _FONTS:
        candidate = template.format("b" if bold else "")
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _gradient():
    base = Image.new("RGB", (WIDTH, HEIGHT), BG_TOP)
    draw = ImageDraw.Draw(base)
    for y in range(HEIGHT):
        t = y / HEIGHT
        draw.line([(0, y), (WIDTH, y)],
                  fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return base


def _ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def _fade(colour, progress: float):
    """Couleur qui émerge du fond : évite l'apparition brutale d'un chiffre."""
    return tuple(int(BG_BOTTOM[k] + (colour[k] - BG_BOTTOM[k]) * progress) for k in range(3))


def build(stats: dict[str, Any], destination: str | Path) -> str:
    """Écrit le GIF et renvoie son chemin. Lève RuntimeError sans Pillow."""
    if not HAVE_PIL:
        raise RuntimeError("Pillow n'est pas installé (pip install -U pillow) : "
                           "bannière impossible.")
    out = Path(destination)
    out.parent.mkdir(parents=True, exist_ok=True)

    total = int(stats.get("total") or 0)
    confirmes = int(stats.get("confirmes") or 0)
    non_confirmes = int(stats.get("non_confirmes") or 0)
    premium = int(stats.get("premium") or 0)
    depuis = int(stats.get("depuis_activation") or 0)
    depuis_ok = int(stats.get("depuis_activation_confirmes") or 0)
    share = (confirmes / total) if total else 0.0

    f_title, f_big, f_label, f_small = _font(30), _font(96), _font(19, False), _font(17, False)
    f_stat = _font(38)
    base = _gradient()
    frames = []

    for i in range(FRAMES):
        img = base.copy()
        draw = ImageDraw.Draw(img)
        progress = _ease(min(1.0, i / (FRAMES * 0.62)))

        # Liseré : balayage lumineux qui boucle sans coupure.
        for x in range(WIDTH):
            phase = (x / WIDTH - (i / FRAMES)) % 1.0
            glow = max(0.0, 1 - abs(phase - 0.5) * 3)
            draw.line([(x, 0), (x, 4)],
                      fill=tuple(int(a + (b - a) * glow) for a, b in zip(ACCENT, ACCENT_2)))

        draw.text((44, 34), str(stats.get("site", "")).upper(), font=f_title, fill=WHITE)
        draw.text((46, 74), "Communauté inscrite", font=f_label, fill=GREY)

        # Le compteur monte jusqu'au vrai total, puis s'y fige.
        draw.text((44, 108), f"{int(total * progress):,}".replace(",", " "),
                  font=f_big, fill=ACCENT_2)
        draw.text((44, 228), "membres inscrits", font=f_label, fill=GREY)

        bx, by, bw, bh = 470, 150, 440, 26
        draw.rounded_rectangle([bx, by, bx + bw, by + bh], 13, fill=(40, 44, 66))
        draw.rounded_rectangle([bx, by, bx + max(6, int(bw * share * progress)), by + bh],
                               13, fill=ACCENT)
        draw.text((bx, by - 30), "E-mails confirmés", font=f_label, fill=GREY)
        draw.text((bx + bw - 86, by - 30), f"{share * 100:4.1f} %", font=f_label, fill=ACCENT)

        for n, (value, label, colour) in enumerate(
                ((confirmes, "confirmés", ACCENT_2),
                 (non_confirmes, "non confirmés", WARN),
                 (premium, "premium", ACCENT))):
            q = _ease(max(0.0, min(1.0, (i - 6 - n * 4) / 14)))
            x = 470 + n * 150
            draw.text((x, 200), str(value), font=f_stat, fill=_fade(colour, q))
            draw.text((x, 248), label, font=f_small, fill=_fade(GREY, q))

        radius = 5 + 2.2 * abs(((i / FRAMES) * 2 % 2) - 1)
        draw.ellipse([WIDTH - 190, 44 - radius, WIDTH - 190 + 2 * radius, 44 + radius],
                     fill=ACCENT_2)
        draw.text((WIDTH - 168, 34), "données live", font=f_small, fill=GREY)
        # La nuance reste sur l'image : le taux brut seul induirait en erreur.
        draw.text((44, 288),
                  f"Vérification e-mail active depuis le {stats.get('verification_active_depuis', '')} : "
                  f"{depuis_ok}/{depuis} des nouveaux comptes confirmés",
                  font=f_small, fill=GREY)

        frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=128))

    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=70, loop=0, optimize=True)
    return str(out)
