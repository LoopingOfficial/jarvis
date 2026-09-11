# Avatar 3D de JARVIS

Humain numérique complet : corps entier riggé, blendshapes faciaux, visèmes,
locomotion réelle, regard vivant et réactions branchées sur les vrais
événements du système.

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

- `ui/dev/avatar_stage.html` — scène autonome, tous les clips et gestes
- `ui/dev/avatar_test.html` — inspection brute du GLB
- `ui/dev/avatar_suite.js` — 38 tests ; console : `await runAvatarSuite()`

## Règle de fond

Une animation spécifique correspond toujours à une action réelle : RECALLING
seulement si la mémoire est consultée, CODING seulement si un outil fichier
tourne, WALKING seulement si un déplacement est demandé. Aucun mouvement
décoratif.
