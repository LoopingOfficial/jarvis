# Architecture JARVIS 3.0

## Vue d'ensemble

```
Navigateur (ui/)                       Processus Python (jarvis/)
┌──────────────────────────┐           ┌──────────────────────────────────────┐
│ AudioManager (unique)    │  REST     │ server.py — API + SSE + statique     │
│  STT · VAD · wake · TTS  │◄─────────►│                                      │
│ Command Center + pages   │  SSE      │ core.py — JarvisCore                 │
└──────────────────────────┘           │  ├── orchestrator.py  (boucle agent) │
                                       │  ├── agents.py        (6 agents)     │
                                       │  ├── tools/           (51 outils)    │
                                       │  │    └── runner.py   (Secure Runner)│
                                       │  ├── connectors.py    (25 types)     │
                                       │  ├── secrets.py       (coffre AES)   │
                                       │  ├── permissions.py   (risque)       │
                                       │  ├── memory.py        (5 portées)    │
                                       │  ├── tasks.py         (persistées)   │
                                       │  ├── automations.py   (scheduler)    │
                                       │  ├── llm/             (6 providers)  │
                                       │  ├── voice.py         (FSM + session)│
                                       │  ├── monitor.py       (métriques)    │
                                       │  ├── events.py        (bus + feed)   │
                                       │  └── audit.py         (journal)      │
                                       └──────────────┬───────────────────────┘
                                                      │
                                              SQLite (WAL) : data/jarvis.db
```

## Chaîne d'exécution d'une demande

```
Utilisateur (voix ou texte)
   ↓
Orchestrator.handle()
   ↓  raccourci déterministe ? (statut, briefing, tâches, agenda…) → outil direct
   ↓  sinon : création d'une Task suivie
Contexte : conversation + mémoire pertinente + connecteurs + état machine
   ↓
LLM (rôle « default », outils exposés en function-calling)
   ↓
Tool Router → Secure Tool Runner
   ├── résolution du connecteur (connector_id)
   ├── niveau de risque (statique ou calculé sur les arguments)
   ├── permissions du connecteur
   ├── confirmation si sensible/destructif  ──► l'utilisateur tranche
   ├── DÉCHIFFREMENT DU SECRET (ici seulement)
   └── appel du service externe
   ↓
Résultat filtré (secrets masqués) → renvoyé au modèle
   ↓  (boucle jusqu'à 12 itérations)
Réponse finale → conversation + mémoire + TTS
```

## Le secret ne remonte jamais

```
LLM  ──"connector_id: prod"──►  Tool Router
                                    │
                              Secure Tool Runner
                                    │  vault.get(prod, "password")
                              Secret Vault (AES-256-GCM)
                                    │
                              Service externe (SSH, API…)
```

Le modèle reçoit uniquement la sortie de l'outil, elle-même passée par
`vault.scrub()`. Les arguments journalisés masquent `password`, `token`,
`api_key`, `secret`, `private_key`.

## Machine à états vocale

```
        ┌──────────────────────────────────────────┐
        ▼                                          │
     IDLE ──► WAKE ──► LISTENING ──► PROCESSING ──► EXECUTING
        ▲       │           │             │             │
        │       └───────────┴─────────────┴─────────────┤
        │                                               ▼
        └────────────── INTERRUPTED ◄───────────────  SPEAKING
```

Transitions vérifiées des deux côtés (`jarvis/voice.py` et `ui/js/voice.js`).
Une transition refusée est comptée et diffusée (`voice.error`), jamais silencieuse.

### Politique de greeting

Le serveur est l'autorité unique. `VoiceSessionManager.should_greet()` fait un
`UPDATE … WHERE greeted=0` atomique : même avec plusieurs onglets, un seul
appel obtient `speak:true`. La valeur `once_per_session` est verrouillée dans
`config.LOCKED_SETTINGS` — l'API refuse toute autre valeur.

Aucun de ces événements n'appelle l'endpoint : reconnexion SSE, heartbeat,
redémarrage STT, timeout VAD, fin de TTS, changement d'état, rechargement de
page tant que la session n'a pas expiré.

## Modèle de données (SQLite)

| Table | Rôle |
|---|---|
| `settings` | Réglages par section |
| `connectors` / `secrets` | Config non sensible / valeurs chiffrées (séparées) |
| `memories` + `memories_fts` | Mémoire par portée, recherche plein texte + vectorielle |
| `knowledge` + `knowledge_fts` | Base de connaissances |
| `conversations` / `messages` | Historique complet |
| `tasks` / `task_logs` | Tâches et traces d'exécution |
| `workflows` / `workflow_runs` | Automatisations et historique |
| `calendar_events` | Agenda local et importé |
| `agents_state` | État runtime des agents |
| `sessions` | Sessions utilisateur — **support du verrou de greeting** |
| `audit_log` | Journal d'audit |
| `feed` / `events` | Live Intelligence Feed et historique d'événements |

Migrations additives uniquement (`ALTER TABLE ADD COLUMN`) : aucune donnée
n'est jamais supprimée par une montée de version. `Database.backup()` produit
une copie cohérente à chaud.

## Niveaux de risque

| Niveau | Exemple | Confirmation |
|---|---|---|
| `read_only` | `ls`, `df -h`, `git status`, lecture de logs | jamais |
| `safe_write` | écrire un fichier (avec `.bak`), lancer un workflow | jamais |
| `sensitive` | `systemctl restart`, `git push`, déploiement, envoi d'email | oui |
| `destructive` | `rm -rf`, `DROP DATABASE`, `push --force`, suppression | oui |

Le niveau est recalculé **sur les arguments réels** : `terminal.run` est
`read_only` pour `ls`, `destructive` pour `rm -rf`.

## Événements temps réel (SSE)

`agent.*`, `task.*`, `tool.*`, `connector.*`, `voice.*`, `memory.*`,
`conversation.*`, `workflow.*`, `system.metrics`, `system.warning`,
`llm.status`, `feed.new`, `settings.updated`, `calendar.updated`.

Le keep-alive est un **commentaire SSE** (`: keepalive`), pas un événement
applicatif : il ne peut donc rien déclencher côté client.

## Performance

Aucune sonde réseau dans le chemin d'une requête : la vérification de
connectivité et l'état des fournisseurs de modèles tournent en arrière-plan et
alimentent un cache. `/api/status` répond en moins de 10 ms même hors ligne.
Tant qu'aucune sonde n'a abouti, l'état affiché est « Vérification… » —
jamais « Connected ».

## Extensibilité

Ajouter un outil :

```python
from jarvis.tools.base import ToolContext, ToolResult, registry
from jarvis.permissions import SAFE_WRITE

def _handler(ctx: ToolContext) -> ToolResult:
    token = ctx.secret("api_key")          # déchiffré ici uniquement
    ...
    return ToolResult(True, "Fait.")

registry.add(
    id="monservice.action", name="Mon service", category="Automatisation",
    description="Ce que fait l'outil (lu par le modèle).",
    handler=_handler, connector_type="http_api", risk=SAFE_WRITE,
    permissions=("write",),
    input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
)
```

Ajouter un type de connecteur : une entrée `ConnectorType` dans
`connectors.py` (champs, permissions par défaut) et une branche de test dans
`_run_test`. L'interface s'adapte automatiquement.

Ajouter un fournisseur de modèles : une sous-classe de `LLMProvider`
(`chat`, `models`, `embed` optionnel) enregistrée dans `PROVIDER_CLASSES`.
