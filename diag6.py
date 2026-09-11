"""Diag runtime: trace exacte du pipeline de résolution pour blog.php sans LLM."""
import json, sys, os, io
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\jerom\Desktop\jarvis-windows\jarvis-windows")

from jarvis.core import JarvisCore
from jarvis.file_router import FileCommandRouter
from jarvis.intents import resolve_connector_intent
from jarvis.remote_paths import resolveRemotePath
from jarvis import __version__

core = JarvisCore()

print("=== BUILD INFO ===")
print(f"jarvis version: {__version__}")

print()
print("=== 1. CONNECTOR REGISTRY: ConnectorRegistry.get('ssh') ===")
conn = core.connectors.raw("ssh")
print(json.dumps({
    "id": conn["id"], "type": conn["type"], "name": conn["name"],
    "working_directory": (conn.get("config") or {}).get("working_directory"),
    "deployment_path": (conn.get("config") or {}).get("deployment_path"),
    "remote_path": (conn.get("config") or {}).get("remote_path"),
    "enabled": conn.get("enabled"), "status": conn.get("status"),
}, indent=2, ensure_ascii=False))

print()
print("=== 2. ACTIVE SSH CONNECTORS (status==connected) ===")
active_ssh = core.connectors.active("ssh")
print(f"count: {len(active_ssh)}")
for a in active_ssh:
    print(json.dumps(a, indent=2, ensure_ascii=False, default=str)[:500])

print()
print("=== 3. FILE SCOPE SETTING ===")
try:
    print("files.default_scope =", repr(core.settings.get("files", "default_scope", "auto")))
except Exception as e:
    print("files setting error:", e)

print()
print("=== 4. TEST resolveRemotePath(connector='ssh', requestedPath='blog.php') ===")
test_cases = ["blog.php", "forums.php", "marketplace.php", "includes/config.php"]
for tc in test_cases:
    try:
        resolved = resolveRemotePath(conn, tc, {})
        print(f"{tc} -> {resolved}")
    except Exception as e:
        print(f"{tc} -> ERROR: {e}")

print()
print("=== 5. FileCommandRouter direct test ===")
router = FileCommandRouter(core)
for msg in ["Affiche moi le contenu de blog.php",
            "affiche blog.php",
            "ouvre blog.php",
            "lis blog.php",
            "donne moi le contenu de blog.php",
            "montre blog.php",
            "Affiche forums.php",
            "Affiche marketplace.php",
            "Affiche includes/config.php"]:
    try:
        result = router.execute(msg, "diag-check")
        if result is None:
            print(f"'{msg}' -> ROUTER RETURNED None (fallback to LLM!)")
        else:
            print(f"'{msg}' -> MATCHED! ok={result.get('ok')} path={result.get('path')} tools={result.get('tools_used')}")
    except Exception as e:
        print(f"'{msg}' -> EXCEPTION: {type(e).__name__}: {e}")

print()
print("=== 6. resolve_connector_intent direct test ===")
intent_text = "via ssh Affiche moi le contenu de blog.php"
intent = resolve_connector_intent(core, intent_text)
if intent:
    print(f"tool_id={intent.tool_id}")
    print(f"connector_id={intent.connector_id}")
    print(f"executable={intent.executable}")
    print(f"arguments={json.dumps(intent.arguments, ensure_ascii=False)}")
    if intent.arguments.get("path"):
        resolved = resolveRemotePath(conn, intent.arguments["path"], core.active_task_context)
        print(f"resolved path = {resolved}")
else:
    print("NO INTENT DETECTED")

print()
print("=== 7. ACTIVE TASK CONTEXT ===")
print(json.dumps(core.active_task_context, indent=2, ensure_ascii=False, default=str))

core.db.close()
print()
print("=== DONE ===")