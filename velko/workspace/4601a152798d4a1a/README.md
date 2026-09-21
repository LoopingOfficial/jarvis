# Bot Discord VELKO

Ce projet contient un vrai bot discord.py et une commande slash `/ping`.
Les tests locaux couvrent la logique de réponse ; ils ne prouvent aucune connexion Discord.

## Connexion manuelle
1. Créer une application et un bot dans https://discord.com/developers/applications.
2. Ajouter ce bot à votre serveur avec les scopes `bot` et `applications.commands`.
3. Créer un environnement : `python3 -m venv .venv`.
4. Installer : `.venv/bin/python -m pip install -r requirements.txt`.
5. Fournir DISCORD_TOKEN dans l'environnement (jamais dans le code ou l'interface).
6. Fournir DISCORD_GUILD_ID si la commande doit être synchronisée sur un serveur précis.
7. Lancer : `.venv/bin/python bot.py`.
8. Tester `/ping` dans Discord et vérifier la réponse effective.

Aucune dépendance n'a été installée automatiquement. Aucun message n'a été envoyé.
Aucun test réseau ou Discord réel n'a été réalisé par les tests locaux.
