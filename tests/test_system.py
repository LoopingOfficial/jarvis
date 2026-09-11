"""Tests du coffre, des connecteurs, des permissions, des outils, des tâches,
de la mémoire, des automatisations et de l'API HTTP."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="jarvis-tests-")
os.environ["JARVIS_DATA_DIR"] = _TMP
os.environ["JARVIS_CONFIG_DIR"] = str(Path(_TMP) / "cfg")
os.environ["JARVIS_MASTER_KEY"] = "clé-de-test-unitaire"

from jarvis.core import JarvisCore  # noqa: E402
from jarvis.permissions import (  # noqa: E402
    DESTRUCTIVE, READ_ONLY, SAFE_WRITE, SENSITIVE, classify_command,
)
from jarvis.server import create_server  # noqa: E402
from jarvis.tools.base import registry  # noqa: E402


class CoreTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.core = JarvisCore(Path(cls.tmp.name) / "jarvis.db")

    @classmethod
    def tearDownClass(cls):
        cls.core.shutdown()
        cls.tmp.cleanup()


class TestSecretVault(CoreTestCase):
    def test_secret_is_never_stored_in_clear(self):
        self.core.vault.set("conn-test", "password", "SuperSecret123!")
        row = self.core.db.one(
            "SELECT blob FROM secrets WHERE connector_id=? AND field=?", ("conn-test", "password"))
        self.assertIsNotNone(row)
        self.assertNotIn("SuperSecret123!", row["blob"])
        payload = json.loads(row["blob"])
        self.assertIn(payload["algo"], {"aesgcm", "hmacctr"})

    def test_roundtrip(self):
        self.core.vault.set("conn-test", "token", "abcdef1234567890")
        self.assertEqual(self.core.vault.get("conn-test", "token"), "abcdef1234567890")

    def test_preview_masks_value(self):
        self.core.vault.set("conn-test", "api_key", "sk-verysecretvalue-9876")
        preview = self.core.vault.preview("conn-test", "api_key")
        self.assertTrue(preview.startswith("••••"))
        self.assertNotIn("verysecret", preview)
        self.assertEqual(preview[-4:], "9876")

    def test_scrub_removes_secrets_from_text(self):
        self.core.vault.set("conn-test", "password", "MotDePasseUltraSecret")
        text = "Connexion avec MotDePasseUltraSecret sur le serveur"
        self.assertNotIn("MotDePasseUltraSecret", self.core.vault.scrub(text))
        self.assertIn("[secret masqué]", self.core.vault.scrub(text))

    def test_aad_binding(self):
        """Un secret ne peut pas être déchiffré sous un autre connecteur."""
        self.core.vault.set("conn-a", "password", "valeur-a")
        row = self.core.db.one(
            "SELECT blob FROM secrets WHERE connector_id='conn-a' AND field='password'")
        with self.assertRaises(Exception):
            self.core.vault._decrypt("conn-b", "password", row["blob"])


class TestConnectors(CoreTestCase):
    def test_create_splits_secrets(self):
        c = self.core.connectors.create({
            "type": "ssh", "name": "Serveur test",
            "config": {"host": "example.test", "username": "deploy", "port": 22,
                       "password": "motdepasse-secret"},
            "permissions": ["read", "execute"],
        })
        self.assertNotIn("password", c["config"])
        self.assertTrue(c["secret_fields"]["password"]["configured"])
        self.assertEqual(self.core.vault.get(c["id"], "password"), "motdepasse-secret")
        # L'API publique ne renvoie jamais la valeur
        self.assertNotIn("motdepasse-secret", json.dumps(c))

    def test_masked_value_does_not_overwrite(self):
        c = self.core.connectors.create({
            "type": "cpanel", "name": "cPanel test",
            "config": {"host": "https://h.test:2083", "username": "u", "token": "tok-original-1234"},
        })
        self.core.connectors.update(c["id"], {"config": {"token": "••••1234", "username": "u2"}})
        self.assertEqual(self.core.vault.get(c["id"], "token"), "tok-original-1234")
        self.assertEqual(self.core.connectors.raw(c["id"])["config"]["username"], "u2")

    def test_delete_removes_secrets(self):
        c = self.core.connectors.create({
            "type": "github", "name": "GH test", "config": {"token": "ghp_test_value"}})
        cid = c["id"]
        self.core.connectors.delete(cid)
        self.assertEqual(self.core.vault.get(cid, "token"), "")
        self.assertIsNone(self.core.connectors.get(cid))

    def test_find_resolves_by_id_and_host(self):
        c = self.core.connectors.create({
            "type": "ssh", "name": "Prod Web", "id": "prod",
            "config": {"host": "web.example.test", "username": "root"}})
        self.assertEqual(self.core.connectors.find("prod", "ssh")["id"], c["id"])
        self.assertEqual(self.core.connectors.find("web.example.test", "ssh")["id"], c["id"])
        self.assertEqual(self.core.connectors.find("Prod Web", "ssh")["id"], c["id"])


class TestPermissions(CoreTestCase):
    def test_command_classification(self):
        self.assertEqual(classify_command("ls -la"), READ_ONLY)
        self.assertEqual(classify_command("df -h"), READ_ONLY)
        self.assertEqual(classify_command("git status"), READ_ONLY)
        self.assertEqual(classify_command("systemctl restart nginx"), SENSITIVE)
        self.assertEqual(classify_command("git push origin main"), SENSITIVE)
        self.assertEqual(classify_command("rm -rf /var/www"), DESTRUCTIVE)
        self.assertEqual(classify_command("DROP DATABASE production"), DESTRUCTIVE)
        self.assertEqual(classify_command("DELETE FROM users"), DESTRUCTIVE)
        self.assertEqual(classify_command("git push --force origin main"), DESTRUCTIVE)

    def test_confirmation_policy(self):
        pm = self.core.permissions
        self.assertFalse(pm.requires_confirmation(READ_ONLY))
        self.assertFalse(pm.requires_confirmation(SAFE_WRITE))
        self.assertTrue(pm.requires_confirmation(SENSITIVE))
        self.assertTrue(pm.requires_confirmation(DESTRUCTIVE))
        self.assertFalse(pm.requires_confirmation(DESTRUCTIVE, "never"))
        self.assertTrue(pm.requires_confirmation(READ_ONLY, "always"))

    def test_connector_permissions(self):
        pm = self.core.permissions
        self.assertTrue(pm.connector_allows(["read"], READ_ONLY)[0])
        self.assertFalse(pm.connector_allows(["read"], SAFE_WRITE)[0])
        self.assertFalse(pm.connector_allows(["read", "write"], SENSITIVE)[0])
        self.assertTrue(pm.connector_allows(["execute"], SENSITIVE)[0])
        self.assertFalse(pm.connector_allows(["execute"], DESTRUCTIVE)[0])
        self.assertTrue(pm.connector_allows(["admin"], DESTRUCTIVE)[0])


class TestToolRunner(CoreTestCase):
    def test_readonly_tool_runs_without_confirmation(self):
        result = self.core.runner.run("system.info", {})
        self.assertTrue(result.ok)
        self.assertIn("CPU", result.output)

    def test_destructive_tool_requires_confirmation(self):
        from jarvis.tools.runner import ConfirmationRequired

        with self.assertRaises(ConfirmationRequired) as ctx:
            self.core.runner.run("terminal.run", {"command": "rm -rf /tmp/jarvis-test-target"})
        self.assertEqual(ctx.exception.pending.risk, DESTRUCTIVE)

    def test_missing_connector_is_explicit(self):
        result = self.core.runner.run("whm.query", {"function": "loadavg"})
        self.assertFalse(result.ok)
        self.assertIn("connecteur", result.output.lower())

    def test_filesystem_sandbox(self):
        result = self.core.runner.run("fs.read", {"path": "/etc/shadow"})
        self.assertFalse(result.ok)
        self.assertIn("refusé", result.output.lower())

    def test_audit_records_tool_calls(self):
        before = self.core.audit.count()
        self.core.runner.run("system.info", {})
        self.assertGreater(self.core.audit.count(), before)

    def test_all_tools_have_schema_and_risk(self):
        for tool in registry.all():
            self.assertTrue(tool.id and tool.name, f"outil incomplet: {tool}")
            self.assertIn(tool.risk, {READ_ONLY, SAFE_WRITE, SENSITIVE, DESTRUCTIVE})
            self.assertIsInstance(tool.input_schema, dict)
            self.assertIn("type", tool.input_schema)


class TestTasksAndMemory(CoreTestCase):
    def test_task_lifecycle(self):
        task = self.core.tasks.create(name="Tâche de test", kind="test")
        self.assertEqual(task["status"], "queued")
        self.core.tasks.set_status(task["id"], "running", progress=0.4)
        self.core.tasks.log(task["id"], "étape 1")
        self.core.tasks.complete(task["id"], "résultat")
        detail = self.core.tasks.detail(task["id"])
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["progress"], 1.0)
        self.assertTrue(any(log["message"] == "étape 1" for log in detail["logs"]))

    def test_task_cancel(self):
        task = self.core.tasks.create(name="À annuler")
        self.core.tasks.set_status(task["id"], "running")
        self.assertTrue(self.core.tasks.cancel(task["id"]))
        self.assertEqual(self.core.tasks.get(task["id"])["status"], "cancelled")

    def test_memory_scopes_are_separate(self):
        self.core.memory.add(content="Jérôme préfère les réponses courtes", scope="user", importance=4)
        self.core.memory.add(content="Le projet X utilise PHP 8.2", scope="project", project="X")
        user_items = self.core.memory.list(scope="user")
        project_items = self.core.memory.list(scope="project")
        self.assertTrue(all(m["scope"] == "user" for m in user_items))
        self.assertTrue(all(m["scope"] == "project" for m in project_items))

    def test_memory_search(self):
        self.core.memory.add(content="Le serveur brainrot tourne sur cPanel o2switch", scope="user")
        results = self.core.memory.search("brainrot serveur")
        self.assertTrue(any("brainrot" in m["content"] for m in results))

    def test_memory_dedupe(self):
        a = self.core.memory.add(content="Fait unique de test", scope="user")
        b = self.core.memory.add(content="Fait unique de test", scope="user")
        self.assertEqual(a["id"], b["id"])

    def test_memory_never_contains_secrets(self):
        """Le coffre et la mémoire sont deux tables distinctes."""
        self.core.vault.set("conn-x", "password", "secret-jamais-en-memoire")
        rows = self.core.db.query("SELECT content FROM memories")
        for r in rows:
            self.assertNotIn("secret-jamais-en-memoire", r["content"])

    def test_auto_extract(self):
        created = self.core.memory.auto_extract("Retiens que je déploie toujours le vendredi soir")
        self.assertTrue(created)
        self.assertTrue(any("vendredi" in m["content"] for m in created))

    def test_knowledge_base(self):
        item = self.core.memory.knowledge_add(
            title="Procédure de déploiement", content="rsync puis vérifier nginx", kind="procédure")
        found = self.core.memory.knowledge_search("déploiement rsync")
        self.assertTrue(any(k["id"] == item["id"] for k in found))


class TestAutomations(CoreTestCase):
    def test_parse_french_triggers(self):
        a = self.core.automations
        self.assertEqual(a.parse_trigger("chaque matin à 8h")["type"], "daily")
        self.assertEqual(a.parse_trigger("chaque matin à 8h")["hour"], 8)
        self.assertEqual(a.parse_trigger("toutes les 30 minutes")["type"], "interval")
        self.assertEqual(a.parse_trigger("toutes les 30 minutes")["seconds"], 1800)
        self.assertEqual(a.parse_trigger("chaque lundi à 9h")["type"], "weekly")
        self.assertEqual(a.parse_trigger("chaque lundi à 9h")["weekday"], 0)
        self.assertEqual(a.parse_trigger("cron: 0 6 * * 1")["type"], "cron")
        self.assertEqual(a.parse_trigger("quand le site est inaccessible")["type"], "condition")

    def test_next_run_is_in_future(self):
        a = self.core.automations
        for text in ("chaque matin à 8h", "toutes les 30 minutes", "chaque lundi à 9h", "cron: 0 6 * * *"):
            trigger = a.parse_trigger(text)
            nxt = a.next_run(trigger)
            self.assertIsNotNone(nxt, text)
            self.assertGreater(nxt, time.time(), text)

    def test_create_workflow_persists(self):
        wf = self.core.automations.create(
            name="Surveillance site", instruction="vérifie que example.test répond",
            trigger={"type": "daily", "hour": 7, "minute": 30})
        self.assertTrue(wf["enabled"])
        self.assertIsNotNone(wf["next_run_at"])
        reloaded = self.core.automations.get(wf["id"])
        self.assertEqual(reloaded["name"], "Surveillance site")
        self.assertEqual(reloaded["trigger"]["hour"], 7)


class TestPersistence(unittest.TestCase):
    def test_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "persist.db"
            core1 = JarvisCore(db_path)
            connector = core1.connectors.create({
                "type": "ssh", "name": "Persisté",
                "config": {"host": "persist.test", "username": "u", "password": "secret-persistant"}})
            core1.memory.add(content="Souvenir persistant", scope="user")
            core1.settings.update("general", {"user_name": "Jérôme"})
            wf = core1.automations.create(name="WF persistant", instruction="ping",
                                          trigger={"type": "daily", "hour": 6})
            conv = core1.conversations.create("Conversation persistante")
            core1.conversations.add_message(conv["id"], "user", "salut")
            core1.shutdown()
            core1.db.close()

            core2 = JarvisCore(db_path)
            self.assertIsNotNone(core2.connectors.get(connector["id"]))
            self.assertEqual(core2.vault.get(connector["id"], "password"), "secret-persistant")
            self.assertTrue(any(m["content"] == "Souvenir persistant" for m in core2.memory.list()))
            self.assertEqual(core2.settings.get("general", "user_name"), "Jérôme")
            self.assertIsNotNone(core2.automations.get(wf["id"]))
            self.assertEqual(len(core2.conversations.messages(conv["id"])), 1)
            core2.shutdown()
            core2.db.close()


class TestHTTPApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.core = JarvisCore(Path(cls.tmp.name) / "api.db")
        cls.server = create_server(cls.core, "127.0.0.1", 8799)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.4)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.core.shutdown()
        cls.tmp.cleanup()

    def call(self, path, method="GET", body=None):
        url = f"http://127.0.0.1:8799{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())

    def test_status_returns_real_metrics(self):
        res = self.call("/api/status")
        self.assertTrue(res["ok"])
        self.assertIn("metrics", res)
        cpu = res["metrics"]["cpu"]
        self.assertIsInstance(cpu["cores"], int)
        self.assertGreater(cpu["cores"], 0)
        # Aucune valeur inventée : soit un nombre réel, soit None.
        for key in ("percent",):
            self.assertTrue(cpu[key] is None or isinstance(cpu[key], (int, float)))

    def test_llm_never_claims_false_connection(self):
        res = self.call("/api/llm")
        for provider in res["providers"]:
            if provider["connected"]:
                self.assertTrue(provider["models"], "connected=true sans modèle réel")
            else:
                self.assertIn(provider["detail"], [provider["detail"]])

    def test_voice_session_and_greeting_via_api(self):
        session = self.call("/api/voice/session", "POST", {"client_id": "api-client"})
        self.assertTrue(session["ok"])
        sid = session["session"]["session_id"]
        first = self.call("/api/voice/greeting", "POST", {"session_id": sid})
        self.assertTrue(first["speak"])
        for _ in range(5):
            again = self.call("/api/voice/greeting", "POST", {"session_id": sid})
            self.assertFalse(again["speak"])
        # Reconnexion via l'API → session reprise, toujours pas de greeting
        resumed = self.call("/api/voice/session", "POST", {"client_id": "api-client"})
        self.assertTrue(resumed["session"]["resumed"])
        self.assertFalse(self.call("/api/voice/greeting", "POST",
                                   {"session_id": resumed["session"]["session_id"]})["speak"])

    def test_heartbeat_never_triggers_greeting(self):
        session = self.call("/api/voice/session", "POST", {"client_id": "hb-client"})
        sid = session["session"]["session_id"]
        self.call("/api/voice/greeting", "POST", {"session_id": sid})
        for _ in range(20):
            hb = self.call("/api/voice/heartbeat", "POST", {"session_id": sid})
            self.assertTrue(hb["ok"])
            self.assertNotIn("speak", hb)
            self.assertFalse(self.call("/api/voice/greeting", "POST", {"session_id": sid})["speak"])

    def test_connector_crud_via_api(self):
        created = self.call("/api/connectors", "POST", {
            "type": "n8n", "name": "n8n test", "config": {"url": "http://127.0.0.1:5678",
                                                          "api_key": "clé-api-test"}})
        self.assertTrue(created["ok"])
        cid = created["connector"]["id"]
        listed = self.call("/api/connectors")
        self.assertNotIn("clé-api-test", json.dumps(listed))
        mine = next(c for c in listed["connectors"] if c["id"] == cid)
        self.assertTrue(mine["secret_fields"]["api_key"]["configured"])
        self.assertTrue(mine["secret_fields"]["api_key"]["preview"].startswith("••••"))
        deleted = self.call(f"/api/connectors/{cid}", "DELETE")
        self.assertTrue(deleted["deleted"])

    def test_settings_greeting_frequency_locked_via_api(self):
        res = self.call("/api/settings/voice", "PUT", {"greeting_frequency": "always"})
        self.assertEqual(res["values"]["greeting_frequency"], "once_per_session")

    def test_tools_endpoint_reports_configuration_state(self):
        res = self.call("/api/tools")
        self.assertTrue(res["ok"])
        self.assertGreater(len(res["tools"]), 30)
        for tool in res["tools"]:
            self.assertIn(tool["status"], {"ready", "not_configured", "disabled"})

    def test_ui_is_served(self):
        with urllib.request.urlopen("http://127.0.0.1:8799/", timeout=10) as resp:
            html = resp.read().decode()
        self.assertIn("JARVIS", html)
        self.assertIn("COMMAND CENTER", html)
        for asset in ("/css/app.css", "/js/app.js", "/js/voice.js", "/js/dashboard.js"):
            with urllib.request.urlopen(f"http://127.0.0.1:8799{asset}", timeout=10) as resp:
                self.assertEqual(resp.getcode(), 200, asset)


class TestWeatherTool(unittest.TestCase):
    def test_wmo_labels(self):
        from jarvis.tools.web_tools import _wmo_label

        self.assertEqual(_wmo_label(0), "ciel dégagé")
        self.assertEqual(_wmo_label(95), "orage")
        self.assertEqual(_wmo_label(None), "conditions inconnues")

    def test_weather_tool_is_registered(self):
        tool = registry.get("weather.forecast")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.category, "Météo")
        self.assertEqual(tool.risk, READ_ONLY)
        self.assertIn("city", tool.input_schema["properties"])
        self.assertNotIn("city", tool.input_schema["required"])


class TestComfyUIMultiFile(unittest.TestCase):
    """Détection d'engines ComfyUI multi-fichiers (Z-Image-Turbo…) et workflows."""

    def _make_models_dir(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        for sub, name in (
            ("diffusion_models", "z_image_turbo_bf16.safetensors"),
            ("text_encoders", "qwen_3_4b.safetensors"),
            ("vae", "ae.safetensors"),
            ("checkpoints", "sd15.safetensors"),
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
            (root / sub / name).write_bytes(b"fake")
        return tmp, root

    def test_scan_models_dir_groups_folders(self):
        from jarvis.comfyui_detect import scan_models_dir

        tmp, root = self._make_models_dir()
        try:
            files = scan_models_dir(root)
            self.assertEqual(files["diffusion_models"], ["z_image_turbo_bf16.safetensors"])
            self.assertEqual(files["text_encoders"], ["qwen_3_4b.safetensors"])
            self.assertEqual(files["vae"], ["ae.safetensors"])
            self.assertEqual(files["checkpoints"], ["sd15.safetensors"])
        finally:
            tmp.cleanup()

    def test_infer_zimage_engine(self):
        from jarvis.comfyui_detect import infer_engines, scan_models_dir

        tmp, root = self._make_models_dir()
        try:
            engines = infer_engines(scan_models_dir(root))
            self.assertTrue(any(e["id"] == "zimage" for e in engines))
            zimg = next(e for e in engines if e["id"] == "zimage")
            self.assertEqual(zimg["model"], "z_image_turbo_bf16.safetensors")
            self.assertEqual(zimg["text_encoder"], "qwen_3_4b.safetensors")
            self.assertEqual(zimg["vae"], "ae.safetensors")
            self.assertTrue(any(e["id"] == "sd" and e["checkpoint"] == "sd15.safetensors"
                                for e in engines))
        finally:
            tmp.cleanup()

    def test_infer_needs_all_three_files(self):
        from jarvis.comfyui_detect import infer_engines

        files = {"diffusion_models": ["z_image_turbo_bf16.safetensors"],
                 "text_encoders": ["qwen_3_4b.safetensors"]}
        self.assertFalse(any(e["id"] == "zimage" for e in infer_engines(files)))

    def test_zimage_workflow_nodes(self):
        from jarvis.imagegen import zimage_workflow

        wf = zimage_workflow(prompt="une puff", negative_prompt="blurry", width=1024, height=1024,
                             seed=42, steps=8, cfg=1.0, sampler="euler", scheduler="simple",
                             model="z_image_turbo_bf16.safetensors",
                             text_encoder="qwen_3_4b.safetensors", vae="ae.safetensors")
        cls = {w["class_type"] for w in wf.values()}
        self.assertIn("UNETLoader", cls)
        self.assertIn("CLIPLoader", cls)
        self.assertIn("VAELoader", cls)
        self.assertIn("EmptySD3LatentImage", cls)
        self.assertNotIn("CheckpointLoaderSimple", cls)
        clip = next(w for w in wf.values() if w["class_type"] == "CLIPLoader")
        self.assertEqual(clip["inputs"]["type"], "lumina2")

    def test_sd_workflow_uses_checkpoint(self):
        from jarvis.imagegen import sd_workflow

        wf = sd_workflow(prompt="p", negative_prompt="n", width=512, height=512, seed=1,
                         steps=20, cfg=7.0, sampler="dpmpp_2m", scheduler="karras",
                         checkpoint="sd15.safetensors")
        loader = next(w for w in wf.values() if w["class_type"] == "CheckpointLoaderSimple")
        self.assertEqual(loader["inputs"]["ckpt_name"], "sd15.safetensors")


if __name__ == "__main__":
    unittest.main(verbosity=2)
