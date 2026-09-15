# JARVIS Brain Inspector — Phase 2

Brain Library + zoom sémantique. Build `JARVIS_BRAIN_INSPECTOR_10`.
Aucune donnée inventée, aucun mock : tout provient de `/api/brain`,
`/api/knowledge` et des événements `brain.*` réels.

---

## 1. Brain Library

Vue de consultation ouverte depuis la barre du graphe (bouton **Brain Library**)
ou depuis l'inspecteur (**Ouvrir dans la Library**). Elle **ne remplace pas** le
graphe : les deux partagent la même identité canonique, le même moteur de
recherche et le même inspecteur.

| Élément | Comportement |
|---|---|
| Onglets | ALL · KNOWLEDGE · CAPABILITIES · MEMORIES, avec compteurs canoniques |
| Lignes | compactes (~90 px) : titre, résumé réel, type, relations, confiance, validations, source, date, tags |
| Clic | ferme la Library, centre le nœud canonique dans le graphe, ouvre l'inspecteur |
| Tri | Relations · Modifié · Créé · Confiance · Validations · Titre |
| SYSTEM | reste dans ALL, sans onglet dédié |

Les trois questions ont chacune leur réponse, sans mélange :
« Que sait JARVIS ? » → KNOWLEDGE · « Que sait-il faire ? » → CAPABILITIES ·
« De quoi se souvient-il ? » → MEMORIES.

---

## 2. Compteurs

Mesurés à l'exécution, après canonicalisation :

```
ALL           87
KNOWLEDGE     37
CAPABILITIES  49     (TOOL 43 · CONNECTOR 5 · WORKFLOW 1)
MEMORIES       0
SYSTEM         1
relations    102     (111 nœuds bruts / 134 arêtes brutes en entrée)
doublons      24     fusionnés en présentation
```

L'en-tête de la vue Brain affiche désormais ces chiffres canoniques
(« 87 concepts · 102 relations — 24 doublons fusionnés · 37 connaissances ·
49 capacités · 0 souvenirs ») au lieu des chiffres bruts.

**MEMORIES = 0** affiche un message explicite : « Aucun souvenir persistant
enregistré. La table des souvenirs est vide. Les connaissances de JARVIS sont
stockées séparément et restent consultables dans l'onglet KNOWLEDGE », avec un
bouton vers cet onglet. Aucune connaissance n'est présentée comme un souvenir.

---

## 3. Recherche

Un seul moteur : `JarvisBrainData.provider`, déjà construit en phase 1 et
réutilisé tel quel par le graphe **et** par la Library. Aucun second moteur.

- Champs couverts quand ils existent : `title`, `content`, `tags`, `source`,
  `evidence`, plus le label, la famille et le type du nœud.
- Insensible aux accents et à la casse.
- Un résultat porte la mention **contenu** quand le terme est dans le texte et
  pas seulement dans le titre.
- Résultat cliquable : sélectionne et centre le nœud canonique, ouvre
  l'inspecteur.

Vérifié : « ssh » → 20 concepts (18 connaissances, 2 capacités) ;
« google sheet » → 3 concepts dont 1 trouvé uniquement dans le contenu.

L'abstraction reste remplaçable : `provider.search(query, ctx)` peut devenir un
appel backend ou vectoriel sans toucher à l'interface.

---

## 4. Filtres

Uniquement ceux que les données supportent réellement :

| Filtre | Source | Présence |
|---|---|---|
| Type | typage canonique | toujours (onglets) |
| Nature de capacité | TOOL / CONNECTOR / WORKFLOW | onglet CAPABILITIES |
| Tags | `knowledge.tags` réels, 8 plus fréquents avec leur compte | 25/25 fiches |
| Vérifié | `verification_method` non vide | 8/25 — le filtre n'apparaît que si au moins une fiche le porte |
| Confiance ≥ 60 % | `confidence_score` | affiché seulement si des fiches en portent |

**Absents volontairement** : LAST USED, POPULAR, MOST RECALLED — ces métriques
ne sont persistées nulle part (voir `docs/jarvis-brain-storage-schema.md` §6).

---

## 5. Zoom sémantique

Le niveau est dérivé du zoom réel de la vue et change la **quantité
d'information**, pas seulement l'échelle.

| Niveau | Seuil | Labels max | Relations | Détail |
|---|---|---|---|---|
| FAR | k ≤ 0,85 | 8 | seulement les liens des concepts forts (α 0,16 / 0,03) | aucun |
| MEDIUM | k ≤ 2,2 | 22 | normales, pondérées par l'importance | titre |
| CLOSE | k > 2,2 | 60 | concentrées sur la sélection (α 0,42 vs 0,05) | titre + type + relations + résumé court (≤ 44 car.) |

Le contenu intégral n'est **jamais** dessiné sur le canvas : il reste dans
l'inspecteur. Un indicateur discret « ZOOM · FAR/MEDIUM/CLOSE » est affiché.

Vérifié : k=0,6 → FAR (5 labels en capitales, plus d'empilement) ;
k=1,5 → MEDIUM ; k=3,5 → CLOSE.

---

## 6. Gestion des labels

Deux mécanismes combinés :

1. **Priorité** — 1. nœud sélectionné · 2. nœuds réellement rappelés ·
   3. résultats de recherche · 4. nœuds fortement connectés · 5. le reste.
2. **Évitement de collision** — chaque label réserve sa boîte ; un label qui
   chevauche une boîte déjà prise n'est pas dessiné. La sélection et le rappel
   passent toujours, les autres cèdent la place.

Les labels du moteur de graphe hérité sont désactivés quand le zoom sémantique
est chargé, pour éviter une double écriture.

**Importance d'un nœud — formule documentée :**

```
score = degré_normalisé × 0,7 + confiance × 0,2 + validations_normalisées × 0,1
```

Seules des données existantes sont utilisées. La topologie reste dominante :
la confiance ne peut pas écraser la structure. Aucun « score d'importance IA ».

---

## 7. Intégration du rappel

Le rappel réel est **prioritaire sur le zoom** : les nœuds renvoyés par
`brain.search` restent affichés même si leur niveau les aurait masqués, puis
l'effet s'estompe au bout de 20 s.

Vérifié sur une question réelle : `brain.search` a renvoyé 5 `node_ids`
(`kb_def39392ec7e`, `kb_0bbfd4ddf5fb`, …) avec la requête et les labels ; la
bannière affiche « RECALLING · « que sais-tu sur ssh ? » · 5 nœuds rappelés »
avec les labels cliquables, et seuls ces nœuds s'illuminent.

**Aucune historique de rappel n'est fabriquée** : `lastRecall.persistant` vaut
toujours `false`, l'inspecteur indique « Observation de cette session uniquement
— le backend ne conserve ni date ni fréquence de rappel », et l'information
disparaît au rechargement.

---

## 8. Timeline

**Non implémentée dans cette passe.** L'ordre demandé était Library → zoom →
timeline ; les deux premiers sont livrés et testés, le troisième ne l'est pas.

Ce qui est prêt côté données (relevé lors de l'audit, rien à ajouter au
backend) : `knowledge.created_at`, `knowledge.updated_at`,
`evidence.verified_at`, `conversations.created_at/updated_at`, et la table
`activity_trace`. Le nom retenu sera **KNOWLEDGE TIMELINE** ou **BRAIN
ACTIVITY**, jamais « Memory Timeline », et une section **SESSION ACTIVITY**
distincte, explicitement marquée non persistée, pourra porter les rappels de la
session courante.

---

## 9. Cache et requêtes

| Ressource | Chargement | Réutilisation |
|---|---|---|
| `/api/brain` | une fois par session (`BrainData.load()`), rechargeable par `load(true)` | topologie du graphe |
| `/api/knowledge` | une fois, à la première ouverture de la Library ou recherche | index partagé par la Library, la recherche, l'inspecteur et le zoom |
| Contenu d'un nœud | aucun appel dédié | lu dans l'index déjà en mémoire |

Il n'y a **aucun appel HTTP par nœud**. L'aperçu au survol n'appelle rien : il
utilise le `meta.summary` déjà présent dans le graphe. Le graphe reste léger :
`/api/brain` ne transporte que des résumés tronqués à 240 caractères.

Test automatisé : « cache des fiches réutilisé » → PASS (même instance d'index
après un second appel).

---

## 10. Performance

- Le graphe n'a pas changé de coût : mêmes nœuds, même boucle de rendu.
- La couche sémantique ajoute une passe de texte bornée à 8/22/60 labels selon
  le niveau, avec sortie anticipée hors écran.
- Les scores et résumés sont calculés **une fois** au montage
  (`Zoom.build()`), pas à chaque frame.
- Les relaxations du graphe restent limitées à 140 itérations au montage.

Mesure de FPS : non refaite ici — elle n'est pas fiable dans mon environnement
(voir `jarvis-spatial-v5-phase3-1-production-readiness.md` §10). À vérifier avec
`await JarvisSpatial.perf()` sur la vue mémoire.

---

## 11. Tests

Harnais : `await JarvisSelfTest.run()`. Dernier passage :
**57 PASS · 0 FAIL · 2 SKIP** sur 59.

Les 16 tests ajoutés dans cette phase :

| Test | Résultat |
|---|---|
| canonicalisation (bruts → canoniques) | PASS — 111 → 87 |
| doublons fusionnés > 0 | PASS — 24 |
| aucun souvenir inventé (MEMORY = 0) | PASS |
| sous-types de capacités présents | PASS — TOOL 43 / CONNECTOR 5 / WORKFLOW 1 |
| cache des fiches réutilisé | PASS |
| recherche dans le contenu | PASS — 20 résultats, dont des correspondances de contenu |
| Library : liste des connaissances | PASS — 37 lignes |
| Library : compteurs canoniques | PASS |
| Library : 0 souvenir annoncé honnêtement | PASS |
| Library : pas de faux souvenir | PASS |
| Library : capacités sous-typées | PASS |
| zoom : seuils sémantiques | PASS |
| zoom : scores d'importance calculés | PASS — 87 nœuds notés |
| zoom : densité de labels croissante | PASS |
| zoom : priorité au rappel | PASS |
| pas de fausse historique de rappel | PASS |

Les 2 SKIP restent ceux des phases précédentes (FPS réels, dry-run réseau réel).

---

## 12. Limites

1. **Timeline non faite** (§8).
2. **Responsive non re-testé** pour la Library sur les cinq résolutions ;
   des points de rupture sont en place (1500 px et 1100 px) mais non vérifiés.
3. **Graphe à 111 nœuds bruts** : le moteur de rendu affiche encore les nœuds
   bruts ; la canonicalisation agit sur les libellés, l'inspecteur, la Library,
   la recherche et les scores, mais les doublons restent dessinés comme points.
   Les unifier dans le rendu demanderait de reconstruire la disposition.
4. **`meta.summary` tronqué à 240 caractères** : l'aperçu au survol et le résumé
   du zoom en dépendent pour les nœuds non-knowledge.
5. **Aucune métrique d'usage** : ni dernière utilisation, ni fréquence de rappel
   — les tris et filtres correspondants n'existent donc pas.
6. **26 résumés sur 87 concepts** : les capacités (outils, connecteurs) n'ont
   pas de texte descriptif dans le graphe, seulement leurs métadonnées.
7. Une **modification backend** a été nécessaire et documentée : exposition de
   `ref_type` / `ref_id` dans le `meta` des nœuds persistés, sans quoi la
   déduplication aurait été une hypothèse (26 correspondances de libellé sur 37).

---

## 13. Fichiers

| Fichier | Rôle |
|---|---|
| `ui/js/v5/spatial_brain_data.js` | canonicalisation, typage, sous-types, cache, recherche |
| `ui/js/v5/spatial_brain_inspector.js` | inspecteur, aperçu, recall, « Ouvrir dans la Library » |
| `ui/js/v5/spatial_brain_library.js` | **nouveau** — Brain Library |
| `ui/js/v5/spatial_brain_zoom.js` | **nouveau** — zoom sémantique, priorité et collision des labels |
| `ui/js/v5/spatial_modules.js` | labels hérités désactivés, intensité des liens déléguée au zoom |
| `ui/js/v5/spatial_selftest.js` | +16 tests |
| `ui/css/v5/spatial_phase2.css` | styles Library |
| `jarvis/brain_manager.py` | exposition de `ref_type` / `ref_id` (additif) |
