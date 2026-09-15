"""Paramètres de morphologie consommés par l'implémentation Blender."""
from .schema import BODY_MORPHS, DEFAULT_PARAMS

BODY_PARAMETER_DEFAULTS = {name: DEFAULT_PARAMS[name] for name in BODY_MORPHS}

