"""ProjectResolver — découverte et résolution automatique des projets réels.

Le resolver scanne les racines autorisées (Settings → Security → fichiers
autorisés) et catalogue les projets selon des marqueurs concrets :
`.git`, `package.json`, `pyproject.toml`, `composer.json`, etc. Il ne devine
jamais : quand plusieurs projets correspondent à la demande, il liste les
candidats et demande une clarification.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFESTS = {
    "package.json": ("node", "package"),
    "pyproject.toml": ("python", "pyproject"),
    "requirements.txt": ("python", "requirements"),
    "composer.json": ("php", "composer"),
    "Gemfile": ("ruby", "gemfile"),
    "go.mod": ("go", "go"),
    "Cargo.toml": ("rust", "cargo"),
    "pom.xml": ("java", "maven"),
    ".git": ("git", "repo"),
}
STRONG_MARKERS = ("package.json", "pyproject.toml", "requirements.txt", "composer.json",
                  "go.mod", "Cargo.toml", "pom.xml", "Gemfile", ".git")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold().strip()


def _json_read(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


@dataclass
class Project:
    id: str
    name: str
    display_name: str
    path: str
    stack: str = ""
    detail: str = ""
    git: bool = False
    branch: str = ""
    has_readme: bool = False
    readme_excerpt: str = ""
    scripts: dict[str, str] = field(default_factory=dict)
    test_commands: list[str] = field(default_factory=list)
    deps_preview: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    description: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "display_name": self.display_name,
            "path": self.path, "stack": self.stack, "detail": self.detail,
            "git": self.git, "branch": self.branch, "has_readme": self.has_readme,
            "scripts": self.scripts, "test_commands": self.test_commands,
            "aliases": self.aliases,
        }


class ProjectResolver:
    def __init__(self, core: Any) -> None:
        self.core = core
        self._projects: dict[str, Project] = {}
        self._loaded = False
        self._lock = __import__("threading").RLock()
        self.current: Project | None = None

    # -- racines autorisées ------------------------------------------------
    def roots(self) -> list[Path]:
        raw = self.core.settings.get("security", "filesystem_roots", ["~"]) or ["~"]
        extra = self.core.settings.get("general", "projects_roots", []) or []
        out: list[Path] = []
        for r in list(raw) + list(extra):
            try:
                out.append(Path(str(r)).expanduser().resolve())
            except Exception:
                continue
        return out or [Path.home()]

    # -- scan --------------------------------------------------------------
    def scan(self, refresh: bool = False) -> dict[str, Project]:
        with self._lock:
            if self._loaded and not refresh and self._projects:
                return dict(self._projects)
            found: dict[str, Project] = {}
            seen_paths: set[str] = set()
            ignored = {"node_modules", ".git", "venv", ".venv", "__pycache__", ".tox",
                       "dist", "build", "core.88", "dev", "demo", "Library", "Applications"}
            for root in self.roots():
                root = Path(root)
                if not root.is_dir():
                    continue
                candidates: list[Path] = [root]
                try:
                    children = [p for p in root.iterdir() if p.is_dir()]
                except Exception:
                    children = []
                candidates += children
                for folder in children:
                    try:
                        candidates += [p for p in folder.iterdir() if p.is_dir()]
                    except Exception:
                        pass
                for folder in candidates:
                    if folder.name.startswith("."):
                        continue
                    resolved = folder.resolve()
                    key = str(resolved)
                    if key in seen_paths or folder.name in ignored:
                        continue
                    if not (folder / ".git").exists() and not self._has_manifest(folder):
                        continue
                    seen_paths.add(key)
                    proj = self._inspect(folder)
                    if proj:
                        found[proj.id] = proj
            self._projects = found
            self._loaded = True
            try:
                self.core.events.emit("project.scanned", {
                    "count": len(found), "projects": [p.id for p in found.values()],
                }, cache=False)
            except Exception:
                pass
            return dict(found)

    def _has_manifest(self, folder: Path) -> bool:
        for marker in STRONG_MARKERS:
            if (folder / marker).exists():
                return True
        try:
            for entry in folder.iterdir():
                if entry.is_file() and "readme" in entry.name.casefold():
                    return True
        except Exception:
            pass
        return False

    def _inspect(self, folder: Path) -> Project | None:
        name = folder.name
        pid = _norm(name).replace(" ", "-") or "projet"
        if pid in self._projects:
            pid = f"{pid}-{len(self._projects)}"
        stack_found: list[str] = []
        scripts: dict[str, str] = {}
        deps: list[str] = []
        aliases: list[str] = []
        desc = ""
        readme = ""
        manifest = _json_read(folder / "package.json")
        if manifest:
            stack_found.append("node")
            pkg = manifest.get("name") or ""
            if pkg:
                aliases.append(str(pkg))
            scripts = {k: str(v) for k, v in (manifest.get("scripts") or {}).items()}
            deps = list((manifest.get("dependencies") or {}).keys())[:12]
            desc = str(manifest.get("description") or "")
            if "discord.js" in str(manifest.get("dependencies") or {}):
                stack_found.append("discord-bot")
        if (folder / "composer.json").exists():
            composer = _json_read(folder / "composer.json")
            if composer:
                stack_found.append("php")
                if composer.get("name"):
                    aliases.append(str(composer["name"]))
                deps += list((composer.get("require") or {}).keys())[:12]
        for m, (stack, _) in MANIFESTS.items():
            if (folder / m).exists() and stack not in stack_found:
                stack_found.append(stack)
        readme_file = next((p for p in folder.iterdir() if p.is_file()
                            and p.name.casefold().startswith("readme")), None) if folder.is_dir() else None
        if readme_file is None:
            try:
                readme_file = next((p for p in folder.iterdir() if p.is_file()
                                    and "readme" in p.name.casefold()), None)
            except Exception:
                readme_file = None
        if readme_file:
            try:
                readme = readme_file.read_text(encoding="utf-8", errors="replace")[:400].strip()
            except Exception:
                readme = ""
        git = (folder / ".git").exists()
        branch = self._branch(folder) if git else ""
        test_commands = self._guess_tests(folder, scripts)
        stack = ", ".join(dict.fromkeys(stack_found)) or "inconnu"
        return Project(
            id=pid, name=name, display_name=name, path=str(folder),
            stack=stack, detail=desc, git=git, branch=branch,
            has_readme=bool(readme), readme_excerpt=readme,
            scripts=scripts, test_commands=test_commands,
            deps_preview=deps, aliases=aliases, description=desc,
        )

    def _branch(self, folder: Path) -> str:
        try:
            import subprocess
            proc = subprocess.run(["git", "-C", str(folder), "rev-parse", "--abbrev-ref", "HEAD"],
                                  capture_output=True, text=True, timeout=10)
            return (proc.stdout or "").strip() or "détaché"
        except Exception:
            return ""

    def _guess_tests(self, folder: Path, scripts: dict[str, str]) -> list[str]:
        cmds: list[str] = []
        if not scripts:
            for candidate in ("pytest", "python -m unittest", "npm test", "phpunit"):
                if candidate.split()[0] == "npm":
                    if (folder / "package.json").exists():
                        cmds.append(candidate)
                elif candidate == "phpunit":
                    if (folder / "phpunit.xml").exists():
                        cmds.append(candidate)
                else:
                    if (folder / "pyproject.toml").exists() or (folder / "requirements.txt").exists():
                        cmds.append(candidate)
            return cmds
        for key in ("test", "test:unit", "lint", "check"):
            if key in scripts:
                cmds.append(f"npm run {key}")
        if not cmds and "dev" in scripts:
            cmds.append("node " + scripts["dev"].split()[-1])
        return cmds

    # -- résolution --------------------------------------------------------
    def all(self, refresh: bool = False) -> list[Project]:
        return sorted(self.scan(refresh=refresh).values(), key=lambda p: p.display_name.casefold())

    def get(self, project_id: str) -> Project | None:
        return self.scan().get(project_id)

    def resolve(self, query: str, *, refresh: bool = False) -> list[Project]:
        """Retourne les candidats triés par pertinence (vide si aucun)."""
        if not query:
            return []
        q = _norm(query)
        projects = self.all(refresh=refresh)
        scored: list[tuple[int, Project]] = []
        for p in projects:
            score = self._score(q, p)
            if score > 0:
                scored.append((score, p))
        scored.sort(key=lambda t: (-t[0], t[1].display_name.casefold()))
        best = {s for s, _ in scored} and scored
        if not best:
            return []
        top = scored[0][0]
        return [p for s, p in scored if s >= max(top - 8, 6)]

    def _score(self, q: str, p: Project) -> int:
        name = _norm(p.name)
        display = _norm(p.display_name)
        aliases = [_norm(a) for a in p.aliases]
        stack = _norm(p.stack)
        # Correspondance exacte sur le nom, l'affichage ou un alias.
        if q == name or q == display or any(q == a for a in aliases):
            return 100
        # Sous-chaîne : « brainrotfortnite » → « brainrot ».
        for label in (name, display, *aliases):
            if label and label in q:
                return 60
        if q in name or q in display or any(q in a for a in aliases):
            return 60
        tokens = [t for t in re.split(r"[^a-z0-9]+", q) if t and t not in {
            "le", "la", "les", "de", "du", "des", "mon", "ma", "mes", "ton", "ta", "tes",
            "son", "sa", "ses", "un", "une", "au", "aux", "et", "ou", "je", "tu", "il",
            "projet", "apprends", "corrige", "grid", "velko", "jarvis", "assistant", "sur", "pour", "est"}]
        if not tokens:
            return 0
        hit = 0
        for token in tokens:
            if token in name or token in display or any(token in a for a in aliases):
                hit += 14
            elif token in stack:
                hit += 3
        return hit

    # -- bot Discord : reconnaissance par preuves, puis mémoire -------------
    DISCORD_QUERY = re.compile(
        r"\b(bot\s*discord|discord\s*bot|mon\s+bot|ton\s+bot|le\s+bot|du\s+bot|"
        r"ce\s+bot|notre\s+bot|drakobot)\b", re.I)

    @classmethod
    def looks_like_discord_bot(cls, query: str) -> bool:
        """La demande désigne-t-elle « le bot Discord » sans nommer de dossier ?"""
        return bool(cls.DISCORD_QUERY.search(query or ""))

    def remembered_bot(self) -> Project | None:
        """Projet bot Discord déjà retenu, s'il existe toujours sur disque."""
        path = str(self.core.settings.get("general", "discord_bot_project", "") or "").strip()
        if not path or not Path(path).is_dir():
            return None
        for proj in self.all():
            if Path(proj.path).resolve() == Path(path).resolve():
                return proj
        # Le projet n'est plus dans le scan (racine changée) : on l'inspecte seul.
        return self._inspect(Path(path))

    def remember_bot(self, project: Project) -> None:
        """Retient l'association « mon bot Discord » → ce projet réel."""
        try:
            self.core.settings.update("general", {"discord_bot_project": project.path})
        except Exception:
            pass
        try:
            self.core.memory.add(
                content=(f"« mon bot Discord » désigne le projet « {project.display_name} » "
                         f"situé dans {project.path}."),
                scope="project", importance=3, source="project_resolver",
                project=project.path, tags=["discord", "bot", "projet"], pinned=True)
        except Exception:
            pass
        try:
            self.core.events.emit("project.bot_remembered", project.summary())
        except Exception:
            pass

    def discord_candidates(self, *, refresh: bool = False, minimum: int = 60
                           ) -> list[DiscordEvidence]:
        """Projets qui SONT réellement des bots Discord, triés par preuves."""
        out: list[DiscordEvidence] = []
        for proj in self.all(refresh=refresh):
            folder = Path(proj.path)
            if not folder.is_dir():
                continue
            ev = inspect_discord_bot(folder, proj)
            if ev.score >= minimum and ev.is_bot():
                out.append(ev)
        out.sort(key=lambda e: (-e.score, e.project.display_name.casefold()))
        return out

    def resolve_discord_bot(self, *, refresh: bool = False
                            ) -> tuple[Project | None, list[DiscordEvidence]]:
        """(projet, candidats).

        Un projet n'est renvoyé que si le choix est réellement évident :
        soit il a déjà été retenu, soit un seul candidat existe, soit il
        devance nettement le suivant. Sinon on rend les candidats pour que
        l'utilisateur tranche — on ne choisit jamais en silence.
        """
        remembered = self.remembered_bot()
        candidates = self.discord_candidates(refresh=refresh)
        if remembered:
            for ev in candidates:
                if Path(ev.project.path) == Path(remembered.path):
                    return remembered, candidates
            return remembered, candidates
        if not candidates:
            return None, []
        if len(candidates) == 1:
            return candidates[0].project, candidates
        if candidates[0].score - candidates[1].score >= 25:
            return candidates[0].project, candidates
        return None, candidates

    def select(self, project: Project) -> None:
        self.current = project
        try:
            self.core.settings.update("general", {"last_project": project.path})
        except Exception:
            pass
        try:
            self.core.events.emit("project.selected", project.summary())
        except Exception:
            pass

    # -- contexte ----------------------------------------------------------
    def context(self, project: Project) -> str:
        lines = [project.display_name, "=" * len(project.display_name), f"Chemin : {project.path}"]
        if project.git:
            lines.append(f"Git : dépôt ({project.branch})")
        else:
            lines.append("Git : pas de dépôt")
        lines.append(f"Stack : {project.stack or 'à déterminer'}")
        if project.description:
            lines.append(f"Description : {project.description}")
        if project.deps_preview:
            lines.append("Dépendances : " + ", ".join(project.deps_preview[:10]))
        if project.scripts:
            lines.append("Scripts disponibles : " + ", ".join(
                f"{k} → {v}" for k, v in list(project.scripts.items())[:12]))
        if project.test_commands:
            lines.append("Tests / lint détectés : " + " ; ".join(project.test_commands))
        if project.readme_excerpt:
            lines.append("\nREADME :\n" + project.readme_excerpt)
        return "\n".join(lines)

# ---------------------------------------------------------------------------
# Reconnaissance d'un bot Discord par PREUVES réelles
#
# « mon bot Discord » ne nomme aucun dossier : on ne devine pas, on lit le
# disque. Chaque point de score correspond à un fait vérifié dans le projet.
# ---------------------------------------------------------------------------
DISCORD_LIBS = ("discord.js", "discord.py", "py-cord", "pycord", "nextcord",
                "disnake", "eris", "discordeno", "@discordjs/rest", "@discordjs/voice",
                "discord-api-types", "discord_py", "hikari")
ENTRY_SIGNS = (r"client\.login\s*\(", r"bot\.run\s*\(", r"\bClient\s*\(", r"\bBot\s*\(",
               r"commands\.Bot\s*\(", r"new\s+Discord\.Client", r"GatewayIntentBits",
               r"discord\.Intents")
TOKEN_SIGNS = ("DISCORD_TOKEN", "BOT_TOKEN", "DISCORD_BOT_TOKEN", "CLIENT_TOKEN",
               "DISCORD_CLIENT_ID")
ENTRY_FILES = ("index.js", "main.js", "bot.js", "app.js", "src/index.js", "src/bot.js",
               "bot.py", "main.py", "src/main.py", "app.py", "index.mjs", "src/index.ts")


def _read_head(path: Path, limit: int = 60_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


@dataclass
class DiscordEvidence:
    """Ce qui a été réellement observé dans le projet, et ce que ça vaut."""
    project: "Project"
    score: int = 0
    framework: str = ""
    entrypoint: str = ""
    start_command: str = ""
    facts: list[str] = field(default_factory=list)
    structure: int = 0            # dossiers commands/events/interactions trouvés

    def is_bot(self) -> bool:
        """Un projet qui PARLE à Discord n'est pas un bot Discord.

        On exige la bibliothèque ET une réalité de bot : un point d'entrée qui
        se connecte, ou l'ossature commandes/événements. Sans cela, un simple
        `discord.py` dans requirements.txt suffisait à faire passer n'importe
        quel projet pour un bot.
        """
        return bool(self.framework) and (bool(self.entrypoint) or self.structure >= 2)

    def summary(self) -> dict[str, Any]:
        out = self.project.summary()
        out.update({"score": self.score, "framework": self.framework,
                    "entrypoint": self.entrypoint, "start_command": self.start_command,
                    "evidence": list(self.facts)})
        return out


def inspect_discord_bot(folder: Path, project: "Project") -> DiscordEvidence:
    """Preuves concrètes qu'un dossier est un bot Discord. Aucune supposition."""
    ev = DiscordEvidence(project=project)

    def add(points: int, fact: str) -> None:
        ev.score += points
        ev.facts.append(fact)

    # 1. Dépendance déclarée — la preuve la plus forte.
    manifest = _json_read(folder / "package.json") or {}
    declared = {**(manifest.get("dependencies") or {}), **(manifest.get("devDependencies") or {})}
    for lib in DISCORD_LIBS:
        if lib in declared:
            ev.framework = ev.framework or lib
            add(40, f"dépendance {lib}@{declared[lib]} dans package.json")
            break
    if not ev.framework:
        for req in ("requirements.txt", "pyproject.toml"):
            text = _read_head(folder / req, 20_000).casefold()
            for lib in DISCORD_LIBS:
                if lib in text:
                    ev.framework = ev.framework or lib
                    add(40, f"dépendance {lib} dans {req}")
                    break
            if ev.framework:
                break

    # 2. Structure typique d'un bot.
    for sub in ("commands", "events", "interactions", "slashCommands", "cogs", "addons"):
        if (folder / sub).is_dir():
            ev.structure += 1
            add(8, f"dossier {sub}/ présent")

    # 3. Point d'entrée : on lit le VRAI fichier.
    main = str(manifest.get("main") or "")
    for candidate in ([main] if main else []) + list(ENTRY_FILES):
        if not candidate:
            continue
        path = folder / candidate
        if not path.is_file():
            continue
        body = _read_head(path)
        if any(re.search(sign, body) for sign in ENTRY_SIGNS):
            ev.entrypoint = candidate
            add(25, f"point d'entrée {candidate} (connexion Discord détectée)")
            if not ev.framework:
                ev.framework = "discord.py" if candidate.endswith(".py") else "discord.js"
            break

    # 4. Jeton attendu par la configuration (jamais lu, seulement constaté).
    for conf in (".env", ".env.example", "config.json", "config/config.json",
                 "config.js", "config/bot.json", "settings.json"):
        text = _read_head(folder / conf, 20_000)
        if text and any(sign in text for sign in TOKEN_SIGNS):
            add(15, f"jeton Discord attendu dans {conf}")
            break

    # 5. Commande de démarrage réelle.
    scripts = {k: str(v) for k, v in (manifest.get("scripts") or {}).items()}
    for key in ("start", "dev", "bot", "serve"):
        if key in scripts:
            ev.start_command = f"npm run {key}"
            add(6, f"script npm « {key} » : {scripts[key][:80]}")
            break
    if not ev.start_command and ev.entrypoint:
        ev.start_command = ("python3 " if ev.entrypoint.endswith(".py") else "node ") + ev.entrypoint

    # 6. Indices faibles : nom, dépôt, emplacement.
    label = _norm(folder.name)
    if "bot" in label or "discord" in label:
        add(10, f"nom du dossier « {folder.name} »")
    if (folder / ".git").exists():
        add(8, "dépôt git")
    # Un dossier de Téléchargements est le plus souvent une archive, pas la
    # copie de travail. On le dit au lieu de l'exclure en silence.
    if "downloads" in _norm(str(folder)) or "telechargements" in _norm(str(folder)):
        add(-12, "situé dans les téléchargements (probable copie)")
    if (folder / "node_modules").is_dir():
        add(6, "dépendances installées (node_modules)")

    # 7. Copie de travail vs archive : une archive ne produit ni données, ni
    # journaux, ni déploiement, et n'a pas bougé depuis des mois.
    for artefact in ("data", "logs", "dist", "deploy.sh", "docker-compose.yml"):
        if (folder / artefact).exists():
            add(9, f"trace d'exploitation réelle : {artefact}")
            break
    try:
        import time as _time
        days = (_time.time() - folder.stat().st_mtime) / 86400
        if days <= 30:
            add(14, f"modifié il y a {int(days)} jour(s)")
        elif days >= 120:
            add(-10, f"inchangé depuis {int(days)} jours (probable archive)")
    except Exception:
        pass
    return ev
