"""JARVIS Core — plateforme d'assistant personnel locale.

Architecture (voir docs/ARCHITECTURE.md) :

    JARVIS CORE (orchestrator)
    ├── Planner / boucle agentique (llm/)
    ├── Tool Router + Secure Tool Runner (tools/)
    ├── Agent Router (agents.py)
    ├── Memory Manager (memory.py)
    ├── Connector Manager + Secret Vault (connectors.py, secrets.py)
    ├── Permission Manager (permissions.py)
    ├── Task Manager (tasks.py)
    ├── Automations / Scheduler (automations.py)
    └── Event Bus (events.py) → SSE (server.py)
"""

__version__ = "3.0.0"
