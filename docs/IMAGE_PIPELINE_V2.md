# Pipeline de génération d'images V2

**BUILD ID : `JARVIS_IMAGE_GENERATION_REBUILD_V1`**

Ce document enregistre ce qui a été mesuré, ce qui a été décidé, et pourquoi.

---

## 1. Audit de l'existant

Inspection réalisée le 14/09/2026 sur la machine cible (RTX 3080 10 Go, 32 Go RAM).

### Ce qui est réellement installé

| Emplacement | Contenu |
|---|---|
| `diffusion_models/` | `z_image_turbo_bf16.safetensors` (12,3 Go) |
| `text_encoders/` | `qwen_3_4b.safetensors` (8,0 Go) |
| `vae/` | `ae.safetensors` (335 Mo) |
| `checkpoints/` | **vide** |
| `upscale_models/` | **vide** → `4x-UltraSharp.pth` ajouté (67 Mo) |
| `loras/`, `controlnet/` | **vides** |

ComfyUI 0.34.0 (Desktop) / 0.35.1 au lancement, PyTorch 2.12+cu130.

### Les causes de la qualité insuffisante

1. **Le moteur « QUALITY » était du code mort.** `choose_image_engine()` route par
   défaut toute demande générique vers `QUALITY_ENGINE`, qui exige un checkpoint
   SDXL. Aucun n'est installé, donc la branche lève systématiquement
   *« installe un checkpoint SDXL »*. Les 5 générations réelles de l'historique
   ont **toutes** été exécutées par le moteur turbo, à 8 steps / 1024².
   Un modèle turbo était donc l'unique moteur — exactement ce qu'il fallait éviter.

2. **Le prompt était dégradé avant d'atteindre le modèle.** `PromptComposer`
   produisait du tag-soup façon Stable Diffusion
   (`pink shark, in underwater ocean, high quality stylized realism, color palette: blue`).
   Or le texte-encodeur est **Qwen3-4B**, qui lit des phrases. Pire,
   `ImageIntentAnalyzer` traduisait à la main un mini-lexique FR→EN et produisait
   du franglais (`"montre moi un requin jaune fluo"` → `"moi un shark yellow neon"`).
   Le style par défaut injecté était littéralement
   `"high quality stylized realism"` — une demande explicite d'aspect stylisé.

3. **Le negative prompt n'a jamais eu d'effet.** Z-Image-Turbo est distillé pour
   CFG 1.0, où la guidance négative est mathématiquement inopérante. Tout le
   `NegativePromptBuilder` tournait à vide.

4. **Le workflow générique était incomplet.** `fast_text2image.json` omettait
   `ModelSamplingAuraFlow(shift=3.0)`, présent dans le workflow golden et requis
   par l'architecture Lumina2.

5. **Aucun upscale réel.** `upscale.json` était un `ImageScale` lanczos —
   de l'interpolation, zéro détail ajouté. `transparent_asset.json` était un
   passthrough vide.

6. **Aucune seconde passe.** Une seule passe à 1024², d'où l'aspect plastique.

### Mesures A/B (portrait, seed fixe 424242)

| Variante | Temps | Pic VRAM | Verdict visuel |
|---|---|---|---|
| A — 8 steps, 1024², shift 3.0 (**pipeline actuel**) | 36,8 s (13,1 s à chaud) | 9 747 Mo | Correct, peu de micro-détail |
| B — 16 steps, 1024² | 43,5 s | 9 790 Mo | Gain marginal |
| C — 1536² natif, 16 steps, shift 5.0 | 62,6 s | 9 092 Mo | **Dégradé** — peau croûteuse |
| D — hi-res fix 1536, denoise 0.40 | 62,0 s | 9 744 Mo | **Meilleur** — cheveux, cils, tissus |
| E — hi-res fix 1536, denoise 0.55 | 41,7 s | 9 712 Mo | Sur-texturé, dérive vers C |
| F — euler, 8 steps | 13,1 s | 9 424 Mo | Équivalent à A |

**Conclusions retenues :**
- Augmenter les steps du sampler distillé ne paie pas (B).
- La haute résolution native dégrade (C) : le modèle n'a pas été entraîné pour.
- Le `shift` doit rester à 3.0 ; 5.0 produit des artefacts.
- Le gain réel vient de la **seconde passe à faible denoise sur une image
  agrandie par ESRGAN** (D), avec un optimum mesuré autour de **0.35–0.40**.

---

## 2. Architecture V2

```
requête utilisateur
   ↓  detect_image_type()      → PORTRAIT | PRODUCT | POSTER | GAMING
   ↓                             UI_CONCEPT | ILLUSTRATION | PHOTOREAL | IMAGE_EDIT
   ↓  resolve_mode()           → FAST | BALANCED | QUALITY | ULTRA
   ↓  PromptEnricher.build()   → sujet verbatim + cadrage + lumière + rendu
   ↓  RenderPlanner.plan()     → résolution, steps, shift, hi-res, garde VRAM
   ↓  ZImageGraphBuilder       → graphe ComfyUI API
   ↓  ImageRenderer.render()   → exécution, étapes, timeout, repli OOM
```

| Module | Rôle |
|---|---|
| `jarvis/image_quality.py` | Modes, pipelines par type, enrichissement, planification |
| `jarvis/zimage_graph.py` | Construction du graphe texte→image, garde du noyau officiel |
| `jarvis/image_edit_graph.py` | img2img, inpainting, outpainting, upscale pur |
| `jarvis/image_runtime.py` | Client ComfyUI, progression, timeouts, repli OOM |
| `jarvis/imagegen_v2.py` | Branchement sur `ImageGenManager` |
| `tools/image_benchmark.py` | Benchmark reproductible V1 vs V2 |

### Profils de qualité

| Mode | Steps | Hi-res | Denoise | Upscale final | Sortie type | Budget |
|---|---|---|---|---|---|---|
| FAST | 8 | — | — | — | 1024² | 60 s |
| BALANCED | 12 | — | — | ×1,5 ESRGAN | 1536² | 120 s |
| QUALITY | 16 | ×1,5 | 0,35 | — | 1536² | 300 s |
| ULTRA | 20 | ×1,6 | 0,38 | ×1,35 + sharpen 0,10 | ~2246² | 900 s |

Le mode est déduit de la formulation et **toujours forçable**. Un mot de rapidité
(« aperçu », « brouillon ») l'emporte sur un mot de qualité : un brouillon ne doit
pas coûter une passe multiple. Les affiches ont un **plancher** à QUALITY.

### Fidélité du prompt

Contrat strict : le sujet de l'utilisateur est conservé **verbatim** et ouvre
toujours le prompt final. Le pipeline ne fait qu'**ajouter** des phrases de
cadrage, lumière et rendu. Aucune traduction, aucune paraphrase. Seuls
l'emballage impératif (« crée-moi une image de… ») et les mots purement
qualitatifs (« 4k », « ultra qualité ») sont retirés — ces derniers survivent
comme sélection de mode.

Le negative prompt est construit mais **déclaré inactif** (`negative_active =
False`, note `negative_prompt_inactive_at_cfg_1`) tant que le moteur tourne à
CFG 1.0. Il n'est pas injecté dans le graphe : mieux vaut l'annoncer que
l'ignorer en silence.

### Texte dans les images

Le modèle ne lettre pas de façon fiable. Pour POSTER et UI_CONCEPT, le texte
demandé est **extrait** (`exact_text`), la composition réserve délibérément des
zones vides, et le prompt demande explicitement de laisser ces zones libres
plutôt que d'inventer des lettres. La typographie se compose ensuite côté
frontend/PIL.

---

## 3. Édition d'image

Aucun checkpoint d'inpainting n'étant installé, les opérations passent par les
mêmes poids Z-Image avec des latents masqués.

| Opération | Denoise | Masque | Fiabilité |
|---|---|---|---|
| `remove` | 0,85 | requis | bonne |
| `replace_background` | 0,90 | requis | bonne |
| `recolor` | 0,55 | requis | bonne |
| `add_object` | 0,95 | requis | **modérée** |
| `restyle` | 0,55 | non | bonne |
| `outpaint` | 1,0 | auto (padding) | **modérée** |
| `upscale` | — | non | bonne (aucune diffusion) |

Les opérations « modérées » portent la note `no_inpainting_checkpoint_installed` :
c'est la limite réelle de l'installation, pas une approximation.

L'upscale pur ne contient **aucun KSampler** — il ne peut donc rien inventer.

---

## 4. VRAM et robustesse

- Le plafond de résolution est indexé sur la VRAM **totale**, pas la VRAM libre :
  ComfyUI streame les poids depuis la RAM, et une passe 1536² a été mesurée à
  9,7 Go de pic sur une carte de 10 Go — elle passe.
- La VRAM libre n'est consultée qu'en dessous de 1 500 Mo (`CRITICAL_FREE_VRAM_MB`).
- La vraie protection est le **repli OOM** : sur erreur mémoire, le rendu est
  relancé un cran plus bas, et `downgraded_from` le signale à l'utilisateur.
- Chaque mode a un **budget temps** ; au-delà, la génération est interrompue
  proprement côté ComfyUI (`/interrupt`).

Attention : Ollama (le LLM de JARVIS), Blender et les jeux occupent la même
carte. Une génération lancée pendant qu'Ollama tient 7 Go réduira la résolution
ou déclenchera le repli.

---

## 5. Benchmark

```bash
python tools/image_benchmark.py --out bench/v1-vs-v2
```

Huit cas (portrait, affiche gaming, produit, paysage, concept UI, personnage
stylisé, sujets multiples, restyle d'image) rendus à seed fixe (776611) par les
deux pipelines. Produit `index.html` — une grille côte à côte pour inspection
humaine — plus `results.json` avec temps, résolution, pic VRAM et erreurs.

### Résultats mesurés (14/09/2026, RTX 3080 10 Go)

| Cas | V1 temps / résolution | V2 temps / résolution | Mode V2 |
|---|---|---|---|
| portrait | 31,2 s — 1024² (1,05 MP) | 83,7 s — 1280×1792 (2,29 MP) | QUALITY |
| poster_gaming | 29,6 s — 1024² | 169,7 s — 1814×2592 (4,70 MP) | ULTRA |
| product | 26,1 s — 1024² | 94,3 s — 1536² (2,36 MP) | QUALITY |
| landscape | 24,5 s — 1024² | 91,2 s — 1536² | QUALITY |
| ui_concept | 56,7 s — 1024² | 119,1 s — 2048×1152 | QUALITY |
| character | 32,3 s — 1024² | 101,0 s — 1536² | QUALITY |
| multi_subject | 26,4 s — 1024² | 78,8 s — 1536² | QUALITY |
| edit_restyle | **échec** (checkpoint SDXL absent) | 86,1 s — 1536² | restyle |

**Moyennes :** V1 — 7/8 réussis, 32,4 s, 1,05 MP. V2 — 8/8 réussis, 103,0 s,
2,64 MP. Pic VRAM comparable (9,1–10,0 Go) : le coût du hi-res fix est en
temps, pas en mémoire.

Le format suit désormais le type demandé — une affiche sort en 2:3, un concept
UI en 16:9 — là où V1 rendait tout en carré 1024².

### Limites constatées

- **Le restyle préserve bien le sujet mais transfère faiblement le style** à
  denoise 0,55. Un changement stylistique marqué demanderait 0,65–0,70, au prix
  d'une dérive du sujet. Valeur laissée à 0,55 faute de mesure comparative.
- **Les opérations masquées exigent un vrai masque.** Aucun modèle de
  segmentation n'est installé : « retire la voiture » ou « change le fond »
  demandent une sélection fournie par l'UI. Le pipeline refuse explicitement
  plutôt que de produire une édition sans effet.

Aucun score automatique : le jugement visuel reste humain.

---

## 6. Réversibilité

V1 n'est pas supprimé. `Settings → Image → pipeline_version = "v1"` rétablit
l'ancien chemin. Les workflows JSON d'origine sont intacts dans
`workflows/comfyui/`. Le noyau de conditioning officiel Z-Image est vérifié à
chaque construction par `assert_official_core()` : toute régression du graphe
échoue bruyamment au lieu de dégrader silencieusement les images.
