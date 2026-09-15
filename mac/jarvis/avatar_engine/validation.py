"""Quality gates partagés avant publication d'un candidat."""
from .schema import REQUIRED_PARTS, VISEMES, ANIMATIONS

REQUIRED_MESHES = tuple(REQUIRED_PARTS)
REQUIRED_VISEMES = tuple(f"viseme_{name}" for name in VISEMES)
REQUIRED_ANIMATIONS = tuple(ANIMATIONS)

