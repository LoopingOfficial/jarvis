"""Outils « statistiques du site » exposés aux agents.

Deux outils seulement, et la frontière entre eux est nette :

* ``site.stats`` lit et rend les chiffres — lecture seule, aucun effet.
* ``site.stats_discord`` publie le rapport dans un salon — écriture, donc
  soumis aux mêmes garde-fous que les autres publications Discord.

Séparer les deux permet à JARVIS de répondre « combien d'inscrits ? » sans rien
publier, et au planificateur d'appeler la publication sans passer par le modèle.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from ..permissions import READ_ONLY, SAFE_WRITE
from .base import ToolContext, ToolResult, registry


def _collect(ctx: ToolContext) -> dict[str, Any]:
    from ..site_stats import collect
    return collect(ctx.core)


def _stats(ctx: ToolContext) -> ToolResult:
    from ..site_stats import SiteStatsError, summary
    try:
        stats = _collect(ctx)
    except SiteStatsError as exc:
        return ToolResult(ok=False, risk=READ_ONLY, output=str(exc))
    except Exception as exc:
        return ToolResult(ok=False, risk=READ_ONLY,
                          output=f"Lecture des statistiques impossible : {exc}")
    return ToolResult(ok=True, risk=READ_ONLY, data=stats, output=summary(stats))


registry.add(
    id="site.stats", name="Statistiques du site", category="Site",
    description=("Compte les inscrits de brainrot-fortnite.com : total, e-mails confirmés et "
                 "non confirmés, nouveaux comptes sur 7 et 30 jours, premium, liens de "
                 "vérification. Lecture seule. Renvoie AUSSI le taux depuis l'activation de la "
                 "vérification d'e-mail : le taux brut seul est trompeur, car les comptes créés "
                 "avant n'ont jamais eu de lien à confirmer."),
    handler=_stats, risk=READ_ONLY, permissions=("read",), agents=(),
    input_schema={"type": "object", "properties": {}},
)


# Une bannière pèse ~350 Ko. Publiée chaque semaine par le planificateur, elle
# remplirait le dossier temporaire sans que personne ne le remarque : on purge
# les anciennes à chaque génération. Un fichier récent est conservé le temps que
# Discord finisse de le téléverser.
BANNER_RETENTION_S = 3600


def _banner_path() -> Path:
    directory = Path(tempfile.gettempdir()) / "jarvis-site-stats"
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - BANNER_RETENTION_S
    for old in directory.glob("inscriptions-*.gif"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass          # fichier verrouillé ou déjà parti : sans importance
    return directory / f"inscriptions-{time.strftime('%Y%m%d-%H%M%S')}.gif"


def _stats_discord(ctx: ToolContext) -> ToolResult:
    from ..site_stats import (SiteStatsError, discord_description, discord_fields, summary)

    channel = str(ctx.arguments.get("channel") or ctx.arguments.get("channel_id") or "").strip()
    if not channel:
        return ToolResult(ok=False, risk=SAFE_WRITE,
                          output="Indique le salon (nom ou identifiant) avec `channel`.")
    try:
        stats = _collect(ctx)
    except SiteStatsError as exc:
        return ToolResult(ok=False, risk=SAFE_WRITE, output=str(exc))
    except Exception as exc:
        return ToolResult(ok=False, risk=SAFE_WRITE,
                          output=f"Lecture des statistiques impossible : {exc}")

    # La bannière est un plus : son absence ne doit pas priver du rapport.
    image, banner_error = "", ""
    if ctx.arguments.get("banner", True):
        try:
            from ..site_stats_banner import build
            image = build(stats, _banner_path())
        except Exception as exc:
            banner_error = str(exc)

    from .discord_tools import _send_embed
    embed_ctx = ToolContext(
        core=ctx.core, agent=ctx.agent, task_id=ctx.task_id,
        conversation_id=ctx.conversation_id, execution_policy=ctx.execution_policy,
        arguments={
            "channel": channel,
            "title": f"📊 {stats['site']} — Inscriptions",
            "description": discord_description(stats),
            "severity": "INFO",
            "fields": discord_fields(stats),
            "inline": True,
            "image": image,
            "footer": f"JARVIS · source : table users · relevé du {stats['releve_le']}",
        })
    result = _send_embed(embed_ctx)
    if not result.ok:
        return result

    data = dict(result.data or {})
    data["stats"] = stats
    output = f"{summary(stats)} Rapport publié dans #{data.get('channel')}."
    if banner_error:
        output += f" (Bannière non générée : {banner_error})"
    return ToolResult(ok=True, risk=SAFE_WRITE, data=data, output=output)


registry.add(
    id="site.stats_discord", name="Publier les statistiques sur Discord", category="Site",
    description=("Relève les inscriptions du site et publie le rapport dans un salon Discord : "
                 "embed détaillé et bannière GIF animée (compteur, taux de confirmation). "
                 "Le salon s'indique par son nom."),
    handler=_stats_discord, risk=SAFE_WRITE, permissions=("read", "write"), agents=(),
    input_schema={"type": "object", "properties": {
        "channel": {"type": "string", "description": "Nom du salon (#staff) ou identifiant."},
        "channel_id": {"type": "string", "description": "Alias de `channel`."},
        "banner": {"type": "boolean", "description": "Joindre la bannière animée (vrai par défaut)."}},
        "required": ["channel"]},
)
