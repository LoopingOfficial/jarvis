"""BrainrotFortniteTools — actions propres à brainrot-fortnite.com.

Cette couche ne remplace aucun outil générique : elle les ORCHESTRE pour le
site, en s'appuyant sur le profil audité (`velko/config/projects/
brainrot-fortnite.json`), la base réelle et le navigateur de VELKO.

Ce qui n'existe pas est dit tel quel (Google Sheet non connecté, mailer non
identifié) : la tâche passe alors en `waiting_user` ou s'arrête, jamais de
résultat inventé.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from pathlib import Path
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
        # Garde-fou §16 : les comptes créés AVANT l'introduction de la vérification
        # (migration 2026-09-01_044) n'ont jamais reçu d'email et ne sont pas des
        # destinataires légitimes. Seuls les non-confirmés post-introduction attendent
        # réellement une relance.
        if ok:
            ok2, awaiting, err2 = repo.scalar(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE email_verified_at IS NULL AND created_at >= '2026-09-01 00:00:00'", "n")
            if ok2:
                count = awaiting
    else:
        return ToolResult(False, f"Audience « {audience} » non définie pour ce site.")
    if not ok:
        return ToolResult(False, f"Audience incalculable : {err}")
    profile = _profile()
    mailer = (profile.get("subsystems") or {}).get("email") or {}
    # Garde-fou §16 : les comptes créés AVANT l'introduction de la vérification
    # (migration 2026-09-01_044) n'ont jamais reçu d'email, ils sont exclus de
    # l'audience. Le chiffre « réellement relançable » est déjà dans `count`.
    legacy = None
    repo = _repo(ctx)
    ok_l, legacy, err_l = repo.scalar(
        "SELECT COUNT(*) AS n FROM users "
        "WHERE email_verified_at IS NULL AND created_at < '2026-09-01 00:00:00'", "n")
    note = ("\n\n[cadre §16] L'audience ne compte que les comptes créés depuis l'introduction "
            "de la vérification (migration 2026-09-01_044). "
            + (f"{count} relançable(s), {legacy} comptes antérieurs exclus (jamais invités "
               "à confirmer)." if ok_l and legacy is not None else
               "Chiffre réellement relançable affiché ci-dessus."))
    if not mailer.get("available"):
        note += ("\n\nATTENTION : aucun mailer central n'a été identifié sur le site et aucun "
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
                      + note,
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
    return ToolResult(False,
                      "Envoi massif : confirmation utilisateur requise à chaque campagne. "
                      "Notez que l'audience « unverified » ne compte que les comptes créés "
                      "depuis l'introduction de la vérification (migration 2026-09-01_044) — "
                      "les comptes antérieurs n'ayant jamais reçu d'email sont exclus.")


# ---------------------------------------------------------------------------
# Web Development Pipeline (lecture seule + patch local)
# ---------------------------------------------------------------------------
# Le pipeline ne touche JAMAIS la production. Il compare la version LIVE du
# site au parc local (/Users/jerome/Desktop/Brainrot) et aux snapshots de
# déploiement (deployment-backups/), puis — si on le demande — écrit un patch
# uni-diff DANS LE SANDBOX LOCAL uniquement. L'application en prod n'est jamais
# faite ici : elle passe par la confirmation humaine.
_SITE_SNAPSHOT_RE = re.compile(r"^(.+)-(\d{8})-(\d{6})$")


def _webdev_local_root(ctx: ToolContext) -> Path:
    profile = _profile() or {}
    path = (profile.get("local") or {}).get("path") or ""
    if not path:
        return Path.home() / "Desktop" / "Brainrot"
    return Path(path)


def _webdev_deployment_backups(ctx: ToolContext) -> Path:
    return _webdev_local_root(ctx) / "deployment-backups"


def _webdev_read_prod(ctx: ToolContext, path: str) -> ToolResult:
    """Lit un fichier LIVE via le connecteur SSH audité (lecture seule)."""
    profile = _profile() or {}
    ssh_id = ((profile.get("production") or {}).get("ssh") or {}).get("connector") or ""
    if not ssh_id:
        return ToolResult(False, "Connecteur SSH du site non renseigné dans le profil.")
    return ctx.core.runner.run("ssh.read_file",
                                {"connector_id": ssh_id, "path": path},
                                agent=ctx.agent, task_id=ctx.task_id)


def _webdev_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _webdev_find_snapshots(ctx: ToolContext, rel_path: str) -> list[dict[str, Any]]:
    """Cherche <rel_path> dans les archives deployment-backups/*/."""
    backups = _webdev_deployment_backups(ctx)
    matches: list[dict[str, Any]] = []
    if not backups.is_dir():
        return matches
    for backup in sorted(backups.iterdir()):
        if not backup.is_dir():
            continue
        candidate = backup / rel_path
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            m = _SITE_SNAPSHOT_RE.match(backup.name)
            matches.append({"backup": backup.name, "stamp": m.group(2) + m.group(3) if m else "",
                            "path": str(candidate), "hash": _webdev_hash(content),
                            "mtime": backup.stat().st_mtime})
    matches.sort(key=lambda s: s["stamp"])
    return matches


def _webdev_to_backup_name(rel_path: str) -> str:
    return rel_path.replace("/", "__").strip("_") or "root"


def _webdev_apply_patch(ctx: ToolContext, rel_path: str, new_content: str,
                        reason: str) -> dict[str, Any]:
    """Écrit dans le sandbox local uniquement : jamais un fichier distant."""
    sandbox = Path(__file__).resolve().parents[3] / "velko" / "dev" / "workspace" / "webdev"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = sandbox / stamp / f"{_webdev_to_backup_name(rel_path)}.patch"
    target.parent.mkdir(parents=True, exist_ok=True)
    old = ""
    local_file = _webdev_local_root(ctx) / rel_path
    if local_file.is_file():
        old = local_file.read_text(encoding="utf-8", errors="replace")
    unified = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new_content.splitlines(keepends=True),
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}"))
    meta = {
        "version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": reason, "target": rel_path,
        "editable": True, "apply_requires_confirm": True,
        "hash_before": _webdev_hash(old), "hash_after": _webdev_hash(new_content),
    }
    payload = (json.dumps(meta, ensure_ascii=False, indent=2) + "\n\n"
               + (unified or "(aucune différence avec le parc local)\n"))
    target.write_text(payload, encoding="utf-8")
    return {"path": str(target), "meta": meta, "diff": unified,
            "sandbox": str(target.parent)}


def _webdev_lint(ctx: ToolContext, rel_path: str, content: str) -> list[str]:
    """Lint local (php -l / node --check) sans exécution distante ni déploiement."""
    issues: list[str] = []
    suffix = Path(rel_path).suffix.lower()
    tmp = Path(__file__).resolve().parents[3] / "velko" / "dev" / "workspace" / "webdev" / (
        f"lint_{_webdev_to_backup_name(rel_path)}_{hashlib.sha256(content.encode()).hexdigest()[:8]}{suffix}")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(content, encoding="utf-8")
        if suffix == ".php":
            code, out = _webdev_cli(["php", "-l", str(tmp)])
            if code != 0:
                issues.append(out.strip() or "php -l a échoué.")
        elif suffix in (".js", ".mjs", ".cjs"):
            code, out = _webdev_cli(["node", "--check", str(tmp)])
            if code != 0:
                issues.append(out.strip() or "node --check a échoué.")
    finally:
        tmp.unlink(missing_ok=True)
    return issues


def _webdev_cli(cmd: list[str]) -> tuple[int, str]:
    import subprocess
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"{cmd[0]} introuvable localement."
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} a dépassé 90 s."


def _webdev_pipeline(ctx: ToolContext) -> ToolResult:
    """Pipeline dev (lecture seule) : état LIVE vs parc local vs archives."""
    rel_path = str(ctx.arguments.get("path") or "").strip().lstrip("/")
    if not rel_path:
        return ToolResult(False, "Chemin relatif requis (ex : includes_app/functions.php).")
    site = _webdev_read_prod(ctx, rel_path)
    remote = (site.data or {}).get("path") if site.ok else None
    prod_content = (site.data or {}).get("content", "") if site.ok else ""
    prod_hash = _webdev_hash(prod_content) if site.ok else None

    local_file = _webdev_local_root(ctx) / rel_path
    local_content = local_file.read_text(encoding="utf-8", errors="replace") if local_file.is_file() else None
    local_hash = _webdev_hash(local_content) if local_content is not None else None

    snapshots = _webdev_find_snapshots(ctx, rel_path)
    newest = snapshots[-1] if snapshots else None

    if site.ok and local_content is not None:
        state = "equal" if prod_hash == local_hash else "different"
    elif site.ok:
        state = "prod_only"
    elif local_content is not None:
        state = "local_only"
    else:
        state = "absent"
    rollback = bool(newest)

    lines = [
        f"WebDevPipeline — {rel_path}",
        f"État : {state}" + ("  (LIVE == parc local)" if state == "equal"
                             else ("  (LIVE diffère du parc local)" if state == "different" else "")),
        f"  → LIVE  : {'lue' if site.ok else 'ABSENTE/echec'} ({remote or '—'})",
        f"  → Local : {'présent' if local_content is not None else 'absent'}",
        f"  hash LIVE  : {prod_hash or '—'}",
        f"  hash Local : {local_hash or '—'}",
        f"Reversibilité (deployment-backups/) : {'OUI — snapshot ' + newest['backup'] if newest else 'NON — aucune archive de ce fichier'}",
        f"  → {len(snapshots)} snapshot(s) trouvé(s) dans les archives de déploiement.",
    ]
    if state == "different" and site.ok and local_content is not None:
        diff = list(difflib.unified_diff(
            local_content.splitlines(keepends=True), prod_content.splitlines(keepends=True),
            fromfile=f"local/{rel_path}", tofile=f"live/{rel_path}"))[:40]
        if diff:
            lines.append("Différences (local → LIVE) :")
            lines.append("".join(diff).rstrip())
    if site.ok and state != "absent" and prod_hash and local_content is not None:
        lines.append("Note : le parc local n'est PAS synchronisé avec la production."
                     if state == "different" else "Parc local synchronisé avec la production.")
    lines.append("Aucune écriture effectuée (pipeline en lecture seule).")
    data = {"path": rel_path, "state": state,
            "copies": {"live": {"ok": site.ok, "path": remote, "hash": prod_hash},
                       "local": {"path": str(local_file), "hash": local_hash}},
            "snapshots": snapshots, "reversible": rollback}
    return ToolResult(True, "\n".join(lines), data=data)


def _webdev_propose_patch(ctx: ToolContext) -> ToolResult:
    """Crée un patch LOCAL (sandbox) à partir du contenu proposé.

    Ne touche jamais la production : le fichier est écrit dans
    velko/dev/workspace/webdev/ et doit être revalidé avant tout déploiement.
    """
    rel_path = str(ctx.arguments.get("path") or "").strip().lstrip("/")
    content = str(ctx.arguments.get("content") or "")
    reason = str(ctx.arguments.get("reason") or "proposition de modification")
    if not rel_path or not content:
        return ToolResult(False, "Il faut « path » (relatif) et « content » (nouveau contenu).")
    result = _webdev_apply_patch(ctx, rel_path, content, reason)
    issues = _webdev_lint(ctx, rel_path, content)
    lines = [
        f"Patch créé dans le sandbox local : {result['path']}",
        f"Action demandée : {reason}",
        f"Diff appliqué ({len(result['diff'].splitlines()) - 1} lignes) — LOCAL UNIQUEMENT.",
    ]
    if issues:
        lines.append("⚠ Vérification locale : " + "; ".join(issues))
    else:
        lines.append("Vérification locale OK (pas de section en lecture seule).")
    lines.append("RIEN n'a été envoyé vers brainrot-fortnite.com.")
    return ToolResult(True, "\n".join(lines),
                      data={"patch": result["path"], "diff": result["diff"],
                            "issues": issues, "deployed": False,
                            "meta": result["meta"]})


def _webdev_deploy_plan(ctx: ToolContext) -> ToolResult:
    """Plan de déploiement (lecture seule) : manifeste + commandes FTP, rien d'exécuté.

    Génère un snapshot manifeste pour les fichiers indiqués : état de chaque fichier
    (présent/absent en local, hash, snapshot de reversibilité existant ?) puis les
    commandes curl --netrc d'upload / de restauration. AUCUN upload n'est lancé.
    """
    raw = ctx.arguments.get("files") or ctx.arguments.get("path") or ""
    if isinstance(raw, str):
        files = [p.strip().lstrip("/") for p in raw.split(",") if p.strip()]
    else:
        files = [str(p).strip().lstrip("/") for p in (raw or []) if str(p).strip()]
    if not files:
        return ToolResult(False, "Indiquez « files » : liste de chemins relatifs (séparés par des virgules).")
    ftp = "ftp.vpcloud.fr"
    local_root = _webdev_local_root(ctx)
    rows, recommended = [], []
    for rel in files:
        local_file = local_root / rel
        exists = local_file.is_file()
        snapshots = _webdev_find_snapshots(ctx, rel)
        newest = snapshots[-1] if snapshots else None
        rows.append({
            "file": rel,
            "local_exists": exists,
            "hash_local": _webdev_hash(local_file.read_text(encoding="utf-8", errors="replace")) if exists else None,
            "snapshot": (newest or {}).get("backup"),
            "rollback_available": bool(newest),
        })
        if not exists:
            recommended.append(f"- {rel} : ABSENT du parc local — vérifier avant tout envoi.")
            continue
        if not newest:
            recommended.append(f"- {rel} : AUCUN snapshot deployment-backups — créer le backup préalable.")
    plan = [
        "Plan de déploiement (généré, NON exécuté) — " + ftp,
    ]
    for r in rows:
        plan.append(
            f"  {r['file']}  local={'ok' if r['local_exists'] else 'ABSENT'} "
            f"hash={r['hash_local'] or '—'}  "
            f"rollback={'oui (' + str(r['snapshot']) + ')' if r['rollback_available'] else 'NON'}",
        )
    if recommended:
        plan.append("À contrôler AVANT tout envoi :")
        plan += recommended
    uploads = [r["file"] for r in rows if r["local_exists"]]
    if uploads:
        plan.append("Commandes d'envoi (préparées, À CONFIRMER) :")
        for rel in uploads:
            plan.append(f'  curl --netrc -T "{local_root / rel}" "ftp://{ftp}/{rel}"')
    plan.append("Aucun fichier n'a été envoyé.")
    return ToolResult(True, "\n".join(plan),
                      data={"ftp_host": ftp, "files": rows, "uploads": uploads,
                            "deployed": False})


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
    ("brainrot.webdev.pipeline", "Pipeline dev (lecture seule)", _webdev_pipeline, READ_ONLY,
     "Compare un fichier site : état production vs parc local vs archives de déploiement, "
     "avec reversibilité. Aucune écriture."),
    ("brainrot.webdev.deploy_plan", "Plan de déploiement", _webdev_deploy_plan, READ_ONLY,
     "Génère le manifeste + commandes FTP curl --netrc pour les fichiers indiqués "
     "(reversibilité vérifiée). RIEN n'est exécuté."),
]

for tool_id, name, handler, risk, description in _TOOLS:
    registry.add(id=tool_id, name=name, category="Brainrot Fortnite", description=description,
                 handler=handler, risk=risk, agents=AGENTS,
                 input_schema={"type": "object", "properties": {
                     "days": {"type": "integer"}, "limit": {"type": "integer"},
                     "status": {"type": "string"}, "id": {"type": "integer"},
                     "slug": {"type": "string"}, "audience": {"type": "string"},
                     "url": {"type": "string"}, "path": {"type": "string"},
                     "content": {"type": "string"}, "reason": {"type": "string"},
                     "files": {"type": "string"}},
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

# Patch local uniquement : écrit dans velko/dev/workspace/webdev/, jamais en prod.
registry.add(
    id="brainrot.webdev.propose_patch", name="Proposer un patch local",
    category="Brainrot Fortnite",
    description=("Écrit un patch uni-diff dans le sandbox local "
                 "velko/dev/workspace/webdev/ (jamais envoyé vers la production)."),
    handler=_webdev_propose_patch, risk=SAFE_WRITE, agents=AGENTS, permissions=("write",),
    dangerous_hint="Écrit sur disque local uniquement ; ne touche pas brainrot-fortnite.com.",
    input_schema={"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
        "reason": {"type": "string"}},
        "required": ["path", "content"]},
)
