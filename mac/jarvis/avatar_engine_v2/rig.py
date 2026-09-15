"""Rig stage contract, intentionally gated until static approval."""
RIG_BONES = ("spine", "neck", "head", "shoulders", "arms", "hands", "fingers",
             "pelvis", "legs", "feet")


def require_static_approval(approved: bool) -> None:
    if not approved:
        raise RuntimeError("Rig stage is blocked until the static avatar is user-approved")
