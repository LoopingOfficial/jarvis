# Velko Control

Pont Windows local pour piloter des fenêtres Win32 depuis un terminal. Il ne dépend ni de Sky ni de CUA et ne modifie pas Unreal Engine.

## Installation

Décompressez le dossier, puis ouvrez PowerShell dans ce dossier. Aucun runtime supplémentaire n'est requis sur Windows PowerShell 5.1+.

## Commandes

```bat
velko-control.bat list
velko-control.bat focus "VELKO_MetaHuman - Unreal Editor"
velko-control.bat screenshot "VELKO_MetaHuman - Unreal Editor" unreal.png
velko-control.bat move 800 450
velko-control.bat click 800 450
velko-control.bat doubleclick 800 450
velko-control.bat type "TEST CODEX COMPUTER USE"
velko-control.bat key "{ENTER}"
velko-control.bat hotkey "^s"
velko-control.bat wait 500
```

Les sorties sont JSON compact sur stdout; les erreurs sont JSON sur stderr avec un code de sortie non nul. `screenshot` capture la zone visible de la fenêtre ou le bureau principal.

## Sécurité

Par défaut, aucune commande destructive (fermeture de fenêtre, kill de processus, suppression ou modification de fichiers) n'existe. Pour imposer une liste blanche, éditez `velko-control.json`:

```json
{"requireAllowlist":true,"allowTitles":["VELKO_MetaHuman - Unreal Editor","Bloc-notes"],"allowDesktopCapture":false}
```

Les actions clavier/souris restent capables de déclencher une action dans l'application ciblée: vérifiez le titre et la position avant d'envoyer une commande. Utilisez `list`, puis `focus`, avant toute action.

## Exemple Unreal

```bat
velko-control.bat list
velko-control.bat focus "VELKO_MetaHuman - Unreal Editor"
velko-control.bat screenshot "VELKO_MetaHuman - Unreal Editor" unreal-before.png
velko-control.bat key "{F5}"
velko-control.bat wait 1000
velko-control.bat screenshot "VELKO_MetaHuman - Unreal Editor" unreal-after.png
```

Le pont ne connaît pas le projet, ne charge aucun plugin Unreal et ne communique qu'avec les fenêtres Windows.
