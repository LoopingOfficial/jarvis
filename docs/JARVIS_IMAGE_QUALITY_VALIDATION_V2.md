# JARVIS — Quality Validation V2

Objectif : prouver quels réglages améliorent réellement la qualité, et supprimer
ceux qui coûtent sans rien apporter.

Règle appliquée partout : **plus lent ≠ meilleur, plus de pixels ≠ meilleur,
plus de steps ≠ meilleur.** Un réglage n'est conservé que si une différence
visuelle est constatable.

---

## 1. État initial

Audit du 14/09/2026, RTX 3080 10 Go / 32 Go RAM, ComfyUI 0.35.1.

| Élément | État trouvé |
|---|---|
| Moteur | Z-Image-Turbo (Lumina2) + Qwen3-4B + `ae.safetensors` |
| Checkpoints SDXL | aucun |
| Upscaler | `4x-UltraSharp.pth` (67 Mo) |
| LoRA / ControlNet / inpaint | aucun |
| Noyau | CFG 1.0, `res_multistep` / `simple`, shift 3.0, verrouillé par `assert_official_core()` |
| Profils V2 | FAST 8 · BALANCED 12+ESRGAN · QUALITY 16+hires 0,35 · ULTRA 20+hires 0,38+post 1,35+sharpen 0,10 |
| Fallback OOM | présent, rétrograde d'un cran |
| Masques | fournis manuellement uniquement |
| VRAM au moment des tests | **10 040 Mo libres** — aucune contention |

### Défaut corrigé avant de mesurer

Quand aucun seed n'était fourni, `job["seed"]` enregistrait `0` alors que le
graphe en tirait un aléatoire : l'image ne pouvait pas être reproduite.
Le seed est désormais résolu dans `RenderPlanner.plan()`, donc plan, graphe et
job portent la même valeur. Sans cela, aucun benchmark n'est reproductible.

---

## 2. Benchmarks

Harnais : `tools/quality_validation.py`. Une seule variable change par planche,
seed fixe 20260914, deux sujets choisis parce qu'ils échouent différemment :
un **portrait** (révèle les artefacts de peau et de cheveux) et une **machine
symétrique** (révèle la dérive géométrique).

### Test A — nombre de steps (1024², sans hi-res)

| Steps | Temps portrait | Temps machine | Constat visuel |
|---|---|---|---|
| 8 | 20,9 s | 10,9 s | Peau propre, légèrement plus douce |
| 10 | 12,2 s | 11,9 s | Équivalent à 12 |
| **12** | **14,0 s** | **14,1 s** | **Meilleur équilibre détail / naturel** |
| 16 | 18,4 s | 18,5 s | Taches de rousseur qui fusionnent en plaques ; machine inchangée |
| 20 | 22,7 s | 22,8 s | Marbrure de peau nette ; machine inchangée |

**Réponse à la question posée :** le gain devient négligeable **dès 12 steps**.
Au-delà, le sampler distillé n'ajoute pas de détail — il **sur-densifie la
texture**. À 20 steps le portrait est *moins bon* qu'à 12 pour +62 % de temps,
et la machine est indiscernable.

### Test B — hi-res fix, courbe de denoise (base 1024, ×1,5, ESRGAN)

| Denoise | Portrait | Machine (géométrie) |
|---|---|---|
| sans hi-res | référence, 14 s | référence, 30 s |
| 0,20 | cheveux et cils nets, peau naturelle | conservée |
| **0,25** | **optimum portrait** — texture fine sans densification | conservée |
| **0,30** | très bon | **optimum machine** — joints, boulons, rayures nets, géométrie identique |
| 0,35 | taches qui commencent à fusionner | conservée |
| 0,40 | densification visible | conservée, léger changement de fond |
| 0,45 | sur-texturé | limite |
| 0,50 | sur-texturé | **dérive** : mécanisme interne différent, pieds modifiés, fond changé |

**Zone optimale : 0,20–0,30.** Le réglage V2 précédent (0,35 pour QUALITY,
0,38 pour ULTRA) était **au-dessus de l'optimum**.

### Test C — résolution de première passe (hi-res ×1,5, denoise 0,30)

| Base | Sortie | Temps machine | Constat |
|---|---|---|---|
| 768 | 1152² | ~45 s | Cohérent, mais design plus simple, moins de détail interne |
| 896 | 1344² | ~48 s | Cohérent |
| 1024 | 1536² | 50,4 s | Cohérent, riche — **défaut sûr** |
| 1152 | 1728² | 62,5 s | Symétrie parfaite, mécanisme le plus riche, aucun artefact |

Contrairement au 1536 natif testé en V1 (qui produisait une peau croûteuse),
**768 → 1152 restent tous cohérents**. 1152 coûte +25 % et apporte une
géométrie légèrement plus riche : c'est ce qui justifie ULTRA.

### Test D — upscale et sharpening

Note de lecture : ComfyUI met en cache la passe de base identique, donc les
temps ci-dessous sont le **coût marginal de l'étage d'upscale seul**.

| Variante | Coût marginal | Constat |
|---|---|---|
| aucun upscale | — | 1024², référence |
| Lanczos seul | 1,2 s | Interpolation : arêtes molles, ressorts et boulons flous |
| **ESRGAN** | **6,5 s** | **Arêtes réellement reconstruites, nettement plus net** |
| ESRGAN + sharpen 0,10 | 1,2 s | **Aucune différence visible** avec ESRGAN seul |
| ESRGAN + sharpen 0,25 | 1,2 s | **Halos sombres** autour des néons, liserés clairs sur le métal |

---

## 3. Réglages gagnants

1. **12 steps** — le plateau de qualité. Vrai pour le portrait comme pour la machine.
2. **Hi-res fix à denoise 0,25–0,30** — le seul levier qui ajoute du vrai détail.
3. **ESRGAN** — reconstruit les arêtes là où Lanczos ne fait qu'interpoler.
4. **Base 1024 par défaut, 1152 pour les géométries complexes.**
5. **Shift 3.0** — confirmé ; 5.0 dégradait (mesuré en V1).

## 4. Réglages rejetés

| Rejeté | Raison mesurée |
|---|---|
| 16 et 20 steps | Aucun gain ; dégrade la peau ; +30 à +62 % de temps |
| Denoise ≥ 0,35 | Sur-texturation des visages |
| Denoise ≥ 0,50 | Dérive géométrique du sujet |
| **Sharpening (toute valeur)** | 0,10 invisible, 0,25 crée des halos — **supprimé** |
| Lanczos comme upscaler | Battu par ESRGAN pour un coût quasi identique |
| Résolution native ≥ 1536 | Dégradation démontrée en V1 |

## 5. Profils finaux

| Mode | Steps | Base | Hi-res | Denoise | Upscale final | Sortie (1:1) | Sharpen |
|---|---|---|---|---|---|---|---|
| FAST | 8 | 1024 | — | — | — | 1024² | — |
| BALANCED | 12 | 1024 | — | — | ESRGAN ×1,5 | 1536² | — |
| QUALITY | 12 | 1024 | ×1,5 | 0,25 | — | 1536² | — |
| ULTRA | 12 | 1152 | ×1,5 | 0,30 | ESRGAN ×1,3 | 2246² | — |

**ULTRA ne diffère de QUALITY que là où une mesure le justifie** : une première
passe plus grande (Test C) et un upscale de livraison (Test D). Il ne monte ni
les steps ni le denoise, précisément parce que les tests A et B montrent que
cela dégraderait l'image. Le profil n'est pas rendu artificiellement plus lourd
pour paraître supérieur.

Comparé à l'état initial, ULTRA perd 8 steps, 0,08 de denoise et tout le
sharpening — il est donc à la fois **plus rapide et meilleur**.

## 6. VRAM

Pics mesurés sur les sweeps : **9 100 – 10 000 Mo** quelle que soit la variante,
y compris la passe hi-res 1728². La VRAM n'est pas le facteur limitant ; le
temps l'est.

Le plafond de résolution reste indexé sur la VRAM **totale** (ComfyUI streame
les 12 Go de poids depuis la RAM). La VRAM libre n'est consultée qu'en dessous
de 1 500 Mo. La protection réelle est le repli OOM, qui relance un cran plus bas
et le signale via `downgraded_from`.

Contention à connaître : Ollama, Blender et les jeux occupent la même carte.
Les mêmes rendus mesurés ici prenaient 2 à 3× plus longtemps lors de la session
précédente, carte partagée avec Ollama.

## 7. Benchmark métier

`tools/business_benchmark.py`, seed fixe 913377, pipeline de production réel.
Grille annotée : `bench/business/index.html` · planche aveugle : `blind.html`.

| Cas | Mode | Temps | Sortie | Verdict |
|---|---|---|---|---|
| Giveaway gaming vertical | ULTRA | 104 s | 1830×2662 | Composition d'affiche correcte, zones de texte respectées, rendu cartoon propre |
| Eternal Machine | ULTRA | 91 s | 2246×2246 | Symétrie et centrage parfaits, aucune déformation ; panneaux un peu sobres |
| Bannière Discord | QUALITY | 30 s | 2048×1152 | Format large correct, sujet non coupé |
| Asset PNG | QUALITY | 51 s | 1536×1536 | Sujet isolable ; détourage réel livré par la segmentation (§8) |
| Personnage gaming | ULTRA | 85 s | 2246×2246 | Anatomie et mains correctes ; **visage absent** (capuche fermée) |
| Affiche à zones de texte | QUALITY | 49 s | 1280×1792 | Bandes réservées respectées |
| Restyle | EDIT | 48 s | 1536×1536 | Sujet préservé, style faiblement transféré |

**Deux défauts trouvés par ce benchmark, et corrigés :**

1. **Texte inventé.** L'affiche giveaway est revenue avec « GIVAWAY » — mal
   orthographié — imprimé deux fois, précisément dans les zones réservées.
   L'instruction anti-lettrage n'était ajoutée que si un texte exact était
   détecté. Elle est désormais systématique pour POSTER et UI_CONCEPT, et la
   typographie est composée en post-traitement (§8).
2. **Cascade d'échecs après timeout.** Un budget BALANCED de 120 s a tué un
   rendu d'affiche, puis les six cas suivants ont échoué en « ComfyUI
   injoignable » — le health-check abandonnait au premier délai alors que le
   serveur finissait de se libérer. Corrigé : budget indexé sur la charge réelle
   **plus une allocation de chargement à froid** (le rechargement des 12 Go de
   poids dépasse à lui seul un rendu à chaud), health-check avec retry, et
   attente de vidage de la file après interruption.

Un troisième défaut a été trouvé et corrigé : l'échec ULTRA sur l'affiche
(`VRAM Allocation failed (non OOM)` / `Fault failed: 2`) n'était **pas**
reconnu comme une pression mémoire, donc le repli automatique ne se déclenchait
pas. Ces messages sont désormais dans `_OOM_MARKERS`.

## 8. Image Edit V3 — segmentation automatique

Démarré seulement après analyse du benchmark qualité, comme demandé.

### Modèle retenu, et écart assumé

Le choix validé était RMBG-1.4 (~176 Mo) pour rester sur le node natif. Or le
loader ComfyUI ne connaît qu'**une** architecture :
`BG_REMOVAL_MODELS = {"birefnet": ...}`. RMBG-1.4 ne s'y charge pas.
BiRefNet conserve la propriété recherchée — node natif, **aucun node tiers** —
au prix de la taille : `Comfy-Org/birefnet` → **423 Mo**. C'est le seul écart
au choix initial, et il est structurel, pas discrétionnaire.

### Architecture

```
image → RemoveBackground (BiRefNet) → masque sujet
      → [ThresholdMask] → [InvertMask si cible = fond] → [GrowMask ±]
      → aperçu du masque  |  SetLatentNoiseMask → édition
      → ImageCompositeMasked (sujet original recollé) → résultat
```

`MaskPlan` déclare explicitement sa cible (`subject` / `background`) plutôt que
de laisser la polarité implicite dans le câblage — c'est l'erreur la plus facile
à commettre et la plus coûteuse à diagnostiquer.

### Fidélité au sujet — mesurée

Changement de fond sur un personnage, masque automatique, sujet recomposité :

| Zone | Écart moyen vs original |
|---|---|
| **Sujet** (alpha > 250, 231 594 px) | **0,28 / 255** — 0,54 % des pixels bougent de plus de 2 |
| Fond | 29,9 / 255 — réellement remplacé |

C'est bien une édition locale, pas « une nouvelle génération ressemblante ».

### Deux bugs trouvés en construisant, et corrigés

1. **`FeatherMask` adoucit les bords rectangulaires de l'image, pas la
   silhouette.** Mesuré : le masque de fond tombait à 22–50 (au lieu de 254) sur
   les 10 px du pourtour, ce qui aurait laissé un **cadre d'ancien fond** après
   un changement d'arrière-plan. Le feather est retiré des masques de silhouette
   (BiRefNet fournit déjà un alpha doux) et réservé aux masques rectangulaires
   d'outpainting, où estomper les bords est justement l'objectif.
2. **Le prompt faisait halluciner un second personnage.** Le premier changement
   de fond a dessiné une deuxième personne dans la zone masquée, parce que le
   prompt — et la consigne interne — parlaient encore du « sujet », or le masque
   est précisément la zone où le modèle est libre. Les clauses de préservation
   sont maintenant retirées du prompt de fond, et la préservation est assurée
   **structurellement** par le recollage, pas par une suggestion au modèle.

### Ce qui fonctionne, et ce qui est refusé

| Demande | État |
|---|---|
| « change seulement le fond » | automatique |
| « garde exactement le personnage » | automatique, fidélité mesurée |
| détourage PNG transparent | automatique, **sans diffusion** (aucune invention possible) |
| restyle | automatique (sans masque) |
| outpaint, upscale | automatique |
| « enlève l'objet à gauche », « modifie uniquement le ciel » | **refusé explicitement** |

BiRefNet sépare sujet et arrière-plan ; il ne sait pas isoler un objet nommé.
`supports_request()` le dit et nomme la solution (GroundingDINO + SAM) au lieu
de renvoyer un masque sujet qui éditerait silencieusement la mauvaise zone.

### Éditeur de masque

Aperçu du masque avant édition, avec inverser / dilater / éroder / réinitialiser
(`POST /api/images/<id>/mask`). Le pinceau manuel n'est pas inclus dans cette
itération, conformément au choix retenu.

## 9. Limites

- Le **sujet change quand la résolution de base change** (latent différent à
  seed constant). Le test C compare donc des compositions voisines, pas la même
  image agrandie ; la conclusion « 768–1152 restent cohérents » tient, mais
  « 1152 est plus riche » repose en partie sur une composition plus favorable.
- Les temps du test D sont des **coûts marginaux** (cache ComfyUI), pas des
  rendus complets.
- Le jugement reste **visuel et humain**. Aucun score automatique n'a été
  inventé, et la planche aveugle existe précisément pour éviter de préférer la
  variante la plus coûteuse par principe.
- Deux sujets seulement (portrait, machine) pour les tests A–D. Les cas métier
  de la section 7 élargissent la couverture.
- **Le restyle transfère faiblement le style** (denoise 0,55). Monter à 0,65–0,70
  renforcerait l'effet au prix d'une dérive du sujet ; non mesuré, donc non changé.
- **Pas de segmentation par texte.** « l'objet à gauche », « le ciel » sont
  refusés, pas approximés. Débloquer cela demande GroundingDINO + SAM (~1,1 Go)
  et des custom nodes tiers.
- **Le personnage du benchmark n'a pas de visage** (capuche fermée) : la
  checklist « visage » de ce cas n'est donc pas évaluable sur ce rendu.
- **L'ULTRA sur très grande affiche reste en limite mémoire.** Il échouait avant
  le correctif ; il rétrograde désormais proprement en QUALITY, mais la
  rétrogradation reste visible dans les métadonnées (`downgraded_from`).
- Les gains de la section 2 reposent sur un **jugement visuel humain**. Aucun
  score automatique n'a été fabriqué.

## 10. Avant / après

| | V1 | V2 initial | V2 validé |
|---|---|---|---|
| QUALITY steps | 8 (turbo seul) | 16 | **12** |
| QUALITY denoise | — (pas de hi-res) | 0,35 | **0,25** |
| ULTRA steps | — | 20 | **12** |
| ULTRA denoise | — | 0,38 | **0,30** |
| Sharpening | — | 0,10 | **supprimé** |
| Upscale | Lanczos (interpolation) | ESRGAN | ESRGAN (confirmé) |
| Seed reproductible | non | **non** | **oui** |
| Édition sur masque manuel | impossible | requis | **automatique** |
| Fidélité du sujet en édition locale | — | non garantie | **0,28/255 mesuré** |
| Texte d'affiche | inventé par le modèle | inventé par le modèle | **composé en PIL** |
| Détourage PNG | absent | absent | **sans diffusion** |
