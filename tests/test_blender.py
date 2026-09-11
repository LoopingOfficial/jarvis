"""Tests Blender réels : vrai job Blender local, vrais fichiers produits."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

os.environ["JARVIS_MASTER_KEY"] = "test-blender-e2e-master-key"

from jarvis.core import JarvisCore

try:
    from jarvis.blender import BlenderNotInstalled
except ImportError:
    BlenderNotInstalled = Exception


class BlenderE2EBase(unittest.TestCase):
    _core: JarvisCore | None = None
    _tmp: tempfile.TemporaryDirectory | None = None
    cid: str = "test-blender"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="jarvis_blender_")
        cls._core = JarvisCore(db_path=Path(cls._tmp.name) / "test.db")
        if not cls._core.blender.available():
            raise unittest.SkipTest("Blender non disponible sur ce PC")
        conf = cls._core.settings.section("blender")
        conf["auto_preview"] = False
        conf["preview_size"] = 640
        conf["use_gpu"] = False
        conf["default_timeout_s"] = 300

    @classmethod
    def tearDownClass(cls):
        if cls._core:
            try:
                cls._core.db.close()
            except Exception:
                pass
        if cls._tmp:
            try:
                shutil.rmtree(cls._tmp.name, ignore_errors=True)
            except Exception:
                pass

    def _submit(self, action: str, settings: dict, reuse: bool = False,
                timeout: int = 180) -> dict:
        start = time.time()
        job = self._core.blender.submit(
            tool=f"blender.{action}", action=action,
            title=f"test_{action}",
            settings=settings,
            conversation_id=self.cid,
            reuse_project=reuse,
            timeout=timeout,
        )
        elapsed = round(time.time() - start, 1)
        job["test_elapsed"] = elapsed
        return job


class TestCreateLamp(BlenderE2EBase):
    def test_01_create_lamp(self):
        job = self._submit("create", {
            "kind": "lamp",
            "prompt": "Lampe de bureau stylisée",
            "material": "metal",
            "color": "3a3f4d",
            "preview": False,
            "export_formats": ["glb"],
        }, reuse=False)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        names = [o["name"] for o in job["outputs"]]
        self.assertIn("model.blend", names)
        self.assertIn("model.glb", names)
        meta = job.get("meta") or {}
        self.assertGreater(meta.get("polycount", 0), 0)
        self.assertTrue((meta.get("verified") or {}).get("glb", {}).get("ok"), "GLB verification")
        self.assertEqual(meta.get("kind"), "blueprint")
        self.assertGreater(len(meta.get("materials", [])), 0)
        glb = meta.get("glb") or {}
        self.assertGreater(glb.get("size", 0), 0, "GLB has size")
        print(f"CREATE OK  {job['test_elapsed']}s  tris={meta['polycount']}  glb={glb.get('size',0)//1024}KB")


class TestModify(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-mod"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_mod_base",
            settings={"kind": "cube", "preview": False, "export_formats": ["glb"]},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_02_modify_scale(self):
        job = self._submit("modify", {
            "instruction": "Rends ce cube plus grand",
            "operations": [{"op": "scale", "factor": 1.5, "axis": "all"}],
            "preview": False,
            "export_formats": ["glb"],
        }, reuse=True)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        meta = job.get("meta") or {}
        self.assertGreater(meta.get("polycount", 0), 0)
        print(f"MODIFY OK  {job['test_elapsed']}s  tris={meta['polycount']}")


class TestInspect(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-inspect"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_inspect_base",
            settings={"kind": "lamp", "preview": False, "export_formats": ["glb"]},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_03_inspect(self):
        job = self._submit("inspect", {
            "export_formats": [],
        }, reuse=True)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        meta = job.get("meta") or {}
        inv = meta.get("inventory") or {}
        self.assertIn("counts", inv)
        self.assertIn("materials", inv)
        self.assertGreater(inv.get("counts", {}).get("triangles", 0), 0)
        print(f"INSPECT OK  {job['test_elapsed']}s  objs={inv['counts']['objects']}  "
              f"tris={inv['counts']['triangles']}  mats={len(inv['materials'])}")


class TestRenderEEVEE(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-render"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_render_base",
            settings={"kind": "cube", "preview": False, "export_formats": []},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_04_render_eevee(self):
        job = self._submit("render", {
            "engine": "eevee",
            "samples": 32,
            "width": 512,
            "height": 512,
            "use_gpu": False,
            "preview": False,
            "export_formats": [],
        }, reuse=True, timeout=240)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        names = [o["name"] for o in job["outputs"]]
        self.assertIn("render.png", names)
        png = Path(job["output_dir"]) / "render.png"
        self.assertGreater(png.stat().st_size, 0, "render.png non vide")
        print(f"RENDER EEVEE OK  {job['test_elapsed']}s  png={png.stat().st_size//1024}KB")


class TestAnimate(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-animate"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_anim_base",
            settings={"kind": "cube", "preview": False, "export_formats": ["glb"]},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_05_animate_spin(self):
        job = self._submit("animate", {
            "clip": "spin", "axis": "z", "speed": 2.0, "duration": 2.0,
            "preview": False, "export_formats": ["glb"],
        }, reuse=True)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        meta = job.get("meta") or {}
        anims = meta.get("animations") or []
        self.assertTrue(any("spin" in a for a in anims), f"fallback absent dans {anims}")
        print(f"ANIMATE SPIN OK  {job['test_elapsed']}s  anims={anims}")

    def test_06_animate_walk_rejected(self):
        job = self._submit("animate", {
            "clip": "walk", "agent": "human", "preview": False, "export_formats": [],
        }, reuse=True)
        self.assertEqual(job["status"], "failed")
        err = job.get("error") or ""
        self.assertIn("spin", err), self.assertTrue(any(
            c in err for c in ["spin", "rotate", "bounce", "orbit"]), f"clips inconnus: {err}")
        print(f"ANIMATE WALK REFUSÉ OK  (erreur honnête reçue)")


class TestMaterial(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-material"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_mat_base",
            settings={"kind": "lamp", "preview": False, "export_formats": ["glb"]},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_07_material_preset(self):
        job = self._submit("material", {
            "preset": "wood",
            "instruction": "Rendu bois chaud",
            "preview": False, "export_formats": ["glb"],
        }, reuse=True)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        meta = job.get("meta") or {}
        self.assertIn("wood", " ".join(meta.get("materials", [])).lower())
        print(f"MATERIAL WOOD OK  {job['test_elapsed']}s  mats={meta['materials']}")


class TestExport(BlenderE2EBase):
    _project_job = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cid = "test-blender-export"
        cls._project_job = cls._core.blender.submit(
            tool="blender.create_model", action="create",
            title="test_export_base",
            settings={"kind": "lamp", "preview": False, "export_formats": ["glb"]},
            conversation_id=cls.cid,
            reuse_project=False, timeout=180)
        if cls._project_job.get("status") != "completed":
            raise unittest.SkipTest(f"Setup failed: {cls._project_job.get('error')}")

    def test_08_export_stl(self):
        job = self._submit("export", {
            "formats": ["stl", "obj"], "preview": False,
        }, reuse=True)
        self.assertEqual(job["status"], "completed", job.get("error", ""))
        names = [o["name"] for o in job["outputs"]]
        self.assertIn("model.stl", names)
        self.assertIn("model.obj", names)
        verified = (job.get("meta") or {}).get("verified") or {}
        self.assertFalse(verified.get("exports_failed", []),
                         f"exports en échec: {verified.get('exports_failed')}")
        exports = (job.get("meta") or {}).get("exports") or []
        print(f"EXPORT STL/OBJ OK  {job['test_elapsed']}s  sizes={[e.get('size', 0)//1024 for e in exports]}KB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
