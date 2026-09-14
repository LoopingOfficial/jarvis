"""Détection déterministe des demandes d'action liées à un connecteur.

Un « connecteur » décrit une cible (host, credentials… dans le vault).
Un « outil » est une action que le modèle peut demander. Ce module fait le
pont : quand l'utilisateur formule une demande sans ambiguïté, JARVIS peut
déclencher l'outil réel sans dépendre de la bonne volonté du modèle —
et ne JAMAIS prétendre avoir exécuté une action qui n'a pas eu lieu.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReadOnlyIntent:
    """Mode d'exécution décidé avant tout appel au modèle."""
    intent: str
    read_only: bool
    write_allowed: bool
    explicit_constraint: bool = False
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "read_only": self.read_only,
            "write_allowed": self.write_allowed,
            "explicit_constraint": self.explicit_constraint,
            "target": self.target,
        }


_READ_ONLY_REQUEST = re.compile(
    r"\b(?:analyse|analyser|audit(?:e|er)?|inspect(?:e|er)?|lis|lire|lit|affiche|"
    r"montre|explique|r[ée]sum(?:e|er)|recherche|rechercher|v[ée]rifie|v[ée]rifier|"
    r"review|revue|code\s+review|security\s+review|"
    r"cherche(?:r)?\s+(?:les?\s+)?(?:faille(?:s)?|erreur(?:s)?|"
    r"vuln[ée]rabilit(?:é|e)?s?)|"
    r"v[ée]rifie(?:r)?\s+(?:la\s+)?s[ée]curit[ée]|inspect(?:e|er)?|"
    r"trouve(?:r)?\s+(?:les?\s+)?(?:faille(?:s)?|erreur(?:s)?|"
    r"vuln[ée]rabilit(?:é|e)?s?))\b",
    re.IGNORECASE,
)
_READ_ONLY_CONSTRAINT = re.compile(
    r"\b(?:ne\s+modifie\s+rien|ne\s+change\s+rien|lecture\s+seule|read\s*[- ]?only|"
    r"sans\s+modifier|analyse\s+seulement)\b", re.IGNORECASE,
)
_SECURITY_REQUEST = re.compile(
    r"\b(?:s[ée]curit[ée]|security|failles?|vuln[ée]rabilit[ée]s?|injection|"
    r"owasp|csrf|xss|sql\s*injection|secrets?|permissions?|audit)\b", re.IGNORECASE,
)
_ANALYSIS_TARGET = re.compile(
    r"(?<![\w./\\])((?:[A-Za-z]:)?[\w./\\~-]+\."
    r"(?:php|html?|css|[cm]?jsx?|tsx?|json|ya?ml|py|txt|log|sql|sh|md|inc))\b", re.IGNORECASE,
)


def has_read_only_constraint(text: str) -> bool:
    return bool(_READ_ONLY_CONSTRAINT.search(text or ""))


def detect_read_only_intent(text: str) -> ReadOnlyIntent:
    """Priority: explicit prohibition, explicit edit, audit, open/read, general."""
    from .goals import has_write_intent
    value = (text or "").strip()
    constrained = has_read_only_constraint(value)
    target_match = _ANALYSIS_TARGET.search(value)
    target = target_match.group(1) if target_match else ""
    # Do not interpret filenames such as security.php or write-test.php as verbs.
    words = _ANALYSIS_TARGET.sub(" ", value)
    editing = has_write_intent(value)
    # « analyse »/« inspecte » décrivent souvent une lecture de contenu.
    # Une demande d'inspection/analyse bornée sur un fichier est historiquement
    # traitée par l'audit read-only. Le routeur principal garde priorité aux
    # ressources (notamment Google Sheet) et à MODEL_ONLY avant cette politique.
    audit = bool(re.search(
        r"\b(?:audit\w*|failles?|vuln[ée]rabilit[ée]s?|s[ée]curit[ée]|security|"
        r"injection|owasp|csrf|xss|secrets?|permissions?)\b", words, re.I))
    reading = bool(_READ_ONLY_REQUEST.search(words) or
                   re.search(r"\b(?:comment|pourquoi|propose|sugg[èe]re)\b", words, re.I))
    audit_request = audit or constrained or (target and bool(re.search(
        r"\b(?:analyse\w*|inspect\w*|readonly|read\s*only)\b", words, re.I)))
    if audit_request and not editing:
        mode = "security_audit_readonly"
    elif editing:
        mode = "file_edit"
    elif reading:
        mode = "read_only"
    else:
        mode = "general"
    readonly = mode in {"security_audit_readonly", "read_only"}
    return ReadOnlyIntent(mode, readonly, not readonly, constrained, target)

# Commandes shell connues, mappées vers une forme exécutable sûre.
KNOWN_COMMANDS: dict[str, str] = {
    "uptime": "uptime",
    "whoami": "whoami",
    "hostname": "hostname",
    "uname -a": "uname -a",
    "uname": "uname -a",
    "pwd": "pwd",
    "df -h": "df -h",
    "df": "df -h",
    "free -h": "free -h",
    "free": "free -h",
    "ls -la": "ls -la",
    "ls -l": "ls -l",
    "ls": "ls -la",
    "date": "date +'%d/%m/%Y %H:%M'",
}

# Mots-clés qui évoquent un type de connecteur.
TYPE_PATTERNS: dict[str, re.Pattern] = {
    "ssh": re.compile(r"\b(ssh|sftp|serveurs?|servers?)\b", re.IGNORECASE),
    "ftp": re.compile(r"\bftp\b", re.IGNORECASE),
    "docker": re.compile(r"\b(docker|conteneurs?|containers?)\b", re.IGNORECASE),
    "n8n": re.compile(r"\b(n8n|workflow)\b", re.IGNORECASE),
    "github": re.compile(r"\bgithub\b", re.IGNORECASE),
    "cpanel": re.compile(r"\bcpanel\b", re.IGNORECASE),
    "whm": re.compile(r"\bwhm\b", re.IGNORECASE),
}

_EXECUTE = re.compile(
    r"\b(?:ex[ée]cute|ex[ée]cuter|lance|lancer|run|fais\s+tourner|affiche|montre|"
    r"donne(?:-moi)?|sors|je\s+veux|j'aimerais)\b",
    re.IGNORECASE,
)
_STATUS = re.compile(
    r"\b(?:[ée]tat|statut|diagnostic|sant[eé]|status|health|check)\b", re.IGNORECASE)
_LOGS = re.compile(r"\blogs?\b|journaux|journalctl", re.IGNORECASE)
_FILE_READ = re.compile(r"\b(?:lis|lire|lit|affiche|montre|cat|trouve|chercher|cherche|contenu)\b", re.IGNORECASE)
_FILE_LIST = re.compile(r"\blist\w*\b", re.IGNORECASE)
_FILE_WRITE = re.compile(r"\b(?:[ée]cris|[ée]crire|modifie|mets\s+[àa]\s+jour|change)\b",
                         re.IGNORECASE)
_FILE_UPLOAD = re.compile(r"\b(?:envoie|envoyer|upload|pousse|pousser)\b", re.IGNORECASE)
_FILE_DOWNLOAD = re.compile(r"\b(?:t[ée]l[ée]charge|r[ée]cup[èe]re|download|r[ée]cup[èe]rer)\b",
                            re.IGNORECASE)
# Verbes « inspecter » : une lecture/liste/état est demandée.
_INSPECT = re.compile(
    r"\b(?:list\w*|lister|montre|affiche|voir|vois|regarde|quels?|quelles?|donne(?:-moi)?|"
    r"r[ée]cup[èe]re|check|v[ée]rifie|ps)\b", re.IGNORECASE)
# Verbes « cycle de vie » d'un conteneur/service.
_LIFECYCLE = re.compile(
    r"\b(?:red[ée]marre|relance|r[ée]initialise|arr[eê]te|stoppe|d[ée]marre|lance|"
    r"start|stop|restart)\b", re.IGNORECASE)
# Termes qui connotent fortement une opération distante (pour déduire SSH).
_REMOTE_OPS = re.compile(
    r"\b(?:uptime|whoami|logs?|journaux|journalctl|état|etat|statut|diagnostic|santé|status|"
    r"restart|red[ée]marre|relance|fichiers?|r[ée]pertoire|dossier|contenu"
    r"|/(?:home|var|etc|opt|usr|root|srv|www|data)[a-zA-Z0-9_./-]*"
    r"|nginx|apache|mysql|service)\b", re.IGNORECASE)
# Mots génériques qui ne sont pas des noms de service.
_GENERIC_WORDS = {"serveur", "ssh", "sftp", "server", "system", "système", "mon", "ma", "le",
                  "la", "les", "de", "du", "des", "service", "web", "site", "app", "application"}
_SERVICE_STOP = tuple(sorted(_GENERIC_WORDS, key=len, reverse=True))


@dataclass
class ConnectorIntent:
    """Une demande d'action reconnue, rattachée à un outil réel.

    `executable` est vrai quand une cible unique et non ambiguë existe, donc
    que JARVIS peut déclencher l'outil sans question ni interprétation.
    """
    connector_type: str
    tool_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    connector_id: str = ""
    connectors: list[dict[str, Any]] = field(default_factory=list)
    executable: bool = False
    description: str = ""

    @property
    def envelope(self) -> dict[str, Any]:
        action = "service_status" if self.tool_id == "ssh.status" else "service_restart" if self.tool_id == "ssh.service" and self.arguments.get("action") == "restart" else "service_stop" if self.tool_id == "ssh.service" else "read_file" if self.tool_id == "ssh.read_file" else "search_file" if self.tool_id in {"ssh.run", "ssh.list"} else self.tool_id
        return {"action": action, "object_type": "file" if "file" in action or action == "search_file" else "service" if action.startswith("service_") else "", "object": self.arguments.get("path") or self.arguments.get("service") or "", "target_type": "remote_server", "connector_type": self.connector_type, "read_only": action not in {"service_restart", "service_stop"}, "destructive": action in {"service_restart", "service_stop"}, "confidence": 0.99}


def _present_type(text: str) -> list[str]:
    return [t for t, rx in TYPE_PATTERNS.items() if rx.search(text)]


def _named_connector(text: str, core, ctype: str) -> dict[str, Any] | None:
    """Id ou nom explicite dans le texte, ex. « connecteur ssh », « via ssh »."""
    for c in core.connectors.routing_candidates(ctype):
        if re.search(rf"\b{re.escape(c['id'])}\b", text, re.IGNORECASE):
            return c
    return None


def _single(core, ctype: str) -> dict[str, Any] | None:
    rows = core.connectors.routing_candidates(ctype)
    return rows[0] if len(rows) == 1 else None


def _known_commands_in(text: str) -> list[str]:
    low = text.casefold()
    matched: list[str] = []
    covered: set[str] = set()
    for phrase in sorted(KNOWN_COMMANDS, key=len, reverse=True):
        if any(phrase in c for c in covered):
            continue  # sous-phrase d'une commande déjà retenue (ex. « df » vs « df -h »)
        if phrase in low:
            matched.append(KNOWN_COMMANDS[phrase])
            covered.add(phrase)
    return matched


def retrieve_ssh_service(text: str) -> tuple[str, str] | None:
    """(service, action) si le texte demande une action sur un service distant."""
    low = text.casefold()
    # Corrections conversationnelles : « arrête de chercher en local » ne
    # contient aucune demande de gestion de service.
    if re.search(r"(?:arr[êe]te|stoppe)\s+de\s+(?:chercher|chercher|regarder)|pas\s+en\s+local|non\s+.*serveur", low):
        return None
    for word, act in (("redémarre", "restart"), ("relance", "restart"),
                      ("réinitialise", "restart"), ("arrête", "stop"),
                      ("stoppe", "stop"), ("démarre", "start"), ("lance", "start"),
                      ("status de", "status"), ("état de", "status")):
        m = re.search(rf"\b{word}\b", low)
        if not m:
            continue
        if act == "status":
            return None  # « status de Nginx » est couvert par ssh.status
        # Le service est cherché APRÈS le verbe (« redémarre le service nginx »).
        after = re.match(r"\s+(?:le\s+|la\s+)?(?:service\s+)?([a-z0-9][a-z0-9_.-]{1,40})",
                         low[m.end():])
        candidate = after.group(1) if after else ""
        if candidate and candidate not in _GENERIC_WORDS and ("service" in low or candidate in {"nginx", "apache", "apache2", "php-fpm", "mysql", "mariadb", "docker"}):
            return candidate, act
        # Ou AVANT le verbe (« le service nginx redémarre »).
        before = re.search(r"(?:le\s+|la\s+)?(?:service\s+)?([a-z0-9][a-z0-9_.-]{1,40})\s*$",
                           low[:m.start()])
        if before and before.group(1) not in _GENERIC_WORDS and ("service" in low or before.group(1) in {"nginx", "apache", "apache2", "php-fpm", "mysql", "mariadb", "docker"}):
            return before.group(1), act
        return None
    return None


def _log_service(text: str) -> str:
    """Extrait un nom de service éventuel derrière « logs », sinon renvoie ''."""
    m = re.search(
        r"\blogs?\s+(?:du\s+)?(?:service\s+)?([a-z0-9][a-z0-9_.-]{1,40})"
        r"|\bjournalctl\s+(?:-u\s+)?([a-z0-9][a-z0-9_.-]{1,40})", text, re.IGNORECASE)
    if not m:
        return ""
    name = (m.group(1) or m.group(2)).rstrip(".")
    if name in _GENERIC_WORDS:
        return ""
    return name


def _path_after(text: str, keys: str) -> str:
    """Tente d'extraire un chemin juste après un mot-clé (ex. « le fichier »)."""
    m = re.search(rf"\b(?:{keys})\s+([^\s,;:!?'\"]+(?:[ ][^\s,;:!?'\"]+)?)", text, re.IGNORECASE)
    if not m:
        return ""
    path = m.group(1).strip(" '\"")
    path = re.split(r"\s+(?:sur|via|avec|dans|et|puis|alors)\b", path)[0]
    if not path or path.casefold() in _GENERIC_WORDS:
        return ""
    return path


def _build(core, ctype: str, tool_id: str, arguments: dict[str, Any],
           connectors: list[dict[str, Any]], connector_id: str, description: str,
           executable: bool | None = None) -> ConnectorIntent:
    exe = bool(connector_id) if executable is None else executable
    return ConnectorIntent(connector_type=ctype, tool_id=tool_id, arguments=arguments,
                           connectors=connectors, connector_id=connector_id,
                           executable=exe, description=description)


def resolve_connector_intent(core, text: str) -> ConnectorIntent | None:
    """Reconnaît une demande d'action liée à un connecteur.

    Retourne None si aucune action claire n'est détectée (conversation
    normale, question, demande plurielle).
    """
    text = (text or "").strip()
    low = text.casefold()
    present = _present_type(text)

    implicit_ssh = False
    if not present and _single(core, "ssh") and _REMOTE_OPS.search(text):
        implicit_ssh = True
        present = ["ssh"]

    if not present:
        return None
    ctype = present[0] if not implicit_ssh else "ssh"
    for strong in ("github", "cpanel", "whm", "ftp"):
        if strong in present:
            ctype = strong
            break

    connectors = [c for c in core.connectors.routing_candidates(ctype)]
    # Docker est « connector_optional » : exécution locale possible sans connecteur.
    if not connectors and ctype != "docker":
        return None
    single = _single(core, ctype)
    named = _named_connector(text, core, ctype) or single or None
    cid = named["id"] if named else ""

    # --- SSH ---------------------------------------------------------------
    if ctype == "ssh":
        service = retrieve_ssh_service(text)
        if service:
            return _build(core, "ssh", "ssh.service",
                          {"service": service[0], "action": service[1]}, connectors, cid,
                          f"ssh.service {service[0]} → {service[1]}")

        if _STATUS.search(text):
            return _build(core, "ssh", "ssh.status", {}, connectors, cid, "ssh.status")

        if _LOGS.search(text):
            svc = _log_service(text)
            return _build(core, "ssh", "ssh.logs",
                          {"service": svc} if svc else {}, connectors, cid,
                          f"ssh.logs{' ' + svc if svc else ''}")

        if _FILE_LIST.search(text) and re.search(r"\bfichiers?\b|dossier|r[ée]pertoire|contenu", low):
            path = _path_after(text, "dans|de|du|sous")
            return _build(core, "ssh", "ssh.list",
                          {"path": path} if path else {}, connectors, cid, "ssh.list")

        if _FILE_READ.search(text):
            path = _path_after(text, "fichier")
            if not path:
                match_file = re.search(r"\b([\w.-]+\.(?:php|html?|css|js|json|log|txt))\b", text, re.I)
                if match_file:
                    filename = match_file.group(1)
                    if re.search(r"\b(?:trouve|chercher|cherche)\b", text, re.I):
                        return _build(core, "ssh", "ssh.run",
                                      {"command": f"find . -type f -name '{filename}' -print -quit"}, connectors, cid,
                                      f"Recherche distante de {filename}")
                    path = filename
                else:
                    return None
            return _build(core, "ssh", "ssh.read_file", {"path": path}, connectors, cid,
                          f"ssh.read_file {path}")

        if _FILE_WRITE.search(text) or _FILE_UPLOAD.search(text) or _FILE_DOWNLOAD.search(text):
            # modifications de fichiers = lecture → écriture → vérification : au modèle.
            return None

        if _EXECUTE.search(text):
            cmds = _known_commands_in(text)
            if not cmds:
                return None  # commande libre : laisser le modèle la définir
            return _build(core, "ssh", "ssh.run", {"command": "; ".join(cmds)}, connectors, cid,
                          f"ssh.run {'; '.join(cmds)}")

        return None

    # --- autres connecteurs -------------------------------------------------
    if ctype == "ftp" and (_FILE_LIST.search(text) or _STATUS.search(text)):
        path = _path_after(text, "dans|sous")
        args: dict[str, Any] = {"action": "list"}
        if path:
            args["path"] = path
        return _build(core, "ftp", "ftp.op", args, connectors, cid, "ftp.op list")

    if ctype == "docker":
        # Une simple mention de « docker » (tutoriel, question) n'est pas une action.
        if not _LIFECYCLE.search(text) and not (
                _INSPECT.search(text) and re.search(r"\b(?:ps|conteneurs?|containers?)\b", low)):
            return None
        if _LIFECYCLE.search(text):
            verb = ("restart" if re.search(r"\bred[ée]marre\b|\brelance\b|\br[ée]initialise\b", low)
                    else "stop" if re.search(r"\barr[eê]te\b|\bstoppe\b", low)
                    else "start")
            container = _path_after(text, "conteneur|container|service|docker")
            args = {"action": verb}
            if container:
                args["container"] = container
            # Un cycle de vie nécessite une cible : conteneur nommé ou connecteur.
            return _build(core, "docker", "docker.control", args, connectors, cid,
                          f"docker.control {verb} {container or '?'}",
                          executable=bool(cid or container))
        return _build(core, "docker", "docker.control", {"action": "ps"}, connectors, cid,
                      "docker.control ps", executable=bool(cid or not connectors))

    if ctype == "n8n" and (_FILE_LIST.search(text) or _STATUS.search(text)):
        return _build(core, "n8n", "n8n.workflow", {"action": "list"}, connectors, cid,
                      "n8n.workflow list")

    if ctype == "github" and (_INSPECT.search(text) or _STATUS.search(text)):
        return _build(core, "github", "github.query", {"action": "repos"}, connectors, cid,
                      "github.query repos")

    if ctype == "cpanel" and (_STATUS.search(text) or _INSPECT.search(text)):
        return _build(core, "cpanel", "cpanel.query", {"action": "summary"}, connectors, cid,
                      "cpanel.query summary")

    if ctype == "whm" and (_STATUS.search(text) or _INSPECT.search(text)):
        return _build(core, "whm", "whm.query", {"function": "loadavg"}, connectors, cid,
                      "whm.query loadavg")

    return None


def intent_summary(core, text: str) -> ConnectorIntent | None:
    return resolve_connector_intent(core, text)

# ===========================================================================
# Intention de création visuelle
# ===========================================================================
# « Crée-moi une image de… » n'est PAS une recherche web. Ce module le décide
# de façon déterministe, avant même que le modèle ne voie la demande.

_IMAGE_NOUN = (r"(?:image|images|photo|photos|illustration|illustrations|visuel|visuels|rendu|"
               r"rendus|affiche|affiches|poster|banni[eè]re|banni[eè]res|banner|miniature|"
               r"thumbnail|fond d'?[eé]cran|wallpaper|dessin|logo|mockup|packshot|artwork|"
               r"vignette|cover|jaquette|flyer)")
_CREATE_VERB = (r"(?:cr[ée]{1,2}[a-z]*|g[ée]n[eèé]r[a-z]*|fais|faire|fabrique|dessine[a-z]*|"
                r"produis|produire|con[cç]ois|concevoir|r[ée]alise[a-z]*|imagine[a-z]*|"
                r"illustre[a-z]*|compose[a-z]*)")

# 1. Verbe de création + nom d'image (« crée-moi une image », « fais une affiche »).
_IMAGE_GENERATE = re.compile(
    r"\b" + _CREATE_VERB + r"\b(?:[- ]?moi)?\s+(?:une?|des|le|la|les|un|du|mon|ma|nos?|"
    r"quelques|\d+)?\s*" + _IMAGE_NOUN + r"\b", re.IGNORECASE)
# 2. Formulation directe : « une image de X », « un visuel pour X ».
_IMAGE_GENERATE_ALT = re.compile(
    r"\b" + _IMAGE_NOUN + r"\s+(?:de|d'|du|des|pour|avec|repr[ée]sentant|montrant)\b",
    re.IGNORECASE)
# 3. Suffixe : « … en image », « … en affiche ».
_IMAGE_SUFFIX = re.compile(r"\ben\s+" + _IMAGE_NOUN + r"\b", re.IGNORECASE)

# 4. Retouche / édition d'une image existante.
_IMAGE_EDIT = re.compile(
    r"\b(?:retouche|retoucher|retouches|modifie|modifier|change|changer|corrige|corriger|"
    r"am[ée]liore|am[ée]liorer|refais|refaire|adapte|adapter)\b[^.?!]{0,60}?\b"
    r"(?:image|photo|visuel|rendu|affiche|illustration|banni[eè]re|miniature)\b", re.IGNORECASE)

_IMAGE_FOLLOWUP_EDIT = re.compile(
    r"\b(?:rends?|rendre|mets?|mettre|change|changer|remplace|remplacer)\b[^.?!]{0,70}?\b"
    r"(?:le|la|l'|ce|cet|cette|requin|shark|chat|cat|chien|dog|animal|objet|fond|couleur|bleu|rose|"
    r"rouge|espace|space|jungle|for[êe]t)\b", re.IGNORECASE)

# 5. Agrandissement.
_IMAGE_UPSCALE = re.compile(
    r"\b(?:upscale[a-z]*|agrandis|agrandir|augmente la (?:r[ée]solution|d[ée]finition)|"
    r"passe[- ]l[ae] en (?:hd|4k|haute d[ée]finition))\b", re.IGNORECASE)

_IMAGE_VARIATION = re.compile(
    r"\b(?:variante|variation|version alternative|autre version)\b[^.?!]{0,50}?"
    r"\b(?:image|photo|visuel|rendu|illustration|ça|cela|la|le)\b", re.IGNORECASE)

_IMAGE_IMPROVE = re.compile(
    r"\b(?:am[ée]liore(?:r)? automatiquement|am[ée]lioration automatique)\b[^.?!]{0,50}?"
    r"\b(?:image|photo|visuel|rendu|illustration)\b", re.IGNORECASE)

_IMAGE_SDXL = re.compile(
    r"\b(?:rends?|rendre|convertis?|convertir|am[ée]liore(?:r)?)\b[^.?!]{0,70}\b"
    r"(?:sdxl|sd xl|qualit[ée])\b", re.IGNORECASE)

_IMAGE_PREVIEW_REQUEST = re.compile(
    r"\b(?:fais|faire|cr[ée]e?|g[ée]n[èe]re?|produis|montre)[- ]?(?:moi)?\s+"
    r"(?:un|une|le|la)?\s*(?:aperçu|apercu|brouillon|preview|test rapide|version rapide)\b",
    re.IGNORECASE)

# 6. Recherche web d'images EXPLICITEMENT demandée : là, pas de génération.
_IMAGE_SEARCH = re.compile(
    r"\b(?:cherche|chercher|trouve(?:[- ]moi)?|trouver|recherche|rechercher|montre[- ]moi des|"
    r"donne[- ]moi des|des exemples? de|r[ée]f[ée]rences?|sur (?:le web|internet|google)|"
    r"google images?)\b", re.IGNORECASE)

# Formulation usuelle de JARVIS : « montre-moi un requin ». Dans ce contexte
# singulier, l'utilisateur demande un visuel à produire ; la forme plurielle
# « montre-moi des images de… » reste une recherche web ci-dessous.
_IMAGE_SHOW_VISUAL = re.compile(
    r"\b(?:montre|affiche)[- ]moi\s+(?:un|une|le|la)\s+[^.?!]{0,100}\b"
    r"(?:requin|shark|chat|cat|chien|dog|oiseau|bird|lion|tigre|tiger|éléphant|elephant|"
    r"voiture|car|robot|drone|fusée|rocket|vaisseau|space|paysage|portrait|maison|"
    r"forêt|jungle|désert|desert|océan|ocean|planète|planet)\b", re.IGNORECASE)


def detect_image_intent(text: str) -> dict[str, Any] | None:
    """Détecte une demande de CRÉATION visuelle.

    Renvoie ``{"action": "image.generate" | "image.edit" | "image.upscale",
    "subject": …, "reason": …}`` ou ``None``.

    Une demande explicite de recherche web d'images renvoie ``None`` : c'est
    bien une recherche, pas une génération.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    has_noun = bool(re.search(_IMAGE_NOUN, raw, re.IGNORECASE))
    wants_creation = bool(_IMAGE_GENERATE.search(raw))

    if _IMAGE_UPSCALE.search(raw) and (has_noun or re.search(r"\b(?:cette|la|l')\b", raw, re.I)):
        return {"action": "image.upscale", "subject": raw, "reason": "upscale demandé"}

    if _IMAGE_VARIATION.search(raw):
        return {"action": "image.variation", "subject": raw, "reason": "variante demandée"}

    if _IMAGE_IMPROVE.search(raw):
        return {"action": "image.improve", "subject": raw, "reason": "amélioration automatique demandée"}

    if _IMAGE_SDXL.search(raw):
        return {"action": "image.improve", "subject": raw, "reason": "rendu SDXL demandé"}

    if _IMAGE_EDIT.search(raw) or _IMAGE_FOLLOWUP_EDIT.search(raw):
        return {"action": "image.edit", "subject": raw, "reason": "retouche demandée"}

    # « trouve-moi des images de… » → vraie recherche web, on ne détourne pas.
    if _IMAGE_SEARCH.search(raw) and not wants_creation:
        return None

    if _IMAGE_SHOW_VISUAL.search(raw):
        return {"action": "image.generate", "subject": raw,
                "reason": "demande de visuel au singulier"}

    if _IMAGE_PREVIEW_REQUEST.search(raw):
        return {"action": "image.generate", "subject": raw,
                "reason": "demande de preview/brouillon"}

    if wants_creation:
        return {"action": "image.generate", "subject": raw, "reason": "verbe de création + image"}

    if has_noun and (_IMAGE_GENERATE_ALT.search(raw) or _IMAGE_SUFFIX.search(raw)):
        return {"action": "image.generate", "subject": raw, "reason": "formulation directe"}

    return None


# ===========================================================================
# Intention de création 3D (atelier Blender)
# ===========================================================================
# « Crée-moi une lampe futuriste en 3D », « anime ce personnage », « exporte
# en GLB » ne sont NI une recherche web NI une génération d'image. Ce module
# le décide de façon déterministe, avant que le modèle ne voie la demande.

_3D_NOUN = (r"(?:mod[eè]les?\s+3d|objets?\s+3d|sc[eè]nes?\s+3d|asset\s+3d|"
            r"maillages?|meshs?|g[ée]om[ée]tries?|3d)")
_3D_THING = (r"(?:lampe|lampadaire|table|bureau|chaise|fauteuil|[ée]tag[eè]re|"
             r"bouteille|canette|tasse|mug|vase|personnage|humano[iï]de|avatar|"
             r"robot|drone|fus[ée]e|vaisseau|immeuble|b[âa]timent|maison|arbre|"
             r"engrenage|bague|anneau|[ée]p[ée]e|panneau|enceinte|plan[eè]te|colonne|"
             r"meuble|d[ée]cor|accessoire|cube|sph[eè]re|cylindre|c[ôo]ne|tore|capsule)")
_3D_CREATE = (r"(?:cr[ée]{1,2}[a-z]*|mod[ée]lise[a-z]*|mod[ée]liser|g[ée]n[eèé]r[a-z]*|"
              r"fabrique[a-z]*|construis|construire|sculpte[a-z]*|fais|faire|"
              r"con[cç]ois|concevoir|design[a-z]*)")

# 1. Création explicitement 3D : « crée un modèle 3D », « modélise une lampe ».
_3D_GENERATE = re.compile(
    r"\b" + _3D_CREATE + r"\b(?:[- ]?moi)?[^.?!]{0,40}?\b" + _3D_NOUN + r"\b",
    re.IGNORECASE)
# 2. Objet reconnu + mention 3D quelque part dans la phrase.
_3D_GENERATE_THING = re.compile(
    r"\b" + _3D_CREATE + r"\b(?:[- ]?moi)?[^.?!]{0,40}?\b" + _3D_THING + r"\b",
    re.IGNORECASE)
_3D_MENTION = re.compile(r"\b3d\b|\bblender\b|\bglb\b|\bgltf\b|\bfbx\b", re.IGNORECASE)

# 3. Suites de conversation : elles n'ont de sens qu'avec un projet 3D ouvert.
_3D_MODIFY = re.compile(
    r"\b(?:plus fine?|plus mince|affine|amincis|plus [ée]paisse?|plus large|"
    r"plus haute?|plus grande?|plus petite?|plus courte?|plus ronde?|arrondis|"
    r"modifie|change|ajuste|retouche|adapte|rends[- ]l[ea]|fais[- ]l[ea] plus)\b",
    re.IGNORECASE)
_3D_ANIMATE = re.compile(
    r"\b(?:anime[a-z]*|animation|fais[- ](?:le|la|lui)\s+(?:marcher|courir|tourner|"
    r"pivoter|s[' ]asseoir|se retourner|lever|saluer|parler|sauter|pointer)|"
    r"walk|idle|run cycle|cycle de marche|fais[- ]l[ea] tourner|"
    r"fais[- ]lui\s+\w+|mets[- ]le en mouvement)\b", re.IGNORECASE)
_3D_EXPORT = re.compile(
    r"\b(?:exporte[a-z]*|exporter|export)\b[^.?!]{0,30}?"
    r"\b(?:glb|gltf|fbx|obj|stl|blend|3d)\b|\ben\s+glb\b|\ben\s+gltf\b|\ben\s+fbx\b",
    re.IGNORECASE)
_3D_RENDER = re.compile(
    r"\b(?:rendu|rends|render|fais un rendu|rendu r[ée]aliste|rendu final|"
    r"cycles|eevee)\b", re.IGNORECASE)
_3D_OPTIMIZE = re.compile(
    r"\b(?:optimise[a-z]*|optimiser|all[eè]ge|d[ée]cime|r[ée]duis les? (?:polygones?|"
    r"triangles?)|low ?poly)\b", re.IGNORECASE)
_3D_MATERIAL = re.compile(
    r"\b(?:mat[ée]riaux?|material|texture[a-z]*|shader|(?:ajoute|mets|applique)[^.?!]{0,25}?"
    r"(?:m[ée]tal|chrome|verre|bois|plastique|n[ée]on|[ée]missif|mat\b|brillant))\b",
    re.IGNORECASE)
_3D_RIG = re.compile(r"\b(?:rig|rigge[a-z]*|rigger|squelette|armature|os\b|bones?)\b",
                     re.IGNORECASE)
_3D_INSPECT = re.compile(
    r"\b(?:combien de (?:polygones?|triangles?|sommets?)|polycount|inspecte[a-z]*|"
    r"est[- ]ce (?:qu[e'] ?il est )?rigg[ée])\b", re.IGNORECASE)

# Une action qui suppose un modèle déjà créé.
_NEEDS_PROJECT = {"blender.modify_model", "blender.animate", "blender.export",
                  "blender.render", "blender.optimize", "blender.material",
                  "blender.rig", "blender.inspect", "blender.generate_preview"}


# ===========================================================================
# Intention de mise à jour d'avatar depuis une image de référence
# ===========================================================================
# « Modifie ton avatar selon cette image », « adapte ton visage à cette photo »
# doivent mener au pipeline avatar.avatar_reference.add → avatar.update_from_reference,
# JAMAIS à une création 3D aléatoire ni à une recherche web.
# Règle anti-faux-positif : une seule mention (« ton style » sans avatar ni
# image) ne suffit pas — sinon « adapte ton style de vie » partirait en pipeline.

_AVATAR_NOUN = re.compile(r"\bavatars?\b", re.IGNORECASE)
_AVATAR_APPEARANCE = re.compile(
    r"\b(?:ton|ta|mon|ma|le|la)\s+(?:visage|tenue|coiffure|chevelure|look|apparence|style)\b",
    re.IGNORECASE)
_AVATAR_REF_MEDIUM = re.compile(
    r"\b(?:image|photo|r[ée]f[ée]rence|visuel|rendu|fichier|mod[èe]le)\b",
    re.IGNORECASE)
_AVATAR_UPDATE = re.compile(
    r"\b(?:modifie|modifier|adapte|adapter|change|changer|transforme|transforme-?toi|"
    r"prend(?:s)?\s+le\s+style|mets[- ]?[aà][- ]?jour|r[ée]fais|refaire|refont|"
    r"utilise|inspire[- ]toi)\b",
    re.IGNORECASE)


def detect_avatar_update_intent(text: str) -> dict[str, Any] | None:
    """Détecte une demande de mise à jour d'avatar depuis une image.

    Renvoie ``{"action": "avatar.update_from_reference", "reason": …}`` ou
    ``None``. Deux déclencheurs :
      - « avatar » explicite + verbe de modification ;
      - apparence (visage/tenue/coiffure/style…) + verbe + mention d'un média
        (image, photo, référence…).
    """
    raw = (text or "").strip()
    if not raw:
        return None
    wants_update = bool(
        (_AVATAR_NOUN.search(raw) or _AVATAR_APPEARANCE.search(raw))
        and _AVATAR_UPDATE.search(raw)
        and (_AVATAR_NOUN.search(raw) or _AVATAR_REF_MEDIUM.search(raw)))
    if not wants_update:
        return None
    return {"action": "avatar.update_from_reference",
            "reason": "mise à jour d'avatar depuis une image de référence"}


# Travail direct sur l'avatar de JARVIS, SANS image de référence :
# « améliore ton visage », « ajoute des cheveux à ton avatar », « inspecte
# ton avatar ». Ces demandes portent sur le master avatar, qui existe
# toujours : elles n'exigent donc aucun projet 3D ouvert.
_AVATAR_PART = re.compile(
    r"\bavatars?\b|\b(?:ton|ta|tes|son|sa|ses)\s+"
    r"(?:visage|t[êe]te|cheveux|chevelure|coiffure|tenue|v[êe]tements?|yeux|"
    r"corps|silhouette|peau|mains?|bouche|sourcils|rig|squelette|apparence|look)\b",
    re.IGNORECASE)
_AVATAR_WORK_VERB = re.compile(
    r"\b(?:am[ée]liore[a-z]*|ajoute[a-z]*|agrandis|agrandir|r[ée]duis|r[ée]duire|"
    r"modifie[a-z]*|change[a-z]*|corrige[a-z]*|ajuste[a-z]*|retouche[a-z]*|"
    r"refais|refaire|sculpte[a-z]*|affine[a-z]*|[ée]paissis|allonge[a-z]*|"
    r"raccourcis|inspecte[a-z]*|analyse[a-z]*|v[ée]rifie[a-z]*|rig|rigge[a-z]*|"
    r"anime[a-z]*|donne[a-z]*|mets|met|rends)\b",
    re.IGNORECASE)


def detect_avatar_work_intent(text: str) -> dict[str, Any] | None:
    """Travail 3D direct sur l'avatar, sans référence image."""
    raw = (text or "").strip()
    if not raw:
        return None
    if not (_AVATAR_PART.search(raw) and _AVATAR_WORK_VERB.search(raw)):
        return None
    return {"action": "avatar.craft",
            "reason": "travail 3D direct sur l'avatar de JARVIS"}


def detect_3d_intent(text: str) -> dict[str, Any] | None:
    """Détecte une demande relevant de l'atelier 3D Blender.

    Renvoie ``{"action": "blender.…", "needs_project": bool, "reason": …}``
    ou ``None``. Les suites (« fais-la plus fine ») portent `needs_project`
    à True : l'orchestrateur ne les retient que si un projet 3D existe
    réellement dans la conversation.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    mentions_3d = bool(_3D_MENTION.search(raw))

    def build(action: str, reason: str) -> dict[str, Any]:
        return {"action": action, "reason": reason,
                "needs_project": action in _NEEDS_PROJECT}

    # Mise à jour d'avatar depuis une image : priorité absolue sur la
    # création 3D générique (une demande « adapte ton avatar à cette image »
    # ne doit JAMAIS devenir une création aléatoire).
    avatar_ref_intent = detect_avatar_update_intent(raw)
    if avatar_ref_intent:
        return build("avatar.update_from_reference",
                     avatar_ref_intent.get("reason", "mise à jour avatar"))

    # Travail direct sur l'avatar (sans image) : le master existe toujours,
    # donc aucun projet 3D ouvert n'est requis.
    avatar_work = detect_avatar_work_intent(raw)
    if avatar_work:
        return {"action": "avatar.craft", "reason": avatar_work["reason"],
                "needs_project": False}

    # Création : le signal le plus fort, il prime sur les suites.
    if _3D_GENERATE.search(raw):
        return build("blender.create_model", "verbe de création + objet 3D")
    if _3D_GENERATE_THING.search(raw):
        # « crée-moi un personnage », « modélise une table » : les objets de
        # cette liste sont concrets, une demande d'image dirait « image »,
        # « visuel » ou « affiche » (détecté avant par detect_image_intent).
        return build("blender.create_model", "création d'un objet modélisable")
    if _3D_EXPORT.search(raw):
        return build("blender.export", "export demandé")
    if _3D_RIG.search(raw):
        return build("blender.rig", "rigging demandé")
    if _3D_ANIMATE.search(raw):
        return build("blender.animate", "animation demandée")
    if _3D_OPTIMIZE.search(raw):
        return build("blender.optimize", "optimisation demandée")
    if _3D_RENDER.search(raw):
        return build("blender.render", "rendu demandé")
    if _3D_INSPECT.search(raw):
        return build("blender.inspect", "inspection du modèle")
    if _3D_MATERIAL.search(raw):
        return build("blender.material", "matériau demandé")
    if _3D_MODIFY.search(raw):
        return build("blender.modify_model", "modification d'un modèle existant")
    if mentions_3d and re.search(_3D_CREATE, raw, re.IGNORECASE):
        return build("blender.create_model", "mention 3D + verbe de création")
    return None
