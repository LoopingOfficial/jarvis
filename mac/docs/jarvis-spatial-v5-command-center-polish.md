# JARVIS Spatial V5 — Command Center polish

Présentation uniquement. Backend, agents, tools, sync, storage, avatar geometry et voice inchangés.

## Canonical graph

Le canvas Atlas (`Memory.render`) dessine `JarvisBrainData.nodes` / `.edges` :

- 87 concepts canoniques, 102 relations (mesuré sur le graphe actuel)
- self-links exclus, relations dédupliquées, degré recalculé
- `br_*` fusionnés n’apparaissent plus comme second point

Header, Library, search, inspector, recall et Atlas partagent la même identité.

## Timeline

**BRAIN ACTIVITY** — `created_at` / `updated_at` / evidence `verified_at` réellement exposés.

**SESSION ACTIVITY** — recalls de session (`persistant: false`). Disparaît au reload.

## Layout CHAT (avant → après)

| Zone | Avant | Après |
|---|---|---|
| Conversation | ~38%–95%, trop étroite | ~680–860 px centrée |
| Avatar | s=.34, dominant | s=.20, buste |
| Mini Brain | flottant | méta IDLE / RECALLING / AGENT |
| Command bar | 58vw, détachée | 48vw, ancre bas |
| Tâche | `TÂCHE OK task_xxx` + points cyan | `EN COURS` / `TERMINÉ` sans id |

## Task status

IDs `task_*` masqués hors `localStorage.JARVIS_DEV=1`. Étapes uniquement si `task.progress.plan` existe.

## Avatar

CHAT réduit. VOICE reste grand. HOME inchangé. Pas de retouche du modèle 3D.

## Mini Brain

Clic → `App.goto('memory')`. Recall actif → focus du premier concept.

## Responsive

Priorité conversation. Mini Brain / avatar masqués avant de rétrécir le chat (≤1100px). Timeline masquée ≤1500px.

## Performance

ObsidianBrain miniature (`width < 200`) : 1 frame / 3.

## Tests

`JarvisSelfTest` : canvas = canonique, pas de doublons/self-links, timeline, id tâche masqué, pas de « RAISONNEMENT ».

Console : `await JarvisSelfTest.run()`

## Captures

À juger en live 1920×1080 et 1366×768 : HOME, CHAT idle/processing/recalling/speaking, Atlas, Library.

## Limites

- Pas de GET `activity_trace` : pas d’historique serveur inventé.
- Étapes READ/COMPARE seulement si le backend les envoie.
- Le modèle 3D debug n’est pas corrigé ici (prévu V2.3).
