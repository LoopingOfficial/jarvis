"""Statistiques d'inscription du site brainrot-fortnite.com.

Schéma réel relevé sur la base ``brainrotfortnite_com`` avant écriture :

* ``users`` (id, email, username, created_at, premium_type, is_admin,
  ``email_verified_at``) — la table des comptes du site.
* ``user_email_verifications`` (user_id, token_hash, expires_at, used_at) —
  les liens de confirmation émis.

Nuance indispensable à la lecture des chiffres
----------------------------------------------
La vérification d'e-mail n'existe que depuis le **1er septembre 2026** : tous
les comptes antérieurs ont ``email_verified_at`` à NULL sans que personne ne
leur ait jamais demandé de confirmer quoi que ce soit. Annoncer « 97 % de
non-confirmés » serait donc faux. Ce module calcule les deux lectures — le brut
et le taux depuis l'activation — et expose la date de bascule pour que la
restitution soit honnête.

Accès à la base
---------------
Le client ``mysql`` n'est pas installé sur le poste Windows : les requêtes
passent par le serveur, à travers un connecteur SSH. La fiche MySQL peut le
déclarer via ``via_ssh`` ; à défaut, ``_ssh_connector`` le déduit de l'hôte ou
du compte d'hébergement plutôt que d'échouer, et refuse de choisir dès que
plusieurs serveurs restent plausibles.

Aucune écriture n'est possible ici : les requêtes sont figées dans le module et
paramétrées uniquement par des dates internes.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from .tools.ssh_client import shell_quote, ssh_exec

BUILD_ID = "JARVIS_SITE_STATS_V1"

# Bascule de la fonctionnalité de vérification d'e-mail, relevée en base :
# première valeur de users.email_verified_at.
VERIFICATION_LIVE_SINCE = "2026-09-01"

SITE_NAME = "brainrot-fortnite.com"


class SiteStatsError(RuntimeError):
    """Erreur d'accès aux statistiques. Jamais silencieuse : un chiffre absent
    ne doit pas se transformer en zéro affiché comme une vérité."""


# --- accès base -------------------------------------------------------------
def _mysql_connector(core) -> dict[str, Any]:
    conn = core.connectors.find("mysql-mariadb", "mysql")
    if conn is None:
        for candidate in core.connectors.list():
            if candidate.get("type") == "mysql":
                conn = core.connectors.find(candidate["id"])
                break
    if conn is None:
        raise SiteStatsError("Aucun connecteur MySQL configuré.")
    return conn


def _ssh_connector(core, mysql_conn: dict[str, Any]) -> dict[str, Any]:
    """Tunnel à emprunter : celui déclaré, sinon un SSH plausible.

    On ne devine pas au hasard : à défaut de `via_ssh`, on prend le connecteur
    SSH dont l'hôte correspond à celui de la base, et sinon l'unique connecteur
    SSH s'il n'y en a qu'un. Au-delà, on demande une configuration explicite.
    """
    declared = str(mysql_conn.get("config", {}).get("via_ssh") or "").strip()
    if declared:
        conn = core.connectors.find(declared, "ssh")
        if conn is None:
            raise SiteStatsError(f"Connecteur SSH « {declared} » introuvable.")
        return conn

    cfg = mysql_conn.get("config", {})
    db_host = str(cfg.get("host") or "").lower()
    db_user = str(cfg.get("username") or "").lower()
    db_name = str(cfg.get("database") or "").lower()

    resolved = [core.connectors.find(c["id"]) for c in core.connectors.list()
                if c.get("type") == "ssh" and c.get("enabled", True)]
    resolved = [c for c in resolved if c]

    # 1. Même hôte : cas évident.
    same_host = [c for c in resolved if str(c.get("config", {}).get("host") or "").lower() == db_host]
    if same_host:
        return same_host[0]

    # 2. Même compte d'hébergement. Sur cPanel, base et utilisateur MySQL sont
    #    prefixes du compte : `brainrotfortnite_com` appartient au compte
    #    `brainrotfortnite`, dont le SSH porte le même nom d'utilisateur. Ce
    #    n'est pas une supposition mais la convention de nommage de cPanel.
    same_account = [c for c in resolved
                    if (u := str(c.get("config", {}).get("username") or "").lower())
                    and (db_user.startswith(u) or db_name.startswith(u))]
    if len(same_account) == 1:
        return same_account[0]

    # 3. Un seul serveur SSH : aucune ambiguïté possible.
    if len(resolved) == 1:
        return resolved[0]

    raise SiteStatsError(
        "Le client mysql n'est pas disponible localement et la fiche MySQL ne déclare aucun "
        "tunnel : renseigne « Via connecteur SSH » dans le connecteur MySQL "
        f"({len(resolved)} serveurs SSH configurés, impossible de choisir).")


def _query(core, sql: str, *, timeout: int = 60) -> list[dict[str, str]]:
    """Exécute une requête de lecture et renvoie des lignes nommées."""
    mysql_conn = _mysql_connector(core)
    ssh_conn = _ssh_connector(core, mysql_conn)
    cfg = mysql_conn.get("config", {})
    password = core.vault.get(mysql_conn["id"], "password", "")
    database = str(cfg.get("database") or "")

    # Le mot de passe passe par MYSQL_PWD, jamais par `-p<motdepasse>` : un
    # argument de ligne de commande est lisible par TOUS les comptes du serveur
    # dans `ps`, y compris pendant la fraction de seconde que dure la requête.
    # C'est aussi ce qui supprime l'avertissement « Using a password on the
    # command line interface can be insecure », qui se glissait en première
    # ligne de la sortie et cassait la lecture de l'en-tête.
    command = (f"MYSQL_PWD={shell_quote(password)} "
               f"mysql -h {shell_quote(str(cfg.get('host', '127.0.0.1')))} "
               f"-P {int(cfg.get('port') or 3306)} "
               f"-u {shell_quote(str(cfg.get('username', '')))} "
               f"{shell_quote(database) if database else ''} "
               f"--batch --raw -e {shell_quote(sql)} 2>&1")

    ssh_secrets = {f: core.vault.get(ssh_conn["id"], f, "")
                   for f in ("password", "private_key", "passphrase")}
    ok, out = ssh_exec(ssh_conn.get("config", {}), ssh_secrets, command, timeout=timeout)
    out = core.vault.scrub(out or "")
    if not ok:
        raise SiteStatsError(f"Requête refusée par le serveur : {out[:300]}")

    # stderr est fusionné dans stdout : une ligne parasite avant l'en-tête
    # décalerait toutes les colonnes. On rejette donc explicitement au lieu de
    # lire des chiffres qui ne correspondraient à rien.
    lines = [line for line in (out or "").splitlines() if line.strip()]
    if any(line.upper().startswith("ERROR") for line in lines):
        raise SiteStatsError(f"MySQL : {out[:300]}")
    if not lines:
        return []
    headers = lines[0].split("\t")
    if len(headers) < 2:
        raise SiteStatsError(f"Réponse MySQL inattendue : {out[:300]}")
    return [dict(zip(headers, line.split("\t"))) for line in lines[1:]]


def _int(row: dict[str, str], key: str) -> int:
    value = (row.get(key) or "").strip()
    if value in ("", "NULL"):
        return 0
    try:
        return int(float(value))
    except ValueError:
        return 0


# --- calcul -----------------------------------------------------------------
def collect(core) -> dict[str, Any]:
    """Relevé complet des inscriptions. Lecture seule."""
    since = VERIFICATION_LIVE_SINCE
    rows = _query(core, f"""
        SELECT COUNT(*) AS total,
               SUM(email_verified_at IS NOT NULL) AS confirmes,
               SUM(email_verified_at IS NULL)     AS non_confirmes,
               SUM(premium_type <> 'none')        AS premium,
               SUM(is_admin = 1)                  AS admins,
               SUM(created_at >= NOW() - INTERVAL 7 DAY)  AS nouveaux_7j,
               SUM(created_at >= NOW() - INTERVAL 30 DAY) AS nouveaux_30j,
               SUM(created_at >= '{since}') AS depuis_verif,
               SUM(created_at >= '{since}' AND email_verified_at IS NOT NULL) AS depuis_verif_confirmes,
               SUM(created_at <  '{since}') AS anterieurs,
               MIN(created_at) AS premier_compte,
               MAX(created_at) AS dernier_compte
        FROM users""")
    if not rows:
        raise SiteStatsError("La table users n'a renvoyé aucune ligne.")
    row = rows[0]

    total = _int(row, "total")
    confirmes = _int(row, "confirmes")
    depuis = _int(row, "depuis_verif")
    depuis_ok = _int(row, "depuis_verif_confirmes")

    liens = _query(core, """
        SELECT COUNT(*) AS emis,
               SUM(used_at IS NOT NULL) AS utilises,
               SUM(used_at IS NULL AND expires_at >= NOW()) AS actifs,
               SUM(used_at IS NULL AND expires_at <  NOW()) AS expires
        FROM user_email_verifications""")
    lien = liens[0] if liens else {}

    return {
        "ok": True,
        "site": SITE_NAME,
        "total": total,
        "confirmes": confirmes,
        "non_confirmes": _int(row, "non_confirmes"),
        "premium": _int(row, "premium"),
        "admins": _int(row, "admins"),
        "nouveaux_7j": _int(row, "nouveaux_7j"),
        "nouveaux_30j": _int(row, "nouveaux_30j"),
        # Les deux lectures du taux, pour ne jamais présenter la brute seule.
        "taux_brut": round(confirmes / total * 100, 1) if total else 0.0,
        "taux_depuis_activation": round(depuis_ok / depuis * 100, 1) if depuis else 0.0,
        "depuis_activation": depuis,
        "depuis_activation_confirmes": depuis_ok,
        "comptes_anterieurs": _int(row, "anterieurs"),
        "jamais_sollicites": _int(row, "anterieurs") - (confirmes - depuis_ok),
        "verification_active_depuis": since,
        "liens_emis": _int(lien, "emis"),
        "liens_utilises": _int(lien, "utilises"),
        "liens_actifs": _int(lien, "actifs"),
        "liens_expires": _int(lien, "expires"),
        "premier_compte": (row.get("premier_compte") or "").strip(),
        "dernier_compte": (row.get("dernier_compte") or "").strip(),
        "releve_le": _dt.datetime.now().strftime("%d/%m/%Y à %H:%M"),
    }


def summary(stats: dict[str, Any]) -> str:
    """Résumé d'une ligne, destiné à une réponse parlée ou écrite."""
    return (f"{stats['total']} comptes inscrits sur {stats['site']}, "
            f"{stats['confirmes']} e-mails confirmés ({stats['taux_brut']} %). "
            f"Depuis l'activation de la vérification le "
            f"{stats['verification_active_depuis']} : "
            f"{stats['depuis_activation_confirmes']}/{stats['depuis_activation']} "
            f"({stats['taux_depuis_activation']} %). "
            f"{stats['nouveaux_7j']} nouveaux comptes sur 7 jours.")


def discord_fields(stats: dict[str, Any]) -> list[dict[str, str]]:
    """Champs prêts pour un embed Discord."""
    return [
        {"name": "👥 Total inscrits", "value": f"**{stats['total']}**"},
        {"name": "✅ E-mails confirmés", "value": f"**{stats['confirmes']}** ({stats['taux_brut']} %)"},
        {"name": "⏳ Non confirmés", "value": f"**{stats['non_confirmes']}**"},
        {"name": "🆕 Depuis l'activation",
         "value": (f"{stats['depuis_activation']} inscrits · "
                   f"**{stats['depuis_activation_confirmes']} confirmés** "
                   f"({stats['taux_depuis_activation']} %)")},
        {"name": "📭 Jamais sollicités", "value": f"**{stats['jamais_sollicites']}** comptes antérieurs"},
        {"name": "🔗 Liens de vérification",
         "value": (f"{stats['liens_emis']} émis · {stats['liens_utilises']} utilisés · "
                   f"{stats['liens_actifs']} actifs")},
        {"name": "📈 Nouveaux (7 j)", "value": f"**{stats['nouveaux_7j']}**"},
        {"name": "📈 Nouveaux (30 j)", "value": f"**{stats['nouveaux_30j']}**"},
        {"name": "💎 Premium", "value": f"**{stats['premium']}** · {stats['admins']} admin"},
    ]


def discord_description(stats: dict[str, Any]) -> str:
    return (f"**{stats['total']} comptes inscrits** sur le site.\n"
            f"La vérification d'e-mail n'existe que depuis le "
            f"**{stats['verification_active_depuis']}** : les comptes antérieurs n'ont jamais eu "
            f"de lien à confirmer, ce qui explique le faible taux global. Sur la période où le "
            f"mécanisme tourne, le taux réel est de "
            f"**{stats['taux_depuis_activation']} %**.")
