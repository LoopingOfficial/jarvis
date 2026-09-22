"""BrainrotFortniteTools — actions propres à brainrot-fortnite.com.

Cette couche ne remplace aucun outil générique : elle les ORCHESTRE pour le
site, en s'appuyant sur le profil audité (`velko/config/projects/
brainrot-fortnite.json`), la base réelle et le navigateur de VELKO.

Ce qui n'existe pas est dit tel quel (Google Sheet non connecté, mailer non
identifié) : la tâche passe alors en `waiting_user` ou s'arrête, jamais de
résultat inventé.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..permissions import READ_ONLY, SAFE_WRITE, SENSITIVE
from .base import ToolContext, ToolResult, registry

AGENTS = ("jarvis", "coding", "analysis", "blog", "browser", "system")


def _repo(ctx: ToolContext):
    from ..brainrot_site import AnalyticsRepository
    return AnalyticsRepository(ctx.core)


def _profile() -> dict[str, Any]:
    from ..brainrot_site import load_profile
    return load_profile()


def _table(rows: list[dict[str, Any]], limit: int = 40) -> str:
    if not rows:
        return "(aucune ligne)"
    headers = list(rows[0].keys())
    out = [" | ".join(headers)]
    for row in rows[:limit]:
        out.append(" | ".join("" if row.get(h) is None else str(row[h]) for h in headers))
    if len(rows) > limit:
        out.append(f"… ({len(rows) - limit} lignes supplémentaires)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------
def _site_inspect(ctx: ToolContext) -> ToolResult:
    profile = _profile()
    if not profile:
        return ToolResult(False, "Profil du projet absent : relancer l'audit "
                                 "(velko/config/projects/brainrot-fortnite.json).")
    local = profile.get("local") or {}
    prod = profile.get("production") or {}
    db = profile.get("database") or {}
    subs = profile.get("subsystems") or {}
    lines = [
        f"Projet : {profile.get('display_name')}",
        f"Local : {local.get('path')} ({local.get('stack')}, git={local.get('git')})",
        f"Production : {prod.get('url')}",
        f"Base : {db.get('schema')} via le connecteur « {db.get('connector_id')} » "
        f"({db.get('tables_total_at_audit')} tables à l'audit)",
        "Sous-systèmes :",
    ]
    for name, data in subs.items():
        state = "disponible" if data.get("available") else "NON DISPONIBLE"
        lines.append(f"  - {name} : {state}" + (f" — {data['note']}" if data.get("note") else ""))
    unavailable = profile.get("unavailable_metrics") or {}
    if unavailable:
        lines.append("Non mesurable avec le schéma actuel :")
        lines += [f"  - {k} : {v}" for k, v in unavailable.items() if not k.startswith("_")]
    return ToolResult(True, "\n".join(lines), data=profile)


def _site_health(ctx: ToolContext) -> ToolResult:
    """Santé RÉELLE : le site répond-il, et la base est-elle joignable ?"""
    profile = _profile()
    url = ((profile.get("production") or {}).get("url")) or "https://brainrot-fortnite.com/"
    checks: list[str] = []
    web = ctx.core.runner.run("web.check", {"url": url}, agent="jarvis")
    checks.append(f"Site {url} → {web.output[:160]}")
    repo = _repo(ctx)
    ok, rows, err = repo.query("SELECT 1 AS ping")
    checks.append("Base : joignable" if ok else f"Base : INJOIGNABLE — {err}")
    return ToolResult(bool(web.ok and ok), "\n".join(checks),
                      data={"url": url, "site_ok": bool(web.ok), "db_ok": ok})


def _site_routes(ctx: ToolContext) -> ToolResult:
    """Pages réellement présentes dans le projet local."""
    from pathlib import Path
    profile = _profile()
    root = Path(((profile.get("local") or {}).get("path")) or "")
    if not root.is_dir():
        return ToolResult(False, "Projet local introuvable : relancer l'audit.")
    pages = sorted(p.name for p in root.glob("*.php"))
    admin = sorted(p.name for p in (root / "admin").glob("*.php")) if (root / "admin").is_dir() else []
    api = sorted(p.name for p in (root / "api").rglob("*.php"))[:60] if (root / "api").is_dir() else []
    lines = [f"Pages publiques ({len(pages)}) : " + ", ".join(pages[:60])]
    if admin:
        lines.append(f"Administration ({len(admin)}) : " + ", ".join(admin[:40]))
    if api:
        lines.append(f"API ({len(api)}) : " + ", ".join(api[:40]))
    return ToolResult(True, "\n".join(lines),
                      data={"pages": pages, "admin": admin, "api": api})


# ---------------------------------------------------------------------------
# Analytics (lecture seule)
# ---------------------------------------------------------------------------
def _analytics_summary(ctx: ToolContext) -> ToolResult:
    from ..brainrot_site import VelkoOpportunityEngine
    engine = VelkoOpportunityEngine(_repo(ctx))
    brief = engine.briefing()
    return ToolResult(True, brief["text"], data=brief)


def _analytics_members(ctx: ToolContext) -> ToolResult:
    snap = _repo(ctx).snapshot()
    return ToolResult(True, "\n".join(snap["rendered"]), data=snap)


def _analytics_registrations(ctx: ToolContext) -> ToolResult:
    days = max(1, min(int(ctx.arguments.get("days") or 30), 365))
    repo = _repo(ctx)
    if not repo.has("users", "created_at"):
        return ToolResult(False, "Cette donnée n'est pas disponible : users.created_at absent.")
    ok, rows, err = repo.query(
        "SELECT DATE(CONVERT_TZ(created_at,'+00:00','+02:00')) AS jour, COUNT(*) AS inscriptions "
        f"FROM users WHERE created_at >= DATE_SUB(NOW(), INTERVAL {days} DAY) "
        "GROUP BY jour ORDER BY jour DESC")
    if not ok:
        # CONVERT_TZ exige les tables de fuseaux : repli sur l'heure serveur, annoncé.
        ok, rows, err = repo.query(
            "SELECT DATE(created_at) AS jour, COUNT(*) AS inscriptions FROM users "
            f"WHERE created_at >= DATE_SUB(NOW(), INTERVAL {days} DAY) "
            "GROUP BY jour ORDER BY jour DESC")
        if not ok:
            return ToolResult(False, f"Requête refusée : {err}")
    total = sum(int(r["inscriptions"]) for r in rows if str(r.get("inscriptions", "")).isdigit())
    return ToolResult(True, f"Inscriptions sur {days} jours : {total}\n" + _table(rows),
                      data={"days": days, "total": total, "rows": rows})


def _analytics_activity(ctx: ToolContext) -> ToolResult:
    days = max(1, min(int(ctx.arguments.get("days") or 30), 365))
    repo = _repo(ctx)
    if not repo.has("activity_presence", "last_seen_at"):
        return ToolResult(False, "Cette donnée n'est pas disponible : aucune table de présence.")
    ok, rows, err = repo.query(
        "SELECT DATE(last_seen_at) AS jour, COUNT(DISTINCT user_id) AS actifs "
        f"FROM activity_presence WHERE last_seen_at >= DATE_SUB(NOW(), INTERVAL {days} DAY) "
        "GROUP BY jour ORDER BY jour DESC")
    if not ok:
        return ToolResult(False, f"Requête refusée : {err}")
    return ToolResult(True,
                      f"Membres actifs par jour ({days} jours) — mesuré sur la dernière présence, "
                      "pas sur les connexions :\n" + _table(rows),
                      data={"days": days, "rows": rows})


def _analytics_email_status(ctx: ToolContext) -> ToolResult:
    repo = _repo(ctx)
    if not repo.has("users", "email_verified_at"):
        return ToolResult(False, "Cette donnée n'est pas disponible : colonne de confirmation absente.")
    ok, rows, err = repo.query(
        "SELECT CASE WHEN email_verified_at IS NULL THEN 'non confirmé' ELSE 'confirmé' END AS etat, "
        "COUNT(*) AS comptes FROM users GROUP BY etat")
    if not ok:
        return ToolResult(False, f"Requête refusée : {err}")
    return ToolResult(True, _table(rows), data={"rows": rows})


def _users_unverified(ctx: ToolContext) -> ToolResult:
    limit = max(1, min(int(ctx.arguments.get("limit") or 50), 500))
    repo = _repo(ctx)
    if not repo.has("users", "email_verified_at", "created_at"):
        return ToolResult(False, "Cette donnée n'est pas disponible avec le schéma actuel.")
    ok, count, _ = repo.scalar("SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NULL", "n")
    ok2, rows, err = repo.query(
        "SELECT id, username, created_at FROM users WHERE email_verified_at IS NULL "
        f"ORDER BY created_at DESC LIMIT {limit}")
    if not ok2:
        return ToolResult(False, f"Requête refusée : {err}")
    # Les adresses ne sont pas affichées : elles ne servent qu'à l'envoi.
    return ToolResult(True, f"{count} membres sans email confirmé (les {len(rows)} plus récents) :\n"
                            + _table(rows),
                      data={"total": count, "rows": rows})


def _users_inactive(ctx: ToolContext) -> ToolResult:
    days = max(1, min(int(ctx.arguments.get("days") or 30), 365))
    repo = _repo(ctx)
    if not repo.has("activity_presence", "last_seen_at"):
        return ToolResult(False, "Cette donnée n'est pas disponible : aucune table de présence.")
    ok, count, err = repo.scalar(
        "SELECT COUNT(*) AS n FROM users u LEFT JOIN activity_presence p ON p.user_id = u.id "
        f"WHERE p.last_seen_at IS NULL OR p.last_seen_at < DATE_SUB(NOW(), INTERVAL {days} DAY)", "n")
    if not ok:
        return ToolResult(False, f"Requête refusée : {err}")
    return ToolResult(True, f"{count} comptes sans activité depuis plus de {days} jours.",
                      data={"days": days, "total": count})


# ---------------------------------------------------------------------------
# Blog
# ---------------------------------------------------------------------------
def _blog_list(ctx: ToolContext) -> ToolResult:
    status = str(ctx.arguments.get("status") or "").strip()
    limit = max(1, min(int(ctx.arguments.get("limit") or 20), 100))
    repo = _repo(ctx)
    where = f"WHERE status = '{re.sub(r'[^a-z_]', '', status.lower())}'" if status else ""
    ok, rows, err = repo.query(
        f"SELECT id, title, slug, status, published_at, updated_at FROM blog_posts {where} "
        f"ORDER BY COALESCE(published_at, updated_at, created_at) DESC LIMIT {limit}")
    if not ok:
        return ToolResult(False, f"Requête refusée : {err}")
    return ToolResult(True, _table(rows), data={"rows": rows})


def _blog_read(ctx: ToolContext) -> ToolResult:
    post_id = int(ctx.arguments.get("id") or 0)
    slug = str(ctx.arguments.get("slug") or "").strip()
    if not post_id and not slug:
        return ToolResult(False, "Indique l'identifiant ou le slug de l'article.")
    where = f"id = {post_id}" if post_id else f"slug = '{re.sub(chr(39), '', slug)}'"
    ok, rows, err = _repo(ctx).query(
        "SELECT id, title, slug, status, meta_title, meta_desc, excerpt, content, "
        f"cover_image, published_at FROM blog_posts WHERE {where} LIMIT 1")
    if not ok or not rows:
        return ToolResult(False, err or "Article introuvable.")
    post = rows[0]
    body = str(post.get("content") or "")
    return ToolResult(True,
                      f"{post.get('title')} ({post.get('status')})\nslug : {post.get('slug')}\n"
                      f"meta title : {post.get('meta_title')}\nmeta desc : {post.get('meta_desc')}\n\n"
                      + body[:4000],
                      data=post)


def _blog_create_draft(ctx: ToolContext) -> ToolResult:
    """Crée un VRAI brouillon dans blog_posts. Jamais publié ici."""
    title = str(ctx.arguments.get("title") or "").strip()
    content = str(ctx.arguments.get("content") or "").strip()
    if not title or not content:
        return ToolResult(False, "Un brouillon exige au minimum un titre et un contenu.")
    slug = (str(ctx.arguments.get("slug") or "").strip()
            or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"))[:180]
    meta_title = str(ctx.arguments.get("meta_title") or title)[:180]
    meta_desc = str(ctx.arguments.get("meta_desc") or "")[:300]
    excerpt = str(ctx.arguments.get("excerpt") or "")[:500]
    cover = str(ctx.arguments.get("cover_image") or "")

    repo = _repo(ctx)
    ok, existing, _ = repo.query(f"SELECT id FROM blog_posts WHERE slug = '{slug}' LIMIT 1")
    if ok and existing:
        return ToolResult(False, f"Un article porte déjà le slug « {slug} » (id {existing[0]['id']}). "
                                 "Change le slug plutôt que d'écraser un contenu existant.")
    ok, category, _ = repo.scalar("SELECT MIN(id) AS n FROM blog_categories", "n")
    author = int(ctx.arguments.get("author_id") or 0)
    if not author:
        ok_a, author, _ = repo.scalar("SELECT MIN(id) AS n FROM users WHERE is_admin = 1", "n")

    def esc(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace("'", "''")

    sql = ("INSERT INTO blog_posts (category_id, title, slug, status, cover_image, excerpt, "
           "content, meta_title, meta_desc, author_id, is_featured, created_at, updated_at) VALUES ("
           f"{int(category or 1)}, '{esc(title)}', '{esc(slug)}', 'draft', '{esc(cover)}', "
           f"'{esc(excerpt)}', '{esc(content)}', '{esc(meta_title)}', '{esc(meta_desc)}', "
           f"{int(author or 1)}, 0, NOW(), NOW())")
    result = ctx.core.runner.run("db.query", {
        "connector_id": repo.connector_id, "query": sql}, agent=ctx.agent,
        task_id=ctx.task_id, confirmed=True)
    if not result.ok:
        return ToolResult(False, f"Création du brouillon impossible : {result.output}")
    ok, rows, _ = repo.query(f"SELECT id, title, slug, status FROM blog_posts WHERE slug = '{slug}' LIMIT 1")
    if not ok or not rows:
        # On ne déclare jamais un brouillon créé sans l'avoir relu en base.
        return ToolResult(False, "L'insertion a été acceptée mais l'article est introuvable "
                                 "en base : la transaction n'a pas abouti. Rien n'a été créé.")
    created = rows[0]
    return ToolResult(True,
                      f"Brouillon créé dans blog_posts : #{created.get('id')} « {title} » "
                      f"(slug {slug}, statut draft). Visible dans admin/blog.php. "
                      "Il n'est PAS publié.",
                      data=created)


def _blog_publish(ctx: ToolContext) -> ToolResult:
    post_id = int(ctx.arguments.get("id") or 0)
    if not post_id:
        return ToolResult(False, "Indique l'identifiant de l'article à publier.")
    repo = _repo(ctx)
    ok, rows, _ = repo.query(f"SELECT id, title, status FROM blog_posts WHERE id = {post_id} LIMIT 1")
    if not ok or not rows:
        return ToolResult(False, "Article introuvable.")
    result = ctx.core.runner.run("db.query", {
        "connector_id": repo.connector_id,
        "query": f"UPDATE blog_posts SET status='published', published_at=NOW(), "
                 f"updated_at=NOW() WHERE id = {post_id}"},
        agent=ctx.agent, task_id=ctx.task_id, confirmed=True)
    if not result.ok:
        return ToolResult(False, f"Publication impossible : {result.output}")
    return ToolResult(True, f"Article #{post_id} « {rows[0].get('title')} » publié.",
                      data={"id": post_id})


# ---------------------------------------------------------------------------
# Brainrots
# ---------------------------------------------------------------------------
def _brainrots_list(ctx: ToolContext) -> ToolResult:
    limit = max(1, min(int(ctx.arguments.get("limit") or 30), 500))
    repo = _repo(ctx)
    cols = repo.columns("brainrots")
    if not cols:
        return ToolResult(False, "Table brainrots introuvable.")
    wanted = [c for c in ("id", "name", "slug", "type_id", "image", "image_url", "updated_at")
              if c in cols] or sorted(cols)[:6]
    ok, rows, err = repo.query(f"SELECT {', '.join(wanted)} FROM brainrots LIMIT {limit}")
    if not ok:
        return ToolResult(False, f"Requête refusée : {err}")
    ok2, total, _ = repo.scalar("SELECT COUNT(*) AS n FROM brainrots", "n")
    return ToolResult(True, f"{total} Brainrots au catalogue. Aperçu :\n" + _table(rows),
                      data={"total": total, "columns": sorted(cols), "rows": rows})


def _sheet_unavailable(ctx: ToolContext, action: str) -> ToolResult:
    """Google Sheet non connecté : on le dit et on attend l'utilisateur."""
    message = ("Google Sheet non connecté : aucun connecteur Google n'est configuré dans "
               "VELKO. Autorisez l'accès (compte de service ou OAuth) dans Settings → "
               "Connectors, puis relancez. Je ne vous demanderai jamais de coller des "
               "identifiants dans la conversation.")
    try:
        if ctx.task_id:
            ctx.core.velko_tasks.waiting_user(
                ctx.task_id, f"Action bloquée : {action} — Google Sheet non connecté.",
                data={"source": "google_sheets"})
    except Exception:
        pass
    return ToolResult(False, message)


def _sheet_connector(ctx: ToolContext):
    for ctype in ("google_sheets", "google", "gsheets"):
        try:
            found = ctx.core.connectors.by_type(ctype, enabled_only=True)
        except Exception:
            found = []
        if found:
            return found[0]
    return None


def _sheet_url(ctx: ToolContext) -> str:
    """URL du Sheet : argument explicite, connecteur, ou profil projet (ordre de priorité)."""
    url = str(ctx.arguments.get("url") or "").strip()
    if url:
        return url
    connector = _sheet_connector(ctx)
    if connector:
        for key in ("url", "sheet_url", "spreadsheet_url"):
            value = str((connector.get("config") or {}).get(key) or "")
            if value:
                return value
    profile = _profile()
    return str((profile.get("subsystems") or {}).get("google_sheet", {}).get("url") or "").strip()


def _brainrots_diff_sheet(ctx: ToolContext) -> ToolResult:
    """Compare le Google Sheet au catalogue. Aucune écriture."""
    url = _sheet_url(ctx)
    if not url:
        catalogue = _brainrots_list(ctx)
        detail = ("\n\nCôté base, je peux déjà vous dire : "
                  + (catalogue.output.splitlines()[0] if catalogue.ok else "catalogue illisible."))
        out = _sheet_unavailable(ctx, "comparaison Sheet ↔ catalogue")
        return ToolResult(False, out.output + detail, data=out.data)
    sheet = ctx.core.runner.run("google.sheets.read", {"url": url}, agent=ctx.agent,
                                task_id=ctx.task_id)
    if not sheet.ok:
        return ToolResult(False, f"Lecture du Sheet impossible : {sheet.output}")
    workbook = sheet.data if isinstance(sheet.data, dict) else {}
    from ..brainrot_compare import ServerReader, compare as compare_brainrots
    site = ServerReader(ctx.core).read()
    comparison = compare_brainrots(workbook, site)
    if not comparison.get("ok"):
        return ToolResult(False, f"Comparaison impossible : {comparison.get('error', 'inconnue')}",
                          data=comparison)
    entries = comparison.get("entries") or []
    creates = [e for e in entries if e.get("status") == "CREATE"]
    updates = [e for e in entries if e.get("status") == "UPDATE"]
    diffs = [e for e in entries if e.get("changed_fields")]
    summary = (f"Diff Sheet ↔ catalogue : {comparison.get('sheet', {}).get('total') or 0} lignes lues, "
               f"{len(creates)} création(s) détectée(s), {len(updates)} mise(s) à jour, "
               f"{len(diffs)} champ(s) à corriger au total. Aucune écriture.")
    if diffs:
        sample = diffs[:5]
        lines = []
        for entry in sample:
            name = entry.get("name") or entry.get("identity") or "?"
            parts = []
            for fd in entry.get("changed_fields"):
                field = fd.get("field") or fd.get("key") if isinstance(fd, dict) else fd
                if isinstance(fd, dict):
                    parts.append(f"{field}: « {fd.get('site')} » → « {fd.get('sheet')} »")
            lines.append(f"- {name}: " + ", ".join(parts))
        summary += "\n" + "\n".join(lines)
    if creates:
        names = " ; ".join((e.get("name") or e.get("identity") or "?") for e in creates[:8])
        summary += f"\nCréations proposées ({len(creates)}) : {names}"
        if len(creates) > 8:
            summary += f" … ({len(creates) - 8} de plus)"
        if len(diffs) > 5:
            summary += f"\n… ({len(diffs) - 5} champ(s) supplémentaire(s))"
    return ToolResult(True, summary, data={"comparison": comparison})


def _brainrots_sync_sheet(ctx: ToolContext) -> ToolResult:
    if _sheet_connector(ctx) is None:
        return _sheet_unavailable(ctx, "synchronisation du catalogue")
    return ToolResult(False, "Synchronisation refusée tant que le diff n'a pas été présenté et "
                             "validé : lancez d'abord brainrot.brainrots.diff_sheet.")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
def _email_prepare(ctx: ToolContext) -> ToolResult:
    """Prépare une campagne : audience réelle, contenu, aucun envoi."""
    audience = str(ctx.arguments.get("audience") or "unverified")
    repo = _repo(ctx)
    if audience == "unverified":
        if not repo.has("users", "email_verified_at"):
            return ToolResult(False, "Audience incalculable : colonne de confirmation absente.")
        ok, count, err = repo.scalar(
            "SELECT COUNT(*) AS n FROM users WHERE email_verified_at IS NULL", "n")
    else:
        return ToolResult(False, f"Audience « {audience} » non définie pour ce site.")
    if not ok:
        return ToolResult(False, f"Audience incalculable : {err}")
    profile = _profile()
    mailer = (profile.get("subsystems") or {}).get("email") or {}
    note = ("\n\nATTENTION : aucun mailer central n'a été identifié sur le site et aucun "
            "connecteur email n'est configuré dans VELKO. L'envoi est donc impossible en "
            "l'état — dites-moi quel système d'envoi le site utilise réellement.")
    body = ("Objet : Confirme ton adresse pour débloquer ton compte Brainrot Fortnite\n\n"
            "Salut {username},\n\n"
            "Ton compte est créé mais ton adresse email n'est pas encore confirmée. "
            "Sans cette confirmation, tu ne reçois ni tes récompenses ni les annonces "
            "du serveur.\n\n"
            "Confirme ton adresse ici : {lien_de_confirmation}\n\n"
            "À bientôt sur brainrot-fortnite.com")
    return ToolResult(True,
                      f"Campagne préparée pour {count} membres sans email confirmé.\n\n"
                      f"--- contenu proposé ---\n{body}\n---\n"
                      "Rien n'a été envoyé."
                      + ("" if mailer.get("available") else note),
                      data={"audience": audience, "recipients": count, "body": body,
                            "mailer_available": bool(mailer.get("available")), "sent": False})


def _email_send(ctx: ToolContext) -> ToolResult:
    profile = _profile()
    mailer = (profile.get("subsystems") or {}).get("email") or {}
    if not mailer.get("available"):
        return ToolResult(False,
                          "Envoi impossible : aucun système d'envoi identifié pour "
                          "brainrot-fortnite.com. Je ne crée pas un second mailer sans votre "
                          "accord — indiquez celui que le site utilise.")
    return ToolResult(False, "Envoi massif : confirmation utilisateur requise à chaque campagne.")


# ---------------------------------------------------------------------------
_TOOLS = [
    ("brainrot.site.inspect", "Profil du site", _site_inspect, READ_ONLY,
     "Profil opérationnel réel de brainrot-fortnite.com : projet local, production, base, "
     "sous-systèmes disponibles ou non, métriques non mesurables."),
    ("brainrot.site.health", "Santé du site", _site_health, READ_ONLY,
     "Vérifie réellement que le site répond et que la base est joignable."),
    ("brainrot.site.routes", "Pages du site", _site_routes, READ_ONLY,
     "Liste les pages, l'administration et les endpoints API réellement présents dans le projet."),
    ("brainrot.analytics.summary", "Synthèse du site", _analytics_summary, READ_ONLY,
     "Point complet : métriques réelles, évolutions, points à surveiller et actions possibles. "
     "À utiliser pour « comment va le site » ou « fais-moi le point »."),
    ("brainrot.analytics.members", "Métriques membres", _analytics_members, READ_ONLY,
     "Métriques membres réelles : total, inscriptions du jour / 7 j / 30 j, actifs, emails confirmés."),
    ("brainrot.analytics.registrations", "Inscriptions par jour", _analytics_registrations, READ_ONLY,
     "Inscriptions réelles jour par jour sur la période demandée."),
    ("brainrot.analytics.activity", "Activité par jour", _analytics_activity, READ_ONLY,
     "Membres actifs par jour, mesurés sur la dernière présence enregistrée."),
    ("brainrot.analytics.email_status", "État des emails", _analytics_email_status, READ_ONLY,
     "Répartition réelle des comptes entre emails confirmés et non confirmés."),
    ("brainrot.users.unverified", "Membres non confirmés", _users_unverified, READ_ONLY,
     "Membres dont l'adresse email n'est pas confirmée (comptage réel)."),
    ("brainrot.users.inactive", "Membres inactifs", _users_inactive, READ_ONLY,
     "Comptes sans activité depuis N jours, d'après la table de présence."),
    ("brainrot.blog.list", "Articles du blog", _blog_list, READ_ONLY,
     "Articles réellement présents dans blog_posts, filtrables par statut."),
    ("brainrot.blog.read", "Lire un article", _blog_read, READ_ONLY,
     "Contenu réel d'un article du blog (par id ou slug)."),
    ("brainrot.brainrots.list", "Catalogue Brainrots", _brainrots_list, READ_ONLY,
     "Catalogue réel des Brainrots en base, avec ses colonnes effectives."),
    ("brainrot.brainrots.diff_sheet", "Diff Sheet ↔ catalogue", _brainrots_diff_sheet, READ_ONLY,
     "Compare le Google Sheet au catalogue en base. Aucune écriture."),
    ("brainrot.email.prepare_campaign", "Préparer une campagne", _email_prepare, READ_ONLY,
     "Calcule l'audience réelle et prépare le contenu. N'envoie rien."),
]

for tool_id, name, handler, risk, description in _TOOLS:
    registry.add(id=tool_id, name=name, category="Brainrot Fortnite", description=description,
                 handler=handler, risk=risk, agents=AGENTS,
                 input_schema={"type": "object", "properties": {
                     "days": {"type": "integer"}, "limit": {"type": "integer"},
                     "status": {"type": "string"}, "id": {"type": "integer"},
                     "slug": {"type": "string"}, "audience": {"type": "string"},
                     "url": {"type": "string"}},
                     "required": []})

# Actions qui touchent la production : confirmation obligatoire.
registry.add(
    id="brainrot.blog.create_draft", name="Créer un brouillon d'article",
    category="Brainrot Fortnite",
    description=("Crée un VRAI brouillon dans blog_posts (statut draft), visible dans "
                 "admin/blog.php. Ne publie jamais."),
    handler=_blog_create_draft, risk=SAFE_WRITE, agents=AGENTS, permissions=("write",),
    input_schema={"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string"},
        "slug": {"type": "string"}, "excerpt": {"type": "string"},
        "meta_title": {"type": "string"}, "meta_desc": {"type": "string"},
        "cover_image": {"type": "string"}, "author_id": {"type": "integer"}},
        "required": ["title", "content"]},
)

registry.add(
    id="brainrot.blog.publish", name="Publier un article", category="Brainrot Fortnite",
    description="Publie un article existant du blog. Action de production : confirmation requise.",
    handler=_blog_publish, risk=SENSITIVE, agents=AGENTS, permissions=("write",),
    dangerous_hint="L'article deviendra visible publiquement sur brainrot-fortnite.com.",
    input_schema={"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
)

registry.add(
    id="brainrot.brainrots.sync_sheet", name="Synchroniser le catalogue",
    category="Brainrot Fortnite",
    description=("Applique le Google Sheet au catalogue. Exige un diff présenté et validé. "
                 "Action massive de production : confirmation requise."),
    handler=_brainrots_sync_sheet, risk=SENSITIVE, agents=AGENTS, permissions=("write",),
    dangerous_hint="Modification massive du catalogue en production.",
    input_schema={"type": "object", "properties": {}, "required": []},
)

registry.add(
    id="brainrot.email.send_campaign", name="Envoyer une campagne", category="Brainrot Fortnite",
    description="Envoie la campagne préparée. Envoi massif : confirmation utilisateur obligatoire.",
    handler=_email_send, risk=SENSITIVE, agents=AGENTS, permissions=("write",),
    dangerous_hint="Envoi d'emails en masse aux membres du site.",
    input_schema={"type": "object", "properties": {"audience": {"type": "string"}}, "required": []},
)
