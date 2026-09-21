# VELKO — interface du moteur

VELKO est l'interface de l'assistant. Le moteur (orchestrateur, agents, outils,
connecteurs, mémoire) vit dans `mac/jarvis` et sert VELKO comme interface unique.

## Lancement

```sh
./mac/run_jarvis.sh
```

Puis ouvrir http://127.0.0.1:8765. Un modèle local doit être joignable
(`ollama serve`), sinon VELKO affiche l'erreur réelle du moteur au lieu de
répondre. Arrêter avec Ctrl+C.

L'ancienne interface reste sur disque et peut être rappelée sans rien modifier :

```sh
JARVIS_UI_DIR=mac/ui ./mac/run_jarvis.sh
```

## Fonctionnement réel

Un unique personnage 3D articulé reste dans la scène. Respiration, tête, paupières, bouche et doigts animés. Conversation assise, écoute, réflexion, parole, lever, rotation, trajet prédéfini, assise au poste, frappe, regard sur trois écrans et retour.

Une demande part vers `POST /api/command`. Le moteur l'exécute réellement : c'est
lui qui appelle les outils, écrit les fichiers, lance les processus. VELKO
s'abonne au flux `GET /api/events` et traduit chaque événement du moteur en
geste : un outil terminal fait taper au clavier sur l'écran 1, une écriture de
fichier sur l'écran 0, une navigation ou Discord sur l'écran 2. Aucun geste
n'est inventé : sans événement, VELKO lit son écran. La fin d'une mission n'est
jamais décidée par une minuterie, seulement par la réponse du moteur.

Quand le moteur réclame une autorisation, VELKO l'annonce et attend votre
réponse ; « Autoriser » ou « Refuser » la transmet à `POST /api/confirm`. Une
mission bloquée ou en échec maintient VELKO au poste, sans limite de durée.
Seul le statut `completed` autorise le retour. Une confirmation permet de
reprendre au poste sans rejouer le déplacement.

La saisie texte et le microphone (SpeechRecognition, Chrome recommandé) parlent
au même moteur. Voix française via SpeechSynthesis.

## Architecture

- `runtime/core.js` : VelkoEventBus, VelkoStateMachine, VelkoSceneDirector, VelkoCameraDirector.
- `runtime/engine-feed.js` : flux `/api/events`, ouvert une seule fois par onglet et partagé avec les écrans. Un navigateur ne tient que six connexions par origine ; un flux par écran saturait la file et bloquait les commandes.
- `runtime/jarvis-client.js` : pont moteur → scène (envoi des demandes, traduction des événements, confirmations).
- `runtime/avatar.js`, `runtime/environment.js`, `runtime/screens.js`, `runtime/main.js` : personnage, décor, moniteurs, rendu et interface.
- `workbench.js` : les trois moniteurs, alimentés par le même flux et par `/api/code/documents`. Lecture seule : les fichiers sont écrits par le moteur, pas depuis cet écran.

Fichiers hérités du prototype autonome, plus utilisés par l'interface :
`server.py`, `workspace_backend.py`, `display_redaction.py`,
`runtime/mission-client.js`. Ils décrivent l'ancien worker borné à deux missions.

## Livrables

- `exports/velko_master.blend` : personnage éditable et animation de transformations.
- `exports/velko_runtime.glb` : personnage et clips procéduraux.
- `exports/velko_office.blend`, `exports/velko_office.glb` : décor séparé.
- `animations/clips.json` : neuf clips échantillonnés, Idle, Listening, Thinking, Speaking, Walk, Sit, Typing, Mouse, Success.
- `assets/logo.png` : logo fourni, réutilisé dans l'interface et le décor.

Le rig est une hiérarchie de transformations articulées, sans armature skinnée ni blendshapes ARKit. Les textures d'écrans sont créées au lancement et restent propres au runtime ; les GLB ne remplacent pas la démo interactive. Le ciel Canvas doit également être reconstruit dans un autre moteur.

## Régénération et vérification

```sh
node velko/scripts/test_demo.mjs
node velko/scripts/test_bridge.mjs
node velko/scripts/scene_manifest.mjs
node velko/scripts/bake_animations.mjs
blender --background --python velko/scripts/export_blender.py -- /Users/jerome/Desktop/jarvis-mac/velko/exports/scene.json
```

Exécuter ces commandes depuis `/Users/jerome/Desktop/jarvis-mac`. Blender 4.2 a été utilisé pour les exports depuis une distribution officielle temporaire, sans installation globale. Le runtime ne nécessite pas Blender.

Test automatisé : cycle complet, événements, états, absence de saut de position, retour, rejouabilité et refus de missions concurrentes. Vérification visuelle du rendu et de la séquence dans le navigateur intégré. Le compteur FPS mesure la session courante ; 60 FPS n'est pas garanti sur tous les appareils.

## Dépendances / licences

Three.js r160, MIT, déjà présent dans `mac/ui/vendor`, copié localement. Copyright 2010–2023 Three.js Authors. Blender 4.2 officiel (GPL), outil de production uniquement, non redistribué. Logo fourni par l'utilisateur, droits conservés par son propriétaire. Personnage, mobilier et animations créés procéduralement pour ce prototype ; aucun modèle tiers téléchargé. Voir `vendor/LICENSE.txt`.

## À améliorer

P1 : visage et cheveux réalistes, IK mains/pieds, contacts chaise/clavier/souris précis, phonèmes pilotés par audio, marche sans glissement, meilleure lumière et optimisation GPU. P2 : armature skinnée, 52 blendshapes ARKit, navigation libre avec collisions, LOD et mouvements secondaires. La trajectoire actuelle contourne le dossier mais ne dispose pas d'un moteur de collision.
