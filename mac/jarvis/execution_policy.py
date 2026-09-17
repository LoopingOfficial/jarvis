"""Backend-only, request-scoped policy, inherited by nested tool calls."""
from contextlib import contextmanager
from contextvars import ContextVar

_current = ContextVar("jarvis_execution_policy", default=None)


def is_readonly(policy):
    policy = policy or {}
    return (policy.get("intent") in {"security_audit_readonly", "security_analysis"}
            or bool(policy.get("read_only")) or policy.get("write_allowed") is False)


def tools_allowed(policy):
    """Vrai si la politique n'interdit pas les outils (défaut : autorisés)."""
    return (policy or {}).get("tools_allowed", True) is not False


def fast_actions_allowed(policy):
    """Vrai si les actions déterministes courtes restent permises."""
    return (policy or {}).get("fast_actions_allowed", True) is not False


def current_policy():
    return dict(_current.get() or {})


@contextmanager
def policy_scope(policy):
    effective = dict(policy or {})
    inherited = current_policy()
    if is_readonly(inherited):
        effective = {**effective, **inherited, "read_only": True, "write_allowed": False}
    if inherited.get("tools_allowed") is False:
        effective["tools_allowed"] = False
    if inherited.get("fast_actions_allowed") is False:
        effective["fast_actions_allowed"] = False
    token = _current.set(effective)
    try:
        yield effective
    finally:
        _current.reset(token)
