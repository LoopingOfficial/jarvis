"""Classification du message utilisateur AVANT tout routage : agir ou répondre ?

JARVIS n'exécute un outil que lorsque l'utilisateur demande réellement
l'exécution d'une action. Un benchmark, une citation, un exemple théorique,
un bloc de données ou une phrase du type « n'utilise aucun outil » ne sont
JAMAIS une autorisation d'agir : le modèle répond alors en mode MODEL_ONLY,
sans accès aux outils ni aux routeurs déterministes.

Principe : « PARLER D'UNE ACTION ≠ DEMANDER L'EXÉCUTION D'UNE ACTION ».
Ce module transforme le message brut en une intention résolue (ResolvedIntent)
et une instruction exécutable (executable_instruction), débarrassée des
fragments cités, des blocs de code et des payloads de données.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .goals import has_write_intent
from .intents import detect_read_only_intent, has_read_only_constraint


# ---------------------------------------------------------------------------
# Marqueurs déterministes de contexte non exécutable
# ---------------------------------------------------------------------------

# Lignes d'en-tête d'un benchmark : `MODE: MODEL_ONLY_BENCHMARK`,
# `MODE: MODEL_ONLY`, `NO_TOOLS`, `TOOLS: OFF`…
_BENCHMARK_MODE_LINE = re.compile(
    r"^\s*(?:MODE|MODEL[_ -]?ONLY|BENCHMARK[_ -]?MODE|TOOLS?|TOOL)\s*[\-–—:]*\s*"
    r"(?:MODEL_ONLY_BENCHMARK|MODEL_?ONLY|BENCHMARK|NO_TOOLS?|OFF|NONE|AUCUN|FALSE)"
    r"(?=\s*$|\s)",
    re.IGNORECASE | re.MULTILINE,
)
_BENCHMARK_FLAGGED = re.compile(
    r"\bBENCHMARK_?MODE\b|\bNO_TOOLS\b|\bTOOLS?\s*[:=]\s*(?:OFF|NONE|FALSE)\b|\bbenchmark\b",
    re.IGNORECASE,
)
# Étiquettes qui encadrent un scénario : `Question :`, `Exemple :`, `Test :`,
# `Consigne :`, `Scénario :`, `Énoncé :`, `Réponse :`…
_LABELED_SECTION = re.compile(
    r"^\s*(?:QUESTION|QUESTIONS?|EXEMPLE|EXAMPLES?|TEST|TESTS?|BENCHMARK|BENCHMARKS|"
    r"[ÉE]NONC[ÉE]|SUJET|CONSIGNE|DIRECTIVE|SC[ÉE]NARIO|SITUATION|SIMULATION|PROMPT|"
    r"HYPOTH[ÈE]SE|INSTRUCTION|DEMANDE|T[ÂA]CHE|[DÉE]PENDANCE|DONN[ÉE]ES?|DATA|INPUT|"
    r"ANSWER|R[ÉE]PONSE|REPONSE)\s*[:\-]",
    re.IGNORECASE | re.MULTILINE,
)
# Phrase encadrante : on décrit une demande faite à JARVIS plutôt qu'on la fait.
_FRAMING = re.compile(
    r"\b(?:on\s+(?:t'|vous\s+)?a?\s*demand[ée]|l'utilisateur\s+(?:a\s+t?|t')\s*demand[ée]|"
    r"un\s+utilisateur\s+(?:demande|demanderait)|quelqu'un\s+(?:a\s+|t'a\s*)?demand[ée]|"
    r"l'utilisateur\s+(?:demande|demanderait|te\s+demande)|tu\s+(?:as|l'as)\s*(?:re[çc]u|entendu)|"
    r"tu\s+imagines|imaginons?|imagine\s*(?:qu'|qu\'un|=  )|supposons?|suppose|hypoth[èe]se|"
    r"cas\s+th[ée]orique|th[ée]oriquement|en\s+th[ée]orie|pour\s+te\s+tester|teste\s+(?:tes|mes)?\s*"
    r"connaissances|question\s+de\s+culture|par\s+exemple|si\s+(?:je\s+te|on\s+te)\s*demand|"
    r"si\s+un\s+utilisateur\s+(?:te\s+)?demand|que\s+ferais\s+tu|que\s+ferait|que\s+dirais\s+tu|"
    r"que\s+r[ée]pondrais\s+tu|comment\s+le\s+g[ée]rerais|comment\s+(?:r[ée]agirais|r[ée]pondrais)[- ]tu|"
    r"dis[- ]moi\s+ce\s+que|tu\s+ferais\s+quoi|qu'en\s+penses[- ]tu|ton\s+avis\s*(?:sur|=)|"
    r"est[- ]ce\s+que\s+tu\s+comprends|est[- ]ce\s+que\s+tu\s+as\s+compris|"
    r"je\s+me\s+demande\s+si\b|tu\s+pourrais\b|vous\s+pourriez\b|pourrais[\- ]tu\b|"
    r"si\s+(?:tu|vous|on)\s+(?:pouvais|pouviez)\s+|envisages\s+tu\b|envisagerais)\b",
    re.IGNORECASE,
)
# Verbes d'action : un fragment cité qui en contient un est un scénario rapporté.
_VERB_TOKENS = re.compile(
    r"\b(?:analyse|analyser|audit|auditer|inspecte|inspecter|ouvre|ouvrir|ouvres|lance|"
    r"lancer|d[ée]marre|arr[eê]te|stoppe|ferme|fermer|quitte|quitter|affiche|montre|lis|lire|"
    r"lit|donne|cherche|chercher|recherche|rechercher|trouve|trouver|ex[ée]cute|ex[ée]cuter|"
    r"cr[ée]e|cr[ée]er|modifie|modifier|corrige|corriger|remplace|remplacer|ajoute|ajouter|"
    r"supprime|supprimer|red[ée]marre|relance|t[ée]l[ée]charge|upload|connecte|v[ée]rifie|"
    r"scan|teste|kill|delete|write|run|deploy|restart|stop|start)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r'«[^»]+»|“[^”]+”|"[^"\n]+"|\'[^\'\n]+\'')
_RESPONSE_LABEL = re.compile(
    r"^\s*(?:R[ÉE]PONSE|REPONSE|ANSWER)\s*[:»]|^\s*»",
    re.IGNORECASE | re.MULTILINE,
)
_FENCED = re.compile(r"```[\w+-]*\s*\n.*?```|~~~\w*\s*\n.*?~~~", re.DOTALL)

# Formulations « n'utilise aucun outil » : interdiction explicite d'agir.
_DO_NOT_EXECUTE = re.compile(
    r"\b(?:n'utilise(?:s)?\s+(?:aucun|pas\s+(?:d'|de\s+)?|pas\s+les\s+)|n'utilis\w+\s+aucun)\s+"
    r"(?:outil|tools?|outillages?|fonctionnalit[a-zé]+)\b|"
    r"\b(?:n'ex[ée]cute|n'execute|n'ex[ée]cute\s+pas)\s+(?:aucun(?:e)?\s+)?(?:commande|outil|action|"
    r"script|t[âa]che|processus)\b|"
    r"\bne\s+(?:lance|d[ée]marre|d[é]clenche)\s+aucun\s+(?:outil|programme|commandes?)\b|"
    r"\bsans\s+(?:utiliser|ex[ée]cuter|appeler|invoquer|lancer)\s+(?:d'?|les\s+)?outils?\b|"
    r"\br[ée]ponds?\s+(?:moi\s+)?sans\s+(?:rien\s+)?(?:ex[ée]cuter|modifier|agir|utiliser\s+"
    r"(?:des|d')outils?)\b|"
    r"\b(?:ne\s+modifie\s+pas\s+(?:le\s+)?syst[èe]me|ne\s+modifie\s+rien\s+(?:au|sur\s+le|le)\s+"
    r"syst[èe]me|ne\s+change\s+rien\s+(?:au|sur\s+le|le)\s+syst[èe]me|"
    r"ne\s+t'ex[ée]cute\s+pas|n'ex[ée]cute\s+rien)\b",
    re.IGNORECASE,
)

# Signaux d'explication / analyse théorique : on demande de raisonner, pas d'agir.
_EXPLANATION = re.compile(
    r"\b(?:explique|expliquer|explique[sz]?[- ]moi|explique[sz]?\s+ça|explique[sz]?\s+comment|"
    r"comment\s+fonctionne|comment\s+ferais|c'est\s+quoi|qu'est\-?ce\s+que|qu'est\s+ce|"
    r"quest\s+ce\s+que|en\s+quoi\s+consiste|d[ée]cris|d[ée]crire|r[ée]sume|resume|synth[ée]tise|"
    r"tu\s+expliques\s+quoi|diff[ée]rence\s+entre|que\s+veut[- ]dire|donne[sz]?\s+une\s+"
    r"d[ée]finition|fais[sz]?\s+(?:moi\s+)?un\s+cours|apprends?\s+moi|tu\s+peux\s+m'expliquer|"
    r"pourquoi\s+ce\s+code|en\s+quoi)\b",
    re.IGNORECASE,
)

# Cible concrète présente HORS des fragments cités : vraie demande d'action.
_READ_FILE_TARGET = re.compile(
    r"\b(?:affiche|montre|donne|lis|lire|ouvre|voir|regarde)(?:\s*[- ]?moi)?\b[^.\n]{0,80}?"
    r"\b[\w./\\~-]+\.(?:php|html?|css|jsx?|tsx?|json|ya?ml|py|txt|log|sql|sh|md|inc)\b",
    re.IGNORECASE,
)
_OPEN_APP_TARGET = re.compile(
    r"^\s*(?:ouvre|ouvrir|lance|lancer|d[ée]marre|start)\s+\w+.*$",
    re.IGNORECASE,
)
_AUDIT_SIGNAL = re.compile(
    r"\b(?:analys\w*|audit\w*|inspect\w*|review|revue|v[ée]rifie(?:r)?\s+(?:la\s+)?s[ée]curit[ée]|"
    r"faill\w*|vuln[ée]rabil\w*|s[ée]curit[ée]|security|owasp)\b",
    re.IGNORECASE,
)
_AUDIT_TARGET = re.compile(
    r"\b(?:fichier|code|source|script|site|serveur|syst[èe]me|module|plugin|th[èe]me|marktplace|"
    r"marketplace|[\w.-]+\.(?:php|py|js|html?|css|json|ya?ml|txt|log)\b|sa|ce\s+fichier|index|wp-)\b",
    re.IGNORECASE,
)
_CONNECTOR_ACTION = re.compile(
    r"\b(?:ssh|ftp|sftp|docker|n8n|cpanel|whm|github|serveurs?|serveur|conteneurs?)\b"
    r"[^.\n]{0,70}?\b(?:ex[ée]cute|ex[ée]cuter|lance|lancer|affiche|donne|lis|lire|regarde|"
    r"v[ée]rifie|[é]tat|status|statut|logs?|journaux|contenu|fichiers?|red[ée]marre|relance|"
    r"arr[eê]te|d[ée]marre|queries?|query|summary|workflows?)\b",
    re.IGNORECASE,
)
_SERVICE_OPS = re.compile(
    r"\b(?:red[ée]marre|relance|arr[eê]te|stoppe|d[ée]marre|lance)\s+(?:le\s+|la\s+)?"
    r"(?:service\s+)?(?:nginx|apache|apache2|mysql|mariadb|php[a-z0-9.-]*[- ]?fpm|docker|"
    r"[a-z][a-z0-9_.-]{1,40})\b",
    re.IGNORECASE,
)
_WEB_SEARCH = re.compile(
    r"\b(?:cherche|chercher|recherche|rechercher|trouve|trouver)(?:[- ]?moi)?\b[^.\n]{0,60}?"
    r"\b(?:sur\s+(?:le\s+)?(?:web|internet|google)|sur\s+internet|sur\s+le\s+web|internet|"
    r"web|google)\b",
    re.IGNORECASE,
)
_EXPLICIT_CONSTRAINT = re.compile(
    r"\b(?:ne\s+modifie\s+rien|ne\s+change\s+rien|lecture\s+seule|read\s*[- ]?only|"
    r"sans\s+modifier|analyse\s+seulement|en\s+garde\s+le\s+fichier)\b",
    re.IGNORECASE,
)
# Verbe d'action + cible nominale : « analyse site », « édite la page »…
_VERB_AND_TARGET = re.compile(
    r"\b(?:analys\w*|audit\w*|inspect\w*|cr[ée]\w*|modifi\w*|corrig\w*|remplac\w*|ajout\w*|"
    r"supprim\w*|lis|lire|affiche|montre|ouvre|cherche\w*|trouve\w*|ex[ée]cut\w*|v[ée]rif\w*)\b"
    r"[^.\n]{0,70}?\b(?:fichiers?|pages?|scripts?|codes?|sites?|serveurs?|marketplace|index|"
    r"application|apps?|conteneurs?|modules?|th[èe]mes?|plugins?|dossiers?|r[ée]pertoires?|"
    r"documents?|workflows?|[\w./~-]+\.\w{1,8})\b",
    re.IGNORECASE,
)

# « Fais-moi un prompt … » : on demande de RÉDIGER une consigne, rien à exécuter.
_PROMPT_GENERATION = re.compile(
    r"\b(?:fais|faites|faire|[ée]cris|[ée]crire|r[ée]dige|r[ée]diger|donne|g[ée]n[èe]re|"
    r"g[ée]n[ée]rer|cr[ée]e|cr[ée]er|produis|produire)[- ]?(?:moi|nous)?\s+(?:un|une|des)?\s*"
    r"prompt(?:s)?\b|"
    r"\b(?:prompt|consigne)[^\n]{0,40}\b(?:pour\s+que|qui\s+demande|sous\s+forme\s+de)\b",
    re.IGNORECASE,
)

# Cible inline : « ce code », « le code ci-dessous », « le fichier joint »
# désignent le contenu déjà fourni, pas un fichier du système à ouvrir.
_INLINE_CODE_TARGET = re.compile(
    r"\b(?:ce\s+code|le\s+code\s+suivant|code\s+ci[- ]dessous|le\s+code\s+ci[- ]dessous|"
    r"ce\s+fichier\s+(?:joint|fourni|ci[- ]joint)|le\s+fichier\s+(?:joint|fourni|ci[- ]joint)|"
    r"cet\s+extrait|ce\s+script\s+(?:joint|fourni)|au\s+p[ée]rim[èe]tre\s+de\s+ce\s+test|"
    r"cette\s+donn|ce\s+json|ce\s+yaml|ce\s+xml|le\s+contenu\s+(?:du\s+)?(?:fichier\s+)?"
    r"ci[- ]dessous)\b",
    re.IGNORECASE,
)


@dataclass
class MessageSegments:
    """Le message décomposé en zones exécutables et non exécutables."""
    raw: str = ""
    code_blocks: list[str] = field(default_factory=list)
    data_payloads: list[str] = field(default_factory=list)
    quoted_instructions: list[str] = field(default_factory=list)
    executable_instruction: str = ""
    model_prompt: str = ""


@dataclass
class ResolvedIntent:
    """Décision de routage prise AVANT tout appel au modèle ou aux outils."""
    intent: str = "model_only"
    read_only: bool = True
    write_allowed: bool = False
    explicit_constraint: bool = False
    target: str = ""
    tools_allowed: bool = False
    fast_actions_allowed: bool = False
    benchmark_mode: bool = False
    trigger_source: str = ""
    reason: str = ""
    model_role: str = "default"
    segments: MessageSegments = field(default_factory=MessageSegments)

    def to_policy_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "read_only": bool(self.read_only),
            "write_allowed": bool(self.write_allowed),
            "explicit_constraint": bool(self.explicit_constraint),
            "target": self.target,
            "tools_allowed": bool(self.tools_allowed),
            "fast_actions_allowed": bool(self.fast_actions_allowed),
            "benchmark_mode": bool(self.benchmark_mode),
            "trigger_source": self.trigger_source,
            "reason": self.reason,
            "model_role": self.model_role,
        }


class MessageContextParser:
    """Sépare le message en instruction exécutable et fragments contextuels."""

    def parse(self, text: str) -> MessageSegments:
        raw = (text or "").strip()
        seg = MessageSegments(raw=raw)
        if not raw:
            return seg

        work = raw
        for m in _FENCED.finditer(work):
            seg.code_blocks.append(m.group(0))
        work = _FENCED.sub(" \n ", work)

        # Toute la section suivant « Réponse : » est la sortie attendue d'un
        # scénario : elle n'est ni une instruction ni un contexte exécutable.
        resp = _RESPONSE_LABEL.search(work)
        if resp:
            work = work[: resp.start()]
            seg.data_payloads.append("<reponse-attendue>")

        # Faut-il interpréter les citations comme des instructions rapportées ?
        fragments = [m.group(0) for m in _QUOTED.finditer(work)]
        framed = bool(_FRAMING.search(raw) or _LABELED_SECTION.search(raw)
                      or _BENCHMARK_MODE_LINE.search(raw) or _BENCHMARK_FLAGGED.search(raw))
        if framed:
            for fragment in fragments:
                if _VERB_TOKENS.search(fragment):
                    seg.quoted_instructions.append(fragment)
                    work = work.replace(fragment, " ")

        # Paires d'accolades ressemblant à du JSON : des données, pas du code.
        work = _strip_braced_objects(work, seg)

        executable = re.sub(r"\s+", " ", work).strip(" \n\t.,;:'\"«»")
        seg.executable_instruction = executable
        seg.model_prompt = raw
        return seg


def _strip_braced_objects(text: str, seg: MessageSegments) -> str:
    """Retire les objets JSON de premier niveau (données), jamais le reste."""
    out: list[str] = []
    i, n = 0, len(text)
    depth = 0
    start = -1
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 1
                elif text[j] == '"':
                    break
                j += 1
            if depth == 0:
                out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start != -1:
                chunk = text[start:i + 1]
                if ":" in chunk:
                    seg.data_payloads.append(chunk)
                    out.append("<donnees-json>")
                else:
                    out.append(chunk)
                start = -1
        elif depth == 0:
            out.append(c)
        i += 1
    if start != -1:
        out.append(text[start:])
    return "".join(out)


class IntentClassifier:
    """Prend la décision de routage : exécuter réellement ou répondre seulement."""

    def __init__(self, parser: MessageContextParser | None = None) -> None:
        self.parser = parser or MessageContextParser()

    def classify(self, text: str) -> ResolvedIntent:
        raw = (text or "").strip()
        seg = self.parser.parse(raw)
        if not raw:
            return self._real(raw, seg, "general", "message vide")

        if _BENCHMARK_MODE_LINE.search(raw):
            return self._model_only(seg, "benchmark_marker",
                                    "en-tête MODE/BENCHMARK détecté")
        if _BENCHMARK_FLAGGED.search(raw):
            return self._model_only(seg, "benchmark_marker",
                                    "marqueur benchmark/no-tools détecté")
        if _DO_NOT_EXECUTE.search(raw):
            return self._model_only(seg, "no_tools_phrase",
                                    "interdiction explicite d'exécuter une action")
        if _PROMPT_GENERATION.search(raw):
            return self._model_only(seg, "prompt_generation",
                                    "réduction d'un prompt, aucune action")
        if _INLINE_CODE_TARGET.search(raw) and (_FENCED.search(raw)
                                                or re.search(r"ci[- ]dessous|ci[- ]joint|"
                                                             r"suivant|joint|fourni", raw, re.I)):
            return self._model_only(seg, "code_sample",
                                    "analyse d'un extrait fourni dans le message")

        executable = seg.executable_instruction
        has_target = self._has_concrete_target(executable)

        if seg.quoted_instructions and not has_target:
            return self._model_only(seg, "quoted_instruction",
                                    "instruction rapportée ou entre guillemets")
        if seg.data_payloads and not has_target and self._looks_like_data_question(seg):
            return self._model_only(seg, "data_payload",
                                    "message d'analyse de données fournies")

        low_exec = executable.casefold()
        framed = bool(_FRAMING.search(raw) or _LABELED_SECTION.search(raw))
        explanation = bool(_EXPLANATION.search(executable))
        if explanation and not has_target:
            return self._model_only(seg, "explanation",
                                    "demande d'explication sans cible concrète")
        if framed and not has_target:
            return self._model_only(seg, "quoted_instruction",
                                    "scénario encadré/théorique sans action réelle")

        # Reste du chemin : vraie demande d'action réelle.
        policy = detect_read_only_intent(executable or raw)
        return self._real(executable or raw, seg, policy.intent,
                          "détection déterministe des actions")

    # -- helpers ----------------------------------------------------------

    def _has_concrete_target(self, executable: str) -> bool:
        if not executable:
            return False
        if _READ_FILE_TARGET.search(executable):
            return True
        if _OPEN_APP_TARGET.search(executable):
            return True
        if (_AUDIT_SIGNAL.search(executable)
                and (_AUDIT_TARGET.search(executable)
                     or has_read_only_constraint(executable))):
            return True
        if _CONNECTOR_ACTION.search(executable):
            return True
        if _SERVICE_OPS.search(executable):
            return True
        if _WEB_SEARCH.search(executable):
            return True
        if has_write_intent(executable) and re.search(
                r"\b(?:fichiers?|pages?|codes?|scripts?|[\w./~-]+\.\w{1,8})\b", executable):
            return True
        if (_VERB_AND_TARGET.search(executable)
                and not _EXPLANATION.search(executable)):
            return True
        try:
            from .intents import detect_3d_intent, detect_avatar_update_intent, detect_image_intent
            if detect_image_intent(executable) or detect_3d_intent(executable) \
                    or detect_avatar_update_intent(executable):
                return True
        except Exception:
            pass
        return False

    def _looks_like_data_question(self, seg: MessageSegments) -> bool:
        if not seg.data_payloads:
            return False
        if _FENCED.search(seg.raw):
            return True
        return bool(re.search(r"\b(?:cette\s+donn|en\s+tant\s+que\s+donn|json|xml|yaml|"
                              r"payload|parse)\b", seg.raw, re.I))

    def _model_only(self, seg: MessageSegments, trigger: str, reason: str) -> ResolvedIntent:
        return ResolvedIntent(
            intent="model_only",
            read_only=True,
            write_allowed=False,
            explicit_constraint=True,
            target="",
            tools_allowed=False,
            fast_actions_allowed=False,
            benchmark_mode=trigger == "benchmark_marker",
            trigger_source=trigger,
            reason=reason,
            model_role="default",
            segments=seg,
        )

    def _real(self, executable: str, seg: MessageSegments, mode: str, reason: str) -> ResolvedIntent:
        policy = detect_read_only_intent(executable)
        if mode in {"security_audit_readonly", "file_edit", "read_only", "general"}:
            pass
        elif policy.intent in {"security_audit_readonly", "file_edit", "read_only", "general"}:
            mode = policy.intent
        return ResolvedIntent(
            intent=mode,
            read_only=bool(policy.read_only),
            write_allowed=bool(policy.write_allowed),
            explicit_constraint=bool(policy.explicit_constraint),
            target=policy.target,
            tools_allowed=True,
            fast_actions_allowed=True,
            benchmark_mode=False,
            trigger_source="detect_read_only",
            reason=reason,
            model_role="default",
            segments=seg,
        )


_CLASSIFIER = IntentClassifier()


def predict_intent(text: str) -> ResolvedIntent:
    return _CLASSIFIER.classify(text)