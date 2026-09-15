"""Windows desktop integration, imported without Windows dependencies on other OSes."""
from __future__ import annotations
import base64
import os
from pathlib import Path
import shutil
import subprocess

# Executable paths relative to common Windows installation directories.
APPS = {
    'chrome': ('chrome.exe', 'Google/Chrome/Application/chrome.exe'),
    'google chrome': ('chrome.exe', 'Google/Chrome/Application/chrome.exe'),
    'edge': ('msedge.exe', 'Microsoft/Edge/Application/msedge.exe'),
    'cursor': ('Cursor.exe', 'Programs/cursor/Cursor.exe'),
    'vscode': ('Code.exe', 'Programs/Microsoft VS Code/Code.exe', 'Microsoft VS Code/Code.exe'),
    'code': ('Code.exe', 'Programs/Microsoft VS Code/Code.exe', 'Microsoft VS Code/Code.exe'),
    'visual studio code': ('Code.exe', 'Programs/Microsoft VS Code/Code.exe', 'Microsoft VS Code/Code.exe'),
    'terminal': ('wt.exe', 'System32/cmd.exe'),
    'powershell': ('System32/WindowsPowerShell/v1.0/powershell.exe',),
    'finder': ('explorer.exe',), 'explorateur': ('explorer.exe',),
    'explorer': ('explorer.exe',), 'notes': ('System32/notepad.exe',),
    'notepad': ('System32/notepad.exe',), 'bloc-notes': ('System32/notepad.exe',),
    'calculatrice': ('System32/calc.exe',), 'spotify': ('Spotify/Spotify.exe',),
    'discord': ('Discord/Update.exe',),
    'docker': ('Docker/Docker/Docker Desktop.exe',),
}


def find_app(name: str) -> str | None:
    name = name.strip()
    path = Path(name)
    if path.is_absolute():
        return str(path) if path.suffix.lower() == '.exe' and path.is_file() else None
    choices = APPS.get(name.casefold(), (name if name.lower().endswith('.exe') else name + '.exe',))
    roots = [os.environ.get(key, '') for key in ('LOCALAPPDATA', 'APPDATA', 'ProgramFiles', 'ProgramFiles(x86)', 'SystemRoot')]
    for choice in choices:
        if '/' not in choice:
            found = shutil.which(choice)
            if found and Path(found).suffix.lower() == '.exe':
                return found
        for root in roots:
            candidate = Path(root) / choice
            if root and candidate.is_file():
                return str(candidate)
    return None


def open_app(name: str) -> None:
    if name.casefold() in {'settings', 'réglages', 'reglages', 'paramètres', 'parametres'}:
        os.startfile('ms-settings:')
        return
    exe = find_app(name)
    if not exe:
        raise FileNotFoundError(f'Application Windows introuvable : {name}. Indique son chemin complet .exe.')
    args = [exe]
    if name.casefold() == 'discord':
        args += ['--processStart', 'Discord.exe']
    subprocess.Popen(args, shell=False)


def launch_ui(url: str) -> bool:
    exe = find_app('chrome') or find_app('edge')
    if not exe:
        return False
    subprocess.Popen([exe, f'--app={url}', '--new-window', '--window-size=1680,1050'], shell=False)
    return True


def notify(title: str, message: str) -> None:
    # User text is passed through environment variables, never inserted into code.
    script = '''Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
try {
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.BalloonTipTitle = $env:JARVIS_NOTIFY_TITLE
$n.BalloonTipText = $env:JARVIS_NOTIFY_MESSAGE
$n.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
} finally { $n.Dispose() }
'''
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    env = os.environ.copy()
    env.update(JARVIS_NOTIFY_TITLE=title[:63], JARVIS_NOTIFY_MESSAGE=message[:255])
    exe = find_app('powershell')
    if not exe:
        raise FileNotFoundError('Windows PowerShell indisponible.')
    subprocess.Popen([exe, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                     env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def master_key() -> bytes:
    """Store the encryption key in the current user's Windows Credential Manager."""
    from keyring.backends.Windows import WinVaultKeyring
    vault = WinVaultKeyring()
    service, account = 'jarvis-windows-vault', 'master-key'
    value = vault.get_password(service, account)
    if value is not None:
        key = base64.b64decode(value, validate=True)
        if len(key) != 32:
            raise ValueError('Clé du coffre Windows invalide.')
        return key
    key = os.urandom(32)
    vault.set_password(service, account, base64.b64encode(key).decode('ascii'))
    return key
