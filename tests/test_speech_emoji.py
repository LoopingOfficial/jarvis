"""Phase 3A — aucun emoji ne doit atteindre le moteur TTS.

Un moteur TTS ne saute pas un emoji : il lit son nom Unicode (« croissant de
lune »). Le texte affiché reste inchangé ; seule la version parlée est nettoyée.
"""
from __future__ import annotations

import unicodedata

import pytest

from jarvis.speech_sanitizer import sanitize_for_speech

# Les dix emojis explicitement demandés au test.
REQUIRED = ["🌙", "✨", "✅", "❌", "⚠️", "🔥", "❤️", "😂", "👍", "🚀"]


@pytest.mark.parametrize("emoji", REQUIRED)
def test_each_required_emoji_is_removed(emoji):
    spoken = sanitize_for_speech(f"Rapport {emoji} terminé")
    assert emoji.strip("️") not in spoken
    assert spoken == "Rapport terminé"


def test_reference_case_from_the_brief():
    assert sanitize_for_speech("Bonne nuit Jérôme 🌙✨") == "Bonne nuit Jérôme"


def test_no_pictographic_codepoint_survives():
    text = "État " + " ".join(REQUIRED) + " final"
    spoken = sanitize_for_speech(text)
    for ch in spoken:
        cat = unicodedata.category(ch)
        assert not (cat == "So"), f"symbole résiduel {ch!r} ({unicodedata.name(ch, '?')})"
    assert spoken == "État final"


def test_emoji_only_message_produces_nothing_to_say():
    """Rien à prononcer — le serveur doit refuser plutôt que lire « fusée »."""
    assert sanitize_for_speech("🚀") == ""
    assert sanitize_for_speech("👍 ✨ 🔥") == ""


def test_display_text_is_untouched():
    """Le sanitizer est une fonction pure : la source n'est pas modifiée."""
    original = "Bonne nuit Jérôme 🌙✨"
    sanitize_for_speech(original)
    assert original == "Bonne nuit Jérôme 🌙✨"


def test_markdown_and_urls_still_handled():
    spoken = sanitize_for_speech(
        "**Rapport** 📊 prêt : voir `config.py` et https://example.com/a/b")
    assert "`" not in spoken and "*" not in spoken
    assert "http" not in spoken and "lien" in spoken
    assert "📊" not in spoken


def test_table_and_code_block_not_spoken():
    spoken = sanitize_for_speech(
        "Résultat ✅\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```py\nx = 1\n```")
    assert "|" not in spoken and "```" not in spoken and "✅" not in spoken
