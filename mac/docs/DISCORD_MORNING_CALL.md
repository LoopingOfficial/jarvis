# Rapport matinal par appel vocal Discord

À 06h00, JARVIS pingue le salon privé (le téléphone vibre), rejoint un salon
vocal dédié, et dès que l'utilisateur arrive : « Il est six heures. Bonjour.
Souhaites-tu ton rapport maintenant, ou dans cinq minutes ? ». Il écoute la
réponse, la transcrit localement, et déroule le rapport ou reprogramme un
rappel.

Tout est gratuit : bot Discord existant, voix Microsoft Edge (`edge-tts`, sans
clé), transcription locale (`faster-whisper`). Aucun appel payant, aucun
abonnement.

| Élément | Emplacement |
| --- | --- |
| Module | [`jarvis/discord_call.py`](../jarvis/discord_call.py) |
| Réglages | section `discord_call` de [`jarvis/config.py`](../jarvis/config.py) |
| Outils agents | [`jarvis/tools/discord_tools.py`](../jarvis/tools/discord_tools.py) (section D) |
| Tests | [`tests/test_discord_call.py`](../tests/test_discord_call.py) |

## Déroulé d'un appel

1. **Collecte** (`MorningReportCollector`) : messagerie (IMAP réel uniquement),
   tâches, projets et blocages, notifications non lues, santé machine. Une
   source indisponible est annoncée comme telle — rien n'est inventé.
2. **Ping** : `📞 JARVIS demande un appel vocal.` dans le salon texte, avec
   mention de `owner_user_id` pour déclencher la notification du téléphone.
3. **Salon vocal** : le bot rejoint et attend jusqu'à `wait_user_s` (5 min).
   Personne ne vient → le rapport est publié à l'écrit et l'appel se termine.
4. **Accroche** : `edge-tts`, voix `fr-FR-HenriNeural`, jouée par FFmpeg.
5. **Écoute** : capture du flux Opus décodé (48 kHz, stéréo) via
   `discord-ext-voice-recv`, fin d'énoncé après 1,2 s de silence, transcription
   par `core.stt` (faster-whisper, local).
6. **Décision** :
   * « maintenant » → le rapport est lu point par point, puis JARVIS répond
     aux questions de suivi (`max_questions`, réponses bornées au rapport) ;
   * « dans 5 minutes » → le bot quitte le salon et se reprogramme à +5 min ;
   * « annule » → rapport écrit uniquement ;
   * incompris deux fois → JARVIS déroule le rapport (quelqu'un s'est levé et
     attend : le silence serait le pire des choix).
7. Le rapport est **toujours** publié à l'écrit à la fin, pour relecture.

## Réglages (`settings.discord_call`)

| Clé | Défaut | Rôle |
| --- | --- | --- |
| `enabled` | `false` | Un assistant qui téléphone seul se demande, il ne se subit pas. |
| `hour` / `minute` | `6` / `0` | Heure de l'appel. |
| `text_channel` | `""` | Salon du ping — identifiant ou nom. |
| `voice_channel` | `📞-jarvis-line` | Salon vocal — identifiant ou nom. |
| `owner_user_id` | `""` | Qui est mentionné, et quelle voix est écoutée. |
| `snooze_minutes` | `5` | Report demandé à l'oral. |
| `wait_user_s` | `300` | Attente dans le salon vocal avant abandon. |
| `voice` | `fr-FR-HenriNeural` | Voix Edge. |
| `max_questions` | `4` | Questions de suivi après le rapport. |

## Dépendances optionnelles et replis

| Manquant | Conséquence |
| --- | --- |
| PyNaCl / FFmpeg | Pas de voix : le rapport est publié à l'écrit dans le salon. |
| `discord-ext-voice-recv` | JARVIS parle mais n'entend pas : il demande une réponse écrite (« maintenant » / « dans 5 minutes »). |
| `faster-whisper` | Idem : repli écrit. |
| `edge-tts` | Idem : repli écrit. |

```bash
pip install -r requirements-audio.txt
```

FFmpeg n'est pas un paquet Python : il doit être présent dans le `PATH`
(`winget install Gyan.FFmpeg`, puis rouvrir le terminal). C'est lui qui décode
le MP3 d'`edge-tts` : PyNaCl seul permet de *rejoindre* le salon vocal, pas d'y
parler.
`discord.morning_call_status` liste précisément ce qui manque et la commande
correspondante.

## Outils

* `discord.morning_call_status` — état, prochaine échéance, capacités, manques.
* `discord.morning_call` — déclenche l'appel tout de suite ; l'outil attend la
  fin de l'appel et rapporte ce qui s'est réellement passé (`delivered`,
  `posted`, `snoozed`, `cancelled`, `no_show`, `failed`).

## Rendu parlé

Le rapport est parlé avant d'être lu à l'écran, ce qui impose deux règles au
collecteur, vérifiées par les tests :

* `plural()` — jamais de « tâche(s) » : `edge-tts` prononce les parenthèses.
* `shorten()` — une avancée de projet peut contenir la demande entière de
  l'utilisateur, URL comprise ; elle est coupée à 110 caractères sur un mot.

## Notes d'architecture

* **Pas d'APScheduler** : le dépôt a déjà `AutomationManager.next_run()`, qui
  sait lire un déclencheur `daily`. Le module reprend le motif de
  `DiscordScheduler` — thread démon, tick de 20 s, échéance recalculée.
* **La boucle du bot n'est jamais bloquée** : collecte IMAP, synthèse vocale et
  transcription passent par `run_in_executor`. Un gel de la boucle couperait le
  heartbeat, que Discord interprète comme une déconnexion.
* `DiscordEngine.spawn()` complète `submit()` : un appel dure des minutes,
  bien au-delà du délai maximum que `submit()` impose (à raison) aux outils.
