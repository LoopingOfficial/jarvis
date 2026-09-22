"""Real-process tests in isolated temporary mission directories."""
import pathlib
import sys
import tempfile
import time
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from workspace_backend import WorkspaceBackend

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = WorkspaceBackend(self.temp.name)
        self.worker._plan = lambda record: None  # Inference independently exercised by live UI.
    def tearDown(self):
        self.temp.cleanup()
    def wait(self, ident):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            record = self.worker.snapshot(ident)
            if record['status'] in ('completed', 'blocked'):
                return record
            time.sleep(.02)
        self.fail('Worker timed out')
    def test_no_work_before_run(self):
        task = self.worker.create('Développe un bot Discord')
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(self.worker.files(task['id']), [])
        self.worker.run(task['id'])
        done = self.wait(task['id'])
        self.assertEqual(done['status'], 'blocked')
        self.assertIsNone(done['currentAction'])
        outputs = ''.join(e.get('text', '') for e in done['events'])
        self.assertIn('Ran 2 tests', outputs)
        self.assertIn('OK', outputs)
        self.assertTrue((pathlib.Path(done['directory']) / 'bot.py').is_file())
        self.assertFalse(any(e['type'] == 'task.completed' for e in done['events']))
    def test_unknown_is_blocked(self):
        done = self.wait(self.worker.start('Fais une vidéo')['id'])
        self.assertEqual(done['status'], 'blocked')
    def test_paths_and_real_write(self):
        task = self.worker.create('test')
        with self.assertRaises(ValueError):
            self.worker.write_file(task['id'], '../escape.py', 'bad')
        self.worker.write_file(task['id'], 'real.py', 'print(42)\n')
        self.assertEqual(self.worker.read_file(task['id'], 'real.py')['content'], 'print(42)\n')
        with self.assertRaises(ValueError):
            self.worker.read_file(task['id'], '.env')

if __name__ == '__main__':
    unittest.main()
