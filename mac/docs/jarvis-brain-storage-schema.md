# JARVIS — Schéma réel de stockage du Brain

Audit effectué le 14/09/2026 sur l'installation réelle
(`data/jarvis.db`, 3,2 Mo) et sur les endpoints en fonctionnement.
Aucune valeur de ce document n'est estimée : tout vient d'une requête SQL ou
d'un appel API exécuté.

---

## 1. Réponse courte : que contiennent les « 108 nœuds / 131 relations » ?

Le graphe **n'est pas une table**. Il est reconstruit à chaque appel par
`BrainManager.graph()` (`jarvis/brain_manager.py`) en agrégeant sept sources
réelles. Composition mesurée :

| Préfixe d'identifiant | Nombre | Origine réelle |
|---|---|---|
| `jarvis` | 1 | le nœud central (nom pris dans les réglages) |
| `kb:<id>` | 25 | table `knowledge` |
| `tool:<id>` | 40 | registre d'outils en mémoire (`core.registry`) |
| `br_<id>` | 36 | table `brain_nodes` (nœuds persistés) |
| `conn:<id>` | 5 | table `connectors` |
| `wf:<id>` | 1 | table `workflows` |
| **Total** | **108** | |

Répartition par famille : `TOOLS` 43 · `KNOWLEDGE` 40 · `SOLUTIONS` 13 ·
`ERRORS` 8 · `SERVERS` 2 · `WORKFLOWS` 1 · `JARVIS` 1.
Par nature : `tool` 45 · `knowledge` 37 · `procedure` 24 · `core` 1 · `workflow` 1.

Les 131 relations sont majoritairement **calculées**, pas stockées :

| Type d'arête | Nombre | Stockée ? |
|---|---|---|
| `learned` (jarvis → savoir) | 57 | calculée |
| `teaches` (outil → savoir) | 38 | calculée |
| `can_use` (jarvis → outil) | 30 | calculée |
| `uses` (jarvis → connecteur) | 5 | calculée |
| `runs` (jarvis → workflow) | 1 | calculée |
| table `brain_edges` | **12 lignes** | persistées, incluses si les deux extrémités existent |

**Conséquence pour l'inspecteur** : un nœud n'a pas toujours de ligne en base.
`tool:*`, `conn:*`, `wf:*` sont des projections d'objets vivants ; seuls
`kb:*` et `br_*` ont un enregistrement durable.

---

## 2. Volumétrie réelle des tables

| Table | Lignes | Remarque |
|---|---|---|
| `knowledge` | **25** | la vraie mémoire de contenu |
| `brain_nodes` | **36** | nœuds persistés, dont beaucoup pointent vers `knowledge` |
| `brain_edges` | **12** | relations persistées |
| `conversations` | **15** | correspond au satellite « 15 CONVERSATIONS » |
| `messages` | **316** | messages de ces conversations |
| `memories` | **0** | **aucun souvenir enregistré** |

Le satellite « 25 SAVOIRS » de la HOME correspond donc à `knowledge`, et
l'absence de souvenirs est réelle — ce n'est pas un défaut d'affichage.

---

## 3. Schéma d'un nœud

### 3.1 Nœud tel que servi par `/api/brain`

```json
{
  "id": "kb_0bbfd4ddf5fb",
  "family": "ERRORS",
  "label": "Commande SSH · échecs récurrents (…)",
  "kind": "knowledge",
  "weight": 0.75,
  "meta": { … }
}
```

`meta` varie selon l'origine — **c'est le point central pour l'UI** :

| Origine | Contenu réel de `meta` |
|---|---|
| `kb:*` (knowledge) | `id`, `kind`, `project`, `confidence` (0–1), `validations`, `summary` (**tronqué à 240 caractères**), `tags[]`, `tools[]` |
| `br_*` (brain_nodes) | le JSON libre de la colonne `meta` — en pratique `tool_id`, `reason`, `kind`, `tags`, `project`, `source` |
| `tool:*` | `tool_id`, `category`, `risk` |
| `conn:*` | `connector_id`, `type`, `status`, `name` |
| `wf:*` | `workflow_id`, `trigger` |
| `mem:*` | `scope`, `importance`, `content` (tronqué à 200), `id` — **actuellement aucun** |

`weight` : pour un savoir, `0.5 + confidence × 0.5` ; pour un souvenir,
`importance / 5` ; pour un connecteur, 1,2 s'il est connecté, sinon 0,8.

### 3.2 Table `knowledge` — la source de contenu

```sql
knowledge(
  id TEXT PRIMARY KEY, title TEXT, content TEXT, source TEXT,
  kind TEXT, tags TEXT /*JSON*/, project TEXT,
  created_at REAL, updated_at REAL,
  confidence_score REAL, validation_count INTEGER, failure_count INTEGER,
  last_validated_at REAL, status TEXT, tools TEXT /*JSON*/,
  verification_method TEXT, evidence TEXT /*JSON*/
)
```

Mesures sur les 25 fiches réelles :

- longueur du contenu : **23 à 5 047 caractères**, moyenne 936 ;
- `kind` : `procedure` 13 · `API_REFERENCE` 8 · `ERROR_FIX` 4 ;
- `source` : `auto-learn` 13 · `https://…` 5 · `learning:errors` 4 · `registry:*` 3 ;
- `tags` : renseignés sur **25/25** ;
- `tools` : renseignés sur **24/25** ;
- `evidence` non vide : **10/25** ;
- `verification_method = official_source` : **8/25**, les 17 autres vides ;
- `status` : `active` partout.

`evidence` (quand présent) contient de la provenance exploitable :
`source_type`, `source_hash`, `source_version`, `verified_at`, `session_id`,
`docs_validated`, `tests_run`, `tests_passed`, `error_reason`, `test_scope`.

### 3.3 Table `brain_nodes`

```sql
brain_nodes(
  id TEXT PRIMARY KEY, kind TEXT, family TEXT, label TEXT,
  ref_type TEXT, ref_id TEXT, meta TEXT /*JSON*/,
  weight REAL, created_at REAL, updated_at REAL
)
```

`ref_type` / `ref_id` donnent la **traçabilité vers la source** : dans les
données actuelles, `ref_type='knowledge'` et `ref_id='kb_…'`. Un nœud `br_*`
est donc presque toujours le reflet d'une fiche `knowledge` déjà présente en
tant que `kb:*` — c'est-à-dire **un doublon sémantique dans le graphe**
(deux nœuds pour un même savoir). À traiter côté UI, pas côté base.

### 3.4 Table `brain_edges`

```sql
brain_edges(
  id TEXT PRIMARY KEY, source TEXT, target TEXT, kind TEXT,
  weight REAL, created_at REAL, meta TEXT /*JSON*/
)
```

Une arête n'est retenue que si ses deux extrémités existent dans le graphe
reconstruit.

### 3.5 Souvenirs (`memories`) — schéma disponible, table vide

```sql
memories(
  id TEXT PRIMARY KEY, scope TEXT, content TEXT, importance INTEGER,
  pinned INTEGER, source TEXT, tags TEXT /*JSON*/, related TEXT /*JSON*/,
  project TEXT, conversation_id TEXT, task_id TEXT,
  created_at REAL, updated_at REAL, embedding BLOB
)
```

Le champ `conversation_id` permettra de relier un souvenir à sa conversation
d'origine **le jour où des souvenirs existeront**. `embedding` est un BLOB :
il ne doit jamais être affiché.

### 3.6 Conversations

`/api/conversations` renvoie par élément :
`id`, `title`, `context`, `archived`, `created_at`, `updated_at`,
`message_count`, `task_count`, `memory_count`, `preview`.

Les messages (`messages`, 316 lignes) sont accessibles séparément via
`/api/conversations/<id>` — à charger **à la demande**, jamais dans le graphe.

---

## 4. Ce qui est disponible pour l'inspecteur, champ par champ

| Champ demandé | Disponible ? | Source |
|---|---|---|
| Titre | oui | `label` du nœud / `title` de la fiche |
| Type | oui | `kind` + `family` |
| **Contenu réel complet** | **oui** | `GET /api/knowledge` → `items[].content` (jusqu'à 5 047 caractères) |
| Résumé | partiel | `meta.summary` (tronqué à 240) — à n'afficher que s'il diffère du contenu |
| Source | oui | `knowledge.source` (`auto-learn`, `registry:<outil>`, `learning:errors`, URL) |
| Date de création | oui | `created_at` |
| Dernière modification | oui | `updated_at` |
| Tags | oui | `tags[]` (25/25) |
| Catégorie | oui | `family` / `kind` |
| Relations | oui | arêtes du graphe |
| Parents / enfants | partiel | `ref_type`/`ref_id` pour `br_*`, sinon direction des arêtes |
| Provenance | partiel | `evidence` (10/25) : type de source, hash, date de vérification |
| Score de confiance | oui | `confidence_score` (0–1) + `validation_count` / `failure_count` |
| Nombre de connexions | oui | calculé sur les arêtes |
| Dernière utilisation | **non** | aucun champ `last_used` / compteur de rappel en base |
| Fréquence de rappel | **non** | non persistée |

**Conclusion d'architecture : aucune modification backend n'est nécessaire.**
Tout le contenu réel est déjà exposé par `/api/knowledge`, `/api/brain`,
`/api/memory`, `/api/conversations` et `/api/tools`. L'inspecteur se construira
en croisant le graphe et ces sources, par jointure sur `meta.id` / `ref_id`.

---

## 5. Recall réel — ce que le backend publie déjà

`BrainManager` émet des événements exploitables sans rien ajouter :

| Événement | Charge utile | Usage pour l'UI |
|---|---|---|
| `brain.search` | `kind`, `query` (200 car.), `node_ids[]` (8 max), `labels[]` | afficher la requête mémoire et éclairer les nœuds réellement rappelés |
| `brain.node.selected` | `id`, `label` | suivre la sélection côté backend |
| `brain.path` | `steps[]` : `{family, labels[]}` | montrer le chemin consulté |
| `brain.tool.active` | `tool_id`, `family` | zone TOOLS |
| `brain.learn.created` | `id`, `kind`, `family`, `label` | nouveau savoir |
| `activity.trace` | `ts`, `kind`, `title`, `detail`, `state` | timeline (table `activity_trace`) |

`brain.search` porte **les identifiants exacts** des nœuds rappelés : la vue
« RECALLING · N nodes recalled » demandée est donc réalisable sans inventer
quoi que ce soit. Elle affichera la requête, les correspondances et les
sources — **jamais** le raisonnement du modèle, qui n'est de toute façon pas
publié par ces événements.

---

## 6. Limites et pièges identifiés

1. **`meta.summary` est tronqué à 240 caractères.** L'afficher seul reviendrait
   à ne pas montrer le contenu réel : l'inspecteur doit aller chercher
   `content` complet via `/api/knowledge`.
2. **Doublons `kb:*` / `br_*`** : un même savoir apparaît deux fois dans le
   graphe (36 `brain_nodes` référencent des `knowledge` déjà présents). Il faut
   les rapprocher par `ref_id` à l'affichage.
3. **45 nœuds sur 108 sont des outils** : ce sont des capacités, pas de la
   mémoire. Une vue « mémoire » honnête doit les distinguer.
4. **Aucun souvenir (`memories` vide)** : l'inspecteur doit le dire clairement
   plutôt que d'afficher une section vide sans explication.
5. **Pas de `last_used` ni de compteur de rappel** : l'importance d'un nœud ne
   peut donc reposer que sur le nombre de relations et, pour les savoirs, sur
   `confidence_score` / `validation_count`.
6. **`embedding` est un BLOB** : hors de question de l'exposer, même en mode
   technique.
7. **Limites de construction** : `graph()` plafonne à 40 souvenirs, 60 savoirs,
   30 outils, 30 connecteurs, 150 `brain_nodes`, 200 `brain_edges`. Au-delà, le
   graphe ne montrera pas tout — à rappeler dans l'UI si les seuils sont atteints.

---

## 7. Décisions retenues pour l'inspecteur (à implémenter)

1. Source de contenu : `/api/knowledge` (contenu intégral), joint au graphe par
   `meta.id` pour `kb:*` et par `ref_id` pour `br_*`.
2. Aucun champ inventé : si `evidence`, `verification_method` ou `project` sont
   vides, la ligne n'est pas affichée.
3. Les nœuds `tool:*`, `conn:*`, `wf:*` affichent ce qu'ils sont réellement
   (capacité, connecteur, automatisation), sans prétendre être des souvenirs.
4. Recherche dans le contenu : indexation locale de `title + content + tags +
   source` des 25 fiches, croisée avec les labels du graphe.
5. Conversations : titre, dates, `message_count`, `preview` ; les messages ne
   sont chargés que sur demande explicite.
6. Importance visuelle : nombre de relations, et `confidence_score` pour les
   savoirs — rien d'autre, faute de données de fréquence.
