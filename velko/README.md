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
- `runtime/jarvis-client.js` : pont moteur → scène (envoi des demandes, traduction des événements en gestes, confirmations).
- `runtime/screen-router.js` : `VelkoScreenRouter`. Décide seul du rôle de chaque moniteur à partir des événements réels. Il n'existe aucun sélecteur de fenêtre, aucune capture d'écran système, aucun partage manuel : les moniteurs appartiennent à VELKO.
- `runtime/avatar.js`, `runtime/environment.js`, `runtime/screens.js`, `runtime/main.js` : personnage, décor, moniteurs, rendu et interface.
- `workbench.js` : les trois panneaux réels, alimentés par le même flux.

## Les trois moniteurs

Le flux d'événements dit QUOI regarder, le moteur fournit le CONTENU. Rien
n'est reconstitué côté écran.

- **Écran gauche — code réel.** `file.opened` ouvre le vrai fichier, `file.changed`
  le recharge. Arborescence réelle du projet, numéros de ligne, coloration,
  lignes modifiées surlignées d'après le vrai `git diff`, et bascule vers le
  diff complet. Lecture seule : l'écriture appartient au moteur.
- **Écran central — terminal réel.** `terminal.command` affiche la commande et
  son cwd, `terminal.output` diffuse stdout/stderr au fil de l'eau,
  `terminal.completed` donne le code de sortie et la durée réels.
- **Écran droit — outil actif.** Navigateur, SSH, git ou Discord, selon la
  source réellement active. Sans session réelle, l'écran l'annonce
  (« NON CONNECTÉ ») au lieu d'afficher une imitation.

## Navigateur de VELKO

VELKO possède sa PROPRE session Chromium (Playwright), pilotée par le moteur.
Ce n'est ni le Chrome de l'utilisateur, ni une fenêtre à sélectionner : il n'y
a aucun `getDisplayMedia`, aucun sélecteur d'écran dans tout le projet.

- `mac/jarvis/browser_manager.py` : thread Playwright unique, file de commandes,
  **contexte persistant** dans `mac/data/browser-profile`. Une authentification
  faite une fois (Discord, un back-office…) survit aux redémarrages.
- Outils réels : `browser.navigate`, `click`, `type`, `scroll`, `back`,
  `forward`, `reload`, `read_page`, `status`, `wait`, `pause`, `close`.
- Événements : `browser.started`, `browser.navigate`, `browser.loaded`,
  `browser.click`, `browser.scroll`, `browser.read`, `browser.frame`,
  `browser.gate`, `browser.error`, `browser.closed`.
- L'écran droit affiche le **flux d'images réel** de cette session
  (`browser.frame`, repli HTTP `/api/browser/frame`). Le moteur ne diffuse des
  frames que si un écran les regarde.
- Connexion, captcha ou choix manuel : `browser.pause` ouvre une barrière.
  VELKO passe la tâche en `waiting_user`, affiche « ACTION REQUISE » et attend.
  Il n'invente jamais la suite.

Installation (Playwright est une dépendance externe, comme Ollama) :

```sh
cd mac && ./.venv/bin/python -m pip install playwright
./.venv/bin/python -m playwright install chromium
```

Sans Playwright, l'écran droit affiche honnêtement l'indisponibilité et la
commande à lancer.

## Trouver le projet sans donner de chemin

« Regarde mon bot Discord » ne nomme aucun dossier. `ProjectResolver`
(`mac/jarvis/projects.py`) note les projets sur des PREUVES lues sur disque :
bibliothèque Discord déclarée, dossiers `commands/`/`events/`, point d'entrée
qui appelle réellement `client.login()`, jeton attendu en configuration, traces
d'exploitation, date de dernière modification. Une simple dépendance ne suffit
pas : sans point d'entrée ni ossature de commandes, un projet qui *parle* à
Discord n'est pas un bot.

- un seul candidat, ou un candidat nettement devant → sélection automatique ;
- plusieurs candidats crédibles → VELKO montre les candidats et demande ;
- le choix est ensuite **mémorisé** (`general.discord_bot_project` + mémoire),
  donc le chemin n'est plus jamais redemandé.

Outils : `project.discord_bot` (trouver), `project.audit` (auditer),
`process.find` (une instance tourne-t-elle déjà ?).

## Audit déterministe

`mac/jarvis/project_audit.py` exécute la séquence complète lui-même — git,
manifeste, arborescence, commandes/événements, point d'entrée, scripts
déclarés, processus réels, état Discord — parce qu'un modèle local s'arrête
volontiers après un seul outil et « conclut » sur une observation unique. Le
modèle ne fait que formuler les constats obtenus. Chaque étape émet ses
événements réels : l'écran gauche ouvre les vrais fichiers lus, l'écran central
reçoit les vraies commandes git.

## Discord

Ordre de préférence réel, sans jamais reconstituer d'interface Discord :

1. session navigateur authentifiée sur `discord.com`, via le profil persistant ;
2. bot Discord du projet (`discord.*`, token dans le coffre) — `discord.start`
   connecte le bot, `discord.list_channels` / `recent_messages` lisent le vrai
   serveur ;
3. API Discord pour les actions qui s'y prêtent.

Si rien n'est authentifié, l'écran affiche « DISCORD — NON CONNECTÉ » et la
raison réelle. Aucune fausse fenêtre Discord n'est jamais rendue.

`discord.web_session` ouvre `discord.com` dans le profil persistant et rend
l'état RÉEL de la session. L'authentification n'est jamais supposée : elle
exige une preuve positive lue dans la page (l'absence de formulaire de
connexion ne suffit pas, une page en cours de chargement est vide). Si la
session n'est pas connectée, la tâche passe en `waiting_user`, l'écran affiche
« ACTION REQUISE — Connexion Discord nécessaire dans la fenêtre VELKO » et
VELKO attend. Il ne demande jamais e-mail, mot de passe, code 2FA ni jeton :
vous vous connectez vous-même, une fois, dans sa fenêtre.

Contenu servi par `GET /api/workspace/file|tree|diff` (`mac/jarvis/workspace_view.py`),
en lecture seule, soumis aux mêmes racines autorisées que les outils
(`security.filesystem_roots`). Les secrets sont masqués à l'affichage
uniquement : le fichier sur disque et les données du moteur restent intacts.

## Gestes

Chaque geste suit un fait réel, jamais l'inverse.

| Fait du moteur | Geste |
| --- | --- |
| `fs.write`, `file.changed`, `file.created` | `TypingNormal` / `TypingFast` |
| `terminal.command`, `ssh.run` | `TypingNormal` puis `PressEnter` |
| `terminal.output`, `terminal.completed`, `process.*` | `ReadScreen` — **mains retirées** |
| `browser.navigate`, `browser.click` | `MouseReach` (la main rejoint la souris) |
| `browser.action` (CLICK) | `MouseClick` |
| `browser.scroll` | `MouseScroll` |
| `browser.type` | `TypingNormal` |
| `browser.loaded` | `ReadScreen` |
| `file.opened` | `ReadScreen` |

VELKO ne tape pas pendant qu'un processus travaille seul : dès la première
ligne de sortie, il retire les mains du clavier et lit l'écran.

## brainrot-fortnite.com — exploitation

Profil audité : `velko/config/projects/brainrot-fortnite.json`. Aucun secret :
seulement les références permettant à VELKO de retrouver le projet, sa base et
ses sous-systèmes, plus ce que le schéma NE PERMET PAS de mesurer.

- `mac/jarvis/brainrot_site.py` : `AnalyticsRepository` (lecture seule stricte,
  fenêtres Europe/Paris, comparaisons 1 j / 7 j / 30 j) et
  `VelkoOpportunityEngine` (observation réelle → pourquoi → action exécutable).
- `mac/jarvis/tools/brainrot_tools.py` : les outils `brainrot.*`. Ils
  orchestrent les outils génériques, ils ne les remplacent pas.

Deux garde-fous portent tout le reste :

1. **Une donnée absente est déclarée absente.** Le schéma n'enregistre pas les
   connexions (pas de `users.last_login`, pas de table de sessions) : VELKO le
   dit et ne l'estime jamais. Les « actifs » viennent de
   `activity_presence.last_seen_at` — c'est une présence, pas une connexion, et
   c'est écrit tel quel.
2. **Une panne n'est pas un schéma incomplet.** Une base injoignable est
   annoncée comme telle. Sans cette distinction, une coupure faisait conclure
   « colonne absente » alors que les données existent.

Politique d'autonomie, explicite dans le profil : lecture, analyse, audit,
navigation, brouillons et préparation de campagne sans confirmation ;
publication d'article, envoi de campagne, synchronisation du catalogue,
déploiement et écriture massive en base avec confirmation.

Google Sheet et système d'envoi d'emails ne sont pas connectés : toute demande
qui en dépend passe en `waiting_user` avec la raison réelle, sans simulation.

## Livrables

- `exports/velko_master.blend` : personnage éditable et animation de transformations.
- `exports/velko_runtime.glb` : personnage et clips procéduraux.
- `exports/velko_office.blend`, `exports/velko_office.glb` : décor séparé.
- `animations/clips.json` : neuf clips échantillonnés, Idle, Listening, Thinking, Speaking, Walk, Sit, Typing, Mouse, Success.
- `assets/logo.png` : logo fourni, réutilisé dans l'interface et le décor.

Le rig est une hiérarchie de transformations articulées, sans armature skinnée ni blendshapes ARKit. Les textures d'écrans sont créées au lancement et restent propres au runtime ; les GLB ne remplacent pas la démo interactive. Le ciel Canvas doit également être reconstruit dans un autre moteur.

## Régénération et vérification

```sh
node velko/scripts/test_bridge.mjs
python3 -m unittest tests.test_workspace_view tests.test_browser_session \
    tests.test_project_resolver tests.test_brainrot_site   # depuis mac/
node velko/scripts/scene_manifest.mjs
node velko/scripts/bake_animations.mjs
blender --background --python velko/scripts/export_blender.py -- /Users/jerome/Desktop/jarvis-mac/velko/exports/scene.json
```

Exécuter ces commandes depuis `/Users/jerome/Desktop/jarvis-mac`. Blender 4.2 a été utilisé pour les exports depuis une distribution officielle temporaire, sans installation globale. Le runtime ne nécessite pas Blender.

Tests automatisés : routage des écrans, mise en attente d'une action avant l'installation au poste, progression relayée uniquement si le moteur en fournit une, correspondance geste ↔ fait réel (dont l'absence de frappe pendant qu'un processus travaille), routage automatique du troisième écran, et vue lecture seule des moniteurs (contenu réel, refus hors racines, vrai git diff, masquage d'affichage des secrets).

Vérification visuelle du rendu et de la séquence dans le navigateur intégré. Le compteur FPS mesure la session courante ; 60 FPS n'est pas garanti sur tous les appareils.

## Dépendances / licences

Three.js r160, MIT, déjà présent dans `mac/ui/vendor`, copié localement. Copyright 2010–2023 Three.js Authors. Blender 4.2 officiel (GPL), outil de production uniquement, non redistribué. Logo fourni par l'utilisateur, droits conservés par son propriétaire. Personnage, mobilier et animations créés procéduralement pour ce prototype ; aucun modèle tiers téléchargé. Voir `vendor/LICENSE.txt`.

## À améliorer

P1 : visage et cheveux réalistes, IK mains/pieds, contacts chaise/clavier/souris précis, phonèmes pilotés par audio, marche sans glissement, meilleure lumière et optimisation GPU. P2 : armature skinnée, 52 blendshapes ARKit, navigation libre avec collisions, LOD et mouvements secondaires. La trajectoire actuelle contourne le dossier mais ne dispose pas d'un moteur de collision.
