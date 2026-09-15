"""Expressions et visèmes pilotables par TTS / FaceController."""
from .schema import EXPRESSION_KEYS, VISEMES

FACE_KEYS = tuple(EXPRESSION_KEYS)
VISEME_KEYS = tuple(f"viseme_{name}" for name in VISEMES)

