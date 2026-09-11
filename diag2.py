import json
from jarvis.core import JarvisCore
from jarvis.llm import base as llm_base
import jarvis.llm.providers as prov

captured = {}
orig = prov.http_json
def spy(url, *, method="GET", headers=None, body=None, verify_ssl=True, timeout=20.0):
    if "/api/chat" in url:
        captured['url'] = url
        captured['body'] = body
    return orig(url, method=method, headers=headers, body=body, verify_ssl=verify_ssl, timeout=timeout)
prov.http_json = spy
llm_base  # noqa

core = JarvisCore()
from jarvis.agents import AGENTS
spec = AGENTS["jarvis"]
from jarvis.tools.base import registry
tools = [t for t in registry.for_agent("jarvis") if t.enabled]
schemas = [t.llm_schema() for t in tools]

# build messages the same way the orchestrator does
conv_id = core.conversations.current_id()
user = "utilise la connexion ssh enregistré et exécute uptime"
core.conversations.add_message(conv_id, "user", user)

def build():
    from jarvis.orchestrator import Orchestrator
    orch = Orchestrator(core)
    return orch._build_messages(spec, user, conv_id, tools)

messages = build()
resp = core.llm.chat(messages, role="default", tools=schemas, temperature=0.3)
print("=== RESPONSE ===")
print("ok:", resp.ok)
print("error:", resp.error)
print("text:", (resp.text or "")[:2000])
print("tool_calls:", resp.tool_calls)
if resp.raw:
    print("RAW:", json.dumps(resp.raw, ensure_ascii=False)[:3000])
print()
print("=== CAPTURED BODY (tools count) ===")
if 'body' in captured:
    b = captured['body']
    print("model:", b.get("model"))
    print("stream:", b.get("stream"))
    print("tools count:", len(b.get("tools", [])))
    tool_names = [t.get("function", {}).get("name") for t in b.get("tools", [])]
    print("tool names:", tool_names)
    print("messages roles:", [m.get("role") for m in b.get("messages", [])])
    print("messages:", json.dumps(b.get("messages", []), ensure_ascii=False)[:2000])
else:
    print("NO /api/chat call captured!")
core.db.close() if hasattr(core.db, 'close') else None
