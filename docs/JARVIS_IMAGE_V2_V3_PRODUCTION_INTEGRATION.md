# JARVIS — Intégration production Image V2 + Image Edit V3

Mise en production de ce qui a été validé par Quality Validation V2.
Aucun nouveau benchmark, aucun nouveau modèle, aucune R&D.

---

## 1. Fichiers modifiés

| Fichier | Changement |
|---|---|
| `jarvis/image_quality.py` | Étapes planifiées alignées sur le vocabulaire réel (`LOADING_MODEL`, `HI_RES`, `UPSCALE`, `SAVING`) |
| `jarvis/image_runtime.py` | Progression pilotée par le **WebSocket ComfyUI** au lieu d'un minuteur ; table `_NODE_STAGE` étendue à l'édition |
| `jarvis/image_edit_graph.py` | Upscale de livraison borné (`MAX_DELIVERY_EDGE`), `source_size` ajouté au plan |
| `jarvis/server.py` | Routes `POST /api/images/import`, `POST /api/images/<id>/edit`, helper `_png_dimensions` ; `source_size` transmis à l'upscale |
| `ui/js/settings.js` | Carte **IMAGE ENGINE** (V2 / Legacy V1) + profil par défaut ; anciens réglages SDXL regroupés sous « LEGACY V1 » |
| `ui/js/image_studio.js` | Import d'image, opérations Image Edit V3, étapes réelles, seed / pipeline / opération affichés |
| `ui/css/image_studio.css` | Styles du bouton d'import |
| `tools/smoke_integration.py` | **Nouveau** — smoke tests via l'API HTTP réelle de JARVIS |
| `tests/test_image_quality_v2.py` | Deux tests mis à jour (renommage des étapes, masque automatique) |

Aucun fichier supprimé. Aucun code V1 retiré.

## 2. V2 activé par défaut

`ImageGenManager._v2_enabled()` lit `image.pipeline_version`, défaut **`v2`**.
Le sélecteur est désormais visible dans **Paramètres → Image Generation → IMAGE ENGINE** :

- **V2** — Z-Image + hi-res + ESRGAN *(défaut)*
- **Legacy V1** — workflow golden figé

Les profils sont exactement ceux validés. Aucun réglage rejeté n'a été
réintroduit : pas de 20 steps, pas de denoise ≥ 0,35, pas de sharpening, pas de
résolution native ≥ 1536. Un test échoue explicitement si l'un d'eux revient
(`tests/test_image_quality_v3.py::TestValidatedProfiles`).

| Mode | Steps | Base | Hi-res | Denoise | Upscale final |
|---|---|---|---|---|---|
| FAST | 8 | 1024 | — | — | — |
| BALANCED | 12 | 1024 | — | — | ESRGAN ×1,5 |
| QUALITY | 12 | 1024 | ×1,5 | 0,25 | — |
| ULTRA | 12 | 1152 | ×1,5 | 0,30 | ESRGAN ×1,3 |

## 3. Image Edit V3 activé

Depuis le panneau **Génération d'images** :

1. **Importer une image** (PNG / JPEG / WebP, 25 Mo) → devient un job éditable.
2. Opérations : changer le fond · PNG transparent · restyler · upscale ·
   supprimer le fond.
3. **Voir le masque** avant d'éditer, avec inverser / dilater / éroder /
   réinitialiser.
4. Lancer l'édition.

La segmentation BiRefNet est déclenchée automatiquement quand l'opération a
besoin d'une sélection. Le changement de fond conserve la méthode **structurelle**
validée : le sujet est recomposité depuis ses pixels d'origine
(`ImageCompositeMasked`), pas simplement suggéré au modèle par le prompt.

Les demandes que la segmentation ne peut pas honorer (« enlève l'objet à
gauche », « modifie uniquement le ciel ») sont **refusées en HTTP 422** avec la
raison et la solution, jamais approximées.

## 4. Comportement de repli

| Situation | Comportement |
|---|---|
| Pression mémoire (`out of memory`, `VRAM Allocation failed`, `Fault failed`, `device not ready`) | Rendu relancé un cran plus bas, `downgraded_from` renseigné et affiché dans l'UI |
| Timeout | Interruption propre, attente de vidage de la file, message indiquant que le format dépasse le mode |
| ComfyUI lent après interruption | Health-check avec 3 tentatives — plus de cascade d'échecs |
| V2/V3 en échec | L'erreur est remontée telle quelle ; **Legacy V1 reste sélectionnable** dans les Paramètres. Aucun repli silencieux vers V1 : masquer un échec de V2 empêcherait de le corriger |

Le seed réellement utilisé est résolu dans `RenderPlanner.plan()` et stocké tel
quel. Aucun `seed = 0` artificiel — sauf pour le détourage, qui ne diffuse pas
et n'a donc légitimement pas de seed.

## 5. Progression UI

Les étapes proviennent des évènements `executing` / `progress` du WebSocket
ComfyUI : c'est le node réellement en cours qui détermine l'étape affichée.
Une étape absente du graphe n'apparaît jamais — un rendu FAST n'affiche pas
`HI_RES`.

Séquence observée sur un rendu QUALITY réel :

```
PREPARING 0.02 → PROMPTING 0.05 → LOADING_MODEL 0.10
→ GENERATING 0.30 → HI_RES 0.62 → SAVING 0.97 → COMPLETE 1.0
```

## 6. Smoke tests

`python tools/smoke_integration.py --base http://127.0.0.1:8766`
Exécutés contre une **instance JARVIS réelle** (routes HTTP de l'UI), pas le
harnais de benchmark. Instance lancée sur le port 8766 pour ne pas interrompre
celle de l'utilisateur sur 8765.

| # | Test | Résultat |
|---|---|---|
| A | Affiche giveaway ULTRA | ok — 1830×2662, 101,3 s, seed 1940561394 |
| B | Bannière Discord BALANCED | ok — 2016×1152, 23,2 s |
| C | Image QUALITY | ok — 1536×1536, 51,0 s |
| D | Image ULTRA | ok — 1280×1792, 58,7 s, **rétrogradé depuis ULTRA** |
| E | Changement de fond | ok — 49,4 s, segmentation `birefnet`, sujet préservé |
| F | Détourage PNG | ok — 1,1 s, alpha correct |
| G | Upscale | ok — 12,9 s *(5120×7168 avant correctif, voir §8)* |
| H | Restyle | ok — 51,6 s |
| I | Affiche avec texte exact | ok — texte composé en PIL, **aucun caractère inventé** |
| J | Refus d'une édition ciblée non supportée | ok — HTTP 422 avec la raison |

**10/10.** Résultats et images : `bench/smoke/`.

Vérification manuelle effectuée sur I (texte exact, bandes propres, pas de
« GIVAWAY »), E (fond remplacé, sujet intact) et F (alpha 0 sur le fond).

## 7. Tests de non-régression

| Suite | Résultat |
|---|---|
| `tests/test_image*.py` | **87 tests — OK** |
| `tests/test_system.py` | **45 tests — OK** |

Deux tests existants mis à jour, aucun supprimé :
- les noms d'étapes (`REFINING`/`UPSCALING` → `HI_RES`/`UPSCALE`) — renommage
  volontaire de l'intégration ;
- « une opération masquée exige un masque » → « obtient un masque automatique »,
  l'ancien contrat étant précisément ce qu'Image Edit V3 supprime.

## 8. Bugs trouvés pendant l'intégration

1. **Upscale non borné.** ESRGAN agrandit toujours ×4 : un source 1280×1792 est
   revenu en **5120×7168, fichier PNG de 40 Mo**, que personne n'avait demandé.
   Corrigé par `MAX_DELIVERY_EDGE = 4096` sur le grand côté, sauf taille cible
   explicite. Le même cas renvoie désormais 2925×4096.
2. **ULTRA rétrogradé sur portrait.** Le cas D a déclenché le repli mémoire
   (`downgraded_from = ULTRA`). Ce n'est pas un bug du repli — il a fonctionné et
   l'a tracé — mais cela confirme qu'ULTRA en format portrait 1440×2016 est à la
   limite des 10 Go. Rien n'a été modifié : rétrograder proprement est le
   comportement voulu.

Un troisième point signalé initialement comme bug n'en était pas un :
l'historique semblait ne contenir que 2 entrées, c'était **mon erreur de lecture**
(je comptais les clés de la réponse JSON). Vérification refaite : **8 entrées**,
toutes avec `pipeline_version`, profil ou `edit_operation`, `seed`, résolution et
durée.

## 9. Limitations restantes

- **Pas de segmentation par texte.** « l'objet à gauche », « le ciel », « la
  voiture » sont refusés explicitement. Débloquer cela demanderait GroundingDINO
  + SAM (~1,1 Go) et des custom nodes tiers — hors périmètre de cette mise en
  production.
- **Le restyle transfère faiblement le style** à denoise 0,55. Monter à
  0,65–0,70 renforcerait l'effet au prix d'une dérive du sujet ; non mesuré,
  donc non changé.
- **ULTRA en grand format portrait rétrograde** régulièrement sur 10 Go de VRAM.
  Fonctionnel, mais l'utilisateur reçoit QUALITY et en est informé.
- **Legacy V1 reste inopérant pour l'édition** (il exige un checkpoint SDXL
  absent). Il ne sert de secours que pour la génération texte→image.
- L'instance de smoke test tourne sur le port 8766 ; l'instance quotidienne de
  l'utilisateur (8765) devra être redémarrée pour charger le nouveau code.

---

## Statut

**GO — PRODUCTION INTEGRATION COMPLETE**
