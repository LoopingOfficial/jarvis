"""Diagnostic d'installation, sondes VRAM multi-vendeurs et dictée serveur."""
from __future__ import annotations

import unittest
from unittest import mock

from jarvis import doctor
from jarvis.gpu_manager import GpuResourceManager
from jarvis.stt import SpeechRecognizer


class FakeSettings:
    def __init__(self, values=None):
        self._values = values or {}

    def get(self, section, key, default=None):
        return self._values.get((section, key), default)


class TestVramProbes(unittest.TestCase):
    """La mesure doit fonctionner hors NVIDIA, et ne jamais inventer de valeur."""

    def test_nvidia_parsed(self):
        with mock.patch.object(GpuResourceManager, "_run", return_value="8192, 2048, 6144\n"):
            measure = GpuResourceManager._vram_nvidia()
        self.assertEqual(measure["total_mb"], 8192)
        self.assertEqual(measure["free_mb"], 6144)
        self.assertEqual(measure["vendor"], "nvidia")

    def test_amd_parsed_from_bytes(self):
        payload = ('{"card0": {"VRAM Total Memory (B)": "8589934592", '
                   '"VRAM Total Used Memory (B)": "1073741824"}}')
        with mock.patch.object(GpuResourceManager, "_run", return_value=payload):
            measure = GpuResourceManager._vram_amd()
        self.assertEqual(measure["vendor"], "amd")
        self.assertEqual(measure["total_mb"], 8192)
        self.assertEqual(measure["free_mb"], 7168)

    def test_windows_fallback_identifies_vendor(self):
        payload = '{"name":"AMD Radeon RX 7800 XT","total":17179869184,"used":2147483648}'
        with mock.patch("jarvis.gpu_manager.sys.platform", "win32"), \
                mock.patch.object(GpuResourceManager, "_run", return_value=payload):
            measure = GpuResourceManager._vram_windows()
        self.assertEqual(measure["vendor"], "amd")
        self.assertEqual(measure["total_mb"], 16384)
        self.assertEqual(measure["free_mb"], 14336)

    def test_probes_tried_in_order(self):
        """Sans NVIDIA, on doit basculer sur AMD puis sur Windows."""
        with mock.patch.object(GpuResourceManager, "_vram_nvidia", return_value=None), \
                mock.patch.object(GpuResourceManager, "_vram_amd", return_value=None), \
                mock.patch.object(GpuResourceManager, "_vram_windows",
                                  return_value={"total_mb": 1, "used_mb": 0, "free_mb": 1,
                                                "vendor": "intel", "source": "x"}):
            self.assertEqual(GpuResourceManager.vram()["vendor"], "intel")

    def test_no_probe_returns_none(self):
        """Aucune sonde disponible : None, jamais une valeur plausible inventée."""
        with mock.patch.object(GpuResourceManager, "_run", return_value=""), \
                mock.patch("jarvis.gpu_manager.sys.platform", "linux"):
            self.assertIsNone(GpuResourceManager.vram())

    def test_broken_probe_does_not_raise(self):
        with mock.patch.object(GpuResourceManager, "_vram_nvidia", side_effect=OSError("boom")), \
                mock.patch.object(GpuResourceManager, "_vram_amd", return_value=None), \
                mock.patch.object(GpuResourceManager, "_vram_windows", return_value=None):
            self.assertIsNone(GpuResourceManager.vram())


class TestSpeechRecognizer(unittest.TestCase):
    def test_language_from_bcp47(self):
        recognizer = SpeechRecognizer(FakeSettings({("voice", "stt_language"): "fr-FR"}))
        self.assertEqual(recognizer._language(), "fr")

    def test_unknown_model_falls_back_to_default(self):
        recognizer = SpeechRecognizer(FakeSettings({("voice", "stt_model"): "gigantesque"}))
        self.assertEqual(recognizer._model_name(), "small")

    def test_empty_audio_refused(self):
        with self.assertRaises(RuntimeError):
            SpeechRecognizer().transcribe(b"")

    def test_missing_backend_says_how_to_install(self):
        with mock.patch("jarvis.stt.backend_available", return_value=False):
            with self.assertRaises(RuntimeError) as caught:
                SpeechRecognizer().transcribe(b"des octets")
        self.assertIn("faster-whisper", str(caught.exception))

    def test_cpu_only_when_no_nvidia(self):
        """Sans carte NVIDIA mesurée, aucune tentative CUDA n'est faite."""
        with mock.patch("jarvis.gpu_manager.GpuResourceManager.vram",
                        return_value={"vendor": "amd", "free_mb": 8000, "total_mb": 8192}):
            self.assertEqual(SpeechRecognizer()._devices(), [("cpu", "int8")])

    def test_cuda_tried_first_then_cpu(self):
        """Une carte NVIDIA donne CUDA en premier, mais le CPU reste en repli :
        cuBLAS peut manquer, et on ne le sait qu'à l'usage."""
        with mock.patch("jarvis.gpu_manager.GpuResourceManager.vram",
                        return_value={"vendor": "nvidia", "free_mb": 8000, "total_mb": 10240}):
            devices = SpeechRecognizer()._devices()
        self.assertEqual(devices[0][0], "cuda")
        self.assertEqual(devices[-1], ("cpu", "int8"))


class FakeCore:
    """Cœur minimal : chaque sonde du doctor doit tolérer un service absent."""

    def __init__(self):
        self.settings = FakeSettings()


class TestDoctor(unittest.TestCase):
    def test_diagnose_never_raises_on_broken_core(self):
        report = doctor.diagnose(FakeCore())
        self.assertEqual(len(report["checks"]), len(doctor.CHECKS))
        self.assertFalse(report["ok"])

    def test_every_failing_check_carries_a_fix(self):
        for check in doctor.diagnose(FakeCore())["checks"]:
            if not check["ok"] and not check["detail"].startswith("Sonde en échec"):
                self.assertTrue(check["fix"], f"{check['name']} échoue sans marche à suivre")

    def test_only_filters_checks(self):
        report = doctor.diagnose(FakeCore(), ["gpu"])
        self.assertEqual([c["name"] for c in report["checks"]], ["gpu"])

    def test_auto_fixable_checks_have_a_repair_recipe(self):
        for check in doctor.diagnose(FakeCore())["checks"]:
            if check["auto_fixable"]:
                self.assertIn(check["name"], doctor.REPAIRS)

    def test_repairs_are_non_interactive(self):
        """Une réparation ne doit jamais attendre une saisie humaine."""
        for commands in doctor.REPAIRS.values():
            for command in commands:
                self.assertNotIn("-i", command)
                self.assertNotIn("--interactive", command)

    def test_fast_checks_exist(self):
        known = {name for name, _ in doctor.CHECKS}
        self.assertTrue(set(doctor.FAST_CHECKS) <= known)

    def test_repair_only_touches_requested_checks(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch("jarvis.doctor.subprocess.run", side_effect=fake_run):
            result = doctor.repair(FakeCore(), ["discord"])
        self.assertEqual(result["attempted"], ["discord"])
        # `repair` diagnostique d'abord (dont la sonde playwright, qui lance
        # elle aussi un sous-processus) : on ne retient que les pip install.
        installs = [c for c in calls if "pip" in c and "install" in c]
        self.assertTrue(installs, "aucune installation lancée")
        self.assertTrue(all("discord.py" in c for c in installs), installs)


if __name__ == "__main__":
    unittest.main()
