"""Bounded, auditable local missions. No fabricated logs, network, or shell execution."""
from __future__ import annotations
import copy, json, os, pathlib, re, subprocess, sys, threading, time, uuid, urllib.request, selectors, ast

BOT = '''import os
import discord
from discord import app_commands
from logic import ping_response

class VelkoBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.environ.get("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

client = VelkoBot()

@client.tree.command(name="ping", description="Vérifier que VELKO répond")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(ping_response(client.latency), ephemeral=True)

if __name__ == "__main__":
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN manquant : aucune connexion Discord effectuée.")
    client.run(token)
'''
LOGIC = '''def ping_response(latency):
    return f"Pong ! Latence : {max(0, round(latency * 1000))} ms."
'''
TESTS = '''import unittest
from logic import ping_response

class PingTests(unittest.TestCase):
    def test_round_trip_latency(self):
        self.assertEqual(ping_response(.042), "Pong ! Latence : 42 ms.")
    def test_negative_latency_is_clamped(self):
        self.assertEqual(ping_response(-1), "Pong ! Latence : 0 ms.")

if __name__ == "__main__":
    unittest.main()
'''
README = '''# Bot Discord VELKO

Ce projet contient un vrai bot discord.py et une commande slash `/ping`.
Les tests locaux couvrent la logique de réponse ; ils ne prouvent aucune connexion Discord.

## Connexion manuelle
1. Créer une application et un bot dans https://discord.com/developers/applications.
2. Ajouter ce bot à votre serveur avec les scopes `bot` et `applications.commands`.
3. Créer un environnement : `python3 -m venv .venv`.
4. Installer : `.venv/bin/python -m pip install -r requirements.txt`.
5. Fournir DISCORD_TOKEN dans l'environnement (jamais dans le code ou l'interface).
6. Fournir DISCORD_GUILD_ID si la commande doit être synchronisée sur un serveur précis.
7. Lancer : `.venv/bin/python bot.py`.
8. Tester `/ping` dans Discord et vérifier la réponse effective.

Aucune dépendance n'a été installée automatiquement. Aucun message n'a été envoyé.
Aucun test réseau ou Discord réel n'a été réalisé par les tests locaux.
'''

class WorkspaceBackend:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.tasks = {}

    def _emit(self, task, type, **data):
        with self.lock:
            event = dict(seq=len(task['events']) + 1, taskId=task['id'], type=type,
                         timestamp=time.time(), **data)
            task['events'].append(event)
            if type == 'action.started':
                task['currentAction'] = event
            elif type == 'action.completed' and (task.get('currentAction') or {}).get('actionId') == data.get('actionId'):
                task['currentAction'] = None
            return event

    def create(self, task):
        if not isinstance(task, str) or not task.strip() or len(task) > 8000:
            raise ValueError('Une mission de 1 à 8000 caractères est requise.')
        ident = uuid.uuid4().hex[:16]
        record = dict(id=ident, task=task.strip(), status='queued', events=[], result=None, currentAction=None,
                      startedAt=time.time(), directory=str(self.workspace / ident), files=[])
        (self.workspace / ident).mkdir()
        with self.lock:
            self.tasks[ident] = record
        self._emit(record, 'task.queued', kind='read', action='read', screen=1)
        return self.snapshot(ident)

    def run(self, ident):
        with self.lock:
            record = self._get(ident)
            if record['status'] != 'queued':
                return self.snapshot(ident)
            record['status'] = 'running'
            self._emit(record, 'task.started', kind='read', action='read', screen=1)
            threading.Thread(target=self._run, args=(record,), daemon=True).start()
        return self.snapshot(ident)

    def start(self, task):
        return self.run(self.create(task)['id'])

    def _get(self, ident):
        if not re.fullmatch(r'[a-f0-9]{16}', ident):
            raise ValueError('Identifiant invalide')
        with self.lock:
            if ident not in self.tasks:
                raise KeyError('Mission inconnue dans cette session')
            return self.tasks[ident]

    def snapshot(self, ident):
        with self.lock:
            record = copy.deepcopy(self._get(ident))
        record['files'] = self.files(ident)
        return record

    def _path(self, ident, path):
        self._get(ident)
        base = (self.workspace / ident).resolve()
        candidate = (base / path).resolve()
        if candidate == base or base not in candidate.parents or '\x00' in path:
            raise ValueError('Chemin hors de la mission')
        if candidate.name.startswith('.') or any(p.startswith('.') for p in pathlib.Path(path).parts):
            raise ValueError('Fichier privé non accessible')
        return candidate

    def files(self, ident):
        self._get(ident)
        base = self.workspace / ident
        return [dict(path=str(p.relative_to(base)), size=p.stat().st_size)
                for p in sorted(base.rglob('*')) if p.is_file() and not p.is_symlink()
                and '__pycache__' not in p.parts and not any(s.startswith('.') for s in p.relative_to(base).parts)]

    def read_file(self, ident, path):
        p = self._path(ident, path)
        if p.stat().st_size > 2_000_000:
            raise ValueError('Fichier trop volumineux')
        record = self._get(ident)
        content = p.read_text()
        self._emit(record, 'file.read', kind='read', action='read', screen=0, path=path)
        return dict(path=path, content=content)

    def write_file(self, ident, path, content):
        if not isinstance(content, str) or len(content.encode()) > 2_000_000:
            raise ValueError('Contenu trop volumineux')
        p = self._path(ident, path)
        if p.suffix not in {'.py', '.txt', '.md', '.json', '.js', '.html', '.css', '.yml', '.yaml'}:
            raise ValueError('Type de fichier non éditable')
        task = self._get(ident)
        aid = uuid.uuid4().hex[:12]
        self._emit(task, 'action.started', actionId=aid, kind='code', action='type', screen=0, path=path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            self._emit(task, 'file.changed', actionId=aid, kind='code', action='type', screen=0, path=path, content=content)
            self._emit(task, 'action.completed', actionId=aid, kind='code', action='type', screen=0, path=path, success=True)
        except Exception as error:
            self._emit(task, 'action.completed', actionId=aid, kind='code', action='type', screen=0, success=False, error=str(error))
            raise
        return dict(path=path, saved=True)

    def _command(self, task, args, cwd):
        aid = uuid.uuid4().hex[:12]
        self._emit(task, 'action.started', actionId=aid, kind='terminal', action='type', screen=1, command=args, cwd=str(cwd))
        try:
            env = os.environ.copy()
            env['PYTHONPYCACHEPREFIX'] = str(self.workspace / task['id'] / '__pycache__')
            proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            with self.lock:
                task['currentAction'] = dict(actionId=aid, kind='terminal', action='read', screen=1, command=args)
            selector=selectors.DefaultSelector(); selector.register(proc.stdout,selectors.EVENT_READ)
            deadline=time.monotonic()+45
            try:
                while selector.get_map():
                    if time.monotonic()>deadline:
                        proc.kill(); proc.wait(); raise subprocess.TimeoutExpired(args,45)
                    for key,_ in selector.select(.1):
                        chunk=os.read(key.fileobj.fileno(),8192)
                        if not chunk: selector.unregister(key.fileobj); continue
                        self._emit(task,'action.output',actionId=aid,kind='terminal',action='read',screen=1,stream='stdout+stderr',text=chunk.decode(errors='replace'))
                proc.wait(timeout=2)
            finally:
                selector.close(); proc.stdout.close()
                if proc.poll() is None: proc.kill(); proc.wait()
            self._emit(task, 'action.completed', actionId=aid, kind='terminal', action='read', screen=1, exitCode=proc.returncode, success=proc.returncode == 0)
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as error:
            self._emit(task, 'action.output', actionId=aid, kind='terminal', action='read', screen=1, stream='stderr', text=str(error))
            self._emit(task, 'action.completed', actionId=aid, kind='terminal', action='read', screen=1, success=False)
            return False

    def _finish(self, task, status, result):
        with self.lock:
            task['status'], task['result'], task['finishedAt'] = status, result, time.time()
            self._emit(task, 'task.' + status, kind='read', action='read', screen=2, result=result, status=status)
            (self.workspace / task['id'] / 'mission-result.json').write_text(json.dumps(task, ensure_ascii=False, indent=2))

    def _plan(self, task):
        """Stream actual loopback Ollama tokens into a visible artifact, without executing them."""
        model = os.environ.get('VELKO_OLLAMA_MODEL', 'qwen2.5-coder:7b')
        aid = uuid.uuid4().hex[:12]
        path = 'bot_generated.py'
        target = self._path(task['id'], path)
        self._emit(task, 'action.started', actionId=aid, kind='code', action='type', screen=0, path=path, model=model)
        content = '# Génération locale VELKO — modèle ' + model + '\n# Code produit pour cette mission, non connecté à Discord.\n\n'; generated = ''
        target.write_text(content)
        started = time.monotonic()
        try:
            payload = {'model': model, 'stream': True, 'prompt': 'Return ONLY valid Python source, no Markdown fences. Write one complete concise Discord bot using discord.py 2.x. Use DISCORD_TOKEN environment variable. Include slash /ping and features reasonably requested. Do not claim execution. No subprocess, no file deletion, no system commands. Maximum 100 lines. Task: ' + task['task'], 'options': {'num_predict': 1600, 'num_ctx': 2048}}
            request = urllib.request.Request('http://127.0.0.1:11434/api/generate', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=60) as response:
                for line in response:
                    if time.monotonic() - started > 90:
                        raise TimeoutError('Délai maximal de génération locale atteint')
                    data = json.loads(line)
                    if data.get('error'):
                        raise RuntimeError(data['error'])
                    chunk = data.get('response', '')
                    if chunk:
                        generated += chunk
                        clean = generated.removeprefix('```python').removeprefix('```').rstrip()
                        if clean.endswith('```'): clean=clean[:-3]
                        content = '# Génération locale VELKO — modèle ' + model + '\n\n' + clean
                        target.write_text(content)
                        self._emit(task, 'file.changed', actionId=aid, kind='code', action='type', screen=0, path=path, content=content)
                    if data.get('done'):
                        break
            ast.parse(content)
            self._emit(task, 'action.completed', actionId=aid, kind='code', action='read', screen=0, success=True)
            return True
        except Exception as error:
            content += '\n\n# Génération interrompue : ' + str(error).replace('\n',' ') + '\n# Le socle bot.py fourni séparément reste un modèle prédéfini.\n'
            target.write_text(content)
            self._emit(task, 'file.changed', actionId=aid, kind='code', action='read', screen=0, path=path, content=content)
            self._emit(task, 'action.completed', actionId=aid, kind='code', action='read', screen=0, success=False, error=str(error))

    def _run(self, task):
        try:
            text = task['task'].lower()
            if 'discord' in text and 'bot' in text:
                generated_ok = self._plan(task)
                for name, content in [('logic.py', LOGIC), ('bot.py', BOT), ('test_bot.py', TESTS), ('requirements.txt', 'discord.py>=2.4,<3\n'), ('README.md', README)]:
                    self.write_file(task['id'], name, content)
                directory = self.workspace / task['id']
                passed = self._command(task, [sys.executable, '-m', 'unittest', '-v', 'test_bot'], directory)
                compiled = self._command(task, [sys.executable, '-m', 'compileall', '-q', 'bot.py', 'logic.py'], directory)
                if not (passed and compiled):
                    self._finish(task, 'blocked', 'Échec des validations locales. Consultez les sorties réelles du terminal.')
                else:
                    dev_only = any(x in text for x in ('code seulement', 'sans connexion', 'développement seulement'))
                    self._finish(task, 'completed' if dev_only and generated_ok else 'blocked',
                        ('Code spécifique généré localement et syntaxe vérifiée. ' if generated_ok else 'Génération spécifique indisponible ou incomplète. ') + 'Socle /ping créé ; 2 tests unitaires locaux et compilation réussis. '
                        'Discord non connecté, dépendances non installées, aucun test sur serveur réel. '
                        + ('Livraison de code uniquement, selon la demande.' if dev_only else 'Mission bloquée : connecter le bot et vérifier /ping sur votre serveur pour valider la tâche.'))
            elif any(x in text for x in ('analyse projet', 'analyse le projet', 'vérifie velko', 'verifie velko', 'teste velko')):
                ok = self._command(task, [sys.executable, '-m', 'py_compile', str(self.root / 'server.py')], self.root)
                for file in sorted((self.root / 'runtime').glob('*.js')):
                    ok = self._command(task, ['node', '--check', str(file)], self.root) and ok
                self._finish(task, 'completed' if ok else 'blocked', 'Vérifications syntaxiques Python et JavaScript ' + ('réussies. Aucun test visuel ni intégration Discord réalisé.' if ok else 'échouées ; consulter le terminal.'))
            else:
                self._finish(task, 'blocked', 'Mission non prise en charge par ce worker local borné. Aucune action exécutée. Missions disponibles : développer un bot Discord ; vérifier VELKO. Aucun agent IA généraliste connecté.')
        except Exception as error:
            self._finish(task, 'blocked', 'Erreur réelle du worker : ' + str(error))
