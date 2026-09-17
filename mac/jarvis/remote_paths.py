"""Résolution CENTRALE des chemins distants.

Une seule fonction est responsable de la résolution des chemins SSR/SSH :

    resolveRemotePath(connector, requestedPath, activeContext)

Règles absolues :
  - Jamais de racine générique de secours (`/var/www/html`, `/var/www`,
    `/home/user`, `server_web`…). Si aucun `working_directory` réel n'existe,
    on lève `MissingRemoteWorkingDirectoryError` au lieu d'inventer un chemin.
  - Un chemin RELATIF est toujours résolu sous la racine de travail effective.
  - Un chemin ABSOLU est validé puis utilisé tel quel, mais toute résolution
    qui atterrit sous une racine générique interdite différente de la racine
    configurée est bloquée (`INVALID_REMOTE_ROOT`).
"""
from __future__ import annotations

from pathlib import PurePosixPath

from .build import is_forbidden_remote_default, trace


class RemotePathResolutionError(Exception):
    """Échec de résolution de chemin distant."""


class MissingRemoteWorkingDirectoryError(RemotePathResolutionError):
    """Aucune racine de travail réelle n'est configurée pour le connecteur."""


class InvalidRemoteRootError(RemotePathResolutionError):
    """Le chemin résolu atterrit sous une racine générique interdite."""


def _root_from(connector: dict, activeContext: dict | None) -> tuple[str, str]:
    """Racine de travail effective + clé d'origine (pour les traces)."""
    cfg = (connector or {}).get("config") or {}
    ctx = activeContext or {}

    current = str(ctx.get("current_remote_directory") or ctx.get("current_directory") or "").strip()
    if current:
        return current.rstrip("/"), "current_remote_directory"

    remote_root = str(ctx.get("remote_root") or "").strip()
    if remote_root:
        return remote_root.rstrip("/"), "active_remote_root"

    for key in ("working_directory", "deployment_path", "remote_path"):
        value = str(cfg.get(key) or "").strip()
        if value:
            if is_forbidden_remote_default(value):
                raise InvalidRemoteRootError(
                    f"INVALID_REMOTE_ROOT: la racine configurée « {value} » "
                    f"(champ {key}) est une valeur générique interdite. Configure un "
                    f"vrai chemin de travail, ex. /home/<utilisateur>/public_html."
                )
            return value.rstrip("/"), f"connector.{key}"

    raise MissingRemoteWorkingDirectoryError(
        "Le connecteur n'a aucun working_directory configuré. "
        "Configure-le dans Settings → Connectors (champ « Répertoire de travail »)."
    )


def _blocked_by_barrier(path: str, root: str) -> str | None:
    """Retourne un message d'erreur si le chemin tombe sous une racine interdite."""
    p = path.rstrip("/") or "/"
    for forbidden in ("/var/www/html", "/var/www", "/home/user"):
        if p == forbidden or p.startswith(forbidden + "/"):
            if not (root == forbidden or root.startswith(forbidden + "/")):
                return (
                    f"INVALID_REMOTE_ROOT: « {p} » atterrit sous une racine générique "
                    f"interdite alors que la racine configurée est « {root} ». "
                    f"Le chemin aurait dû être résolu sous « {root} »."
                )
    return None


def _norm_posix(path: str) -> str:
    """Normalise lexiquement un chemin POSIX (collapse . et ..)."""
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


def resolveRemotePath(connector: dict, requestedPath: str | None, activeContext: dict | None = None) -> str:
    """Résout un chemin de fichier distant de façon sûre et déterministe.

    Retourne le chemin absolu POSIX.
    """
    requested = str(requestedPath or ".").strip()
    root, origin = _root_from(connector or {}, activeContext or {})

    if requested.startswith("/"):
        candidate = _norm_posix(requested)
        barrier = _blocked_by_barrier(candidate, root)
        if barrier:
            trace(f"INVALID_REMOTE_ROOT input={requested!r} scope=remote root={root}")
            raise InvalidRemoteRootError(barrier)
        trace(f"input={requestedPath!r} resolved_path={candidate} root={root} origin={origin} bypass=absolute")
        return candidate

    root_norm = _norm_posix(root)
    candidate = _norm_posix(root + "/" + requested)
    if candidate != root_norm and not candidate.startswith(root_norm + "/"):
        trace(f"INVALID_TRAVERSAL input={requested!r} root={root}")
        raise InvalidRemoteRootError(
            f"Chemin distant « {requested} » sort du répertoire de travail autorisé « {root} »."
        )
    barrier = _blocked_by_barrier(candidate, root)
    if barrier:
        trace(f"INVALID_REMOTE_ROOT input={requested!r} root={root}")
        raise InvalidRemoteRootError(barrier)
    trace(f"input={requestedPath!r} resolved_path={candidate} root={root} origin={origin} bypass=relative")
    return candidate


def active_remote_root(connector: dict, activeContext: dict | None = None) -> str:
    """Racine de travail effective (pour contextes et traces), sans lever d'exception."""
    try:
        root, _ = _root_from(connector or {}, activeContext or {})
        return root
    except RemotePathResolutionError:
        return ""