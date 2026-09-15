"""Hiérarchie et chaînes IK exposées au runtime."""
from .schema import AVATAR_STATES

RIG_ROOT = "JARVIS_Armature"
IK_CHAINS = ("arm_L", "arm_R", "leg_L", "leg_R")
REQUIRED_BONE_GROUPS = ("Root", "Spine", "Head", "Arms", "Hands", "Fingers", "Legs")

