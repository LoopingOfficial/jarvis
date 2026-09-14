"""Exemple d'initialisation des tâches Discord planifiées.

Deux tâches concrètes, telles qu'on les veut en production :

  1. VÉRIFICATION D'ÉTAT — toutes les 30 minutes, un embed d'état dans un salon
     de supervision.
  2. ANNONCE MATINALE — tous les jours à 09h00, un message dans le salon
     d'annonces.

Deux façons de l'exécuter :

    # JARVIS tourne déjà (recommandé) : passe par l'API locale.
    python tools/discord_schedule_examples.py --channel-status 123 --channel-news 456

    # Depuis du code JARVIS (plugin, migration, test) :
    from tools.discord_schedule_examples import seed_examples
    seed_examples(core, status_channel_id="123", news_channel_id="456")

Les identifiants de salon sont ceux de Discord (clic droit sur le salon →
« Copier l'identifiant », mode développeur activé). Le script est idempotent :
une tâche portant le même nom est mise à jour au lieu d'être dupliquée.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def example_tasks(status_channel_id: str, news_channel_id: str) -> list[dict]:
    """Les deux tâches de l'exemple, au format attendu par le planificateur."""
    return [
        {
            # 1. Toutes les 30 minutes : vérification d'état.
            "name": "Vérification d'état — supervision",
            "interval": "30m",                      # accepte aussi « */30 * * * * »
            "tool_to_call": "discord.send_embed",
            "target_channel_id": status_channel_id,
            "params": {
                "title": "État des systèmes",
                "description": ("JARVIS est en ligne. Cette vérification est publiée "
                                "automatiquement toutes les 30 minutes."),
                "severity": "SUCCESS",
            },
            "status": "ACTIVE",
        },
        {
            # 2. Tous les jours à 09h00 : annonce matinale.
            "name": "Annonce matinale",
            "interval": "0 9 * * *",                # équivalent lisible : « 09:00 »
            "tool_to_call": "discord.send_message",
            "target_channel_id": news_channel_id,
            "params": {
                "content": ("Bonjour à toutes et à tous ! Voici le point du jour : "
                            "l'équipe est disponible de 9h à 18h, les tickets ouverts "
                            "sont traités par ordre d'arrivée."),
            },
            "status": "ACTIVE",
        },
    ]


# ---------------------------------------------------------------------------
# Voie 1 : depuis du code JARVIS (on a déjà le cœur sous la main)
# ---------------------------------------------------------------------------
def seed_examples(core, *, status_channel_id: str, news_channel_id: str) -> list[dict]:
    """Crée (ou met à jour) les deux tâches d'exemple et les renvoie."""
    scheduler = core.discord_scheduler
    existing = {task["name"]: task for task in scheduler.list()}
    created = []
    for spec in example_tasks(status_channel_id, news_channel_id):
        previous = existing.get(spec["name"])
        if previous:                                # idempotence : on met à jour
            created.append(scheduler.update(previous["id"], spec))
        else:
            created.append(scheduler.add(source="example", **spec))
    return created


# ---------------------------------------------------------------------------
# Voie 2 : depuis l'extérieur, via l'API HTTP locale
# ---------------------------------------------------------------------------
def _request(base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{base_url}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def seed_via_api(base_url: str, status_channel_id: str, news_channel_id: str) -> list[dict]:
    listing = _request(base_url, "GET", "/api/discord/schedules")
    existing = {t["name"]: t for t in (listing.get("schedules") or [])}
    results = []
    for spec in example_tasks(status_channel_id, news_channel_id):
        previous = existing.get(spec["name"])
        if previous:
            results.append(_request(base_url, "PUT",
                                    f"/api/discord/schedules/{previous['id']}", spec))
        else:
            results.append(_request(base_url, "POST", "/api/discord/schedules", spec))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Installe les tâches Discord d'exemple.")
    parser.add_argument("--channel-status", required=True,
                        help="Identifiant du salon de supervision (vérification toutes les 30 min).")
    parser.add_argument("--channel-news", required=True,
                        help="Identifiant du salon d'annonces (annonce quotidienne à 09h00).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"URL de JARVIS (défaut : {DEFAULT_BASE_URL}).")
    args = parser.parse_args(argv)

    try:
        results = seed_via_api(args.base_url, args.channel_status, args.channel_news)
    except urllib.error.URLError as exc:
        print(f"JARVIS injoignable sur {args.base_url} : {exc}. Démarre-le puis relance.",
              file=sys.stderr)
        return 1

    for result in results:
        if not result.get("ok", True) or not result.get("schedule"):
            print(f"Échec : {result.get('error') or result}", file=sys.stderr)
            continue
        task = result["schedule"]
        print(f"[{task['status']}] {task['name']} — {task['tool_to_call']} {task['interval']}, "
              f"prochaine exécution : {task['next_run_iso'] or '—'} (id {task['id']})")
    print("\nRappel : le bot doit être connecté (outil discord.start) pour que "
          "les tâches aboutissent ; sinon l'échec est journalisé dans l'audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
