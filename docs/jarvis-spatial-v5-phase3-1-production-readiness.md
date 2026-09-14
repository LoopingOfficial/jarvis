# JARVIS Spatial OS V5 — Phase 3.1 · Production readiness

Clôture des points laissés NON VALIDÉS par
`jarvis-spatial-v5-phase3-validation.md`.
Build : `JARVIS_SPATIAL_OS_V5_PHASE_31c`. Backend : **aucune modification**.

Règle de ce document : ce qui n'a pas été exécuté est marqué comme tel. Aucune
animation n'est présentée comme une preuve de fonctionnement.

---

## 1. Dry run réel

### Ce qui a été construit

| Élément | État |
|---|---|
| Route utilisée | `POST /api/brainrot-sync/refresh` — lecture seule côté backend |
| Garde-fou frontend `dryRunOnly` | **ajouté**, actif par défaut |
| Routes bloquées par le garde-fou | `/apply`, `/rollback`, `/confirm-scope` |
| Journal des requêtes | `JarvisDryRun.requestLog` : horodatage, URL, méthode, durée, statut, `blocked` |
| Adapter de normalisation | `JarvisDryRun.normalizePlan()` |
| Application | jamais depuis cette vue : bouton « Appliquer dans le Workspace… » qui repasse au chemin existant (sélection → confirmation → idempotence → sauvegarde → rollback) |

### Normalisation du plan — champs couverts

Vocabulaire de référence relevé dans le backend (`brainrot_compare.py`,
`brainrot_sync.py`) :

- `action` : `CREATE`, `UPDATE`, `CONFLICT`, `INVALID`, `SERVER_ONLY` ;
- `readiness_status` : `READY`, `READY_WITHOUT_IMAGE`, `NEEDS_REVIEW`, `BLOCKED` ;
- `plan.counts` : `creates`, `updates`, `deletes`, `blocked`, `needs_review`,
  `applicable`, `without_image` ;
- `comparison.counts` : `NO_CHANGE`, `CREATE`, `UPDATE`, `CONFLICT`, `INVALID`,
  `SERVER_ONLY`.

L'adapter mappe explicitement, sans hypothèse silencieuse :

| Attendu par l'UI | Sources acceptées |
|---|---|
| `action` | `action`, `status`, `op` (normalisé en majuscules) |
| `name` / `id` | `identity`, `name`, `slug`, `identity_key`, `id` |
| `before` | `current_site_values`, `site_values`, `before` |
| `after` | `proposed_values`, `new_values`, `after` |
| `changed` | `changed_fields`, `changed`, sinon **déduit** par comparaison stricte des valeurs fournies (UPDATE seulement) |
| `readiness` / `reason` | `readiness_status` / `readiness_reason`, `reason` |
| `counts.create/update/delete` | `creates`/`CREATE`/`create` … sinon comptage sur les entrées |
| `noChange` | `comparison.counts.NO_CHANGE` (ou `no_change`) |
| non applicable | toute action hors `CREATE`/`UPDATE`/`DELETE` |

### Ce qui a été testé

- Garde-fou : `/apply` et `/rollback` **bloqués**, `/refresh` autorisé — PASS.
- Normalisation : forme actuelle et forme historique (`status` + `before`/`after`
  sans `counts`) — PASS, y compris la déduction du champ modifié.
- Rendu du diff avec un plan complet : `2 CREATE · 1 UPDATE · 0 DELETE ·
  270 NO_CHANGE`, avec `price 120 → 140` et `rarity Epic → Legendary` — PASS.
- Journal alimenté, **aucune écriture réellement émise** — PASS.

### Ce qui n'a PAS été testé

**Le chemin réseau réel n'a pas été exécuté** : il demande l'URL de ton Google
Sheet. Le code l'accepte sans modification — rail → SYNC → coller l'URL →
« Lancer le DRY RUN », ou en console :
`await JarvisDryRun.run('<url du sheet>')`.
Le journal `JarvisDryRun.requestLog` permettra de vérifier après coup l'URL
appelée, la méthode et l'absence de route d'écriture.

---

## 2. Scénarios d'erreur de synchronisation

Joués en interceptant `J.post` localement : **aucune requête n'a été émise**,
aucune donnée touchée.

| Scénario | Attendu | Résultat | Message affiché |
|---|---|---|---|
| Backend injoignable | `NETWORK` | PASS | « Backend injoignable — la relecture n'a pas pu être lancée. Aucun changement n'a été écrit. » |
| Timeout (180 s) | `TIMEOUT` | implémenté, non déclenché en test | « Request timed out — … Aucun changement n'a été écrit. » |
| Réponse malformée | `MALFORMED` | PASS (aucune exception JS) | « Réponse inexploitable du serveur (format inattendu). » |
| Erreur backend (`ok:false`) | `BACKEND_ERROR` | PASS | message serveur ; si la cause mentionne le Sheet : « Feuille Google inaccessible — la source n'a pas pu être lue » |
| Plan absent de la réponse | `MALFORMED` | PASS | « Le serveur n'a pas renvoyé de plan exploitable. » |
| Plan vide | message dédié | PASS | « Aucun changement détecté — la feuille et le site sont alignés. » (aucune scène de sync ne tourne) |

Dans tous les cas d'échec : état `ERROR` du Brain pendant 4 s puis retour à
`IDLE`, bouton d'application désactivé, plan effacé.

**Retry / Rollback : toujours pas de bouton.** La règle de la phase précédente
est conservée — `hash mismatch` et `verify failure` n'ont pas pu être provoqués
sur un pipeline réel, et `/api/brainrot-sync/rollback` exige un `backup_path`
produit par une application réelle. Aucun bouton n'est affiché tant que ces
conditions ne sont pas vérifiables.

---

## 3. Brain

Ajouté : **le Brain est cliquable** (clic, `Entrée` ou `Espace` au clavier,
`role="button"`, `aria-label`). Il ouvre un panneau d'observation latéral.

Contenu, uniquement observable :

- état courant (`IDLE`, `THINKING`, `RECALLING`, `SPEAKING`…) ;
- zones sollicitées (MEMORY / KNOWLEDGE / CONTEXT / TOOLS) allumées selon la
  chaleur réelle ;
- contexte actif : vue courante, nombre de travaux en cours, volume de mémoire ;
- **rappels récents** — alimentés par `brain.search`, `brain.path`, `memory.created` ;
- **sources consultées** — alimentées par `tool.started` ;
- **agents sollicités** — alimentés par `agent.started`, avec l'action réelle ;
- raccourci vers le graphe mémoire.

Aucune chaîne de pensée du modèle n'est affichée — vérifié par test automatisé
(`brain · pas de chaîne de pensée` → PASS). Les listes sont vides tant qu'aucun
événement n'a été observé : rien n'est pré-rempli.

---

## 4. Graphe mémoire — navigation et performance

Ajouté :

| Fonction | Implémentation |
|---|---|
| Zoom | molette, centré sur le curseur, facteur 0,35 → 6 |
| Pan | glisser-déposer (un glissement n'est pas interprété comme un clic) |
| Recentrage | bouton « Recentrer » (remet l'origine, garde le zoom) |
| Reset | bouton « Reset » + `Échap` : zoom, position, focus, recherche |
| Recherche | champ dédié, insensible aux accents |
| Focus voisins | clic sur un nœud → nœud + relations directes + voisins mis en avant, **le reste atténué** |
| Profondeur | `Memory.depth` (1 par défaut), expansion par vagues |
| Quitter le focus | `Échap`, ou clic dans le vide |

**Bug corrigé** : l'expansion du voisinage se propageait en cascade dans la même
passe et finissait par éclairer tout le graphe (104 nœuds sur 108). Elle se
calcule désormais par vagues successives — testé : focus limité au voisinage
réel — PASS.

**Charge 250 / 500 / 1000 nœuds : NON TESTÉ.** Je n'ai pas injecté de fixtures
de charge dans cette passe. Le graphe réel (108 nœuds / 131 relations) a été
parcouru 3 fois de suite sans incident. La relaxation est faite une fois au
montage (140 itérations) puis le rendu est purement descriptif, ce qui limite le
coût, mais cela reste une hypothèse tant que la mesure n'est pas faite.

---

## 5. Outils

Organisation réelle par familles, **déduites de l'identifiant réel de l'outil**
(`ssh.*`, `web.*`, `fs.*`, `blender.*`…). Un outil non reconnaissable tombe dans
`OTHER` — jamais affecté au hasard.

Familles obtenues sur les 95 outils réels :

```
WEB 5 · FILES 6 · SYSTEM 3 · CODE 3 · DATABASE 1 · SSH 10 · API 1
BROWSER 1 · COMMUNICATION 3 · MEDIA 36 · AI 1 · AUTOMATION 6 · MEMORY 5 · OTHER 14
```

Colonnes affichées : nom, identifiant, famille, connecteur (si présent), niveau
de risque, état (actif / désactivé). La description réelle est en infobulle.

**Niveaux de risque harmonisés** à partir du champ backend :
`LOW` (lecture) · `MEDIUM` (écriture réversible) · `HIGH` (action importante) ·
`CRITICAL` (opération système/destructive). `HIGH` et `CRITICAL` sont
visuellement distincts (ambre encadré, rouge encadré à interlettrage renforcé).
Les quatre niveaux sont présents dans le catalogue réel.

**`last_used` : non affiché** — le backend ne l'expose pas. Aucune date inventée.

---

## 6. Command bar

| Fonction | État |
|---|---|
| Historique ↑ / ↓ | **ajouté**, 60 entrées, persisté (`JARVIS_CMD_HISTORY`) |
| Non-interférence multiligne | les flèches ne sont captées que si le texte ne contient pas de saut de ligne |
| Brouillon préservé | ↓ en fin d'historique restaure le texte en cours de saisie |
| `Ctrl/⌘ + K` | focalise la command bar |
| `Ctrl/⌘ + Maj + K` ou `Ctrl + Espace` | ouvre la palette |
| `Échap` | ferme dans l'ordre : palette → panneau Brain → panneau détail → menu → défocalise la barre |

**Palette de commandes** : 127 entrées construites à partir du réel — vues,
réglages par section, 8 agents (`/api/agents`), outils (`/api/tools`), plus deux
actions sûres (dry run, bascule d'interface). Navigation ↑/↓/Entrée, recherche
insensible aux accents (« memoire » trouve « Mémoire »), `Échap` ferme.
Une entrée d'outil **n'exécute pas** l'outil : elle l'affiche dans la vue Outils.

**Détection contextuelle (URL / fichier / image collés) : NON IMPLÉMENTÉE.**

---

## 7. Settings

- **Recherche ajoutée** : champ dans la barre compacte, filtre les blocs du
  panneau réellement rendu et met en évidence les intitulés correspondants,
  insensible aux accents.
- Navigation clavier : focus visible sur tous les contrôles (`:focus-visible`).

### Mécanisme de persistance — vérifié

| Aspect | Réalité mesurée |
|---|---|
| Support | `localStorage` du navigateur (clé `JARVIS_UI_MODE`) |
| Écriture | uniquement sur choix explicite (Réglages → Developer, ou `?ui=…`) |
| Absence de clé | interprétée comme défaut `spatial_v5` |
| `?ui=legacy_v4` → rechargement | mode `legacy_v4` relu, shell V4 monté — PASS |
| `?ui=spatial_v5` → rechargement | mode `spatial_v5` relu, shell V5 monté — PASS |
| Portée | **locale au navigateur/profil** : survit au redémarrage de JARVIS et du navigateur, pas à un changement de navigateur. Aucune persistance serveur ajoutée. |

---

## 8. Accessibilité

| Point | État |
|---|---|
| Focus clavier visible | contour cyan sur tous les éléments focusables (`:focus-visible`) |
| Boutons icon-only étiquetés | rail (9 entrées), micro, envoi, diagnostics système, boutons du graphe, fermeture des panneaux |
| Champs étiquetés | command bar, recherche mémoire, recherche outils, recherche réglages, URL du dry run (label masqué visuellement, lu par les lecteurs d'écran) |
| Rôles | `navigation` (rail), `log` (chat), `dialog` (palette, panneau Brain), `listbox`/`option` (palette) |
| Clavier | `Entrée` et `Espace` activent le Brain ; ↑/↓/Entrée dans la palette ; `Échap` en cascade |
| Contraste | inchangé (pas de refonte visuelle) — **non mesuré au ratio WCAG** |
| Tab order | non réordonné explicitement — **non audité** |

---

## 9. Responsive

Audit géométrique : débordement horizontal, chevauchements rail / command bar /
salutation / Brain, et Brain entièrement dans le cadre.

| Résolution | Vues testées | Débordement | Chevauchement | Brain dans le cadre |
|---|---|---|---|---|
| 2560 × 1440 | HOME, MEMORY, TOOLS | non | aucun | oui |
| 1920 × 1080 | HOME, MEMORY, SYNC | non | aucun | oui |
| 1600 × 900 | (phase 3) | non | aucun | oui |
| 1440 × 900 | (phase 3) | non | aucun | oui |
| 1366 × 768 | HOME, TOOLS, AGENTS | non | aucun | oui |

Command bar plafonnée à 820 px en 2560 (clamp), graphe et liste d'outils
fonctionnels à toutes les tailles testées. Workspace et Settings non re-audités
géométriquement à 2560.

---

## 10. Performance

### Sonde enrichie

`await JarvisSpatial.perf(secondes)` retourne désormais : vue, durée réelle,
FPS moyen, FPS minimum, **temps de frame moyen**, **frames > 33 ms**,
**frames > 50 ms**, heap, instances de Brain, présence de l'avatar, temps de
chargement, viewport, et une liste `alertes` (FPS moyen < 55, frames longues).

`await JarvisSpatial.perfAll(5)` enchaîne HOME, CHAT, MEMORY, AGENTS, TOOLS,
SETTINGS, SYNC avec 5 s par vue.

### Mesures obtenues ici — **invalides comme référence**

Exécutée dans mon panneau, la sonde a relevé une frame unique de **5 970 ms** :
l'environnement gèle le rendu. C'est précisément ce que la sonde doit signaler,
et cela confirme qu'aucune mesure de FPS prise ici ne vaut pour ta machine.

| Mesure | Valeur | Fiabilité |
|---|---|---|
| Chargement | 1,05 s (mesuré en phase 3, hors gel) | fiable |
| Heap au repos | 53 Mo | fiable |
| FPS | non mesurable ici | **à faire chez toi** |

Procédure : ouvre JARVIS dans ta fenêtre, console du navigateur, puis
`await JarvisSpatial.perfAll(5)`. Signale-moi toute vue avec `alertes`.

---

## 11. Long-run

Parcours : 5 cycles de `memory → agents → tools → settings → servers → chat →
command`, soit **35 changements de vue**.

| Contrôle | Avant | Après |
|---|---|---|
| Heap JS | 54 Mo | 54 Mo (**delta 0**) |
| Instances de Brain | 1 | 1 |
| `rAF` Agents / Memory hors vue | — | arrêtés |
| Canvas | 12 | 12 |
| Hôtes de vue V5 | — | 5 (un par page migrée) |
| Panneaux détail | — | 2 (agents, mémoire) |
| Palettes / panneaux Brain orphelins | — | 0 |
| Compteur de travail | 0 | 0 |
| Interventions du watchdog | 0 | **0** |
| Erreurs console nouvelles | — | 0 |

Le watchdog n'est pas intervenu : conforme à l'exigence (une intervention aurait
été traitée comme un bug, pas comme un succès).

---

## 12. Tests — PASS / FAIL

Harnais exécutable : `await JarvisSelfTest.run()`
(`ui/js/v5/spatial_selftest.js`). Dernier passage : **41 PASS · 0 FAIL · 2 SKIP**
sur 43.

| Domaine | Tests | Résultat |
|---|---|---|
| Garde-fou d'écriture | /apply bloqué, /rollback bloqué, /refresh autorisé | 3 PASS |
| Normalisation du plan | compteurs, NO_CHANGE, non applicable, champ changé, forme historique, déduction | 6 PASS |
| Erreurs de sync | réseau, malformé, backend, plan absent, plan vide, plan valide, diff, journal, aucune écriture | 9 PASS |
| Command bar | historique, persistance, ↑, ↓ | 4 PASS |
| Palette | accents, outils SSH, Échap | 3 PASS |
| Brain | panneau ouvert, pas de chaîne de pensée, Échap | 3 PASS |
| Outils | familles, filtre, niveaux de risque | 3 PASS |
| Mémoire | graphe monté, recherche, focus voisinage, reset | 4 PASS |
| Nettoyage | rAF agents, rAF mémoire, instance unique de Brain | 3 PASS |
| Interface | mode persisté, V4 disponible | 2 PASS |
| Watchdog | non déclenché | 1 PASS |
| Non automatisables | FPS réels, dry-run réel | 2 SKIP |

### Bugs trouvés par ces tests et corrigés

1. **Hôte V5 détruit pendant l'attente réseau** — `Pages.render()` réécrit la
   page héritée ; les vues Agents / Mémoire / Outils montaient leur conteneur
   *avant* d'attendre l'API et le retrouvaient détaché (graphe à 0 nœud,
   `TypeError` en console). Corrigé : le conteneur est monté **après** la
   réponse réseau, et les sous-éléments sont accédés avec garde.
2. **Focus du graphe trop large** (cf. §4).
3. Deux défauts du harnais lui-même, corrigés : la vue SYNC doit être rendue
   avant d'être jugée, et le journal des requêtes contient volontairement les
   tentatives *bloquées* — l'assertion vérifie désormais l'absence d'écriture
   **réellement émise**.

---

## 13. Limites restantes

1. **Dry run réseau réel** : non exécuté (URL du Sheet requise).
2. **`hash mismatch`, `verify failure`, WRITE partiel** : non provoqués ; pas de
   bouton Retry/Rollback tant que leur comportement réel n'est pas vérifié.
3. **Charge du graphe à 250 / 500 / 1000 nœuds** : non testée.
4. **FPS réels** : à mesurer sur ta machine (§10).
5. **Détection contextuelle de la command bar** (URL / fichier / image) : non
   implémentée.
6. **Agents** : états `WAITING` / `DISABLED` distincts et scénario multi-agents
   réel — non traités.
7. **Accessibilité** : contraste non mesuré au ratio, ordre de tabulation non
   audité.
8. **Polish CSS final et audit d'animations** : non faits en passe dédiée. Les
   seules modifications visuelles de cette phase sont fonctionnelles (focus
   visible, niveaux de risque, barre du graphe, états vides et d'erreur).
9. **Workspace et Settings à 2560 × 1440** : non re-audités géométriquement.
10. **Persistance du mode d'interface** : locale au navigateur, pas de stockage
    serveur.

---

## 14. Checklist de livraison

| Critère | État |
|---|---|
| Aucun faux indicateur | **OK** (case GPU fantôme supprimée en phase 3) |
| Aucun mock présenté comme réel | **OK** (fixtures de test explicitement identifiées) |
| Aucune boucle ne survit à sa vue | **OK** (vérifié sur 35 navigations) |
| Aucun watchdog sur parcours normal | **OK** (0 intervention) |
| V4 fonctionnel | **OK** (V5 → V4 → V5 vérifié) |
| Sync dry-run validée | **partiel** — logique, garde-fou, normalisation et rendu validés ; appel réseau réel non exécuté |
| Erreurs sync gérées | **OK** (6 scénarios, 0 crash) |
| Settings persiste | **OK** (mécanisme documenté) |
| Navigation clavier de base | **OK** |
| Aucune erreur console | **OK** (le message résiduel visible dans mon panneau provient d'une version antérieure en cache : le module chargé est `PHASE_31c`) |
| Pas de fuite mémoire continue | **OK** (delta 0 Mo sur 35 navigations) |

**Conclusion** : tous les critères sont remplis sauf le dry run réseau réel, qui
ne dépend plus que de ton URL de Sheet.

---

## 15. Fichiers de cette phase

| Fichier | Rôle |
|---|---|
| `ui/js/v5/spatial_dryrun.js` | réécrit : garde-fou, journal, adapter, erreurs, diff |
| `ui/js/v5/spatial_ux.js` | **nouveau** : Brain cliquable, historique, raccourcis, palette, recherche réglages, a11y |
| `ui/js/v5/spatial_selftest.js` | **nouveau** : 43 tests exécutables |
| `ui/js/v5/spatial_modules.js` | zoom/pan/recherche/focus mémoire, familles et risques d'outils, montage après réseau |
| `ui/js/v5/spatial_shell.js` | sonde `perf()` enrichie + `perfAll()` |
| `ui/css/v5/spatial_phase2.css` | styles : dry run, diff, panneau Brain, palette, graphe, risques |
| `ui/index.html` | chargement des modules, version `JARVIS_SPATIAL_OS_V5_PHASE_31c` |
