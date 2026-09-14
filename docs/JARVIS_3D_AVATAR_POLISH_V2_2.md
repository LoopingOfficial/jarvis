# JARVIS_3D_AVATAR_POLISH_V2_2

Passe de finition visuelle sur la base `JARVIS_3D_AVATAR_POLISH_V2_1`.
**L'avatar n'est pas active** : `ui/js/avatar/avatar.js:75` pointe toujours sur
`/assets/avatar/jarvis_premium.glb`.

Candidats produits :

| Variante | Fichier | Poids |
| --- | --- | --- |
| HIGH | `ui/assets/avatar/jarvis_v2_2_high.glb` | 4,03 Mo |
| BALANCED | `ui/assets/avatar/jarvis_v2_2_balanced.glb` | 3,06 Mo |

---

## 1. Changements visuels

### Vêtements — refonte de la construction

Le haut n'est plus **dérivé du maillage du corps**. C'était la cause racine :
une copie décalée de la peau conserve l'anatomie, et sept passes de lissage ne
suffisaient pas à effacer tétons et nombril. Une découpe face par face donnait
en plus des bords dentelés.

Le torse est désormais une **surface loftée** à partir de sections
horizontales. Pour chaque hauteur, le rayon du corps est mesuré angle par
angle, puis :

- **symétrisé** (moyenne gauche/droite) ;
- **filtré passe-bas** sur l'angle (noyau de ±7 échantillons).

Un téton est une fréquence angulaire élevée, la cage thoracique une basse : le
filtrage supprime le premier et conserve la seconde. L'anatomie devient
impossible par construction, pas par correction.

Autres pièces :

- **manches** : tubes effilés le long des os épaule → coude → mi-avant-bras,
  rayon mesuré sur le bras puis décollé ;
- **col** : anneau lofté autour de l'axe du cou, rayon symétrisé — la symétrie
  est structurelle, plus le résultat d'un hasard de maillage ;
- **matière** : bleu-charbon désaturé (`0.064, 0.073, 0.088` linéaire,
  rugosité 0.74) au lieu du gris plat. Se distingue nettement de la peau sous
  le même éclairage.

L'empiècement d'épaule par sélection de faces a été **abandonné** : il traçait
une ligne de coupe dentelée en travers du torse. La lecture « technique »
repose sur le col et la matière.

### Visage

Identité inchangée. Uniquement :

- **sourcils** : nettement plus sombres (`0.043, 0.030, 0.023`) et quasi mats
  (rugosité 0.94) — ils lisaient comme du plastique brun ; épaisseur portée de
  2,2 à 2,8 mm, plaqués 0,5 mm plus près de la peau ;
- **lèvres** : rugosité 0.36 → 0.52 et teinte moins saturée. Le gloss a disparu ;
  la couleur vient du bake, pas du matériau.

Peau, yeux (sclère, iris, anneau limbique, cornée) et cheveux : **inchangés**,
conformément à la consigne de non-régression.

### Expressions

Aucun code ajouté : la différenciation existait déjà et a été vérifiée. Chaque
état applique une humeur distincte via `behavior.setMood()`, qui pilote
`smileLeft/Right`, `browUp`, `browDown`, `squintLeft/Right` —
donc pas seulement la bouche :

| État | Humeur | Morphs dominants |
| --- | --- | --- |
| IDLE | neutral | smile 0.12 |
| LISTENING | attentive | smile 0.16, browUp 0.18 |
| THINKING / RECALLING | thinking | browDown 0.22, squint 0.18 |
| SPEAKING | friendly | smile 0.38, browUp 0.12 + visèmes |
| SUCCESS | pleased | smile 0.55, browUp 0.20 |
| WARNING / ERROR | concerned | browUp 0.30, squint 0.10 |

**Clignement** : mesuré sur 60 s simulées — 14 clignements, intervalles
3,46 / 5,28 / 4,94 / 6,16 / 3,08 / 6,02 s, plus des doubles clignements à
0,3 s. Non périodique, confirmé par test automatique.

### Cadrages

Trois vues ajoutées dans `ui/js/avatar/framing.js`, **enregistrées dans la
table `CAMERA_MODES` existante** : `CameraDirector` continue de gérer seul les
transitions, rien n'est dupliqué.

| Vue | Distance | Hauteur caméra | LookAt | Champ vertical | Marge haute | Marge basse |
| --- | --- | --- | --- | --- | --- | --- |
| HOME | 2,25 m | 1,40 m | 1,26 m | 30° | 6,9 % | 63,3 % |
| CHAT | 1,72 m | 1,46 m | 1,38 m | 29° | 5,0 % | 54,5 % |
| VOICE | 1,26 m | 1,56 m | 1,52 m | 26° | 5,3 % | 32,8 % |

Toutes entre 26° et 30°, soit l'équivalent d'un 85–100 mm : au-delà de ~35° le
nez grossit et les oreilles reculent. `safeFrame(nom)` renvoie les marges
réelles et sert de test automatique.

---

## 2. Sourcils

**Solution finale : objet séparé, rigidement attaché à l'os `head`, et décalé
en transform depuis le morph.**

La fusion géométrique reste écartée : elle produisait une géométrie qui
**n'était plus rendue du tout** — vérifié en forçant le matériau en rouge vif,
invisible au rendu, alors que les 88 sommets existaient, portaient le bon slot
et se situaient 2,3 mm au-dessus de la peau.

`JarvisAvatar._updateBrows()` lit `browUp` / `browDown` et applique un décalage
vertical de +4,5 / −4,0 mm, calé sur la course réelle de la shape key mesurée
au build. Les sourcils suivent donc l'animation.

**Limite assumée** : c'est une translation rigide, pas une déformation. Les
sourcils montent et descendent avec l'arcade, mais ne se déforment pas
(pas de froncement asymétrique, pas de courbure). À l'échelle des cadrages
HOME/CHAT/VOICE, l'écart n'est pas discernable ; en très gros plan, il le
serait.

---

## 3. Animation — actions réellement évaluées

Mesure rigoureuse, `isRunning() && getEffectiveWeight() > 0` :

| État | Actions instanciées | Actions évaluées |
| --- | --- | --- |
| IDLE | 41 | **1** (`idle_neutral@1`) |
| SPEAKING | 41 | **1–2** (`idle_attentive@1` + geste additif ponctuel) |
| Après 54 transitions | 41 | **1** (`idle_neutral@1`) |

Deux défauts corrigés dans `ui/js/avatar/animation_state.js` :

1. `LocomotionController._fade()` appelle `reset()` sur la nouvelle action, ce
   qui **annule un `fadeOut` en vol**. Quand `cycleIdle()` relançait une base
   pendant un crossfade, l'ancienne action restait démarrée à poids 1
   indéfiniment. `_reap()` arrête explicitement toute action qui n'est ni la
   base courante ni le geste en cours — three.js n'arrête jamais une action
   seul.
2. `_fade()` sort immédiatement si l'action demandée est déjà `this.base`, y
   compris lorsqu'elle a été arrêtée entre-temps. L'avatar se retrouvait alors
   **sans aucune base**. `_forceBase()` remet `locomotion.base` à `null` avant
   de relancer, et un filet de sécurité dans `_reap()` garantit qu'une base
   tourne toujours.

Mémoire, sur 6 cycles de 9 états (54 transitions) :

- `AnimationAction` créées : **41 → 41**, aucune création en trop ;
- minuteurs de nettoyage en attente : **0** ;
- aucun listener dupliqué (les écouteurs sont posés une fois dans
  `_bindLifecycle()` et retirés dans `dispose()`).

---

## 4. HIGH / BALANCED

| | HIGH | BALANCED |
| --- | --- | --- |
| Poids | 4,03 Mo | 3,06 Mo |
| Triangles | 50 204 | 50 204 |
| Meshes | 21 | 21 |
| Os | 55 | 55 |
| Morphs | 35 | 35 |
| Clips | 41 | 41 |
| Visèmes | 12 | 12 |
| Albédo | 2048 JPEG | 1024 JPEG |
| Normale | 1024 PNG | 512 PNG |
| Rugosité | 512 JPEG | 256 JPEG |
| Chargement local | 1 853 ms (à froid) | 608 ms |

HIGH passe de 4,24 Mo (V2.1) à 4,03 Mo malgré la refonte du vêtement, et reste
sous la cible de 5 Mo.

---

## 5. Draco

**Draco a été retiré.** `ui/vendor/` ne contient que `DRACOLoader.js`, pas le
décodeur (`draco_decoder.js` / `.wasm`). La variante BALANCED livrée en V2.1
était donc effectivement impossible à charger, et câbler `DRACOLoader` aurait
imposé d'ajouter au dépôt des binaires tiers téléchargés.

BALANCED est désormais un GLB standard, allégé par ses textures en
demi-résolution — même géométrie, aucune dépendance nouvelle. **Chargement
vérifié : 608 ms, 34/35 tests.**

Un **repli** a été ajouté dans `JarvisAvatar3D` : si le modèle demandé échoue,
le composant retente une fois sur `options.fallbackUrl` avant de basculer sur
le fallback DOM. L'avatar n'est jamais vide.

---

## 6. Performance

**Le FPS n'a pas pu être mesuré** et aucun chiffre n'est inventé. Le banc de
test s'exécute dans une pane qui se masque entre deux appels ; le rendu se met
alors en pause (comportement voulu, ajouté en Phase 1) et
`requestAnimationFrame` est bridé.

Mesuré réellement :

| Métrique | HIGH |
| --- | --- |
| Triangles | 50 204 |
| Meshes / draw calls | 21 |
| Programmes shader | 4 |
| Chargement à froid | 1 853 ms |
| Chargement BALANCED | 608 ms |

Une **sonde utilisable dans l'application visible** a été ajoutée :

```js
await JarvisAvatar3DInstance.profile(3000, 'SPEAKING')
```

Elle renvoie `fpsAvg`, `fpsMin`, `frameMsAvg`, `frameMsP95`, `frameMsMax`,
`drawCalls`, `triangles`, `geometries`, `textures`, `jsHeapMB`. Si le rendu est
suspendu ou `requestAnimationFrame` bridé, elle renvoie une **erreur explicite**
plutôt qu'un chiffre faux — vérifié : `frames insuffisantes (0)`.

Sonde mixer conservée : `engine.animation.inspect(engine.mixer)` distingue
`created` (instanciées) de `active` (évaluées). `getEffectiveWeight()` renvoyant
1 pour une action **jamais démarrée**, compter sans `isRunning()` donnait 41 au
lieu de 1 — c'est l'erreur de mesure commise en V2.1, désormais impossible.

---

## 7. Comparaisons

Rendus stricts, **même résolution (620×780), même caméra, même pose, même
expression, même éclairage, même exposition (−1,05), même environnement** pour
les trois versions :

```
build/avatar_v2/compare22/
  V20_*.png   V2   (jarvis_v2_high.glb)
  V21_*.png   V2.1 (jarvis_v2_1_high.glb)
  V22_*.png   V2.2 (jarvis_v2_2_high.glb)
```

Neuf vues par version : `face_front`, `face_34`, `bust_front`, `bust_34`,
`home`, `chat`, `voice`, `speaking`, `thinking`.

Les hauteurs de visée sont **absolues** (personnage calibré à 1,78 m) et non
relatives au sommet du crâne : un écart d'un millimètre entre deux versions
rendait sinon les planches incomparables.

Comparatifs antérieurs conservés : `build/avatar_v2/compare/` (V1 vs V2) et
`build/avatar_v2/compare21/` (V2 vs V2.1).

---

## 8. Tests

`ui/dev/avatar3d_test.html` — **34/35 PASS** sur HIGH comme sur BALANCED,
0 erreur console, 0 os NaN.

Couverture : GLB valide, textures liées (3 cartes), UV, morphs (35), os (55),
visèmes (12/12), animations (41), états (7), `setEmotion`, `lookAt`, `reset`,
resize, perte et restauration du contexte WebGL, `AnimationStateController`,
`_reap`, nettoyage IDLE, nettoyage SPEAKING, endurance sur 54 transitions,
absence d'`AnimationAction` en trop, absence de minuteur orphelin, clignement
non périodique, cadrages HOME/CHAT/VOICE et leurs marges.

Seul échec : **`fps mesuré`**, pour la raison environnementale décrite en §6.

Non-régression par rapport à la baseline V2.1 :

| Métrique | V2.1 | V2.2 |
| --- | --- | --- |
| Triangles | 55 404 | 50 204 |
| Meshes | 18 | 21 |
| Os | 55 | 55 |
| Morphs | 35 | 35 |
| Clips | 41 | 41 |
| Visèmes | 12 | 12 |
| HIGH | 4,24 Mo | 4,03 Mo |
| Actions actives IDLE | 1 | 1 |
| Actions actives SPEAKING | 2 | 1–2 |
| Tests | 27/28 | 34/35 |

Les triangles baissent (refonte du vêtement) et les meshes augmentent de 3
(manches gauche/droite + col, en remplacement de la chemise dérivée).

---

## 9. Limites

Aucune n'est masquée.

1. **Vêtement en plein-pied.** Le haut tient au cadrage buste, mais en vue
   complète (`V22_home.png`) la jonction torse/manche reste visiblement
   construite : arête nette à l'emmanchure et **liserés de peau visibles** à
   l'aisselle, au coude et à la hanche. C'est le défaut principal restant.
   Les cadrages réellement utilisés (HOME, CHAT, VOICE) le montrent peu ou pas,
   mais le mode `FULL_BODY` existant l'expose.
2. **Pantalon et chaussures** restent dérivés du maillage du corps (ancienne
   méthode). Seul le haut a été refait.
3. **Sourcils** : translation rigide, pas de déformation (voir §2).
4. **FPS non mesuré** (voir §6). La cible RTX 3080 reste à valider par toi,
   dans l'application visible, avec `profile()`.
5. **Pas de carte de tissu.** Le textile est un PBR uni avec une rugosité
   constante : pas de microstructure, pas de coutures, pas de variation
   tonale. La distinction peau/tissu passe par la couleur et la rugosité, pas
   par une texture.
6. **Cheveux** inchangés : la calotte reste lisse, sans mèches individuelles.
7. La **densité de texel** de la peau est celle de l'atlas MPFB complet : la
   tête occupe environ un sixième de l'atlas, donc les pores sont à la limite
   de la résolution en très gros plan.

---

## Activation

Non effectuée, comme demandé. Pour activer après validation, une seule ligne
dans `ui/js/avatar/avatar.js:75` :

```js
this.modelUrl = options.modelUrl || '/assets/avatar/jarvis_v2_2_high.glb';
```

Les modèles antérieurs (`jarvis_premium.glb`, `jarvis_v2_high.glb`,
`jarvis_v2_1_high.glb`) restent en place.

**BUILD_ID : JARVIS_3D_AVATAR_POLISH_V2_2**
