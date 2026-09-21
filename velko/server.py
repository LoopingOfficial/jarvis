#!/usr/bin/env python3
"""Local-only VELKO prototype. No cloud requests or arbitrary command execution."""
import http.server, json, pathlib, argparse, webbrowser, re
from urllib.parse import urlparse, parse_qs, unquote
from workspace_backend import WorkspaceBackend
from display_redaction import display_copy, MARKER
ROOT = pathlib.Path(__file__).resolve().parent
WORKER = WorkspaceBackend(ROOT)
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT),**kw)
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()
    def _json(self, data, status=200):
        body=json.dumps(display_copy(data), ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def _local(self):
        host=self.headers.get('Host','')
        allowed={'127.0.0.1:'+str(self.server.server_port),'localhost:'+str(self.server.server_port)}
        origin=self.headers.get('Origin')
        return host in allowed and (origin is None or origin in {'http://'+h for h in allowed})
    def _body(self):
        if not self.headers.get('Content-Type','').startswith('application/json'): raise ValueError('JSON requis')
        length=int(self.headers.get('Content-Length',0))
        if length<=0 or length>2_100_000: raise ValueError('Taille de requête invalide')
        return json.loads(self.rfile.read(length))
    def do_GET(self):
        if not self._local(): self._json({'error':'Origine non autorisée'},403); return
        parsed=urlparse(self.path); path=parsed.path
        if path.startswith('/workspace/') or any(p.startswith('.') for p in unquote(path).split('/') if p):
            self.send_error(403); return
        try:
            if path=='/api/status':
                self._json({'backend':'local-worker','mode':'real','screens':'automatic-workspace','supported':['discord-bot','velko-check'],'generalAgent':False}); return
            if path=='/api/missions/current':
                with WORKER.lock: ident=next(reversed(WORKER.tasks),None)
                self._json(WORKER.snapshot(ident) if ident else {'id':None,'status':'idle','events':[],'files':[]}); return
            match=re.fullmatch(r'/api/missions/([a-f0-9]{16})(?:/(files|file))?',path)
            if match:
                ident,kind=match.groups()
                if kind=='file': result=WORKER.read_file(ident,parse_qs(parsed.query).get('path',[''])[0])
                elif kind=='files': result=WORKER.files(ident)
                else: result=WORKER.snapshot(ident)
                self._json(result); return
            if path.startswith('/api/'): self._json({'error':'Route inconnue'},404); return
            super().do_GET()
        except (KeyError,FileNotFoundError): self._json({'error':'Mission ou fichier introuvable'},404)
        except (ValueError,OSError) as error: self._json({'error':str(error)},400)
    def do_POST(self):
        if not self._local(): self._json({'error':'Origine non autorisée'},403); return
        path=urlparse(self.path).path
        if path=='/api/missions' or re.fullmatch(r'/api/missions/[a-f0-9]{16}/(run|file)',path):
            try:
                data=self._body()
                if path=='/api/missions':
                    with WORKER.lock:
                        if any(t['status'] in ('queued','running') for t in WORKER.tasks.values()):
                            self._json({'error':'Une mission est déjà en cours'},409); return
                    result=WORKER.create(data.get('task',''))
                else:
                    ident,kind=path.split('/')[3:5]
                    if kind=='file' and MARKER in data.get('content',''): raise ValueError('Vue masquée non enregistrable : les secrets sur disque sont préservés.')
                    result=WORKER.run(ident) if kind=='run' else WORKER.write_file(ident,data.get('path',''),data.get('content',''))
                self._json(result); return
            except KeyError: self._json({'error':'Mission introuvable'},404); return
            except (ValueError,OSError) as error: self._json({'error':str(error)},400); return
        if self.path != '/api/export': self._json({'error':'Route inconnue'},404); return
        n=int(self.headers.get('Content-Length',0))
        if n>100_000_000: self.send_error(413); return
        try:
            data=json.loads(self.rfile.read(n)); (ROOT/'exports').mkdir(exist_ok=True)
            (ROOT/'exports'/'scene.json').write_text(json.dumps(data))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        except Exception as e: self.send_error(400,str(e))
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=8766); p.add_argument('--no-open',action='store_true'); a=p.parse_args()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    print('VELKO: http://127.0.0.1:%s'%a.port,flush=True)
    if not a.no_open: webbrowser.open('http://127.0.0.1:%s'%a.port)
    server.serve_forever()
