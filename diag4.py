import json
from jarvis.core import JarvisCore
core = JarvisCore()
conn = core.connectors.raw("ssh")
print("connector raw:", {k: v for k, v in conn.items() if k not in ("config",)})
print("config keys:", list(conn.get("config", {}).keys()))
print("secret fields configured:", core.connectors.get("ssh").get("secret_fields"))
result = core.runner.run("ssh.run", {"command": "uptime", "connector_id": "ssh"}, conversation_id="x")
print(json.dumps(result.to_dict(), ensure_ascii=False, default=str)[:2000])
core.db.close() if hasattr(core.db, 'close') else None
