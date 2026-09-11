from jarvis.core import JarvisCore
core = JarvisCore()
items = core.memory.knowledge_list(limit=50)
for it in items:
    print("="*80)
    print("id:", it.get("id"))
    print("title:", repr(it.get("title")))
    print("status:", it.get("status"), "| validations:", it.get("validation_count"))
    print("content:", repr((it.get("content") or "")[:300]))
core.db.close() if hasattr(core.db,"close") else None
