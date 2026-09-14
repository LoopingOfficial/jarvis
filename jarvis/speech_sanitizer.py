"""Speech Sanitizer — transforme une réponse UI en une phrase naturellement parlable.

Pipeline : LLM response → UI response → speech sanitizer → TTS.
La version affichée peut contenir du Markdown, des URLs, du code, des tableaux ;
la version parlée doit n'être que du langage naturel, lisible à l'oral.
"""
from __future__ import annotations

import re

# ---- Regroupements : tout symbole de Markdown/backtick est retiré, pas lu. ----
_BLOCK_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\n.*?```", re.DOTALL)
_BLOCK_QUOTE = re.compile(r"\n?>\s?", re.MULTILINE)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_THEMATIC = re.compile(r"^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$", re.MULTILINE)

# *em* ou **gras** : on vide l'étoile sans couper le mot.
_EMPHASIS = re.compile(r"\*{1,3}([^*\n]*?)\*{1,3}")
_TILDE_STRIKE = re.compile(r"~{1,2}([^~\n]*?)~{1,2}")
_LIST_BULLET = re.compile(r"^\s*(?:[-*+•])\s+", re.MULTILINE)
_LIST_ORDERED = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")
_CODE_INLINE = re.compile(r"`([^`\n]*)`")
_HTML_TAG = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|nbsp|#\d+);")

# Cellules de tableau : remplacées par un espace (les `|` ne doivent pas être lus).
_TABLE_SEP = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_ROW_KEEP = re.compile(r"\s*\|\s*")

_PUNCT_LIST = re.compile(r"[,;:]\s{2,}")
_SPACES = re.compile(r"\s{2,}")

# Symboles d'unités / signes lisibles oralement.
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_COLON_LABEL = re.compile(r"(?P<label>[\wàâäéèêëîïôöùûüç'\- ]{2,})\s*:\s+")

# Chiffres → orthographe naturelle (français).
_NUMBERS = re.compile(r"(\d+(?:[.,]\d+)?)")
_NUMBER_WORDS = {
    "0": "zéro", "1": "un", "2": "deux", "3": "trois", "4": "quatre", "5": "cinq",
    "6": "six", "7": "sept", "8": "huit", "9": "neuf", "10": "dix",
}
_SMALL_WORDS = {
    "11": "onze", "12": "douze", "13": "treize", "14": "quatorze", "15": "quinze",
    "16": "seize", "17": "dix-sept", "18": "dix-huit", "19": "dix-neuf",
    "20": "vingt", "30": "trente", "40": "quarante", "50": "cinquante",
    "60": "soixante", "70": "soixante-dix", "80": "quatre-vingts", "90": "quatre-vingt-dix",
}

# Symboles isolés éventuellement produits par le markdown résiduel.
_TRAIL_SYMBOLS = re.compile(r"[*_`#<>]{2,}")


def _number_to_words(match: re.Match | str) -> str:
    raw = match.group(0) if hasattr(match, "group") else match
    try:
        if "," in raw or "." in raw:
            parts = re.split(r"[.,]", raw)
            int_part, dec_part = parts[0], parts[1]
            whole = _int_words(int(int_part)) if int_part else "zéro"
            dec = " ".join(_digits_word(c) for c in dec_part)[-len(" ".join(_digits_word(c) for c in dec_part)):]
            return f"{whole} virgule {dec}"
        return _int_words(int(raw))
    except Exception:
        return _NUMBER_WORDS.get(raw, raw)


def _int_words(n: int) -> str:
    s = str(n)
    if s in _NUMBER_WORDS:
        return _NUMBER_WORDS[s]
    if s in _SMALL_WORDS:
        return _SMALL_WORDS[s]
    if 21 <= n <= 99:
        tens, unit = divmod(n, 10)
        tens_word = _SMALL_WORDS.get(str(tens * 10), str(tens * 10))
        if unit:
            sep = " et " if unit == 1 else "-"
            return f"{tens_word}{sep}{_NUMBER_WORDS[str(unit)]}"
        return tens_word
    if 100 <= n <= 999:
        hundreds, rest = divmod(n, 100)
        head = "" if hundreds == 1 else f"{_int_words(hundreds)} "
        plural = "s" if rest == 0 and hundreds > 1 else ""
        tail = f" {_int_words(rest)}" if rest else ""
        return f"{head}cent{plural}{tail}"
    return s


def _digits_word(c: str) -> str:
    return _NUMBER_WORDS.get(c, c)


def _percent_to_words(match: re.Match) -> str:
    return f"{_number_to_words(match.group(0)[:-1].strip())} pour cent"


def sanitize_for_speech(text: str) -> str:
    """Version orale naturelle d'une réponse : sans Markdown, symboles ni URLs."""
    t = (text or "").strip()
    if not t:
        return ""
    t = _BLOCK_FENCE.sub(" ", t)
    t = _HTML_TAG.sub(" ", t)
    t = _ENTITY.sub(" ", t)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub("lien", t)
    t = _EMAIL.sub("adresse e-mail", t)
    t = _TABLE_SEP.sub(" ", t)
    t = _TABLE_ROW_KEEP.sub(" ", t)
    t = _HEADING.sub("", t)
    t = _THEMATIC.sub(". ", t)
    t = _BLOCK_QUOTE.sub(" ", t)
    t = _LIST_BULLET.sub("", t)
    t = _LIST_ORDERED.sub("", t)
    t = _EMPHASIS.sub(r"\1", t)
    t = _TILDE_STRIKE.sub(r"\1", t)
    t = _CODE_INLINE.sub(lambda m: m.group(1) or " ", t)
    t = _TRAIL_SYMBOLS.sub(" ", t)
    # « tiré-tiré » / « astérisque » : les tirets de liste résiduels sont lissés.
    t = re.sub(r"^\s*-\s+", "", t, flags=re.MULTILINE)
    t = _PUNCT_LIST.sub(", ", t)
    t = _PERCENT.sub(_percent_to_words, t)
    t = t.replace("+", " plus ").replace("=", " égale ")
    t = _COLON_LABEL.sub(r". \1 : ", t)
    t = _NUMBERS.sub(_number_to_words, t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = _SPACES.sub(" ", t)
    t = t.strip()
    return t


def readable_preview(text: str, limit: int = 200) -> str:
    """Version courte (affichage) sans saut de ligne excessif."""
    t = _BLOCK_FENCE.sub(" [code] ", text or "")
    t = _LINK.sub(r"\1", t)
    t = _SPACES.sub(" ", t)
    return t.strip()[:limit]


# Marqueurs de prompt interne. Un texte qui en contient un est destiné au
# modèle, jamais à l'écran : il porte le contenu du classeur, la politique de
# sources ou les consignes de grounding.
INTERNAL_PROMPT_MARKERS = (
    "CONTENU STRUCTURÉ", "CONTENU STRUCTURE", "SOURCE_POLICY", "ANALYSE_DETERMINISTE",
    "DETERMINISTIC_WORKBOOK_SUMMARY", "PERIMETRE_OBLIGATOIRE", "FORMAT_REPONSE_ANALYSE",
    "DEMANDE DE L'UTILISATEUR", "VALIDATION_FAILED", "FAITS_VERIFIES",
    "PASSAGE_A_CORRIGER", "VALEURS_REFUSEES", "TACHE :",
)

# Libellés autorisés dans le bandeau système. Le frontend n'affiche que ceux-ci
# pour l'analyse d'un classeur ; tout le reste est un texte de prompt.
PUBLIC_ACTIVITY_LABELS = (
    "Connexion au Google Sheet", "Téléchargement du classeur", "Lecture des onglets",
    "Détection des tableaux", "Analyse des données", "Vérification des sources",
    "Correction d'affirmations", "Préparation du Workspace", "Analyse terminée",
)


def is_internal_prompt(text: str) -> bool:
    """Vrai si le texte est un prompt interne et non un message d'utilisateur."""
    head = (text or "")[:4000]
    return any(marker in head for marker in INTERNAL_PROMPT_MARKERS)


def public_task_label(text: str, explicit: str = "") -> str:
    """Nom de tâche PUBLIABLE : jamais un fragment de prompt interne.

    `explicit` est le libellé voulu par l'appelant. À défaut, un message
    d'utilisateur ordinaire reste affiché tel quel — c'est l'information utile —
    tandis qu'un prompt interne est remplacé par un libellé neutre. Le garde-fou
    est ici, au point d'émission : un filtre uniquement côté interface laisserait
    fuir le texte par tout autre consommateur du même événement.
    """
    if explicit:
        return explicit[:200]
    if is_internal_prompt(text):
        return "Analyse des données"
    return (text or "")[:200]
