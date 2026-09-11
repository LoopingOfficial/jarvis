"""End-to-end test for the code.file.opened event chain."""
import sys, os, tempfile, json, time
from pathlib import Path

# 1. Redirect data dir BEFORE any imports
tmp = Path(tempfile.mkdtemp(prefix="jarvis_test_"))
sys.path.insert(0, r"C:\Users\jerom\Desktop\jarvis-windows\jarvis-windows")
import jarvis.config as cfg
cfg.DATA_DIR = tmp
cfg.DB_PATH = tmp / "jarvis.db"
# Patch before avatar_reference sees it
import jarvis.avatar_reference
jarvis.avatar_reference.REFS_DIR = tmp / "avatar_references"
jarvis.avatar_reference.REFS_DIR.mkdir(exist_ok=True)

# 2. Create JARVISCore
from jarvis.core import JarvisCore
core = JarvisCore()

# 3. Record events from this point
captured_events = []
original_emit = core.events.emit
def capture_emit(event_type, payload=None, *, persist=False):
    evt = original_emit(event_type, payload, persist=persist)
    captured_events.append({"type": event_type, "data": payload or {}})
    return evt
core.events.emit = capture_emit

# 4. Create a fake SSH connector
connector_id = "test_ssh_" + str(int(time.time()))
core.db.execute(
    "INSERT INTO connectors(id, name, type, config, status, enabled, permissions, created_at, updated_at) "
    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (connector_id, "Test Server", "ssh",
     json.dumps({"host": "test.local", "username": "root", "working_directory": "/var/www/html"}),
     "connected", 1, '["read","write"]', time.time(), time.time())
)
core.connectors._cache = None

# 5. Patch ssh_exec
FAKE_CONTENT = '<?php\necho "Hello World";\n// index page\n?>'
import jarvis.tools.remote_tools as rt
original_ssh_exec = rt.ssh_exec
def fake_ssh_exec(config, secrets, cmd, timeout=30):
    return True, FAKE_CONTENT
rt.ssh_exec = fake_ssh_exec

# 6. Run file_router
conv_id = core.conversations.create("test")["id"]
print(f"\n=== Running file_router.execute('affiche moi index.php') ===")
result = core.orchestrator.file_router.execute("affiche moi index.php", conv_id)
print(f"result ok: {result['ok']}")
print(f"response: {result.get('response', '')[:100]}")

# 7. Check events
print(f"\n=== Events captured: {len(captured_events)} ===")
code_opened = [e for e in captured_events if e["type"] == "code.file.opened"]
print(f"code.file.opened events: {len(code_opened)}")
for e in code_opened:
    d = e["data"]
    print(f"  source={d.get('source')}  connector_id={d.get('connector_id')}  path={d.get('path')}")
    print(f"  content_length={len(d.get('content') or '')}  language={d.get('language')}")
    print(f"  content_preview: {(d.get('content') or '')[:80]!r}")

tool_events = [e for e in captured_events if e["type"].startswith("tool.")]
print(f"\ntool events: {len(tool_events)}")
for e in tool_events:
    print(f"  {e['type']}: tool={e['data'].get('tool')} ok={e['data'].get('ok')}")

# 8. Frontend simulation
print("\n=== Frontend simulation ===")
if code_opened:
    d = code_opened[0]["data"]
    key = d["source"] + "::" + d["path"]
    ext = d["path"].rsplit(".", 1)[-1].lower() if "." in d["path"] else ""
    uri = f"monaco://ssh/{d.get('connector_id') or '_'}/{d['path']}"
    print(f"  tab_key = {key}")
    print(f"  detected ext = {ext}")
    print(f"  monaco_uri = {uri}")
    print(f"  language for monaco = {d.get('language', ext)}")
    
    # Verify openRemoteFile would find the path
    path = d.get("path")
    if not path:
        print("  ERROR: path is empty! openRemoteFile would return early!")
    elif not d.get("source"):
        print("  ERROR: source is empty!")
    else:
        print("  OK: openRemoteFile would create model and open tab")
else:
    print("FAIL: NO code.file.opened event found!")

# Cleanup
rt.ssh_exec = original_ssh_exec
core.events.emit = original_emit
print("\n=== DONE ===")
