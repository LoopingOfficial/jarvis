# JARVIS Spatial OS V5 — Validation Phase 3

Document de validation produit le 14/09/2026 sur l'application réelle
(`http://127.0.0.1:8765`, build `JARVIS_SPATIAL_OS_V5_PHASE_3`).

Règle appliquée dans tout ce document : **rien n'est déclaré testé qui ne l'a pas
été réellement**. Les points non validés sont listés en fin de document.

---

## 1. Architecture réelle — REAL / DERIVED / SIMULATED / MOCK

Vérification faite en confrontant, à l'exécution, chaque valeur affichée à la
réponse de l'API correspondante (`/api/status`, `/api/agents`, `/api/tools`,
`/api/brain`).

### REAL — données obtenues du backend

| Élément affiché | Source | Valeur UI / Valeur API au moment du test |
|---|---|---|
| CPU (HUD) | `/api/status` → `metrics.cpu.percent` | 36 % / 29 % (écart = rafraîchissement 8 s, donnée horodatée) |
| RAM (HUD) | `metrics.memory.percent` | 95 % / 95 % |
| Modèle actif (HUD) | `llm[].connected → default_model` | `jarvis-coder-windows` / identique |
| Outils (HUD + satellite) | `tools.enabled` | 95 / 95 sur 95 |
| Opérateur (salutation) | `status.user_name` | Jérôme / Jérôme |
| Santé système (sous-titre) | `core.system.status` | « Système dégradé » (RAM 95 %) |
| Agents (satellite + constellation) | `/api/agents` | 8 / 8 |
| État de chaque agent | `agents[].status`, `current_action`, `enabled`, `runs`, `last_error` | lu à chaque rendu et à chaque événement `agent.*` |
| Savoirs (satellite) | `status.memory.knowledge` | 25 / 25 |
| Conversations (satellite) | `status.memory.conversations` | 15 / 15 |
| SSH / Ollama (satellites) | `connectors.items[]`, `llm[]` | état réel de connexion |
| Graphe mémoire | `/api/brain` | 108 nœuds, 131 relations / identique |
| Familles / clusters mémoire | `nodes[].family` | 7 familles réelles (JARVIS, ERRORS, TOOLS, SOLUTIONS, KNOWLEDGE, SERVERS, WORKFLOWS) |
| Catalogue d'outils | `/api/tools` | 95 outils, 22 catégories, connecteur et niveau de risque réels |
| Réglages | `/api/settings` via `Settings` hérité | toutes les sections d'origine |
| Étapes de synchronisation | `SyncFeedback.setStep()` réel | BACKUP / HASH / WRITE / VERIFY / REFRESH |
| Plan de synchronisation (dry run) | `POST /api/brainrot-sync/refresh` | `entries[]`, `counts`, `plan_hash` |
| États du Brain | SSE `jarvis.state`, `tool.*`, `agent.*`, `memory.*`, `brain.*`, `voice.*`, `image.generation.*` | voir matrice §3 |
| Niveau audio (pulsation + lipsync) | `App.robotAudioLevel` (analyser WebAudio du TTS) | réel |

### DERIVED — calculé à partir de données réelles

- Classement d'un outil en zone du Brain (SSH → TOOLS, Sheet → KNOWLEDGE, etc.) :
  déduit du `tool_id` réel reçu.
- Compteurs CREATE / UPDATE / DELETE du dry run : comptés sur les `entries[]` du
  plan serveur (`action`).
- « Système dégradé / en alerte » : dérivé de `core.system.status`.
- Phase affichée dans le chat (RAISONNEMENT, LECTURE MÉMOIRE, DÉLÉGATION…) :
  dérivée de l'état du Brain, lui-même issu des événements réels.
- Chemin éclairé dans le graphe mémoire lors d'un rappel : nœuds dont le label
  correspond à la requête réelle de `brain.search` / `brain.path`, plus un saut
  de voisinage.
- Compteur « actifs » de la constellation : agents dont `status === 'active'` ou
  qui portent un `current_action`.

### SIMULATED — purement visuel, sans prétention d'activité

Ces éléments existent pour la profondeur et la lisibilité de la scène. Aucun ne
prétend représenter un travail en cours :

- champ d'étoiles et parallax de la scène ;
- respiration lente du Brain au repos et quelques impulsions internes en IDLE
  (signature « vivant », pas « occupé ») ;
- ligne d'horizon, grain, halos ;
- rotation lente de la constellation d'agents (les liens restent en pointillés
  tant qu'aucun agent ne travaille) ;
- micro-mouvements de l'avatar au repos (respiration, clignements) — produits par
  le moteur avatar existant.

### MOCK — valeurs codées en dur

**Aucune dans l'interface livrée.** Vérifications :

- `grep` sur les cinq modules V5 : aucun nombre littéral n'est affiché
  (`textContent = "<nombre>"` ou `>NN<`) ;
- les seuls appels de données sont `/api/agents`, `/api/brain`, `/api/tools`,
  `/api/status`, `/api/brainrot-sync/refresh` ;
- **corrigé pendant cette phase** : la case `GPU` du HUD affichait `—` en
  permanence alors que le backend ne publie aucune métrique GPU
  (`'gpu' in status === false`). La case est désormais masquée tant que la
  donnée n'existe pas, au lieu d'occuper un emplacement fantôme.
- Le jeu de données utilisé pour valider le rendu du **diff** (§4) est un plan de
  test injecté localement dans la page, jamais affiché en production et jamais
  envoyé au serveur. Il est identifié comme tel ci-dessous.

---

## 2. Matrice événement → état du Brain

Mapping implémenté dans `ui/js/v5/spatial_events.js`. Colonne « observé » =
constaté en exécution réelle.

| Situation réelle | Événement backend | État Brain | Observé |
|---|---|---|---|
| attente | — | `IDLE` | oui |
| écoute micro | `voice.local: LISTENING/WAKE` | `LISTENING` | non rejoué en Phase 3 |
| compréhension | `voice.local: PROCESSING` | `THINKING` | oui |
| recherche mémoire | `brain.search`, `brain.path`, `memory.*` | `RECALLING` | oui |
| raisonnement | `jarvis.state: THINKING` | `THINKING` | oui |
| agent appelé | `agent.started` | `DELEGATING` | oui |
| outil utilisé | `tool.started` | `READING` / `CODING` / `SEARCHING` / `GENERATING` selon l'outil réel | oui |
| réponse vocale | `jarvis.state: SPEAKING`, `voice.*` | `SPEAKING` | oui |
| erreur | `tool.completed{ok:false}`, `agent.failed` | `ERROR` | non rejoué en Phase 3 |
| synchronisation | `SyncFeedback.open/setStep` | `SYNCING` | oui |

Séquence complète observée sur une commande réelle (« dis bonjour ») :
`THINKING → CODING → RECALLING → SUCCESS → SPEAKING → IDLE`, avatar synchronisé
(`THINKING → SPEAKING → IDLE`).

Aucune animation d'état n'est déclenchée sans événement : les zones s'allument
sur `activateZone` et s'éteignent sur l'événement de fin correspondant.

---

## 3. Bugs trouvés et corrigés (Phase 3)

| Bug | Cause | Correction |
|---|---|---|
| Case GPU vide en permanence dans le HUD | le backend ne publie pas de métrique GPU ; l'UI affichait un `—` figé | la case est masquée quand la donnée est absente (`spatial_shell.js`) |
| Boucles d'animation laissées ouvertes | `Agents`/`Memory` gardaient leur `requestAnimationFrame` après avoir quitté la page (sortie précoce mais boucle vivante) | annulation explicite sur `jarvis:page` (`spatial_modules.js`) |
| Watchdog silencieux | une remise à zéro du compteur de travail ne laissait aucune trace | journal `JarvisSpatialEvents.watchdogLog` : état précédent, compteur, dernier événement, délai, raison, plus un `console.warn` |

Rappel des deux bugs corrigés en Phase 2, dont l'origine est confirmée ici :
`Settings` est déclaré `const` (donc absent de `window`, comme `J`), et un
compteur de travail non soldé figeait l'état `SPEAKING`.

---

## 4. Synchronisation — DRY RUN et diff

### Ce qui a été construit

- Vue **DRY RUN** (`ui/js/v5/spatial_dryrun.js`), accessible par le rail → SYNC.
- Elle s'appuie sur une route backend **déjà en lecture seule** :
  `POST /api/brainrot-sync/refresh` — « Relit Sheet + site et recalcule le plan.
  Lecture seule. » Aucun code de synchronisation n'a été modifié.
- La vue **n'appelle jamais** `/api/brainrot-sync/apply`. Le bouton
  « Appliquer dans le Workspace… » repasse la main au chemin existant et validé
  (sélection → `confirm-scope` → confirmation → idempotence → sauvegarde →
  rollback).
- **Diff lisible** construit à partir du plan réel : `CREATE`, `UPDATE` avec
  `champ : avant → après` (issu de `changed_fields`, `current_site_values`,
  `proposed_values`), `DELETE` (le moteur n'en produit pas), et une section
  « non applicables » avec la raison renvoyée par le serveur.

### Ce qui a été testé

- Montage de la vue, état initial, bouton « Appliquer » désactivé tant qu'aucun
  plan n'existe : **testé**.
- Rendu du diff : **testé avec un plan de test injecté localement dans la page**,
  de structure identique à celle produite par `prepare_plan()`
  (`identity`, `action`, `changed_fields`, `current_site_values`,
  `proposed_values`, `readiness_status`, `counts`). Résultat obtenu :
  `2 CREATE · 1 UPDATE · 0 DELETE · 273 NO_CHANGE · 1 BLOQUÉS`, avec
  `price 120 → 140` et `rarity Epic → Legendary`.
  **Aucun appel réseau, aucune donnée de production touchée.**

### Ce qui n'a pas été testé

- **Un dry run réel n'a pas été lancé.** Il faut l'URL du Google Sheet, et
  l'exécuter revient à lire tes données ; je ne l'ai pas fait sans ton accord.
  La marche à suivre : rail → SYNC → coller l'URL → « Lancer le DRY RUN ».
  L'opération est en lecture seule côté serveur.
- Les scénarios d'erreur (§19 : API injoignable, timeout, hash divergent, WRITE
  partiel, VERIFY en échec) n'ont pas été provoqués. La scène sait afficher
  `ERROR` sur une étape (branche rouge localisée) mais les actions de reprise
  (« Retry Verify », « Rollback ») **ne sont pas exposées** : je ne les ai pas
  ajoutées faute d'avoir pu vérifier leur comportement réel de bout en bout.

---

## 5. Performance

| Mesure | Valeur | Fiabilité |
|---|---|---|
| Chargement (`loadEventEnd`) | 1,05 s | fiable |
| Heap JS au repos | 53 Mo | fiable |
| Heap après 21 navigations | 54 Mo (+1 Mo) | fiable |
| Instances de Brain après parcours | 1 | fiable |
| Canvas actifs | 12 (stable) | fiable |
| FPS moyen HOME idle | 23 | **non représentatif** |
| FPS min HOME idle | 4 | **non représentatif** |

Les FPS ci-dessus ont été mesurés depuis le panneau de navigation de mon
environnement, dont le rafraîchissement est bridé (frames > 50 ms constatées en
continu, y compris sur une page statique). **Ils ne disent rien de ta machine.**

Mesure à faire toi-même, dans la console de ton navigateur :

```
await JarvisSpatial.perf()
```

Retourne : fps moyen, fps minimum, contexte, présence de l'avatar, nombre
d'instances de Brain, heap JS, temps de chargement, viewport. À répéter sur
HOME idle, HOME pendant une réponse vocale, graphe mémoire, agents, outils,
réglages, sync.

Optimisations déjà en place : pause quand l'onglet est caché, quand le module est
invisible (`offsetParent === null`) et quand le Brain sort du champ ; le moteur
avatar se met lui-même en pause hors écran (IntersectionObserver) ; les boucles
des vues sont désormais annulées à la sortie de page.

---

## 6. Test longue durée / fuites

Parcours : 3 cycles de `agents → memory → tools → settings → servers → command →
chat` (21 changements de vue), puis mesure.

| Contrôle | Résultat |
|---|---|
| Heap | 53 → 54 Mo |
| Instances de Brain | 1 (pas de duplication) |
| `requestAnimationFrame` Agents / Memory après sortie | arrêtés |
| Hôtes de vue V5 | 5 (un par page migrée, pas d'empilement) |
| Canvas | 12, stable |
| Menus orphelins | 0 |
| Scènes de sync orphelines | 0 |
| Compteur de travail | 0 |
| Interventions du watchdog | 0 |
| Erreurs console | 0 |

---

## 7. Responsive

Audit géométrique (débordement horizontal, chevauchements rail / command bar /
salutation / Brain / avatar) réalisé en Phase 2 et inchangé depuis :

| Résolution | Débordement | Chevauchement | Notes |
|---|---|---|---|
| 1920 × 1080 | non | aucun | référence |
| 1600 × 900 | non | aucun | — |
| 1440 × 900 | non | aucun | — |
| 1366 × 768 | non | aucun | Brain 498 px, salutation à 566, command bar à 662 |
| 2560 × 1440 | **non testé** | — | à valider |

---

## 8. Régressions V4 / V5

- `?ui=legacy_v4` → shell V4 complet, aucun élément V5 présent dans le DOM.
- `?ui=spatial_v5` → shell V5, avatar monté, aucune erreur.
- Le mode est relu au démarrage depuis `localStorage.JARVIS_UI_MODE`. Précision
  vérifiée : tant qu'aucun choix explicite n'a été fait, la clé est **absente** et
  l'interface retombe sur le défaut `spatial_v5` ; elle n'est écrite que lorsque
  tu choisis explicitement (Réglages → Developer, ou `?ui=…`). Un choix explicite
  survit donc bien au redémarrage.
- Bascule aussi disponible dans Réglages → Developer → **INTERFACE**.
- Toutes les pages héritées (23) restent montées et fonctionnelles dans la
  surface de travail V5 ; les vues migrées pilotent les contrôles d'origine
  (boutons Lancer / Désactiver des agents, navigation des réglages).

Limite connue : la préférence est **locale au navigateur**. Elle survit au
redémarrage de JARVIS et du navigateur, mais pas à un changement de navigateur ou
de profil. Aucune persistance serveur n'a été ajoutée (cela demanderait une
nouvelle clé de réglages côté backend).

---

## 9. Limites — ce qui reste non validé ou non fait

Demandes de la Phase 3 non couvertes dans cette passe :

1. **FPS réels** : à mesurer sur ta machine avec `JarvisSpatial.perf()` (§5).
2. **Dry run réel** sur ton Sheet : non lancé (nécessite l'URL et ton accord).
3. **Scénarios d'erreur de synchronisation** (§19) : non provoqués ; actions
   « Retry Verify » / « Rollback » non exposées tant que leur comportement réel
   n'est pas vérifié.
4. **Brain interactif au clic** (§5 de ta demande) : non implémenté.
5. **Mémoire — zoom / pan / recentrage / filtres / profondeur** (§6) : non
   implémenté ; seuls le survol, le clic-détail et la mise en évidence du rappel
   existent.
6. **Agents — états WAITING / DISABLED distincts** (§7) et **scénario
   multi-agents** (§8) : non implémentés / non testés.
7. **Tools — regroupement par familles, dernière utilisation** (§9) et
   **hiérarchie de risque LOW/MEDIUM/HIGH/CRITICAL harmonisée** (§10) : la vue
   affiche le risque renvoyé par le backend, sans remise en forme ni mise en
   garde renforcée.
8. **Command bar** : détection contextuelle URL/fichier/image, historique
   ↑ / ↓, palette de commandes (§12, §13, §14) : non implémentés. `⌘K` focalise
   déjà la barre, `Escape` ferme les menus.
9. **Settings** : recherche de paramètres et navigation clavier (§11) : non
   implémentées.
10. **Polish visuel final** (§20), **audit d'animations** (§21),
    **accessibilité** (§27) et **tests automatisés** (§29) : non faits.
11. **2560 × 1440** : non testé.
12. **Voix / TTS** : la chaîne complète a été validée en Phase 2 (états, lipsync,
    retour IDLE). Les visèmes fins, le regard et les micro-mouvements n'ont pas
    été inspectés image par image.

---

## 10. Fichiers de cette phase

| Fichier | Rôle |
|---|---|
| `ui/js/v5/spatial_dryrun.js` | vue DRY RUN + diff (nouveau) |
| `ui/js/v5/spatial_shell.js` | HUD : suppression de la case GPU fantôme |
| `ui/js/v5/spatial_modules.js` | arrêt des boucles d'animation à la sortie de page |
| `ui/js/v5/spatial_events.js` | journal de diagnostic du watchdog |
| `ui/css/v5/spatial_phase2.css` | styles DRY RUN / diff |
| `ui/index.html` | chargement du module, version `JARVIS_SPATIAL_OS_V5_PHASE_3` |

Backend : **aucune modification**.
