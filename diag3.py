import json
from jarvis.core import JarvisCore
core = JarvisCore()
user = "utilise la connexion ssh enregistré et exécute uptime"
conv_id = core.conversations.current_id()
print("conv_id:", conv_id)
result = core.orchestrator.handle(user, conversation_id=conv_id, source="test")
print("=== RESULT ===")
print(json.dumps(result, ensure_ascii=False, default=str)[:1500])
print()
print("=== LAST MESSAGES ===")
msgs = core.conversations.messages(conv_id, limit=6)
for m in msgs:
    print(f"[{m['role']}] {str(m.get('content'))[:400]}")
core.db.close() if hasattr(core.db, 'close') else None
