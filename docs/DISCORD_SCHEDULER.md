# Tâches Discord planifiées

Exécution automatique d'outils Discord à intervalle régulier : message d'état
toutes les 30 minutes, annonce quotidienne à 09h00, purge hebdomadaire d'un
salon de spam…

| Élément | Emplacement |
| --- | --- |
| Moteur de planification | [`jarvis/discord_scheduler.py`](../jarvis/discord_scheduler.py) |
| Outils exposés aux agents | [`jarvis/tools/discord_tools.py`](../jarvis/tools/discord_tools.py) (section G) |
| API HTTP | [`jarvis/server.py`](../jarvis/server.py) — `/api/discord/schedules` |
| Tables | `discord_schedules`, `discord_schedule_runs` ([`jarvis/db.py`](../jarvis/db.py)) |
| Exemple d'initialisation | [`tools/discord_schedule_examples.py`](../tools/discord_schedule_examples.py) |
| Tests | [`tests/test_discord_scheduler.py`](../tests/test_discord_scheduler.py) |

## Cycle de vie

Le planificateur est construit avec le cœur (`core.discord_scheduler`) et sa
boucle démarre avec les autres services de fond (`core.start_background()`),
en même temps que le serveur JARVIS. C'est un thread démon qui se réveille
toutes les 15 secondes (`settings.discord.scheduler_tick_s`), relit les tâches
`ACTIVE` échues et déclenche chacune dans son propre thread. Une tâche dont
l'exécution précédente n'est pas terminée n'est pas relancée en parallèle.

Le moteur Discord, lui, reste créé à la demande : planifier une tâche
n'oblige pas à connecter le bot. Si le bot est hors ligne à l'échéance,
l'outil répond « bot non connecté », l'échec est journalisé, et la tâche
reprend à l'échéance suivante.

## Champs d'une tâche

| Champ | Description |
| --- | --- |
| `id` | Identifiant unique (`dsch_…`), généré à la création. |
| `name` | Nom de l'action (« Annonce matinale »). Obligatoire : c'est ce qu'on relit six mois plus tard. |
| `interval` | `« 30m »`, `« 2h »`, `« 09:00 »`, `« 0 9 * * * »`, ou un déclencheur structuré. Plancher : une minute. |
| `target_channel_id` | Salon Discord concerné, injecté dans l'argument attendu par l'outil. |
| `tool_to_call` | Outil `discord.*` à déclencher (`discord.send_message`, `discord.send_embed`, `discord.purge`…). Les noms plats (`discord_send_message`) sont acceptés. |
| `params` | Paramètres passés à l'outil (contenu du message, nombre de messages à purger…). |
| `status` | `ACTIVE` ou `PAUSED`. |
| `allow_destructive` | Accord explicite, requis pour planifier une action irréversible. |

Champs calculés, en lecture seule : `next_run_at` / `next_run_iso`,
`last_run_at`, `last_status`, `last_output`, `run_count`, `failures`.

## Sécurité

* **Périmètre fermé.** Seuls les outils `discord.*` déjà enregistrés sont
  planifiables : une tâche ne peut pas devenir un vecteur d'exécution
  arbitraire (`system.shell` est refusé à la création).
* **Confirmation déplacée, pas supprimée.** Une action irréversible (purge)
  demanderait normalement une confirmation à chaque appel — impossible à 3 h du
  matin. Elle exige donc `allow_destructive=True` **à la création**, accord
  consigné dans l'audit ; la modification d'une tâche ne peut pas l'activer en
  silence. La création d'une tâche passe elle-même par une confirmation
  (`confirmation_policy="always"` sur `discord.schedule_add`).
* **Mentions de masse neutralisées.** `discord.send_message` et
  `discord.send_embed` désactivent `@everyone` / `@here` : une tâche récurrente
  qui pingue tout le serveur par erreur ne se rattrape pas.
* **Audit.** Chaque déclenchement écrit une ligne dans `audit_log`
  (horodatage, identifiant de tâche, outil, SUCCÈS/ÉCHEC, durée, réponse du
  bot) et une ligne dans `discord_schedule_runs`. Les secrets sont masqués par
  le coffre avant écriture.
* **Robustesse.** Aucune exception ne remonte à la boucle : une panne de l'API
  Discord est capturée, comptée et journalisée. Au-delà de cinq échecs
  consécutifs la tâche passe en `PAUSED` — marteler une API en vrac noie
  l'audit sans rien résoudre. Un succès remet le compteur à zéro.

## Pilotage par l'agent (outils)

| Outil | Rôle |
| --- | --- |
| `discord.schedule_add` | Ajoute une tâche planifiée. |
| `discord.schedule_list` | Liste les tâches, leur statut et leur prochaine exécution. |
| `discord.schedule_delete` | Supprime (`mode: delete`), suspend (`pause`) ou réactive (`resume`). |

En Python, les mêmes opérations portent aussi les noms de l'API métier :
`add_scheduled_task`, `get_scheduled_tasks`, `delete_scheduled_task`.

## API HTTP

```
GET    /api/discord/schedules              liste + 30 derniers déclenchements
POST   /api/discord/schedules              création
PUT    /api/discord/schedules/<id>         modification
DELETE /api/discord/schedules/<id>         suppression
POST   /api/discord/schedules/<id>/status  {"status": "ACTIVE" | "PAUSED"}
POST   /api/discord/schedules/<id>/run     exécution immédiate (test)
```

## Exemple

```bash
python tools/discord_schedule_examples.py --channel-status 123456789 --channel-news 987654321
```

Installe deux tâches :

1. **Vérification d'état** — toutes les 30 minutes, un embed `SUCCESS` dans le
   salon de supervision (`discord.send_embed`).
2. **Annonce matinale** — tous les jours à 09h00 (`0 9 * * *`), un message dans
   le salon d'annonces (`discord.send_message`).

Le script est idempotent : relancé, il met à jour les tâches du même nom au
lieu de les dupliquer. La fonction `seed_examples(core, …)` fait la même chose
depuis du code JARVIS.
