# Avatar 3D de JARVIS

## Ce qui tourne aujourd'hui : la tête holographique

L'accueil affiche une tête holographique **construite en code**, pas un modèle
chargé. Aucun GLB n'est téléchargé : elle apparaît en une frame.

```
ui/js/avatar/holo_head.js      géométrie sculptée, squelette, poids de peau
ui/js/avatar/holo_visemes.js   texte français → suite de visèmes
ui/js/avatar/holo_viewer.js    rendu, comportement, parole, API publique
```

**Pourquoi construite et non importée.** Le dépôt a accumulé treize GLB et
quatre documents de tentatives (voir plus bas). Un modèle importé arrive avec
le rig d'un autre, ses conventions d'axes et ses morphs manquants : on passe
son temps à deviner ce qu'on a reçu. Ici chaque os est posé par le code, donc
connu.

**Le rig** est un vrai `THREE.Skeleton` — huit os, la peau pondérée par
formule, pas par carte peinte :

```
racine → cou → tête → mâchoire (parole)
                    → œil G / œil D (regard)
                    → sourcil G / D (expression)
```

La rampe de poids de la mâchoire fait que la joue suit le menton ; sans elle on
verrait la découpe bouger d'un bloc.

**La parole.** L'API Web Speech n'expose ni le signal audio ni les phonèmes —
`tts.audio_level` n'est jamais émis sur ce chemin. Faire onduler la mâchoire
au hasard serait inventer une donnée. On articule donc le texte RÉELLEMENT
prononcé, transporté par `tts.started`, découpé en sept visèmes (digrammes
français traités avant les lettres seules : « ou », « on », « ch »…). La dérive
de l'estimation est corrigée en continu par `tts.boundary`, émis à chaque mot
par le moteur vocal — un tiers de l'écart rattrapé par évènement, sinon la
bouche saute.

**Le comportement** est branché sur des évènements réels, jamais joué à vide :

| évènement | état | effet |
|---|---|---|
| `tts.started` / `tts.completed` | SPEAKING | mâchoire + visèmes + hochements |
| `voice.listening` | LISTENING | sourcils levés, regard stable |
| `chat.thinking` | THINKING | regard qui dérive vers le haut, sourcils froncés |
| `agent.started` / `agent.completed` | WORKING | présence accrue |
| bus outils/agents | — | `gesture()` : hochement bref |

Clignements toutes les 3 à 7 secondes, parfois par deux ; saccades oculaires
rapides sous une tête lente ; respiration ; suivi du curseur atténué et
temporaire. Mesuré sur 11 s au repos : 3 clignements, 16 saccades, mâchoire
immobile, 50 fps.

## Historique : l'humain numérique en GLB

Les treize `.glb` de `ui/assets/avatar/` et la chaîne Blender ci-dessous sont
l'approche précédente, conservée mais **plus affichée sur l'accueil**.

## Chaîne de production

```
assets/blender/rig.py          squelette (55 os, doigts + yeux)
assets/blender/mesh_lib.py     primitives : loft, sphere, sculpt, cut_faces
assets/blender/body.py         géométrie : crâne sculpté, corps, vêtements
assets/blender/shapekeys.py    23 expressions + 12 visèmes
assets/blender/animations.py   28 clips (idle, locomotion, gestes additifs)
assets/blender/build_avatar.py assemblage + skinning + export GLB
assets/blender/preview.py      rendus de contrôle
```

Reconstruire le modèle (déterministe, même résultat à chaque exécution) :

```bash
"C:/Program Files/Blender Foundation/Blender 3.6/blender.exe" --background \
  --python assets/blender/build_avatar.py -- \
  --out ui/assets/avatar/jarvis.glb --blend assets/blender/jarvis_avatar.blend
```

Rendus de contrôle :

```bash
"C:/Program Files/Blender Foundation/Blender 3.6/blender.exe" --background \
  assets/blender/jarvis_avatar.blend --python assets/blender/preview.py -- --out preview
```

## Runtime

```
ui/js/avatar/core.js        chargement GLB, carte du rig, IK deux os
ui/js/avatar/behavior.js    comportement humain, regard, lip sync, gestes
ui/js/avatar/locomotion.js  scène, marche, caméra
ui/js/avatar/avatar.js      assemblage, machine à états, qualité GPU
ui/js/avatar_bridge.js      branchement sur le bus d'événements JARVIS
ui/js/three_app.js          bootstrap + adaptateur `window.JarvisRobot`
```

### Ordre d'une frame (il compte)

1. `mixer` — clips de base (idle/marche) + gestes **additifs**
2. `behavior` — respiration, transfert de poids, doigts, expression
3. `gaze` — yeux puis tête, en repère monde
4. `lipsync` — visèmes
5. `locomotion` — déplacement, choix du clip suivant
6. foot IK — les pieds ne traversent jamais le sol
7. `director` — la caméra suit le corps

## Anti-glissement de pied

La vitesse de lecture du cycle de marche est asservie à la vitesse au sol :

```
timeScale = vitesse / (foulée_par_cycle / durée_du_cycle)
```

La foulée (1,3666 m par cycle de 1,0667 s) est mesurée à la construction et
exportée dans les extras glTF. Le pied en appui ne dérive donc pas.

## API

```js
JarvisAvatar.setState('LISTENING' | 'THINKING' | 'SPEAKING' | …)
JarvisAvatar.speak(texte, { duration })      // lip sync par visèmes
JarvisAvatar.stopSpeaking({ bargeIn: true }) // interruption propre
JarvisAvatar.gesture({ gesture, emotion, intensity })
JarvisAvatar.moveTo('brain' | 'desk' | 'home' | …)
JarvisAvatar.lookAt(place | vecteur)
JarvisAvatar.setCameraMode('CALL' | 'FULL_BODY' | …)
JarvisAvatar.setQuality('low' | 'balanced' | 'high' | 'ultra')
JarvisAvatar.setGpuBusy(true)                // laisse le GPU à Ollama/ComfyUI
```

Côté serveur : `GET /api/avatar`, `POST /api/avatar/command`
(`move` / `look` / `gesture` / `view` / `state`), et les outils
`avatar.move`, `avatar.look`, `avatar.gesture` exposés au modèle.

## Bancs d'essai

Les pages autonomes de `ui/dev/` (scène de clips, inspection du GLB, suite de
38 tests) ont été supprimées avec le reste des maquettes héritées. Elles
restent récupérables dans l'historique git si un banc d'essai redevient utile.

## Règle de fond

Une animation spécifique correspond toujours à une action réelle : RECALLING
seulement si la mémoire est consultée, CODING seulement si un outil fichier
tourne, WALKING seulement si un déplacement est demandé. Aucun mouvement
décoratif.
