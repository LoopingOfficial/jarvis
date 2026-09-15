"""Coffre-fort d'identifiants — profils de connexion pour agents autonomes.

Ce module N'IMPLÉMENTE PAS de cryptographie : il réutilise `SecretVault`
(jarvis/secrets.py), déjà en AES-256-GCM avec AAD et clé maître dans le
Gestionnaire d'identifiants Windows / Keychain / fichier 0600. Écrire une
seconde couche de chiffrement créerait un second endroit où se tromper.

Répartition des données
-----------------------
  * `vault_credentials` : métadonnées SEULEMENT (service, URL, type d'auth,
    domaines autorisés, expiration, rotation). Lisible sans risque.
  * `secrets`           : les valeurs sensibles, chiffrées, sous l'identité
    `cred:<credential_id>` — l'AAD du chiffrement lie donc chaque secret à sa
    fiche : déplacer un blob d'une fiche à l'autre le rend indéchiffrable.
  * `vault_grants`      : qui a le droit d'obtenir quoi. Sans ligne ici, un
    agent n'obtient rien, même si la fiche existe.

Principe du moindre privilège
-----------------------------
`get_credentials()` refuse par défaut. Une demande passe seulement si toutes
les conditions sont réunies : fiche active et non expirée, habilitation
nominative non révoquée, portée (scope) demandée accordée, quota d'usage non
épuisé, et — pour une injection web — domaine cible explicitement autorisé.
Chaque tentative, accordée ou refusée, est journalisée.

Ce que l'on ne fait jamais
--------------------------
Aucune valeur en clair ne sort par l'API HTTP, n'entre dans un prompt LLM, ni
n'apparaît dans un log : `CredentialBundle` masque sa propre représentation et
`SecretVault.scrub()` nettoie les sorties d'outils.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import totp as totp_mod
from .db import new_id

# Champs secrets par type d'authentification. Sert à la validation d'entrée et
# à savoir quoi purger lors d'une rotation.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "basic":   ("username", "password"),
    "api_key": ("api_key",),
    "oauth2":  ("access_token", "refresh_token", "client_id", "client_secret"),
    "cookies": ("cookies", "local_storage"),
}
TOTP_FIELD = "totp_secret"
ALL_SECRET_FIELDS = tuple(sorted({f for fields in SECRET_FIELDS.values() for f in fields} | {TOTP_FIELD}))

SCOPES = ("read", "login", "rotate")
STATUS_ACTIVE, STATUS_EXPIRED, STATUS_REVOKED = "active", "expired", "revoked"


class VaultDenied(PermissionError):
    """Accès refusé. Le message est destiné à l'utilisateur et à l'audit."""


def _now() -> float:
    return time.time()


def _loads(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if value is not None else fallback
    except Exception:
        return fallback


def host_of(url: str) -> str:
    """Hôte en minuscules, sans port ni `www.` — ce que l'on compare."""
    raw = str(url or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    host = (urllib.parse.urlparse(raw).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def domain_matches(target: str, allowed: str) -> bool:
    """`allowed` couvre le domaine et ses sous-domaines, jamais au-delà.

    « example.com » autorise « app.example.com » mais PAS « example.com.evil.net »
    ni « notexample.com » : la comparaison se fait sur les étiquettes, pas sur
    une inclusion de chaîne — c'est exactement le piège qui laisse un agent se
    connecter sur un site de phishing.
    """
    t, a = host_of(target), host_of(allowed) or str(allowed or "").strip().lower().lstrip(".")
    if not t or not a:
        return False
    return t == a or t.endswith("." + a)


@dataclass
class AgentContext:
    """Qui demande, pour quelle tâche, et vers quelle cible."""
    agent_id: str
    task_id: str = ""
    tool: str = ""
    purpose: str = ""
    target_url: str = ""

    def audit_fields(self) -> dict[str, Any]:
        return {"agent": self.agent_id or "inconnu", "task_id": self.task_id, "tool": self.tool}


@dataclass
class CredentialBundle:
    """Identifiants déchiffrés, destinés au seul code d'automatisation.

    `__repr__` et `__str__` sont volontairement masqués : un `print(bundle)`,
    une trace d'exception ou un log accidentel ne doivent jamais révéler un mot
    de passe. Les valeurs ne sont accessibles que par accès explicite.
    """
    credential_id: str
    service_name: str
    service_url: str
    auth_type: str
    secrets: dict[str, str] = field(repr=False, default_factory=dict)
    has_totp: bool = False

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (f"<CredentialBundle {self.credential_id} service={self.service_name!r} "
                f"auth={self.auth_type} champs={sorted(self.secrets)} valeurs=masquées>")

    __str__ = __repr__

    def get(self, field_name: str, default: str = "") -> str:
        return self.secrets.get(field_name, default)

    @property
    def username(self) -> str:
        return self.secrets.get("username", "")

    @property
    def password(self) -> str:
        return self.secrets.get("password", "")

    @property
    def api_key(self) -> str:
        return self.secrets.get("api_key", "")

    def as_basic_auth(self) -> tuple[str, str]:
        return self.username, self.password

    def as_headers(self) -> dict[str, str]:
        """En-têtes HTTP prêts à l'emploi selon le type d'authentification."""
        if self.auth_type == "api_key" and self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        if self.auth_type == "oauth2" and self.secrets.get("access_token"):
            return {"Authorization": f"Bearer {self.secrets['access_token']}"}
        return {}

    def as_cookies(self) -> list[dict[str, Any]]:
        """Cookies au format Playwright/Selenium (liste de dictionnaires)."""
        return _loads(self.secrets.get("cookies", "[]"), [])


class CredentialVault:
    """CRUD des fiches + habilitations + délivrance contrôlée aux agents."""

    def __init__(self, db, secret_vault, audit=None, events=None) -> None:
        self._db = db
        self._secrets = secret_vault
        self._audit = audit
        self._events = events

    # -- journalisation ---------------------------------------------------
    def _record(self, action: str, *, status: str = "ok", ctx: AgentContext | None = None,
                credential_id: str = "", detail: Any = "") -> None:
        if self._audit is None:
            return
        fields = ctx.audit_fields() if ctx else {}
        try:
            self._audit.record(
                action=action, status=status, connector_id=credential_id,
                detail=detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False),
                **fields,
            )
        except Exception:
            pass                                   # l'audit ne doit jamais casser l'appel métier

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        try:
            self._events.emit(kind, payload)
        except Exception:
            pass

    # -- fiches -----------------------------------------------------------
    def _row(self, credential_id: str):
        return self._db.one("SELECT * FROM vault_credentials WHERE id=?", (credential_id,))

    # Champs dont l'aperçu est TOTALEMENT masqué. Montrer les quatre derniers
    # caractères aide à reconnaître une clé API parmi plusieurs ; sur un mot de
    # passe ou un secret TOTP, c'est de l'information offerte à qui regarde
    # l'écran, sans contrepartie utile.
    FULLY_MASKED = ("password", "client_secret", TOTP_FIELD, "cookies", "local_storage")

    def _preview(self, credential_id: str, field: str) -> str:
        if field in self.FULLY_MASKED:
            return "••••••••" if self._secrets.has(f"cred:{credential_id}", field) else ""
        return self._secrets.preview(f"cred:{credential_id}", field)

    def public(self, row) -> dict[str, Any]:
        """Vue sûre : aperçus masqués uniquement, jamais une valeur complète."""
        data = {k: row[k] for k in row.keys()}
        cid = data["id"]
        data["allowed_domains"] = _loads(data.get("allowed_domains"), [])
        data["tags"] = _loads(data.get("tags"), [])
        data["has_totp"] = bool(data.get("has_totp"))
        data["secret_fields"] = {
            f: {"configured": True, "preview": self._preview(cid, f)}
            for f in self._secrets.fields(f"cred:{cid}") if f != TOTP_FIELD
        }
        data["expired"] = self.is_expired(row)
        data["rotation_due"] = self.rotation_due(row)
        data["grants"] = self.grants_for(cid)
        return data

    def get(self, credential_id: str) -> dict[str, Any] | None:
        row = self._row(credential_id)
        return self.public(row) if row else None

    def list(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM vault_credentials"
        if not include_revoked:
            sql += f" WHERE status != '{STATUS_REVOKED}'"
        sql += " ORDER BY service_name COLLATE NOCASE"
        return [self.public(r) for r in self._db.query(sql)]

    def upsert(self, payload: dict[str, Any], *, user: str = "jerome") -> dict[str, Any]:
        """Crée ou met à jour une fiche. Les secrets passés sont chiffrés puis oubliés.

        Un secret absent du payload n'est PAS effacé : mettre à jour l'URL d'un
        service ne doit pas supprimer son mot de passe. Pour retirer une valeur,
        il faut l'envoyer explicitement vide.
        """
        cid = str(payload.get("id") or "").strip() or new_id("cred")
        existing = self._row(cid)
        auth_type = str(payload.get("auth_type") or (existing["auth_type"] if existing else "basic")).strip()
        if auth_type not in SECRET_FIELDS:
            raise ValueError(f"Type d'authentification inconnu : {auth_type}")

        domains = payload.get("allowed_domains")
        if isinstance(domains, str):
            domains = [d.strip() for d in domains.replace(",", " ").split() if d.strip()]
        if domains is None:
            domains = _loads(existing["allowed_domains"], []) if existing else []
        # Le domaine de l'URL de service est autorisé d'office : sans cela, la
        # fiche serait inutilisable tant qu'on n'a pas saisi son propre domaine.
        service_url = str(payload.get("service_url") or (existing["service_url"] if existing else "")).strip()
        auto = host_of(service_url)
        if auto and not any(domain_matches(service_url, d) for d in domains):
            domains = [*domains, auto]

        now = _now()
        fields = {
            "service_name": str(payload.get("service_name") or (existing["service_name"] if existing else "")).strip(),
            "service_url": service_url,
            "allowed_domains": json.dumps(sorted(set(domains)), ensure_ascii=False),
            "auth_type": auth_type,
            "username_preview": str(payload.get("username") or "")[:64]
                                 or (existing["username_preview"] if existing else ""),
            "connector_id": str(payload.get("connector_id") or (existing["connector_id"] if existing else "")).strip(),
            "status": str(payload.get("status") or (existing["status"] if existing else STATUS_ACTIVE)),
            "expires_at": payload.get("expires_at", existing["expires_at"] if existing else None),
            "rotation_days": int(payload.get("rotation_days", existing["rotation_days"] if existing else 0) or 0),
            "notes": str(payload.get("notes") or (existing["notes"] if existing else "")),
            "tags": json.dumps(payload.get("tags") or _loads(existing["tags"], []) if existing else [], ensure_ascii=False),
        }
        if not fields["service_name"]:
            raise ValueError("Le nom du service est obligatoire.")

        if existing:
            self._db.execute(
                "UPDATE vault_credentials SET service_name=?, service_url=?, allowed_domains=?, auth_type=?, "
                "username_preview=?, connector_id=?, status=?, expires_at=?, rotation_days=?, notes=?, tags=?, "
                "updated_at=? WHERE id=?",
                (*fields.values(), now, cid))
        else:
            self._db.execute(
                "INSERT INTO vault_credentials(id, service_name, service_url, allowed_domains, auth_type, "
                "username_preview, connector_id, status, expires_at, rotation_days, notes, tags, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, *fields.values(), now, now))

        self._store_secrets(cid, payload)
        self._record("vault.credential.saved", credential_id=cid,
                     detail={"service": fields["service_name"], "auth_type": auth_type, "user": user})
        self._emit("vault.credential.saved", {"id": cid, "service_name": fields["service_name"]})
        return self.get(cid) or {}

    def _store_secrets(self, cid: str, payload: dict[str, Any]) -> None:
        """Chiffre les valeurs fournies. Les clés absentes sont laissées intactes."""
        holder = f"cred:{cid}"
        for field_name in ALL_SECRET_FIELDS:
            if field_name not in payload:
                continue
            value = payload.get(field_name)
            if field_name == TOTP_FIELD and value:
                # Accepte aussi bien un secret base32 qu'une URI otpauth://.
                raw = str(value).strip()
                parsed = totp_mod.parse_uri(raw) if raw.startswith("otpauth://") else None
                if parsed:
                    value = parsed["secret"]
                    self._db.execute(
                        "UPDATE vault_credentials SET totp_digits=?, totp_period=?, totp_algorithm=? WHERE id=?",
                        (parsed["digits"], parsed["period"], parsed["algorithm"], cid))
                else:
                    value = totp_mod.normalize_secret(raw)
                totp_mod.generate(value)            # échoue ici si le secret est inexploitable
            self._secrets.set(holder, field_name, "" if value is None else str(value))
        if "username" in payload:
            self._db.execute("UPDATE vault_credentials SET username_preview=? WHERE id=?",
                             (str(payload.get("username") or "")[:64], cid))
        has_totp = 1 if self._secrets.has(holder, TOTP_FIELD) else 0
        self._db.execute("UPDATE vault_credentials SET has_totp=? WHERE id=?", (has_totp, cid))

    def delete(self, credential_id: str, *, user: str = "jerome") -> bool:
        """Suppression définitive : fiche, secrets chiffrés et habilitations."""
        row = self._row(credential_id)
        if not row:
            return False
        self._secrets.delete_all(f"cred:{credential_id}")
        self._db.execute("DELETE FROM vault_grants WHERE credential_id=?", (credential_id,))
        self._db.execute("DELETE FROM vault_credentials WHERE id=?", (credential_id,))
        self._record("vault.credential.deleted", credential_id=credential_id,
                     detail={"service": row["service_name"], "user": user})
        return True

    def revoke(self, credential_id: str, *, reason: str = "", user: str = "jerome") -> bool:
        """Révocation : la fiche reste visible (traçabilité) mais ne délivre plus rien.

        On révoque aussi toutes les habilitations : un agent ne doit pas pouvoir
        continuer à s'en servir parce que sa ligne de grant, elle, est encore là.
        """
        if not self._row(credential_id):
            return False
        now = _now()
        self._db.execute("UPDATE vault_credentials SET status=?, updated_at=? WHERE id=?",
                         (STATUS_REVOKED, now, credential_id))
        self._db.execute("UPDATE vault_grants SET revoked_at=?, updated_at=? WHERE credential_id=? AND revoked_at IS NULL",
                         (now, now, credential_id))
        self._record("vault.credential.revoked", credential_id=credential_id,
                     detail={"reason": reason, "user": user})
        self._emit("vault.credential.revoked", {"id": credential_id, "reason": reason})
        return True

    def rotate(self, credential_id: str, new_secrets: dict[str, Any], *, user: str = "jerome") -> dict[str, Any]:
        """Remplace les valeurs sensibles et réarme le compteur de rotation."""
        row = self._row(credential_id)
        if not row:
            raise ValueError("Fiche introuvable.")
        self._store_secrets(credential_id, new_secrets)
        now = _now()
        # Une rotation doit rendre la fiche RÉELLEMENT utilisable : conserver une
        # échéance déjà passée laisserait un statut « active » que `_check`
        # refuserait aussitôt. On la repousse (rotation planifiée) ou on la lève.
        days = int(row["rotation_days"] or 0)
        expires = row["expires_at"]
        if days > 0:
            expires = now + days * 86400
        elif expires and float(expires) <= now:
            expires = None
        self._db.execute(
            "UPDATE vault_credentials SET last_rotated_at=?, updated_at=?, status=?, expires_at=? WHERE id=?",
            (now, now, STATUS_ACTIVE, expires, credential_id))
        self._record("vault.credential.rotated", credential_id=credential_id,
                     detail={"champs": sorted(k for k in new_secrets if k in ALL_SECRET_FIELDS), "user": user})
        self._emit("vault.credential.rotated", {"id": credential_id})
        return self.get(credential_id) or {}

    # -- expiration / rotation -------------------------------------------
    def is_expired(self, row) -> bool:
        expires = row["expires_at"]
        return bool(expires) and float(expires) <= _now()

    def rotation_due(self, row) -> bool:
        days = int(row["rotation_days"] or 0)
        if days <= 0:
            return False
        last = float(row["last_rotated_at"] or row["created_at"] or 0)
        return (_now() - last) >= days * 86400

    def sweep_expired(self) -> list[str]:
        """Passe les fiches échues en `expired`. À appeler périodiquement.

        Marquer plutôt que supprimer : l'utilisateur doit voir POURQUOI un agent
        s'est arrêté, et pouvoir faire une rotation au lieu de tout ressaisir.
        """
        touched: list[str] = []
        for row in self._db.query("SELECT * FROM vault_credentials WHERE status=?", (STATUS_ACTIVE,)):
            if self.is_expired(row):
                self._db.execute("UPDATE vault_credentials SET status=?, updated_at=? WHERE id=?",
                                 (STATUS_EXPIRED, _now(), row["id"]))
                self._record("vault.credential.expired", status="warn", credential_id=row["id"],
                             detail={"service": row["service_name"]})
                self._emit("vault.credential.expired", {"id": row["id"], "service_name": row["service_name"]})
                touched.append(row["id"])
        return touched

    # -- habilitations ----------------------------------------------------
    def grant(self, credential_id: str, agent_id: str, *, scopes: list[str] | None = None,
              max_uses: int = 0, expires_at: float | None = None, ttl_seconds: int = 0,
              task_pattern: str = "", reason: str = "", user: str = "jerome") -> dict[str, Any]:
        """Autorise UN agent sur UNE fiche, avec une portée explicite."""
        if not self._row(credential_id):
            raise ValueError("Fiche introuvable.")
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            raise ValueError("Un agent doit être nommé : pas d'habilitation globale.")
        wanted = [s for s in (scopes or ["login"]) if s in SCOPES]
        if not wanted:
            raise ValueError(f"Portées valides : {', '.join(SCOPES)}")
        if ttl_seconds and not expires_at:
            expires_at = _now() + int(ttl_seconds)
        now = _now()
        self._db.execute(
            "INSERT INTO vault_grants(id, credential_id, agent_id, scopes, task_pattern, max_uses, used, "
            "expires_at, revoked_at, reason, created_by, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,0,?,NULL,?,?,?,?) "
            "ON CONFLICT(credential_id, agent_id) DO UPDATE SET scopes=excluded.scopes, "
            "task_pattern=excluded.task_pattern, max_uses=excluded.max_uses, used=0, "
            "expires_at=excluded.expires_at, revoked_at=NULL, reason=excluded.reason, updated_at=excluded.updated_at",
            (new_id("grant"), credential_id, agent_id, json.dumps(wanted), task_pattern,
             int(max_uses or 0), expires_at, reason, user, now, now))
        self._record("vault.grant.created", credential_id=credential_id,
                     detail={"agent": agent_id, "scopes": wanted, "max_uses": max_uses, "user": user})
        self._emit("vault.grant.created", {"credential_id": credential_id, "agent_id": agent_id})
        return self.grants_for(credential_id)

    def revoke_grant(self, credential_id: str, agent_id: str, *, user: str = "jerome") -> bool:
        cur = self._db.execute(
            "UPDATE vault_grants SET revoked_at=?, updated_at=? WHERE credential_id=? AND agent_id=? AND revoked_at IS NULL",
            (_now(), _now(), credential_id, agent_id))
        if cur.rowcount:
            self._record("vault.grant.revoked", credential_id=credential_id,
                         detail={"agent": agent_id, "user": user})
        return bool(cur.rowcount)

    def grants_for(self, credential_id: str) -> list[dict[str, Any]]:
        rows = self._db.query(
            "SELECT * FROM vault_grants WHERE credential_id=? ORDER BY agent_id", (credential_id,))
        out = []
        for r in rows:
            g = {k: r[k] for k in r.keys()}
            g["scopes"] = _loads(g.get("scopes"), [])
            g["active"] = g["revoked_at"] is None and not (g["expires_at"] and float(g["expires_at"]) <= _now())
            out.append(g)
        return out

    # -- délivrance -------------------------------------------------------
    def _check(self, credential_id: str, ctx: AgentContext, scope: str):
        """Toutes les conditions du moindre privilège, dans l'ordre du plus grave."""
        row = self._row(credential_id)
        if not row:
            raise VaultDenied(f"Aucune fiche « {credential_id} » dans le coffre.")
        if row["status"] == STATUS_REVOKED:
            raise VaultDenied(f"Les identifiants « {row['service_name']} » ont été révoqués.")
        if self.is_expired(row):
            raise VaultDenied(f"Les identifiants « {row['service_name']} » ont expiré : une rotation est nécessaire.")

        grant = self._db.one(
            "SELECT * FROM vault_grants WHERE credential_id=? AND agent_id=?", (credential_id, ctx.agent_id))
        if not grant:
            raise VaultDenied(
                f"L'agent « {ctx.agent_id} » n'est pas habilité pour « {row['service_name']} ». "
                "Accorde-lui explicitement l'accès depuis le coffre.")
        if grant["revoked_at"] is not None:
            raise VaultDenied(f"Habilitation révoquée pour l'agent « {ctx.agent_id} ».")
        if grant["expires_at"] and float(grant["expires_at"]) <= _now():
            raise VaultDenied(f"Habilitation expirée pour l'agent « {ctx.agent_id} ».")
        if scope not in _loads(grant["scopes"], []):
            raise VaultDenied(
                f"Portée « {scope} » non accordée à « {ctx.agent_id} » sur « {row['service_name']} ».")
        max_uses = int(grant["max_uses"] or 0)
        if max_uses and int(grant["used"] or 0) >= max_uses:
            raise VaultDenied(
                f"Quota d'utilisation épuisé ({max_uses}) pour « {ctx.agent_id} » sur « {row['service_name']} ».")
        pattern = str(grant["task_pattern"] or "").strip()
        if pattern and pattern not in f"{ctx.task_id} {ctx.purpose}":
            raise VaultDenied(f"Habilitation restreinte au contexte « {pattern} ».")

        # Injection web : la cible doit être explicitement autorisée. C'est le
        # garde-fou contre une page de phishing atteinte par un lien piégé.
        if ctx.target_url:
            allowed = _loads(row["allowed_domains"], [])
            if not any(domain_matches(ctx.target_url, d) for d in allowed):
                raise VaultDenied(
                    f"Domaine non autorisé pour « {row['service_name']} » : "
                    f"{host_of(ctx.target_url) or ctx.target_url}. Autorisés : {', '.join(allowed) or 'aucun'}.")
        return row, grant

    def authorize(self, credential_id: str, ctx: AgentContext, scope: str = "login") -> bool:
        """Vérification sèche, sans déchiffrer : utile pour un pré-contrôle d'UI."""
        try:
            self._check(credential_id, ctx, scope)
            return True
        except VaultDenied:
            return False

    def get_credentials(self, credential_id: str, agent_context: AgentContext,
                        *, scope: str = "login") -> CredentialBundle:
        """Point d'entrée unique des outils d'automatisation (Playwright, HTTP…).

        Refuse par défaut, déchiffre au dernier moment, journalise toujours.
        Le résultat ne doit jamais être renvoyé à un LLM ni sérialisé en JSON.
        """
        started = time.perf_counter()
        try:
            row, grant = self._check(credential_id, agent_context, scope)
        except VaultDenied as denied:
            self._record("vault.access.denied", status="denied", ctx=agent_context,
                         credential_id=credential_id,
                         detail={"raison": str(denied), "scope": scope, "url": agent_context.target_url})
            self._emit("vault.access.denied", {"credential_id": credential_id,
                                               "agent_id": agent_context.agent_id, "reason": str(denied)})
            raise

        holder = f"cred:{credential_id}"
        wanted = SECRET_FIELDS.get(row["auth_type"], ())
        secrets = {f: self._secrets.get(holder, f) for f in wanted}
        secrets = {k: v for k, v in secrets.items() if v}

        now = _now()
        self._db.execute(
            "UPDATE vault_credentials SET last_used_at=?, use_count=use_count+1, updated_at=? WHERE id=?",
            (now, now, credential_id))
        self._db.execute("UPDATE vault_grants SET used=used+1, updated_at=? WHERE id=?", (now, grant["id"]))

        self._record("vault.access.granted", ctx=agent_context, credential_id=credential_id,
                     detail={"service": row["service_name"], "scope": scope,
                             "champs": sorted(secrets), "url": agent_context.target_url})
        self._emit("vault.access.granted", {"credential_id": credential_id, "service_name": row["service_name"],
                                            "agent_id": agent_context.agent_id})
        return CredentialBundle(
            credential_id=credential_id, service_name=row["service_name"], service_url=row["service_url"],
            auth_type=row["auth_type"], secrets=secrets, has_totp=bool(row["has_totp"]),
        )

    def totp_code(self, credential_id: str, agent_context: AgentContext) -> dict[str, Any]:
        """Code 2FA à 6 chiffres pour une connexion autonome.

        Renvoie aussi `expires_in` : sous quelques secondes, l'agent a intérêt à
        attendre la fenêtre suivante plutôt qu'à se faire refuser le code.
        """
        row, _grant = self._check(credential_id, agent_context, "login")
        if not row["has_totp"]:
            raise VaultDenied(f"Aucun secret TOTP enregistré pour « {row['service_name']} ».")
        secret = self._secrets.get(f"cred:{credential_id}", TOTP_FIELD)
        if not secret:
            raise VaultDenied(f"Secret TOTP illisible pour « {row['service_name']} ».")
        code = totp_mod.generate(secret, digits=int(row["totp_digits"] or 6),
                                 period=int(row["totp_period"] or 30),
                                 algorithm=row["totp_algorithm"] or "SHA1")
        remaining = totp_mod.seconds_remaining(period=int(row["totp_period"] or 30))
        # Le code lui-même n'est PAS journalisé : seul le fait qu'on en a produit un.
        self._record("vault.totp.generated", ctx=agent_context, credential_id=credential_id,
                     detail={"service": row["service_name"]})
        return {"code": code, "expires_in": remaining, "service_name": row["service_name"]}

    # -- journal ----------------------------------------------------------
    def audit_entries(self, limit: int = 100, credential_id: str = "") -> list[dict[str, Any]]:
        """Journal filtré sur les actions du coffre (table `audit_log` partagée)."""
        if credential_id:
            rows = self._db.query(
                "SELECT * FROM audit_log WHERE action LIKE 'vault.%' AND connector_id=? ORDER BY ts DESC LIMIT ?",
                (credential_id, limit))
        else:
            rows = self._db.query(
                "SELECT * FROM audit_log WHERE action LIKE 'vault.%' ORDER BY ts DESC LIMIT ?", (limit,))
        return [{k: r[k] for k in r.keys()} for r in rows]

    def stats(self) -> dict[str, Any]:
        total = int(self._db.scalar("SELECT COUNT(*) FROM vault_credentials") or 0)
        active = int(self._db.scalar("SELECT COUNT(*) FROM vault_credentials WHERE status='active'") or 0)
        grants = int(self._db.scalar("SELECT COUNT(*) FROM vault_grants WHERE revoked_at IS NULL") or 0)
        denied = int(self._db.scalar(
            "SELECT COUNT(*) FROM audit_log WHERE action='vault.access.denied' AND ts > ?",
            (_now() - 7 * 86400,)) or 0)
        return {"total": total, "active": active, "grants": grants, "denied_7d": denied,
                "backend": getattr(self._secrets, "backend", "")}
