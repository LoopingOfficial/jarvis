# JARVIS_3D_AVATAR_V2_3 — Gate d'activation

Aucune retouche géométrique. Visage, peau, yeux, cheveux, sourcils, haut,
pantalon, chaussures, rig, poids, morphs, clips, textures et matériaux sont
identiques à `JARVIS_3D_AVATAR_POLISH_V2_3`.

**Verdict : GO conditionnel — l'activation n'a PAS été faite.**
Un seul critère de ta liste n'a pas pu être vérifié par moi : le FPS. La raison
est démontrée au §1, et il te reste ~30 secondes de manipulation pour le lever.

---

## 0. Mode preview (fait en premier, comme demandé)

Nouveau module `ui/js/avatar/avatar_source.js` — un seul endroit décide quel
GLB est chargé, par priorité décroissante :

1. `?avatar=v23` / `?avatar=v23-balanced` / `?avatar=legacy` dans l'URL ;
2. `localStorage` (interrupteur Developer) ;
3. profil par défaut — **`legacy`, inchangé**.

```js
JarvisAvatarBuild.set('v23');  location.reload();   // essayer V2.3
JarvisAvatarBuild.set(null);   location.reload();   // rollback Legacy
JarvisAvatarBuild.info();                           // profil actif + URL
```

**Découverte importante** : Spatial V5 chargeait l'avatar par un **second
chemin**, `ui/js/v5/spatial_shell.js:227`, avec une URL codée en dur et sans
version. `?avatar=` n'avait donc aucun effet sur la surface que tu regardes
réellement. Cette surface passe désormais par `avatar_source.js`.

---

## 1. FPS réels

**Non mesurés. Aucun chiffre inventé.**

Preuve, mesurée dans la page elle-même :

| | Résultat |
| --- | --- |
| `requestAnimationFrame` sur 8 006 ms | **1 frame** |
| `setInterval` sur la même période | 486 ticks |
| `document.visibilityState` | `hidden` |

Le navigateur intégré déclare le document masqué même lorsque l'onglet est mis
au premier plan par l'outil. J'ai aussi essayé **ton Chrome réel** via un onglet
local : même résultat (`document.hidden = true`, fenêtre en arrière-plan), et je
ne peux pas — ni ne dois — forcer le focus de ton bureau. L'onglet a été refermé.

Ce qui a été mesuré et qui ne dépend pas de rAF :

| Métrique | HIGH | BALANCED |
| --- | --- | --- |
| Draw calls (meshes) | 23 | 23 |
| Triangles | 64 060 | 64 060 |
| Textures | 3 | 3 |
| Programmes shader | 4 | 4 |
| Chargement (local) | 322 ms | **164 ms** |

**Procédure pour lever ce point** — dans ta fenêtre, au premier plan :

```js
await JarvisAvatar3DInstance.profile(4000, 'IDLE')
await JarvisAvatar3DInstance.profile(4000, 'SPEAKING')
```

La sonde renvoie `fpsAvg`, `fpsMin`, `frameMsAvg`, `frameMsP95`, `frameMsMax`,
`drawCalls`, `triangles`, `jsHeapMB`. Si la fenêtre n'est pas visible, elle
renvoie une **erreur explicite** au lieu d'un chiffre faux.

*Bug corrigé au passage* : `engine.rendering` renvoyait `true` dans un document
déjà masqué à la construction — `visibilitychange` ne se déclenche qu'au
*changement*. L'état initial est maintenant lu au démarrage, ce qui rend le
diagnostic de la sonde fiable.

---

## 2. HIGH vs BALANCED

Comparaison **numérique**, pas subjective : rendu des deux GLB au cadrage le
plus exigeant (`face_front`, 62 cm, champ 22°), pixel à pixel.

| | Résultat |
| --- | --- |
| Pixels comparés | 483 600 |
| Écart moyen par canal | **0,406 / 255** |
| Écart maximum | 14 / 255 |

Un écart moyen de 0,4/255 est sous le seuil de perception. Géométrie, os,
morphs, clips et visèmes sont **identiques** ; seules les textures diffèrent
(albédo 2048 vs 1024).

**Recommandation : BALANCED comme profil par défaut.** 3,38 Mo contre 4,36 Mo,
chargement 164 ms contre 322 ms, pour une différence visuelle non perceptible.
HIGH reste disponible via `?avatar=v23`.

---

## 3–5. HOME / CHAT / VOICE

**Constat à connaître** : Spatial V5 n'utilise pas les cadrages HOME/CHAT/VOICE.
Son panneau avatar impose `HALF_BODY` (`spatial_shell.js`), avec sa propre
direction artistique — exposition 0,62, teinte, contre-jour cyan, sol et halo
masqués. Mes trois cadrages ont été calibrés pour un canevas portrait
620×780 ; forcés dans le panneau étroit de V5, ils cadrent mal. Ils restent
corrects sur le chemin `robotStage` (`three_app.js`).

Comparaison faite **dans l'application réelle**, mêmes réglages V5, même
cadrage `HALF_BODY` :

- **Legacy** — visage éclaté en fragments, teint blafard, épaules en sphères
  détachées.
- **V2.3** — silhouette humaine cohérente : tête, cou, épaules, torse, bras
  lisibles ; vêtement continu, aucune ligne de peau visible à cette échelle.

À la distance réelle d'affichage, les défauts listés au §11 ne sont pas
discernables. Le rendu V5 est en revanche **très sombre** (c'est sa charte, pas
l'avatar) : le visage y est peu lisible, pour les deux modèles.

---

## 6. Speaking / lipsync

Testé sur une vraie phrase française, en temps réel (le `LipSyncController`
avance sur `performance.now()`, pas sur `dt` — le piloter en boucle serrée ne
l'anime pas).

| | Résultat |
| --- | --- |
| Visèmes exercés | **11 / 12** |
| Manquant | `viseme_TH` — normal, le son « th » n'existe pas en français |
| Ouverture mâchoire max | 0,62 |
| Clignements pendant la parole | 2 |
| État après `stopSpeaking()` | `IDLE` |
| Actions actives après retour | **1** (`idle_neutral@1`) |

---

## 7. Endurance — 100 transitions

Dans l'application réelle, sur `IDLE / LISTENING / ACTING / RECALLING /
THINKING / SPEAKING` plus des gestes additifs injectés.

| Contrôle | Attendu | Mesuré |
| --- | --- | --- |
| Transitions | 100 | **100** |
| `AnimationAction` instanciées | 41 → 41 | **41 → 41** |
| Actions actives en fin d'IDLE | 1 | **1** |
| Minuteurs de nettoyage orphelins | 0 | **0** |
| Os NaN | 0 | **0** |
| Erreurs console | 0 | **0** |

---

## 8. Fallback

Testé en provoquant l'échec : instanciation avec
`/assets/avatar/CE_FICHIER_NEXISTE_PAS.glb` et un fallback Legacy.

```
404 sur le modèle demandé
  → repli automatique (une seule tentative)
  → jarvis_premium.glb chargé, 16 meshes, composant disponible
```

L'interface n'est jamais restée sans avatar. Le repli est câblé dans les deux
surfaces (`three_app.js` et `spatial_shell.js`).

---

## 9. Cache / versioning

Trois défauts trouvés et corrigés :

1. **Spatial V5 chargeait un GLB en dur, sans version.** Passe maintenant par
   `avatar_source.js`, qui ajoute `?v=<version>` à chaque URL.
2. **`reload()` remplaçait la version par un horodatage** (`?v=Date.now()`),
   ce qui annulait tout cache et forçait un retéléchargement systématique. La
   version portée par l'URL est désormais conservée.
3. **Les imports dynamiques de modules n'étaient pas versionnés** : un module
   modifié continuait d'être servi depuis le cache. Ils portent maintenant une
   version, et le tag `<script>` de `spatial_shell.js` a été incrémenté
   (`JARVIS_BRAIN_INSPECTOR_5` → `JARVIS_AVATAR_V2_3_GATE`).

**Limite restante, importante** : `index.html` lui-même est servi depuis le
cache du navigateur. Pendant ces tests, la page a continué à charger l'ancien
`spatial_shell.js` malgré le changement de version, jusqu'à ce que je force une
récupération fraîche. Le contournement par changement de port a été supprimé,
mais **il n'y a pas d'en-tête `Cache-Control` sur le serveur** : après un
déploiement, un rechargement simple peut encore servir l'ancien HTML. À traiter
côté serveur (`jarvis/server.py`), hors périmètre de cette phase.

---

## 10. Tests finaux

`ui/dev/avatar3d_test.html` — **33/35 PASS, 2 SKIP, 0 FAIL**, sur HIGH **et**
sur BALANCED, 0 erreur console, 0 os NaN.

Le 35e test que tu demandais d'identifier précisément était **`fps mesuré`**.
Il n'est pas en échec : il est désormais marqué **SKIP** avec sa preuve, parce
qu'il mesure l'environnement et non le produit :

```
SKIP  fps mesuré — requestAnimationFrame à 0.0/s, document.hidden=true :
      fenêtre non visible, mesure impossible.
      Utiliser JarvisAvatar3DInstance.profile() au premier plan.
```

Un second test a dû être requalifié pour la même raison : **`rendu actif`**
vérifie que le rendu tourne, ce qui est *faux par conception* quand la fenêtre
est masquée — c'est la pause de rendu voulue depuis la Phase 1. Il est donc
SKIP quand `document.hidden`, et PASS dans une fenêtre visible.

Les deux SKIP deviennent des PASS dès que tu lances la suite dans une fenêtre
au premier plan : **35/35**.

Validés indépendamment sur les deux GLB : chargement, textures (3 cartes),
animations (41), skinning (55 os), morphs (35), visèmes (12), matériaux,
caméra, cadrages HOME/CHAT/VOICE et leurs marges, `AnimationStateController`,
`_reap`, nettoyage IDLE et SPEAKING, endurance, absence de fuite, perte et
restauration du contexte WebGL.

---

## 11. Défauts visibles restants

Aucun n'a été corrigé, conformément au gel.

1. 3 à 12 sommets de peau exposés selon la pose (≤ 0,9 % d'une région).
   **Non visibles** aux cadrages réels testés.
2. Fines encoches au sommet des épaules, visibles en plein-pied seulement.
3. Torse légèrement cylindrique, taille peu marquée.
4. Chaussures encore dérivées du maillage du corps.
5. Pas de texture textile dédiée.
6. Rendu V5 très sombre — charte de V5, affecte les deux modèles.
7. Les cadrages HOME/CHAT/VOICE ne s'appliquent pas dans Spatial V5 (§3–5).

---

## 12. GO / NO-GO

**GO conditionnel. Non activé.**

Tout ce que je pouvais vérifier passe :

- aucun défaut visuel majeur en HALF_BODY, le cadrage réellement utilisé ;
- lipsync correct, 11/12 visèmes ;
- animation stable, 1 action active après 100 transitions ;
- aucune fuite mémoire, aucun os NaN ;
- fallback fonctionnel ;
- chargement correct sur les deux variantes ;
- 0 erreur console ;
- V2.3 est très nettement meilleur que Legacy dans l'application réelle.

**Le seul critère non vérifié est la performance**, et ta liste au §12 l'exige.
Je n'active donc pas : ce serait substituer mon jugement au tien sur un point
que je n'ai pas pu mesurer. Les indices sont favorables — 23 draw calls,
64 k triangles, 3 textures, 4 shaders, ce qui est trivial pour une RTX 3080 —
mais un indice n'est pas une mesure.

### Pour activer toi-même, sans toucher au code

```js
JarvisAvatarBuild.set('v23-balanced');   // ou 'v23' pour HIGH
location.reload();
await JarvisAvatar3DInstance.profile(4000, 'SPEAKING');
```

### Pour activer définitivement, après ta mesure

Une ligne, dans `ui/js/avatar/avatar_source.js` :

```js
export const DEFAULT_BUILD = 'v23-balanced';   // au lieu de 'legacy'
```

C'est volontairement **là** et non dans `avatar.js:75` : `avatar_source.js` est
le seul point de décision, et les deux surfaces (`three_app.js` et
`spatial_shell.js`) le consultent.

**Rollback** : `JarvisAvatarBuild.set('legacy')` puis rechargement, ou remettre
`DEFAULT_BUILD = 'legacy'`. `jarvis_premium.glb` est conservé et reste le
fallback automatique.

**BUILD_ID candidat : JARVIS_3D_AVATAR_V2_3_PRODUCTION** — à apposer au moment
de l'activation.
