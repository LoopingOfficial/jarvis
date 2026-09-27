import tempfile
import unittest
from pathlib import Path

from jarvis.distributed.mission import Mission
from jarvis.distributed.code_change import CodeChangeSet, PatchExecutor
from jarvis.distributed.planner import MissionPlanner
from jarvis.distributed.tools import ToolRegistry


class DistributedOperatorTests(unittest.TestCase):
    def test_planner_returns_structured_dependencies_and_capabilities(self):
        mission = Mission("Corrige le bug de notifications et exécute les tests")
        tasks = MissionPlanner().plan(mission)
        by_id = {task.task_id: task for task in tasks}
        self.assertEqual({"inspect", "analyze", "code", "test", "review", "synthesis"}, set(by_id))
        self.assertEqual(["inspect"], by_id["analyze"].depends_on)
        self.assertEqual(["code"], by_id["test"].depends_on)
        self.assertEqual(["coding", "structured_output"], by_id["code"].required_capabilities)
        self.assertNotIn("tools", by_id["code"].required_capabilities)
        self.assertEqual([], by_id["test"].required_capabilities)

    def test_tools_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(directory)
            Path(directory, "sample.py").write_text("needle\n", encoding="utf-8")
            self.assertEqual(["sample.py"], registry.execute_tool("list_files", {}))
            self.assertTrue(registry.execute_tool("search_code", {"pattern": "needle"}))
            with self.assertRaises(ValueError):
                registry.execute_tool("read_file", {"path": "../outside.txt"})

    def test_write_tools_are_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = ToolRegistry(directory)
            with self.assertRaises(PermissionError):
                registry.execute_tool("write_file", {"path": "x.txt", "content": "blocked"})

    def test_structured_patch_applies_and_rolls_back_inside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "bug.py")
            path.write_text("return 1\n", encoding="utf-8")
            change_set = CodeChangeSet.from_json('{"changes":[{"operation":"replace","path":"bug.py","reason":"fix","before":"return 1\\n","after":"return 2\\n"}]}')
            executor = PatchExecutor(directory)
            self.assertEqual(["bug.py"], executor.apply(change_set))
            self.assertEqual("return 2\n", path.read_text(encoding="utf-8"))
            executor.rollback()
            self.assertEqual("return 1\n", path.read_text(encoding="utf-8"))

    def test_structured_patch_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            change_set = CodeChangeSet.from_json('{"changes":[{"operation":"replace","path":"../bug.py","reason":"bad","before":"x","after":"y"}]}')
            with self.assertRaises(ValueError):
                PatchExecutor(directory).validate(change_set)


if __name__ == "__main__":
    unittest.main()
