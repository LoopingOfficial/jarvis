"""Reconnaissance d'un bot Discord et audit de projet, sur de vrais dossiers.

Ces tests construisent de vrais projets sur disque : un bot Discord Node, un
bot Python, une archive et un projet qui parle simplement à Discord sans en
être un. Ils vérifient que VELKO trouve le bon projet SANS qu'on lui donne de
chemin, et que l'audit repose sur des lectures réelles.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from jarvis.projects import ProjectResolver, Project, inspect_discord_bot
from jarvis.project_audit import ProjectAuditPipeline


def _bot_node(root: Path, *, name: str = "drakobot") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "main": "index.js",
        "scripts": {"start": "node index.js", "test": "node --test"},
        "dependencies": {"discord.js": "^14.23.2"},
    }))
    (root / "index.js").write_text(
        "const { Client, GatewayIntentBits } = require('discord.js');\n"
        "const client = new Client({ intents: [GatewayIntentBits.Guilds] });\n"
        "client.login(process.env.DISCORD_TOKEN);\n")
    (root / ".env.example").write_text("DISCORD_TOKEN=\n")
    for sub in ("commands", "events"):
        (root / sub).mkdir(exist_ok=True)
        (root / sub / "ping.js").write_text("module.exports = {};\n")
    return root


class DiscordEvidenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def _evidence(self, folder: Path):
        proj = Project(id=folder.name, name=folder.name,
                       display_name=folder.name, path=str(folder))
        return inspect_discord_bot(folder, proj)

    def test_bot_node_reconnu(self):
        ev = self._evidence(_bot_node(self.home / "MonBot"))
        self.assertTrue(ev.is_bot())
        self.assertEqual(ev.framework, "discord.js")
        self.assertEqual(ev.entrypoint, "index.js")
        self.assertEqual(ev.start_command, "npm run start")
        self.assertTrue(any("discord.js" in f for f in ev.facts))

    def test_bot_python_reconnu(self):
        folder = self.home / "PyBot"
        folder.mkdir()
        (folder / "requirements.txt").write_text("discord.py==2.3.2\n")
        (folder / "bot.py").write_text(
            "import discord\nbot = discord.Client()\nbot.run(TOKEN)\n")
        (folder / "cogs").mkdir()
        ev = self._evidence(folder)
        self.assertTrue(ev.is_bot())
        self.assertEqual(ev.entrypoint, "bot.py")

    def test_projet_qui_parle_a_discord_nest_pas_un_bot(self):
        """Une dépendance seule ne fait pas un bot : c'est le piège à éviter."""
        folder = self.home / "AssistantAvecConnecteurDiscord"
        folder.mkdir()
        (folder / "requirements.txt").write_text("discord.py==2.3.2\nflask\n")
        ev = self._evidence(folder)
        self.assertFalse(ev.is_bot())

    def test_copie_de_telechargement_moins_bien_notee(self):
        actif = self._evidence(_bot_node(self.home / "BotActif"))
        copie = self._evidence(_bot_node(self.home / "Downloads" / "BotActif"))
        self.assertGreater(actif.score, copie.score)
        self.assertTrue(any("téléchargements" in f for f in copie.facts))


class ResolutionTest(unittest.TestCase):
    """Le resolver doit trouver le bot sans qu'on lui donne de chemin."""

    class _Settings:
        def __init__(self, root): self._root = root; self.saved = {}
        def get(self, section, key, default=None):
            if key in ("filesystem_roots", "projects_roots"):
                return [self._root] if key == "filesystem_roots" else []
            return self.saved.get(key, default if default is not None else "")
        def update(self, section, values): self.saved.update(values)

    class _Events:
        def emit(self, *a, **k): pass

    class _Core:
        def __init__(self, settings, events):
            self.settings = settings; self.events = events

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name).resolve()
        self.settings = self._Settings(str(self.home))
        self.core = self._Core(self.settings, self._Events())
        self.resolver = ProjectResolver(self.core)

    def tearDown(self):
        self._tmp.cleanup()

    def test_un_seul_bot_est_choisi_automatiquement(self):
        _bot_node(self.home / "MonBot")
        proj, candidates = self.resolver.resolve_discord_bot(refresh=True)
        self.assertIsNotNone(proj)
        self.assertEqual(Path(proj.path).name, "MonBot")
        self.assertEqual(len(candidates), 1)

    def test_deux_bots_equivalents_font_poser_la_question(self):
        _bot_node(self.home / "BotA")
        _bot_node(self.home / "BotB")
        proj, candidates = self.resolver.resolve_discord_bot(refresh=True)
        self.assertIsNone(proj)                  # aucun choix silencieux
        self.assertEqual(len(candidates), 2)

    def test_le_projet_retenu_est_reutilise(self):
        _bot_node(self.home / "BotA")
        _bot_node(self.home / "BotB")
        choisi = self.resolver.scan(refresh=True)
        cible = next(p for p in choisi.values() if p.name == "BotB")
        self.resolver.remember_bot(cible)
        proj, _ = self.resolver.resolve_discord_bot(refresh=True)
        self.assertIsNotNone(proj)               # plus d'ambiguïté
        self.assertEqual(Path(proj.path).name, "BotB")

    def test_formulations_reconnues(self):
        for q in ("regarde mon bot Discord", "corrige le bot", "analyse du bot", "DrakoBot"):
            self.assertTrue(ProjectResolver.looks_like_discord_bot(q), q)
        for q in ("analyse le projet velko", "quel est l'état du mac"):
            self.assertFalse(ProjectResolver.looks_like_discord_bot(q), q)


class AuditTest(unittest.TestCase):
    class _Events:
        def __init__(self): self.sent = []
        def emit(self, kind, payload, **k): self.sent.append((kind, payload))

    class _Core:
        def __init__(self, events): self.events = events

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _bot_node(Path(self._tmp.name).resolve() / "MonBot")
        self.events = self._Events()
        self.core = self._Core(self.events)
        self.project = Project(id="monbot", name="MonBot", display_name="MonBot",
                               path=str(self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def test_audit_execute_toutes_les_etapes(self):
        report = ProjectAuditPipeline(self.core).run(self.project)
        noms = {s["step"] for s in report["steps"]}
        for attendu in ("git", "manifeste", "arborescence", "commands", "events",
                        "point_d_entree", "scripts", "processus", "discord"):
            self.assertIn(attendu, noms, attendu)

    def test_les_constats_viennent_du_disque(self):
        report = ProjectAuditPipeline(self.core).run(self.project)
        self.assertIn("index.js", report["summary"])
        self.assertIn("n'est pas un dépôt git", report["summary"])
        self.assertIn("aucune instance locale", report["summary"])

    def test_le_depot_git_reel_est_lu(self):
        run = lambda *a: subprocess.run(a, cwd=str(self.root), capture_output=True, check=True)
        run("git", "init", "-q", "-b", "principale")
        run("git", "add", "-A")
        run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        (self.root / "index.js").write_text("// modifié\n")
        report = ProjectAuditPipeline(self.core).run(self.project)
        git = next(s for s in report["steps"] if s["step"] == "git")
        self.assertEqual(git["data"]["branch"], "principale")
        self.assertIn("index.js", git["data"]["status"])

    def test_les_ecrans_recoivent_des_evenements_reels(self):
        ProjectAuditPipeline(self.core).run(self.project, task_id="t1")
        kinds = [k for k, _ in self.events.sent]
        self.assertIn("file.opened", kinds)      # écran gauche
        paths = [p.get("path") for k, p in self.events.sent if k == "file.opened"]
        self.assertTrue(any(str(self.root / "package.json") == p for p in paths))


if __name__ == "__main__":
    unittest.main()
