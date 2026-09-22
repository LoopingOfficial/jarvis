"""Vue lecture seule de l'espace de travail réel, pour les moniteurs de VELKO.

Les écrans de VELKO n'affichent que des faits : le flux d'événements dit QUOI
regarder (`file.opened`, `file.changed`…), ce module fournit le CONTENU RÉEL
correspondant, lu sur le disque au moment de la demande.

Rien n'est mis en cache, rien n'est reconstitué, rien n'est simulé : si le
fichier n'existe pas ou sort des dossiers autorisés, la réponse le dit.

Deux garde-fous :

* la même politique de sécurité que les outils (`security.filesystem_roots`) ;
* un masquage d'affichage des secrets (`redact`) — appliqué UNIQUEMENT au texte
  envoyé aux écrans, jamais au fichier ni aux données utilisées par le moteur.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

MAX_BYTES = 2_000_000
MAX_TREE_ENTRIES = 400
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
             "build", ".next", ".cache", ".DS_Store"}

LANGUAGES = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".json": "json",
    ".md": "markdown", ".html": "html", ".css": "css", ".sh": "shell", ".yml": "yaml",
    ".yaml": "yaml", ".php": "php", ".go": "go", ".rs": "rust", ".sql": "sql",
    ".toml": "toml", ".ini": "ini", ".env": "ini",
}

# Masquage d'affichage. Chaque motif capture le NOM du champ (groupe 1) et
# remplace la valeur. On préfère rater un masquage plutôt qu'abîmer du code :
# ces motifs visent des affectations de secret, pas du texte quelconque.
_SECRET_KEY = (r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|apikey|access[_-]?key|"
               r"private[_-]?key|client[_-]?secret|auth|bearer|credential|cookie|session[_-]?id)")
_PATTERNS = [
    # clé: "valeur" / clé = 'valeur' / clé=valeur  (JSON, YAML, .env, code).
    # Le nom peut être préfixé (DB_PASSWORD, bot_token) et cité ("api_key": …),
    # d'où l'absence de \b et la tolérance au guillemet fermant.
    re.compile(rf"(?i)([A-Za-z0-9_.-]*{_SECRET_KEY})([\"']?\s*[:=]\s*)([\"']?)"
               rf"([^\s\"',;}}\)]{{4,}})(\3)"),
    # En-tête HTTP Authorization: Bearer xxx
    re.compile(r"(?i)\b(authorization)(\s*:\s*)(bearer\s+)?([A-Za-z0-9._\-]{8,})"),
]
_STANDALONE = [
    # Jetons reconnaissables même sans nom de champ.
    re.compile(r"\b(sk-[A-Za-z0-9]{16,})"),                       # OpenAI
    re.compile(r"\b(ghp_[A-Za-z0-9]{20,})"),                      # GitHub
    re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})"),              # Slack
    re.compile(r"\b(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"),  # JWT
    re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)"),
]
MASK = "••••masqué••••"


def redact(text: str) -> str:
    """Masque les secrets POUR L'AFFICHAGE. N'altère aucune donnée réelle."""
    if not text:
        return text
    out = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{MASK}{m.group(5)}", text)
    out = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{MASK}", out)
    for rx in _STANDALONE:
        out = rx.sub(MASK, out)
    return out


def _roots(core) -> list[Path]:
    raw = core.settings.get("security", "filesystem_roots", ["~"]) or ["~"]
    out: list[Path] = []
    for r in raw:
        try:
            out.append(Path(str(r)).expanduser().resolve())
        except Exception:
            continue
    return out or [Path.home()]


def resolve(core, raw: str) -> tuple[Path | None, str]:
    """Chemin absolu réel, ou l'erreur exacte à afficher sur l'écran."""
    if not raw:
        return None, "Chemin manquant."
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        return None, f"Chemin relatif « {raw} » : les écrans n'affichent que des chemins absolus."
    try:
        resolved = p.resolve()
    except Exception as exc:
        return None, f"Chemin invalide : {exc}"
    for root in _roots(core):
        if resolved == root or root in resolved.parents:
            return resolved, ""
    return None, f"Accès refusé : {resolved} est hors des dossiers autorisés (Settings → Security)."


def project_root(path: Path) -> Path:
    """Racine réelle du projet : dépôt git, sinon marqueur de projet, sinon dossier."""
    here = path if path.is_dir() else path.parent
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
        if any((candidate / m).exists() for m in ("package.json", "pyproject.toml",
                                                  "requirements.txt", "Cargo.toml", "go.mod")):
            return candidate
    return here


def read_file(core, raw: str) -> dict[str, Any]:
    path, err = resolve(core, raw)
    if err:
        return {"ok": False, "error": err, "path": raw}
    if not path.is_file():
        return {"ok": False, "error": f"Fichier introuvable : {path}", "path": str(path)}
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"ok": False, "error": f"Lecture impossible : {exc}", "path": str(path)}
    if size > MAX_BYTES:
        return {"ok": False, "path": str(path),
                "error": f"Fichier trop volumineux pour l'écran ({size // 1024} Ko)."}
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "path": str(path), "error": "Fichier binaire : pas d'affichage code."}
    except Exception as exc:
        return {"ok": False, "path": str(path), "error": f"Lecture impossible : {exc}"}
    root = project_root(path)
    return {"ok": True, "path": str(path), "name": path.name,
            "relative": str(path.relative_to(root)) if root in path.parents else path.name,
            "project": str(root), "project_name": root.name,
            "language": LANGUAGES.get(path.suffix.lower(), "text"),
            "lines": text.count("\n") + 1, "size": size,
            "content": redact(text)}


def tree(core, raw: str) -> dict[str, Any]:
    """Arborescence RÉELLE du projet contenant ce chemin (deux niveaux)."""
    path, err = resolve(core, raw)
    if err:
        return {"ok": False, "error": err}
    root = project_root(path)
    entries: list[dict[str, Any]] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > 2 or len(entries) >= MAX_TREE_ENTRIES:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda c: (c.is_file(), c.name.lower()))
        except OSError:
            return
        for child in children:
            if len(entries) >= MAX_TREE_ENTRIES:
                return
            if child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            entries.append({"path": str(child), "name": child.name, "depth": depth,
                            "dir": child.is_dir()})
            if child.is_dir():
                walk(child, depth + 1)

    walk(root, 0)
    return {"ok": True, "project": str(root), "project_name": root.name, "entries": entries}


def git_diff(core, raw: str) -> dict[str, Any]:
    """`git diff` RÉEL du fichier. Aucun diff n'est reconstruit côté écran."""
    path, err = resolve(core, raw)
    if err:
        return {"ok": False, "error": err}
    root = project_root(path)
    if not (root / ".git").exists():
        return {"ok": True, "repo": "", "diff": "", "detail": "Ce projet n'est pas un dépôt git."}
    try:
        proc = subprocess.run(["git", "diff", "--", str(path)], cwd=str(root),
                              capture_output=True, text=True, timeout=20)
    except Exception as exc:
        return {"ok": False, "error": f"git diff : {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "git diff a échoué.").strip()[:400]}
    diff = proc.stdout
    changed = sorted({int(m.group(1)) for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)", diff, re.M)})
    return {"ok": True, "repo": str(root), "diff": redact(diff)[:200_000],
            "hunks": changed, "detail": "" if diff else "Aucune modification non validée."}
