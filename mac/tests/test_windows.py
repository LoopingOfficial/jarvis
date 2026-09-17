import base64
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from jarvis import windows
from jarvis.permissions import classify_command, DESTRUCTIVE, SENSITIVE


class TestWindows(unittest.TestCase):
    def test_executable_discovery_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='Jarvis Windows ') as root:
            path = Path(root) / 'Google/Chrome/Application/chrome.exe'
            path.parent.mkdir(parents=True)
            path.touch()
            with patch.dict(os.environ, {'LOCALAPPDATA': root}), patch('jarvis.windows.shutil.which', return_value=None):
                self.assertEqual(windows.find_app('chrome'), str(path))

    @patch('jarvis.windows.subprocess.Popen')
    @patch('jarvis.windows.find_app', return_value='C:/Program Files/Chrome/chrome.exe')
    def test_launch_without_shell(self, find, popen):
        self.assertTrue(windows.launch_ui('http://127.0.0.1:8765/'))
        self.assertFalse(popen.call_args.kwargs['shell'])
        self.assertEqual(popen.call_args.args[0][1], '--app=http://127.0.0.1:8765/')

    @patch('jarvis.windows.find_app', return_value=None)
    def test_missing_browser_fallback(self, find):
        self.assertFalse(windows.launch_ui('http://127.0.0.1:8765/'))
        with self.assertRaises(FileNotFoundError):
            windows.open_app('missing')

    @patch('jarvis.windows.subprocess.Popen')
    @patch('jarvis.windows.find_app', return_value='powershell.exe')
    def test_notification_text_never_becomes_code(self, find, popen):
        text = "'; $(Remove-Item test); '"
        windows.notify('Test', text)
        args = popen.call_args.args[0]
        script = base64.b64decode(args[-1]).decode('utf-16-le')
        self.assertNotIn(text, script)
        self.assertEqual(popen.call_args.kwargs['env']['JARVIS_NOTIFY_MESSAGE'], text)

    def test_windows_risk_classification(self):
        for command in ('del /q file', 'rmdir /s folder', 'Remove-Item -Recurse folder', 'Format-Volume D:'):
            with self.subTest(command=command):
                self.assertEqual(classify_command(command), DESTRUCTIVE)
        self.assertEqual(classify_command('Stop-Service test'), SENSITIVE)

    def test_credential_manager_roundtrip(self):
        vault = Mock()
        vault.get_password.return_value = None
        module = SimpleNamespace(WinVaultKeyring=lambda: vault)
        with patch.dict(sys.modules, {'keyring.backends.Windows': module}):
            created = windows.master_key()
            self.assertEqual(len(created), 32)
            vault.get_password.return_value = vault.set_password.call_args.args[2]
            self.assertEqual(windows.master_key(), created)
            self.assertEqual(vault.set_password.call_count, 1)

    def test_corrupt_key_rejected(self):
        vault = Mock()
        vault.get_password.return_value = base64.b64encode(b'bad').decode()
        with patch.dict(sys.modules, {'keyring.backends.Windows': SimpleNamespace(WinVaultKeyring=lambda: vault)}):
            with self.assertRaises(ValueError):
                windows.master_key()
            vault.set_password.assert_not_called()

    def test_no_plaintext_fallback_when_windows_vault_fails(self):
        from jarvis.secrets import MasterKey
        with patch.dict(os.environ, {'JARVIS_MASTER_KEY': ''}), patch('jarvis.secrets.IS_WINDOWS', True), \
             patch('jarvis.windows.master_key', side_effect=RuntimeError('unavailable')), patch('jarvis.secrets._file_set') as write:
            with self.assertRaises(RuntimeError):
                MasterKey()
            write.assert_not_called()

    @patch('jarvis.tools.remote_tools.subprocess.run')
    def test_docker_uses_argument_list(self, run):
        from jarvis.tools.remote_tools import _docker
        run.return_value = SimpleNamespace(returncode=0, stdout='ok', stderr='')
        result = _docker(SimpleNamespace(arguments={'action': 'logs', 'container': 'test'}, config={}))
        self.assertTrue(result.ok)
        self.assertEqual(run.call_args.args[0], ['docker', 'logs', '--tail', '100', 'test'])
        self.assertFalse(run.call_args.kwargs['shell'])

    @patch('jarvis.tools.remote_tools.subprocess.run')
    def test_sql_arguments_preserve_quotes_and_password(self, run):
        from jarvis.tools.remote_tools import _db_query
        run.return_value = SimpleNamespace(returncode=0, stdout='42', stderr='')
        for kind, var in [('mysql', 'MYSQL_PWD'), ('postgres', 'PGPASSWORD')]:
            ctx = SimpleNamespace(arguments={'query': "SELECT 'été et espaces'", 'database': 'test db'},
                                  config={'username': 'test'}, connector={'type': kind}, secret=lambda _: 'a&b%"c',
                                  core=SimpleNamespace(vault=SimpleNamespace(scrub=lambda text: text)))
            result = _db_query(ctx)
            self.assertTrue(result.ok)
            self.assertEqual(run.call_args.args[0][-1], "SELECT 'été et espaces'")
            self.assertIn('test db', run.call_args.args[0])
            self.assertFalse(run.call_args.kwargs['shell'])
            self.assertEqual(run.call_args.kwargs['env'][var], 'a&b%"c')
            self.assertNotIn('a&b%"c', run.call_args.args[0])
