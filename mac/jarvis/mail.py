"""Couche mail structurée + tri déterministe de la boîte de réception.

Pourquoi ce module existe
-------------------------
`email.read` (tools/web_tools.py) renvoie une chaîne déjà formatée
(« - expéditeur — objet »). C'est lisible par un humain, mais il n'y a rien à
classer là-dedans : ni identifiant de message, ni corps, ni pièces jointes.
Ce module apporte la couche structurée qui manquait, et le tri qui s'appuie
dessus.

Deux fournisseurs derrière la même interface
--------------------------------------------
- `ImapMailProvider` : IMAP réel, via un connecteur `email` unifié ou l'ancien
  connecteur `imap`. Le mot de passe ne transite jamais en clair : il sort du
  vault au dernier moment.
- `MockMailProvider` : messages lus dans un fichier JSON local. Ce n'est pas
  un gadget de démonstration — sans lui, le tri n'est ni testable hors-ligne
  ni reproductible, et on ne pourrait rien prouver de son comportement.

Le tri est DÉTERMINISTE
-----------------------
Aucun appel au modèle : des règles explicites, ordonnées, dont chacune porte
le motif de sa décision. Ce motif voyage avec la carte jusqu'à l'écran, pour
qu'on puisse toujours répondre à « pourquoi ce message est-il là ? ». Un
classement par LLM coûterait un appel par message, ne serait pas reproductible
et rendrait les tests non déterministes.

Le tri ne modifie RIEN dans la boîte : lire et classer est en lecture seule.
Répondre, transférer ou archiver sont des actions distinctes, qui passeront
par le garde-fou de confirmation.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR
from .mail_providers import mail_settings

# Les cinq catégories du tri. L'ordre de ce tuple est celui des colonnes.
REPLY = "reply"
FORWARD = "forward"
INVOICE = "invoice"
QUOTE = "quote"
ARCHIVE = "archive"

CATEGORIES = (REPLY, FORWARD, INVOICE, QUOTE, ARCHIVE)
CATEGORY_LABELS = {
    REPLY: "À répondre",
    FORWARD: "À transférer",
    INVOICE: "Factures",
    QUOTE: "Devis",
    ARCHIVE: "Archives",
}

MOCK_FILE = Path(DATA_DIR) / "mail_mock.json"


# ---------------------------------------------------------------------------
# Message structuré
# ---------------------------------------------------------------------------
@dataclass
class MailMessage:
    id: str
    sender: str = ""              # « Pierre Martin <pierre@atelier.fr> »
    sender_email: str = ""
    subject: str = ""
    date: str = ""
    body: str = ""
    attachments: list[str] = field(default_factory=list)
    unread: bool = True

    @property
    def snippet(self) -> str:
        flat = re.sub(r"\s+", " ", self.body or "").strip()
        return flat[:180]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "sender": self.sender, "sender_email": self.sender_email,
            "subject": self.subject, "date": self.date, "snippet": self.snippet,
            "attachments": list(self.attachments), "unread": self.unread,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MailMessage":
        sender = str(raw.get("sender") or raw.get("from") or "")
        return cls(
            id=str(raw.get("id") or ""),
            sender=sender,
            sender_email=str(raw.get("sender_email") or _extract_email(sender)),
            subject=str(raw.get("subject") or ""),
            date=str(raw.get("date") or ""),
            body=str(raw.get("body") or ""),
            attachments=[str(a) for a in (raw.get("attachments") or [])],
            unread=bool(raw.get("unread", True)),
        )


def _extract_email(sender: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", sender or "")
    return match.group(0).lower() if match else ""


# ---------------------------------------------------------------------------
# Fournisseurs
# ---------------------------------------------------------------------------
class MockMailProvider:
    """Boîte de réception locale (JSON). Aucune connexion réseau."""

    name = "mock"

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path or MOCK_FILE)

    def available(self) -> bool:
        return self.path.is_file()

    def fetch(self, limit: int = 20, unread_only: bool = False) -> list[MailMessage]:
        if not self.path.is_file():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        items = raw.get("messages", raw) if isinstance(raw, dict) else raw
        messages = [MailMessage.from_dict(m) for m in items if isinstance(m, dict)]
        if unread_only:
            messages = [m for m in messages if m.unread]
        return messages[:max(1, limit)]


class ImapMailProvider:
    """Boîte de réception réelle, via un connecteur `imap` ou `email` unifié.

    Le mot de passe ne transite jamais en clair : il sort du vault au dernier
    moment, dans `fetch`.
    """

    name = "imap"

    def __init__(self, core: Any, connector: dict[str, Any]) -> None:
        self.core = core
        self.connector = connector

    @property
    def _config(self) -> dict[str, Any]:
        return self.connector.get("config") or {}

    @property
    def _settings(self) -> dict[str, Any]:
        return mail_settings(self._config, self.connector.get("type") or "")

    def available(self) -> bool:
        return bool(self._settings.get("imap_host"))

    def fetch(self, limit: int = 20, unread_only: bool = False) -> list[MailMessage]:
        password = self.core.vault.get(self.connector["id"], "password", "")
        return read_mail(self.connector.get("type") or "", self._config, password,
                         limit=limit, unread_only=unread_only)


def read_mail(
    connector_type: str, config: dict[str, Any], password: str,
    *, limit: int = 20, unread_only: bool = False,
) -> list[MailMessage]:
    """Lit une boîte IMAP (connecteur `imap` ou `email`) et renvoie des
    messages structurés. Lecture seule : ``BODY.PEEK`` ne marque rien comme lu."""
    import email as email_lib
    import imaplib
    from email.header import decode_header, make_header

    settings = mail_settings(config or {}, connector_type or "")
    host, port = settings["imap_host"], int(settings["imap_port"])
    if not host:
        raise ValueError("Aucun serveur IMAP configuré.")
    box = (imaplib.IMAP4_SSL(host, port) if settings["imap_ssl"]
           else imaplib.IMAP4(host, port))
    try:
        box.login(settings["username"], password)
        box.select("INBOX")
        typ, data = box.search(None, "UNSEEN" if unread_only else "ALL")
        ids = (data[0].split() if data and data[0] else [])[-max(1, limit):]
        out: list[MailMessage] = []
        for mid in reversed(ids):
            # BODY.PEEK : lire ne doit pas marquer le message comme lu.
            typ, raw = box.fetch(mid, "(BODY.PEEK[])")
            if typ != "OK" or not raw or not raw[0]:
                continue
            parsed = email_lib.message_from_bytes(raw[0][1])

            def header(name: str) -> str:
                value = parsed.get(name, "")
                try:
                    return str(make_header(decode_header(value)))
                except Exception:
                    return str(value)

            sender = header("From")
            out.append(MailMessage(
                id=mid.decode() if isinstance(mid, bytes) else str(mid),
                sender=sender, sender_email=_extract_email(sender),
                subject=header("Subject"), date=header("Date"),
                body=_body_text(parsed), attachments=_attachment_names(parsed),
                unread=unread_only,
            ))
        return out
    finally:
        try:
            box.close()
        except Exception:
            pass
        try:
            box.logout()
        except Exception:
            pass


def send_mail(
    connector_type: str, config: dict[str, Any], password: str,
    *, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
) -> None:
    """Envoie un email via SMTP (connecteur `email` ou `smtp`)."""
    import smtplib
    from email.message import EmailMessage
    from email.utils import formataddr

    settings = mail_settings(config or {}, connector_type or "")
    host, port = settings["smtp_host"], int(settings["smtp_port"])
    if not host:
        raise ValueError("Aucun serveur SMTP configuré.")
    sender = formataddr((settings["from_name"], settings["from_address"])) or settings["username"]
    if not sender:
        raise ValueError("Adresse d'expéditeur manquante.")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg["Subject"] = subject
    msg.set_content(body)

    # Port 465 = SSL implicite (SMTP_SSL), 587 = STARTTLS classique.
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=25)
    else:
        server = smtplib.SMTP(host, port, timeout=25)
    try:
        if port != 465 and settings["smtp_tls"]:
            server.starttls()
        if settings["username"]:
            server.login(settings["username"], password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


def test_connection(
    connector_type: str, config: dict[str, Any], password: str,
) -> tuple[bool, str]:
    """Tente une vraie connexion IMAP et/ou SMTP, sans lire ni envoyer.

    Renvoie ``(ok, détail)``. Utilisé par le « Tester » des connecteurs :
    un socket ouvert ne suffit pas, on veut prouver que l'authentification passe.
    """
    settings = mail_settings(config or {}, connector_type or "")
    results: list[str] = []
    ok = True

    if settings["imap_host"]:
        try:
            import imaplib

            box = (imaplib.IMAP4_SSL(settings["imap_host"], int(settings["imap_port"]))
                   if settings["imap_ssl"]
                   else imaplib.IMAP4(settings["imap_host"], int(settings["imap_port"])))
            try:
                box.login(settings["username"], password)
                results.append(f"IMAP ok ({settings['imap_host']})")
            finally:
                try:
                    box.logout()
                except Exception:
                    pass
        except Exception as exc:
            ok = False
            results.append(f"IMAP: {exc}")

    if settings["smtp_host"]:
        smtp_port = int(settings["smtp_port"])
        try:
            import smtplib

            # Port 465 = SSL implicite (SMTP_SSL), 587 = STARTTLS classique.
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(settings["smtp_host"], smtp_port, timeout=20)
            else:
                server = smtplib.SMTP(settings["smtp_host"], smtp_port, timeout=20)
            try:
                server.ehlo()
                if smtp_port != 465 and settings["smtp_tls"]:
                    server.starttls()
                    server.ehlo()
                if settings["username"]:
                    server.login(settings["username"], password)
                results.append(f"SMTP ok ({settings['smtp_host']}:{smtp_port})")
            finally:
                try:
                    server.quit()
                except Exception:
                    pass
        except Exception as exc:
            ok = False
            results.append(f"SMTP: {exc}")

    if not results:
        return False, "Aucun serveur SMTP/IMAP configuré."
    return ok, "; ".join(results)


def _body_text(parsed) -> str:
    """Texte lisible du message. On ignore les pièces jointes binaires."""
    if not parsed.is_multipart():
        try:
            return parsed.get_payload(decode=True).decode("utf-8", "replace")
        except Exception:
            return str(parsed.get_payload())[:4000]
    for part in parsed.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            try:
                return part.get_payload(decode=True).decode("utf-8", "replace")
            except Exception:
                continue
    return ""


def _attachment_names(parsed) -> list[str]:
    names = []
    if parsed.is_multipart():
        for part in parsed.walk():
            name = part.get_filename()
            if name:
                names.append(str(name))
    return names


# ---------------------------------------------------------------------------
# Tri déterministe
# ---------------------------------------------------------------------------
# Chaque motif est commenté par ce qu'il attrape réellement. Les accents sont
# optionnels partout : les objets de mails sont écrits à la va-vite.
_RE_INVOICE = re.compile(
    r"\b(factur\w*|invoice\w*|r[eé]glement|paiement|[eé]ch[eé]ance|avoir|"
    r"relance de paiement|montant d[uû])\b", re.I)
_RE_QUOTE = re.compile(
    r"\b(devis|quotation|proposition commerciale|chiffrage|estimation|"
    r"offre de prix|demande de tarif)\b", re.I)
_RE_FORWARD = re.compile(
    r"\b(transf[eé]r\w*|transmettre|pour information|pour info|"
    r"[aà] qui de droit|service concern[eé]|fwd)\b|^\s*(tr|fwd)\s*:", re.I)
_RE_AUTOMATED = re.compile(
    r"(no[-_.]?reply|do[-_.]?not[-_.]?reply|mailer-daemon|postmaster|"
    r"notification|bounce)", re.I)
_RE_NEWSLETTER = re.compile(
    r"\b(newsletter|d[eé]sabonn\w*|unsubscribe|promotion|offre sp[eé]ciale|"
    r"black friday|soldes)\b", re.I)
_RE_ASK = re.compile(
    r"\b(pouvez-vous|pourriez-vous|peux-tu|peux tu|merci de bien vouloir|"
    r"serait-il possible|j'aimerais|je souhaite|pouvons-nous|"
    r"quand est-ce|comment faire|est-ce que vous)\b", re.I)

_RE_ATT_INVOICE = re.compile(r"(factur|invoice)", re.I)
_RE_ATT_QUOTE = re.compile(r"(devis|quote)", re.I)


@dataclass
class Classification:
    category: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "category_label": CATEGORY_LABELS[self.category],
                "reason": self.reason}


def classify(message: MailMessage) -> Classification:
    """Range un message dans une des cinq catégories, avec son motif.

    L'ordre des règles est significatif : on va du signal le plus spécifique
    (une pièce jointe nommée « facture ») au plus général (rien à faire).
    """
    subject = message.subject or ""
    body = message.body or ""
    haystack = f"{subject}\n{body}"

    # 1. Pièces jointes : le signal le plus fiable, il ne dépend pas du style.
    for name in message.attachments:
        if _RE_ATT_INVOICE.search(name):
            return Classification(INVOICE, f"Pièce jointe « {name} »")
        if _RE_ATT_QUOTE.search(name):
            return Classification(QUOTE, f"Pièce jointe « {name} »")

    # 2. Facture / devis annoncés dans l'objet : intention explicite.
    if _RE_INVOICE.search(subject):
        return Classification(INVOICE, "Objet mentionnant une facture ou un paiement")
    if _RE_QUOTE.search(subject):
        return Classification(QUOTE, "Objet mentionnant un devis")

    # 3. Demande de transfert explicite.
    if _RE_FORWARD.search(subject) or _RE_FORWARD.search(body):
        return Classification(FORWARD, "Demande de transfert ou de mise en relation")

    # 4. Personne n'attend de réponse. La newsletter est testée avant le
    #    générique « expéditeur automatique » pour que le motif affiché soit
    #    le vrai : une carte doit toujours dire pourquoi elle est là.
    sender_blob = f"{message.sender} {message.sender_email}"
    if _RE_NEWSLETTER.search(sender_blob) or _RE_NEWSLETTER.search(haystack):
        return Classification(ARCHIVE, "Newsletter ou message promotionnel")
    if _RE_AUTOMATED.search(sender_blob):
        return Classification(ARCHIVE, "Expéditeur automatique (no-reply / notification)")

    # 5. Facture / devis évoqués dans le corps seulement (signal plus faible,
    #    donc examiné après les expéditeurs automatiques).
    if _RE_INVOICE.search(body):
        return Classification(INVOICE, "Corps du message mentionnant une facture")
    if _RE_QUOTE.search(body):
        return Classification(QUOTE, "Corps du message mentionnant un devis")

    # 6. Une vraie question adressée à l'utilisateur.
    if _RE_ASK.search(haystack):
        return Classification(REPLY, "Demande directe adressée à toi")
    if "?" in subject:
        return Classification(REPLY, "Question posée dans l'objet")
    if "?" in body:
        return Classification(REPLY, "Question posée dans le message")

    # 7. Rien d'actionnable détecté. On le dit, on ne l'invente pas.
    return Classification(ARCHIVE, "Aucun signal d'action détecté")


# ---------------------------------------------------------------------------
# Processeur : lecture + tri + diffusion temps réel
# ---------------------------------------------------------------------------
class MailProcessor:
    """Lit la boîte, classe au fil de l'eau et publie chaque décision."""

    def __init__(self, core: Any) -> None:
        self.core = core

    def provider(self, connector_id: str = "", use_mock: bool | None = None):
        """Choisit le fournisseur. Le mock n'est utilisé que s'il est demandé
        explicitement, ou si aucun connecteur IMAP/email n'est configuré ET que
        le fichier de mock existe — jamais en remplacement silencieux d'une vraie
        boîte qui répondrait mal."""
        if use_mock is True:
            return MockMailProvider()
        connector = None
        try:
            if connector_id:
                connector = self.core.connectors.raw(connector_id)
            else:
                for ctype in ("email", "imap"):
                    active = self.core.connectors.active(ctype)
                    if len(active) == 1:
                        connector = self.core.connectors.raw(active[0]["id"])
                        break
        except Exception:
            connector = None
        if connector and mail_settings(
            connector.get("config") or {}, connector.get("type") or ""
        ).get("imap_host"):
            return ImapMailProvider(self.core, connector)
        if use_mock is False:
            return None
        mock = MockMailProvider()
        return mock if mock.available() else None

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        try:
            self.core.events.emit(kind, payload)
        except Exception:
            pass

    def process(self, *, limit: int = 20, unread_only: bool = False,
                connector_id: str = "", use_mock: bool | None = None,
                task_id: str = "") -> dict[str, Any]:
        provider = self.provider(connector_id, use_mock)
        if provider is None:
            return {"ok": False,
                    "error": ("Aucune boîte mail disponible : configure un connecteur IMAP "
                              "(Settings → Connectors) ou fournis un fichier de mock.")}

        self._emit("mail.inbox.started", {"source": provider.name, "limit": limit,
                                          "unread_only": unread_only, "task_id": task_id})
        try:
            messages = provider.fetch(limit=limit, unread_only=unread_only)
        except Exception as exc:
            self._emit("mail.inbox.failed", {"source": provider.name, "error": str(exc)[:300]})
            return {"ok": False, "error": f"Lecture de la boîte impossible : {exc}"[:300]}

        buckets: dict[str, list[dict[str, Any]]] = {c: [] for c in CATEGORIES}
        cards: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            verdict = classify(message)
            card = {**message.to_dict(), **verdict.to_dict(), "source": provider.name}
            buckets[verdict.category].append(card)
            cards.append(card)
            # Diffusion au fil du tri : le Kanban se remplit en direct, il
            # n'attend pas la fin du traitement.
            self._emit("mail.message.classified", {"index": index, "total": len(messages),
                                                   "task_id": task_id, **card})

        counts = {c: len(buckets[c]) for c in CATEGORIES}
        self._emit("mail.inbox.completed", {"source": provider.name, "total": len(messages),
                                            "counts": counts, "task_id": task_id})
        return {"ok": True, "source": provider.name, "total": len(messages),
                "counts": counts, "buckets": buckets, "cards": cards}


def summarize(result: dict[str, Any]) -> str:
    """Résumé court, destiné à la réponse de l'outil (et donc au modèle)."""
    if not result.get("ok"):
        return str(result.get("error") or "Tri impossible.")
    counts = result.get("counts") or {}
    total = result.get("total") or 0
    if not total:
        return "Aucun message à trier."
    detail = ", ".join(f"{CATEGORY_LABELS[c]} : {counts.get(c, 0)}"
                       for c in CATEGORIES if counts.get(c))
    return f"{total} message(s) triés — {detail}."
