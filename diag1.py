import json
from jarvis.core import JarvisCore
from jarvis.agents import AGENTS
from jarvis.tools.base import registry

core = JarvisCore()
print("=== CONNECTORS ===")
for c in core.connectors.list():
    print(f"- id={c['id']!r} type={c['type']!r} name={c['name']!r} enabled={c['enabled']} perms={c['permissions']} status={c['status']}")

print()
print("=== TOOLS for_agent('jarvis') ===")
tools = [t for t in registry.for_agent("jarvis") if t.enabled]
print(f"count={len(tools)}")
for t in tools:
    print(f"- {t.id:20s} connector_type={t.connector_type!r:8s} enabled={t.enabled}")

print()
print("=== OLLAMA SCHEMAS (ssh tools) ===")
schemas = [t.llm_schema() for t in tools]
for s in schemas:
    if 'ssh' in s['name'] or 'run' in s['name']:
        print(json.dumps(s, ensure_ascii=False, indent=1)[:2000])

print()
print("=== RESOLVE provider ===")
provider, model = core.llm.resolve("default")
print("provider:", provider.type if provider else None, "model:", model, "supports_tools:", provider.supports_tools if provider else None)
core.db.close() if hasattr(core.db, 'close') else None
