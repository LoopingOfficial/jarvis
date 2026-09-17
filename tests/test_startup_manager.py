"""Tests du Startup Manager — mocks uniquement, aucun vrai processus.

Couvre la checklist de la mission §13 : JARVIS déjà lancé, port libre/occupé,
LLM disponible/indisponible/fallback, Doctor HEALTHY/DEGRADED/BLOCKED, mauvais
point d'entrée, startup réussi/échoué, et l'arrêt propre qui n'ose jamais un
kill forcé.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis import startup  # noqa: E402
from jarvis import doctor  # noqa: E402


def _check(name, status=doctor.OK, *, severity=doctor.BLOCKING, blocking=False,
           detail="", providers=None):
    details = {"providers": providers} if providers is not None else {}
    return {"name": name, "label": doctor.SYSTEM_LABELS.get(name, name),
            "status": status, "detail": detail, "message": detail,
            "recommendation": "", "severity": severity,
            "required": severity == doctor.BLOCKING,
            "blocking": bool(blocking), "details": details}


def _report(health, *, checks=None, blocking=None):
    return {"status": doctor.OK if health == doctor.HEALTHY else doctor.WARNING,
            "health": health, "warnings": 0, "errors": 0,
            "blocking": blocking or [], "degraded": [], "optional_offline": [],
            "checks": checks if checks is not None else [], "data_dir": "x"}


def _port(state, port=startup.MAIN_API_PORT, pid=4242, name="python.exe"):
    if state == "listening":
        return {"port": port, "state": "listening", "pid": pid, "name": name}
    return {"port": port, "state": "free"}


def _free_ports(except_listening=None):
    listening = set(except_listening or [])

    def port_state(port):
        return _port("listening" if port in listening else "free", port)
    return port_state


class FakeRunner:
    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def __call__(self, entrypoint):
        self.calls.append(entrypoint)
        return self.code


class FakeSpawn:
    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return None


class PreflightTests(unittest.TestCase):
    def test_doctor_healthy_est_ready(self):
        result = startup.preflight(run_doctor=lambda: _report(doctor.HEALTHY))
        self.assertTrue(result["ready"])
        self.assertFalse(result["blocked"])

    def test_doctor_degraded_reste_ready(self):
        result = startup.preflight(run_doctor=lambda: _report(doctor.DEGRADED))
        self.assertTrue(result["ready"])
        self.assertFalse(result["blocked"])

    def test_doctor_blocked_nest_pas_ready(self):
        result = startup.preflight(run_doctor=lambda: _report(
            doctor.BLOCKED, blocking=["llm_server"]))
        self.assertFalse(result["ready"])
        self.assertTrue(result["blocked"])

    def test_rendu_affiche_les_libelles_et_le_verdict(self):
        text = startup.render_preflight(_report(
            doctor.DEGRADED,
            checks=[_check("python"), _check("database"),
                    _check("comfyui", doctor.WARNING, severity=doctor.DEGRADED)]))
        self.assertIn("JARVIS STARTUP", text)
        self.assertIn("Python", text)
        self.assertIn("ComfyUI", text)
        self.assertIn("DEGRADED", text)
        self.assertIn("Preflight: READY", text)


class StartPortTests(unittest.TestCase):
    def _base(self, **kw):
        defaults = dict(run_doctor=lambda: _report(doctor.HEALTHY),
                        port_state=_free_ports(), probe_health=lambda port: None,
                        spawn=FakeSpawn(), runner=FakeRunner(0),
                        entrypoint=Path(__file__), no_launch=False,
                        launcher=None, log_dir=Path(tempfile.gettempdir()) /
                        "jarvis_startup_tests", emit=lambda text: None)
        defaults.update(kw)
        return defaults

    def test_port_libre_et_jarvis_lance(self):
        runner = FakeRunner(0)
        code = startup.start(**self._base(runner=runner))
        self.assertEqual(code, 0)
        self.assertEqual(len(runner.calls), 1)

    def test_jarvis_deja_lance_ne_relance_pas(self):
        runner = FakeRunner(0)
        code = startup.start(**self._base(
            port_state=_free_ports(except_listening=[startup.MAIN_API_PORT]),
            probe_health=lambda port: {"ok": True, "version": "3.0.0"},
            runner=runner))
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])

    def test_port_occupe_par_un_autre_processus_refuse_sans_tuer(self):
        runner = FakeRunner(0)
        code = startup.start(**self._base(
            port_state=_free_ports(except_listening=[startup.MAIN_API_PORT]),
            probe_health=lambda port: None,
            runner=runner))
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])

    def test_mauvais_point_dentree(self):
        runner = FakeRunner(0)
        code = startup.start(**self._base(
            entrypoint=Path(tempfile.gettempdir()) / "jarvis_inexistant.py",
            runner=runner))
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])


class StartLlmTests(unittest.TestCase):
    def _base(self, **kw):
        defaults = dict(run_doctor=lambda: _report(doctor.HEALTHY),
                        port_state=_free_ports(), probe_health=lambda port: None,
                        spawn=FakeSpawn(), runner=FakeRunner(0),
                        entrypoint=Path(__file__), no_launch=False,
                        launcher=None, log_dir=Path(tempfile.gettempdir()) /
                        "jarvis_startup_tests", emit=lambda text: None)
        defaults.update(kw)
        return defaults

    def test_llm_deja_en_cours_ne_lance_rien(self):
        spawn = FakeSpawn()
        code = startup.start(**self._base(
            port_state=_free_ports(except_listening=[startup.LLM_API_PORT]),
            launcher=Path("C:/faux/llama.bat"), spawn=spawn))
        self.assertEqual(code, 0)
        self.assertEqual(spawn.commands, [])

    def test_llm_indisponible_avec_launcher_est_lance(self):
        spawn = FakeSpawn()
        code = startup.start(**self._base(
            launcher=Path(__file__), spawn=spawn))
        self.assertEqual(code, 0)
        self.assertEqual(len(spawn.commands), 1)

    def test_llm_indisponible_sans_launcher_reporte_offline(self):
        messages = []
        code = startup.start(**self._base(
            launcher=None, emit=messages.append))
        self.assertEqual(code, 0)
        self.assertTrue(any("MANUAL START REQUIRED" in m for m in messages))

    def test_fallback_provider_empeche_limpossible(self):
        report = _report(doctor.DEGRADED, checks=[
            _check("llm_server", doctor.OK, severity=doctor.BLOCKING,
                   detail="Ollama", providers=[
                       {"name": "Ollama", "type": "ollama", "connected": True}])])
        messages = []
        spawn = FakeSpawn()
        code = startup.start(**self._base(
            run_doctor=lambda: report, launcher=Path(__file__),
            spawn=spawn, emit=messages.append))
        self.assertEqual(code, 0)
        self.assertEqual(spawn.commands, [])
        self.assertTrue(any("AVAILABLE VIA OLLAMA" in m for m in messages))

    def test_doctor_blocked_ne_lance_pas_jarvis(self):
        runner = FakeRunner(0)
        messages = []
        code = startup.start(**self._base(
            run_doctor=lambda: _report(doctor.BLOCKED, blocking=["llm_server"]),
            runner=runner, emit=messages.append))
        self.assertEqual(code, 1)
        self.assertEqual(runner.calls, [])
        self.assertTrue(any("JARVIS START FAILED" in m for m in messages))


class StartupResultTests(unittest.TestCase):
    def _base(self, **kw):
        defaults = dict(run_doctor=lambda: _report(doctor.HEALTHY),
                        port_state=_free_ports(), probe_health=lambda port: None,
                        spawn=FakeSpawn(), runner=FakeRunner(0),
                        entrypoint=Path(__file__), no_launch=False,
                        launcher=None, log_dir=Path(tempfile.gettempdir()) /
                        "jarvis_startup_tests", emit=lambda text: None)
        defaults.update(kw)
        return defaults

    def test_startup_reussi_code_0(self):
        self.assertEqual(startup.start(**self._base(runner=FakeRunner(0))), 0)

    def test_startup_echoue_code_non_nul(self):
        messages = []
        self.assertEqual(startup.start(**self._base(
            runner=FakeRunner(3), emit=messages.append)), 3)
        self.assertTrue(any("JARVIS START FAILED" in m for m in messages))

    def test_no_launch_ne_demarre_pas(self):
        runner = FakeRunner(0)
        code = startup.start(**self._base(runner=runner, no_launch=True))
        self.assertEqual(code, 0)
        self.assertEqual(runner.calls, [])


class StopTests(unittest.TestCase):
    def test_jarvis_absent_renvoie_0(self):
        messages = []
        code = startup.stop(port_state=_free_ports(),
                            probe_health=lambda port: None,
                            emit=messages.append)
        self.assertEqual(code, 0)
        self.assertTrue(any("PAS EN COURS" in m for m in messages))

    def test_port_occupe_par_un_autre_ne_tue_rien(self):
        sent = []
        messages = []
        code = startup.stop(
            port_state=_free_ports(except_listening=[startup.MAIN_API_PORT]),
            probe_health=lambda port: None,
            sender=lambda pid: sent.append(pid) or True,
            emit=messages.append)
        self.assertEqual(code, 1)
        self.assertEqual(sent, [])

    def test_arret_propre_confirme_le_pid(self):
        sent = []
        state = {"calls": 0}

        def port_state(port):
            state["calls"] += 1
            if state["calls"] > 1:
                return _port("free", port)
            return _port("listening", port, pid=777, name="python.exe")

        code = startup.stop(
            port_state=port_state,
            probe_health=lambda port: {"ok": True, "version": "3.0.0"},
            sender=lambda pid: sent.append(pid) or True,
            emit=lambda text: None)
        self.assertEqual(code, 0)
        self.assertEqual(sent, [777])


class LlmStateTests(unittest.TestCase):
    def test_running_prioritaire(self):
        result = startup.llm_state(_report(doctor.HEALTHY),
                                   _port("listening", startup.LLM_API_PORT), None)
        self.assertEqual(result["state"], "running")
        self.assertIn("ALREADY RUNNING", result["message"])

    def test_offline_sans_launcher(self):
        result = startup.llm_state(_report(doctor.HEALTHY),
                                   _port("free", startup.LLM_API_PORT), None)
        self.assertEqual(result["state"], "offline")
        self.assertIn("MANUAL START REQUIRED", result["message"])

    def test_launchable_avec_launcher(self):
        result = startup.llm_state(_report(doctor.HEALTHY),
                                   _port("free", startup.LLM_API_PORT),
                                   Path("llama.bat"))
        self.assertEqual(result["state"], "launchable")


class LogTests(unittest.TestCase):
    def test_log_sans_secret_et_ecrit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = startup.write_log(
                {"ts": "2026-01-01T00:00:00", "event": "test", "result": "ok"},
                Path(tmp))
            self.assertTrue(path.exists())
            line = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(line["event"], "test")

    def test_find_llama_launcher_detecte_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "start_llama.bat").write_text(
                'llama-server.exe -m model.gguf', encoding="utf-8")
            found = startup.find_llama_launcher(root=root)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "start_llama.bat")

    def test_find_llama_launcher_ignore_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(startup.find_llama_launcher(root=Path(tmp)))


if __name__ == "__main__":
    unittest.main()
