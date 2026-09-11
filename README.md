# JARVIS pour Windows

Copie adaptée de JARVIS Command Center, avec la correction des appels d’outils Ollama.
Cible : Windows 10/11, Python 3.11 ou plus récent, interface dans Chrome ou Edge.

## Installation et lancement

1. Copie le dossier `jarvis-windows` sur ton PC, dans un dossier où tu peux écrire
   (Documents, par exemple). Ne le lance pas directement depuis une archive ZIP.
2. Installe Python 3.11+ pour Windows avec l’option **Add python.exe to PATH**.
3. Double-clique sur **install_windows.bat**. Une connexion Internet est nécessaire
   pour télécharger les dépendances. Aucun droit administrateur n’est demandé.
4. Double-clique sur **run_jarvis.bat**. L’interface s’ouvre à
   **http://127.0.0.1:8765/**. Garde la console ouverte ; Ctrl+C arrête Jarvis.
5. Dans **Settings → AI Providers**, configure un fournisseur ou ton Ollama local.
   Les modèles Ollama doivent être installés séparément sur le PC. Les modèles
   spécifiques à MLX présents sur le Mac ne sont pas utilisables tels quels sur Windows.
6. Ajoute tes connexions dans **Settings → Connectors** et choisis ton projet Windows.

## Fonctions adaptées

- Lanceurs Windows avec environnement Python `.venv` isolé et vérification des erreurs.
- Ouverture de Chrome/Edge en fenêtre d’application, sinon navigateur par défaut.
- Applications : Explorateur, Bloc-notes, Terminal, PowerShell, Chrome, Edge,
  Cursor, VS Code, Spotify, Discord et Docker selon leur présence sur le PC.
  Tu peux aussi fournir le chemin complet d’un fichier `.exe`.
- Notifications Windows, selon les réglages de notifications du système ;
  les notifications restent également dans le fil de Jarvis.
- Clé du coffre dans le Gestionnaire d’identifiants Windows via le backend
  Windows de keyring, sans repli automatique vers une clé en fichier texte.
- Métriques CPU, mémoire et réseau via psutil.
- Commandes locales exécutées par **cmd.exe**. Pour PowerShell, indique explicitement
  `powershell -NoProfile -Command "..."`. Les commandes SSH restent celles du serveur distant.
- Détection des principales commandes destructives Windows dans les confirmations.

## Voix et outils externes

Autorise le microphone dans Chrome ou Edge. La reconnaissance et la synthèse vocales
reposent sur le navigateur ; leur disponibilité dépend de sa configuration.
Le double clap est optionnel : dans une console ouverte dans ce dossier, lance
`.venv\Scripts\python.exe -m pip install -r requirements-audio.txt`, puis définis
`JARVIS_CLAP_ENABLED=1` dans `.env`.

Git, Docker et OpenCode doivent être installés séparément si tu utilises ces outils.
Les déploiements utilisant `rsync` nécessitent un `rsync` utilisable depuis Windows ;
SSH/SFTP avec Paramiko fonctionne indépendamment de cet outil.
Pour ramener Jarvis au premier plan après un double clap, la version Windows ouvre
sa page dans le navigateur ; elle ne garantit pas la réutilisation de la fenêtre existante.

## Données

Cette distribution ne contient ni identifiants, ni clés API, ni base de conversations
provenant du Mac. Les données seront créées dans `data` au premier démarrage.
Ne copie pas simplement le coffre macOS : sa clé est attachée à l’environnement Mac.
Reconfigure les connecteurs sur le PC. Pour une sauvegarde Windows, conserve les
fichiers de données et prévois aussi la conservation de leur clé de chiffrement.

## Vérification

Tests : `.venv\Scripts\python.exe -m unittest discover -s tests -v`.
Les tests Windows simulent les API natives quand ils tournent sur macOS.
Cette édition a été préparée sur Mac : l’installation et les intégrations natives
restent à valider sur un PC Windows réel.
