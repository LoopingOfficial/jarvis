# JARVIS_3D_AVATAR_POLISH_V2_3

Correction des défauts de vêtement visibles en plein-pied, à partir de
`JARVIS_3D_AVATAR_POLISH_V2_2`.

**L'avatar n'est toujours pas activé** : `ui/js/avatar/avatar.js:75` pointe sur
`/assets/avatar/jarvis_premium.glb`.

| Variante | Fichier | Poids |
| --- | --- | --- |
| HIGH | `ui/assets/avatar/jarvis_v2_3_high.glb` | 4,36 Mo |
| BALANCED | `ui/assets/avatar/jarvis_v2_3_balanced.glb` | 3,38 Mo |

Visage, cheveux, yeux, peau, sourcils, animations, `AnimationStateController`,
visèmes, rig et cadrages : **inchangés**.

---

## 1. Cause des défauts V2.2

Trois causes distinctes, toutes structurelles — aucune n'était un problème de
réglage.

**a) Le filtre du tronc écrêtait les bosses.** V2.2 lissait le rayon du corps
par une **moyenne glissante**. Une moyenne rabote les maxima : partout où le
corps dépassait (hanche, haut du bras), l'enveloppe passait donc *sous* la
peau. C'est mathématiquement inévitable, pas un mauvais paramètre.

**b) Les manches utilisaient un rayon moyen unique par anneau.** Un bras est de
section elliptique : un rayon unique sous-couvre dans les directions les plus
larges. D'où les liserés mesurés à `|x| = 0,33–0,42`, que j'avais d'abord
attribués au torse.

**c) Manches et torse étaient deux tubes indépendants**, chacun avec ses
propres poids automatiques.

Mesure objective de départ (outil décrit au §9) : **129 sommets de peau
exposés** — torse 7,32 %, aisselle 6,31 %, hanche 3,19 %.

---

## 2. Construction des emmanchures

**Dilatation au lieu de moyenne.** Le profil est désormais :

```
rayons bruts → plafonnement des aberrations → symétrisation (max G/D)
            → DILATATION (max glissant) → lissage léger
```

La dilatation garantit une enveloppe **≥ au corps en tout point**, le lissage
retire l'anatomie sans jamais repasser sous la peau. Les liserés deviennent
impossibles par construction.

Le plafonnement est indispensable : à hauteur de poitrine, les rayons touchent
les **bras** (à ~40 cm de l'axe en T-pose). Sans plafond, la dilatation
propageait cette valeur sur toute la circonférence et le vêtement devenait un
ballon — c'est arrivé, et c'est ce qui a imposé un écrêtage à un multiple de la
médiane du profil.

**Manches.** Rayon mesuré **direction par direction** puis dilaté. Les deux
premiers anneaux sont placés volontairement **à l'intérieur du tronc**
(jusqu'à 3,5 cm en retrait) : la couture disparaît sous le haut et aucune
rotation du bras ne peut ouvrir de trou. L'emmanchure n'est pas élargie — le
recouvrement vient du retrait, pas du volume, ce qui évite l'épaulière.

**Plafond variable en hauteur.** Le tronc est serré (×1,26) sur la majeure
partie, élargi (×1,42) au niveau des épaules pour mordre sur le deltoïde et
rejoindre la manche. Sans cela une encoche s'ouvrait au sommet de l'épaule.

**Poids hérités de la peau.** Les vêtements ne reçoivent plus de poids
automatiques indépendants : chaque sommet copie ceux du sommet de peau le plus
proche. Tissu et peau se déforment donc ensemble, dans toutes les poses.

---

## 3. Haut final

- Tronc lofté sur 9 sections, 44 segments, dilaté puis lissé.
- Jeu du tissu : 10 mm à l'ourlet, jusqu'à 17 mm au milieu du torse.
- **Ourlet remonté à −7,2 cm sous la hanche.** En V2.3 je l'avais d'abord
  descendu à −15 cm pour garantir le recouvrement : sous ~7 cm, les cuisses se
  séparent et une section circulaire unique doit englober **les deux jambes**,
  ce qui produisait une jupe évasée. Le recouvrement est donc assuré par la
  taille du pantalon, remontée, et non par un haut plus long.
- Pas de dilatation verticale sur l'ourlet, sinon il reste large pendant que le
  corps se resserre.
- Col : inchangé (anneau lofté autour de l'axe du cou, symétrique par
  construction).

---

## 4. Pantalon

Reconstruit — le défaut était réel : l'ancien pantalon était dérivé du maillage
du corps et suivait les volumes anatomiques.

- **Deux tubes de jambe** loftés sur 8 sections, 24 segments, enveloppe dilatée.
- **Départ sous l'entrejambe** (−5,8 cm sous la hanche). Démarrés à hauteur de
  hanche, les premiers anneaux devaient englober tout le bassin : il en
  sortait deux plaques latérales en forme d'ailes.
- **Pièce de bassin** séparée, plus étroite (plafond ×1,16), qui couvre de la
  taille jusqu'à l'entrejambe et reste cachée sous le haut.
- Jambe droite et ample (13,5 à 20 mm de jeu), resserrée à la cheville,
  légèrement élargie au genou. Aucun pli sculpté.

**Recouvrement taille** : haut jusqu'à `hanche − 7,2 cm`, pantalon jusqu'à
`hanche + 6,2 cm` → **13,4 cm de chevauchement**, entièrement caché.

---

## 5. Chaussures

Conservées en dérivation du maillage (la forme du pied doit être suivie de
près), avec un décalage porté de 8,5 à 10,5 mm et un lissage renforcé. Elles
lisent correctement de profil. C'est la seule pièce encore dérivée du corps.

---

## 6. Tests d'animation

Couverture mesurée **dans les poses réelles**, action et image appliquées :

| Pose | Image | Sommets exposés | Pire région |
| --- | --- | --- | --- |
| repos (bind) | — | 3 | aisselle 0,45 % |
| `idle_neutral` | 1 | 3 | aisselle 0,45 % |
| `gesture_explain` | 30 | 6 | aisselle 0,45 % |
| `gesture_welcome` | 25 | 12 | aisselle 0,68 % |
| `gesture_open_hand` | 35 | 9 | torse 0,46 % |
| `gesture_arms_cross` | 40 | 5 | aisselle 0,90 % |
| `gesture_thinking` | 30 | 3 | aisselle 0,45 % |
| `walk_forward` | 16 | 3 | aisselle 0,45 % |

**Deux bugs de mesure trouvés en route, à connaître :**

1. Les actions n'étaient pas réellement appliquées : depuis Blender 4.4 une
   action possède des **slots**, et sans slot lié elle est assignée mais
   n'anime rien. Toutes les poses donnaient donc le même résultat.
2. Une fois les poses appliquées, j'ai mesuré jusqu'à **87 % d'aisselle
   exposée** et cru à un défaut majeur. C'était faux : les régions étaient
   classées d'après la position **posée**, si bien qu'un avant-bras tendu vers
   l'avant était étiqueté « aisselle » — de la peau volontairement nue comptée
   comme un défaut. Les régions sont maintenant calculées une fois en pose de
   repos et indexées par sommet.

Je le signale explicitement parce que le premier chiffre était alarmant et
faux, et qu'il aurait pu justifier des heures de corrections inutiles.

---

## 7. HIGH / BALANCED

| | HIGH | BALANCED |
| --- | --- | --- |
| Poids | 4,36 Mo | 3,38 Mo |
| Triangles | 64 060 | 64 060 |
| Meshes | 23 | 23 |
| Os | 55 | 55 |
| Morphs | 35 | 35 |
| Clips | 41 | 41 |
| Visèmes | 12 | 12 |
| Albédo | 2048 JPEG | 1024 JPEG |
| Chargement local | 322 ms | 301 ms |

Pas de Draco, conformément à la consigne : BALANCED reste un GLB standard
allégé par ses textures.

Le poids monte de 4,03 à 4,36 Mo et les triangles de 50 204 à 64 060 : c'est
le coût de la subdivision appliquée au haut, aux manches et aux jambes, qui
est ce qui rend le tissu lisse. Sous la cible de 5 Mo.

---

## 8. Comparaisons

`build/avatar_v2/compare23/` — mêmes résolution, caméra, pose, expression,
éclairage, exposition et environnement pour les deux versions.

```
V22_*.png   V2.2 (jarvis_v2_2_high.glb)
V23_*.png   V2.3 (jarvis_v2_3_high.glb)
```

Vues : `face_front`, `face_34`, `bust_front`, `bust_34`, `home`, `chat`,
`voice`, `speaking`, `thinking`, `full_front`, `full_34`, `full_side`, plus les
crops `crop_shoulder`, `crop_armpit`, `crop_waist`, `crop_hip`.

**Les cadrages V2.2 sont conservés à l'identique** : les zones qui montraient
un défaut avant le montrent toujours si le défaut persiste. Le crop
`crop_armpit` est le plus démonstratif — V2.2 y montre une tache de peau, V2.3
du tissu continu.

Comparatifs antérieurs conservés : `compare/` (V1 vs V2), `compare21/`
(V2 vs V2.1), `compare22/` (V2 / V2.1 / V2.2).

---

## 9. Tests

`ui/dev/avatar3d_test.html` — **34/35 PASS sur HIGH et sur BALANCED**,
0 erreur console, 0 os NaN.

**Seul test en échec : `fps mesuré`.** Raison exacte : le banc s'exécute dans
une pane qui se masque entre deux appels ; le rendu se met alors en pause
(comportement voulu depuis la Phase 1) et `requestAnimationFrame` cesse d'être
appelé. La sonde `profile()` renvoie une erreur explicite plutôt qu'un chiffre
faux. Aucune autre régression.

Non-régression animation, refaite intégralement :

| | V2.2 | V2.3 |
| --- | --- | --- |
| Actions actives IDLE | 1 | **1** |
| Actions après 54 transitions | 1 | **1** |
| Actions instanciées | 41 → 41 | **41 → 41** |
| Minuteurs orphelins | 0 | **0** |
| Geste ancien resté actif | aucun | **aucun** |

**Nouvel outil : `build/avatar_v2/coverage.py`.** Pour chaque sommet de peau
d'une zone censée être habillée, il tire un rayon le long de la normale ; si
le rayon ne rencontre aucune pièce de vêtement, la peau est exposée. Critère
objectif, indépendant de la caméra, de l'éclairage et de mon jugement. Il
accepte une action et une image, donc il valide aussi en mouvement.

```
blender -b --factory-startup -P build/avatar_v2/coverage.py -- \
        build/avatar_v2/step5_final.blend gesture_welcome 25
```

Bilan : **129 sommets exposés en V2.2 → 3 en V2.3** au repos, 3 à 12 selon la
pose.

---

## 10. Limitations restantes

1. **3 à 12 sommets restent exposés** selon la pose (≤ 0,9 % d'une région). Ce
   sont des sommets isolés à la lisière de l'emmanchure et à la hanche, pas
   des surfaces. Non visibles aux cadrages testés, mais non nuls.
2. **Petites encoches au sommet des épaules** en plein-pied : la jonction
   haut/manche reste perceptible comme une fine fente sur certains angles.
   Réduites par rapport à V2.2, pas éliminées.
3. **Chaussures** : seule pièce encore dérivée du maillage du corps. Correctes
   de profil, sommaires de face.
4. **Aucune carte de tissu** : le textile reste un PBR uni à rugosité
   constante — pas de microstructure, pas de coutures, pas de variation
   tonale. Inchangé depuis V2.2.
5. **Silhouette du tronc encore cylindrique** : la dilatation supprime
   l'anatomie mais aplatit aussi la taille. Le haut lit comme un vêtement
   ample, pas cintré.
6. **FPS non mesuré** (§9). À valider par toi dans l'application visible avec
   `profile()`.
7. Le **poids et le nombre de triangles augmentent** (4,03 → 4,36 Mo ;
   50 204 → 64 060 tris).
8. Les shape keys ne s'appliquent qu'au corps : **le col ne se déforme pas**
   avec les mouvements du cou au-delà du skinning osseux.

---

## Activation

Non effectuée. Pour activer, une seule ligne dans `ui/js/avatar/avatar.js:75` :

```js
this.modelUrl = options.modelUrl || '/assets/avatar/jarvis_v2_3_high.glb';
```

**BUILD_ID : JARVIS_3D_AVATAR_POLISH_V2_3**
