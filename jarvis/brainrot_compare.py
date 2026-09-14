"""Comparaison déterministe Google Sheet ↔ Brainrot-Fortnite (lecture seule).

Aucune donnée ne vient du texte du modèle : le Sheet est lu par
SEMANTIC_ANALYSIS_V6, le site par le connecteur SSH existant, et le diff est
purement calculé. Aucune écriture, aucune suppression n'est produite en V1.

Schéma réel du site, relevé sur le serveur avant écriture de ce module
(``data/wiki/all_brainrots_sheet_data.php``, 272 entrées, toutes identiques) :
``['name' => str, 'rarity' => str, 'income' => int, 'cost' => int]``.
Le site ne porte aujourd'hui ni identifiant canonique ni slug ; l'échelle
d'identité les utilisera automatiquement le jour où ces champs existeront.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .sheet_semantics import _as_number, detect_regions, region_columns

BUILD_ID = "JARVIS_BRAINROT_COMPARE_V1"

SITE_DATA_PATH = "data/wiki/all_brainrots_sheet_data.php"
SHEET_TAB = "ALL BRAINROTS"

# Catalogue d'images : endpoint public du site, sans authentification, relevé
# sur le serveur avant écriture de ce module. Il expose exactement le mapping
# faisant autorité (table SQL `brainrots`) : id, name, slug, img_path.
#
# Ce catalogue est indispensable : déduire l'image du nom de fichier serait
# faux. « Skibidi Toilet » pointe sur `img/brainrots/skidibi.png`, un nom que
# ni le slug ni la normalisation ne produisent.
IMAGE_CATALOG_URL = "https://brainrot-fortnite.com/ajax/brainrots-ajax.php"
SITE_PUBLIC_BASE = "https://brainrot-fortnite.com"
IMAGE_CATALOG_PAGE_SIZE = 120
# Le site sert `img/placeholder.svg` quand le fichier réel manque sur disque.
# Ce n'est pas le visuel du Brainrot : le propager afficherait une image
# générique à la place du monogramme, donc on le traite comme une absence.
IMAGE_PLACEHOLDER_RE = re.compile(r"/placeholder[^/]*$", re.I)

NO_CHANGE, CREATE, UPDATE, CONFLICT, INVALID, SERVER_ONLY = (
    "NO_CHANGE", "CREATE", "UPDATE", "CONFLICT", "INVALID", "SERVER_ONLY")

# Mapping explicite, établi après inspection des DEUX schémas réels.
# En-tête exact du Sheet -> clé exacte du site. Rien n'est deviné : un champ
# absent de cette table n'est jamais comparé.
FIELD_MAPPING = {
    "Name": "name",
    "Rarity": "rarity",
    "Base Income ($/s)": "income",
    "Price ($)": "cost",
}
IDENTITY_FIELD = "Name"
# Champs réellement comparés (l'identité n'est pas un écart à corriger).
COMPARED_FIELDS = [f for f in FIELD_MAPPING if f != IDENTITY_FIELD]
NUMERIC_FIELDS = {"Base Income ($/s)", "Price ($)"}

PHP_ROW_RE = re.compile(
    r"\[\s*'name'\s*=>\s*'((?:[^'\\]|\\.)*)'\s*,\s*"
    r"'rarity'\s*=>\s*'((?:[^'\\]|\\.)*)'\s*,\s*"
    r"'income'\s*=>\s*([0-9.eE+-]+)\s*,\s*"
    r"'cost'\s*=>\s*([0-9.eE+-]+)\s*\]")


# --- Normalisation ----------------------------------------------------------
def normalize_text(value: Any) -> str:
    """Casse et espaces neutralisés — POUR LE MATCHING UNIQUEMENT.

    La valeur d'origine est toujours conservée à côté : c'est elle qui est
    montrée en preuve et comparée, jamais cette forme réduite.
    """
    # Un nom purement numérique (« 14 », « 67 » — ce sont de vrais Brainrots)
    # revient du XLSX en flottant : sans cette mise en forme canonique, « 14.0 »
    # et « 14 » deviendraient un faux CREATE plus un faux SERVER_ONLY.
    if isinstance(value, float) and not isinstance(value, bool) and value.is_integer():
        value = int(value)
    text = "" if value is None else str(value)
    text = text.replace(" ", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().casefold()


def slugify(value: Any) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalize_text(value))).strip("-")


def normalize_number(value: Any) -> float | None:
    """Nombre réel derrière « 1.5K », « 1,2T » ou 250000. None si non numérique."""
    return _as_number(value)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


# --- Lecture du Sheet -------------------------------------------------------
def _merged_lookup(sheet: dict[str, Any]) -> dict[tuple[int, int], tuple[int, int]]:
    """Chaque cellule couverte par une fusion pointe vers la cellule porteuse."""
    lookup: dict[tuple[int, int], tuple[int, int]] = {}
    for merge in sheet.get("merges") or []:
        for row in range(merge["first_row"], merge["last_row"] + 1):
            for col in range(merge["first_col"], merge["last_col"] + 1):
                lookup[(row, col)] = (merge["first_row"], merge["first_col"])
    return lookup


def resolve_sheet_tab(workbook: dict[str, Any], preferred: str = SHEET_TAB) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Résout l'onglet du classeur sans jamais supposer un nom fixe.

    Ordre documenté :
      1. `selected_tab` renvoyé par le lecteur (gid de l'URL résolu) ;
      2. nom exact ;
      3. nom égal hors casse ;
      4. nom contenant « brainrot » (unique ou le plus peuplé si ambigu) ;
      5. premier onglet non vide ;
      6. premier onglet du classeur.

    Retourne ``(sheet, mode, tous_les_noms)``. ``mode`` décrit comment l'onglet
    a été choisi ; il est exposé dans la réponse pour que la résolution reste
    transparente et vérifiable.
    """
    sheets = workbook.get("sheets") or []
    names = [str(s.get("name") or "") for s in sheets]
    if not sheets:
        return None, "empty", []
    # `selected_tab` n'est un CHOIX que si l'URL portait un gid explicite.
    # Sans gid, le lecteur retombe sur le premier onglet peuple ("Home" ici) :
    # le prendre pour une demande de l'utilisateur faisait lire le mauvais
    # onglet et echouer la comparaison en SHEET_TABLE_NOT_FOUND.
    selected = workbook.get("selected_tab") if workbook.get("selected_tab_explicit") else ""
    if selected:
        for index, sheet in enumerate(sheets):
            if str(sheet.get("name") or "") == str(selected):
                return sheet, "url_gid", names
    for sheet in sheets:
        if str(sheet.get("name") or "") == preferred:
            return sheet, "exact", names
    pref = preferred.casefold()
    exact_folded = [(s) for s in sheets if str(s.get("name") or "").casefold() == pref]
    if len(exact_folded) == 1:
        return exact_folded[0], "casefold", names
    brainrot = [s for s in sheets if "brainrot" in str(s.get("name") or "").casefold()]
    if len(brainrot) == 1:
        return brainrot[0], "contains", names
    if len(brainrot) > 1:
        best = max(brainrot, key=lambda s: s.get("useful_rows", 0))
        return best, "ambiguous_populated", names
    populated = [s for s in sheets if s.get("useful_rows", 0) > 0]
    if populated:
        return populated[0], "first_populated", names
    return sheets[0], "first", names


def _select_source_region(cells: list, regions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Table principale choisie par ses EN-TETES REELS, jamais par sa position.

    L'onglet « ALL BRAINROTS » contient plusieurs tableaux (« ALL TRAIT MULTS »,
    « ALL TYPE MULTS », les blocs de calcul). Prendre la premiere region
    detectee dependait de l'ordre de detection et pouvait retomber sur un
    tableau de multiplicateurs, dont les colonnes (Name/Multiplier) produisent
    des enregistrements valides mais faux.

    Le score est le nombre d'en-tetes de FIELD_MAPPING reellement presents ;
    l'identite (`Name`) est obligatoire, et un tableau qui ne porte qu'elle ne
    suffit pas a etre la source d'une comparaison.
    """
    best, best_score = None, 0
    for region in regions:
        headers = {p["name"] for p in region_columns(cells, region) if p["filled"]}
        if IDENTITY_FIELD not in headers:
            continue
        score = sum(1 for field in FIELD_MAPPING if field in headers)
        if score > best_score:
            best, best_score = region, score
    # Au moins une colonne comparee en plus de l'identite : sinon ce n'est pas
    # la table des brainrots mais un tableau annexe qui lui ressemble.
    return best if best_score >= 2 else None


def read_sheet_records(workbook: dict[str, Any], tab: str = SHEET_TAB) -> dict[str, Any]:
    """Enregistrements de la table principale, rareté résolue par les fusions.

    L'onglet est résolu de façon robuste (``resolve_sheet_tab``) et le nom
    RÉELLEMENT utilisé est renvoyé dans ``tab`` ; ``tab_requested`` conserve le
    nom demandé et ``tab_resolution`` explique comment il a été choisi.
    """
    sheet, mode, names = resolve_sheet_tab(workbook, tab)
    if mode == "empty":
        return {"ok": False, "error": "SHEET_EMPTY", "tab": tab, "tab_requested": tab,
                "records": [], "available_tabs": names}
    if not sheet:
        return {"ok": False, "error": "SHEET_TAB_NOT_FOUND", "tab_requested": tab,
                "tab": tab, "records": [], "available_tabs": names}
    real_name = str(sheet.get("name") or tab)
    cells = sheet.get("cells") or []
    if not cells or sheet.get("useful_rows", 0) == 0:
        return {"ok": False, "error": "SHEET_EMPTY", "tab_requested": tab,
                "tab": real_name, "tab_resolution": mode, "records": [],
                "available_tabs": names}
    regions = [r for r in detect_regions(cells, real_name) if r["kind"] == "table"]
    region = _select_source_region(cells, regions)
    if not region:
        # Diagnostic structure : dire CE QUI a ete vu vaut mieux qu'un
        # « onglet non identifie » qui n'oriente vers aucune correction.
        return {"ok": False, "error": "COMPARE_SOURCE_NOT_FOUND", "tab_requested": tab,
                "tab": real_name, "tab_resolution": mode, "records": [],
                "available_tabs": names, "sheets_found": names,
                "regions_found": [r.get("title") or r.get("id") for r in regions],
                "headers_found": [[p["name"] for p in region_columns(cells, r) if p["filled"]]
                                  for r in regions]}

    numbers = sheet.get("row_numbers") or list(range(1, len(cells) + 1))
    position = {number: index for index, number in enumerate(numbers)}
    merged = _merged_lookup(sheet)
    profiles = region_columns(cells, region)
    # Seules les colonnes réellement porteuses de données comptent : les
    # colonnes d'espacement vides ne sont ni des champs ni des écarts.
    profiles = [p for p in profiles if p["filled"]]
    headers = [p["name"] for p in profiles]
    columns = {p["name"]: p["column"] for p in profiles}

    def value_at(row_number: int, column: int) -> Any:
        # Une cellule fusionnée ne porte sa valeur qu'en haut à gauche : la
        # rareté d'une section est donc lue là, jamais devinée par recopie.
        source_row, source_col = merged.get((row_number, column), (row_number, column))
        index = position.get(source_row)
        if index is None or index >= len(cells):
            return ""
        row = cells[index]
        return row[source_col - 1] if source_col - 1 < len(row) else ""

    records = []
    # On n'itère QUE sur les lignes de la région détectée : une ligne d'un bloc
    # voisin lue avec les colonnes de ce tableau produirait des enregistrements
    # fantômes (« 14.0 » vu comme un nom) et de faux CREATE.
    for index in region["row_indexes"]:
        if index >= len(numbers):
            continue
        row_number = numbers[index]
        raw = {name: value_at(row_number, column) for name, column in columns.items()}
        if _is_blank(raw.get(IDENTITY_FIELD)):
            continue
        records.append({"row": row_number, "raw": raw,
                        "identity": normalize_text(raw[IDENTITY_FIELD]),
                        "slug": slugify(raw[IDENTITY_FIELD])})
    return {"ok": True, "tab": real_name, "tab_requested": tab,
            "tab_resolution": mode, "table": region["title"] or region["id"],
            "header_row": region["header_row"], "headers": headers,
            "columns": columns, "records": records,
            "available_tabs": names}


# --- Lecture du site --------------------------------------------------------
def parse_site_php(text: str) -> list[dict[str, Any]]:
    """Parse le tableau PHP du site. Aucune exécution de code : lecture stricte."""
    records = []
    for index, match in enumerate(PHP_ROW_RE.finditer(text), start=1):
        name, rarity, income, cost = match.groups()
        name = name.replace("\\'", "'").replace("\\\\", "\\")
        rarity = rarity.replace("\\'", "'").replace("\\\\", "\\")
        line = text.count("\n", 0, match.start()) + 1
        records.append({"index": index, "line": line,
                        "raw": {"name": name, "rarity": rarity,
                                "income": _as_number(income), "cost": _as_number(cost)},
                        "identity": normalize_text(name), "slug": slugify(name)})
    return records


class ServerReader:
    """Lecture seule du site via le connecteur SSH déjà configuré."""

    def __init__(self, core: Any, connector_id: str = "ssh", path: str = SITE_DATA_PATH) -> None:
        self.core = core
        self.connector_id = connector_id
        self.path = path

    def read(self) -> dict[str, Any]:
        result = self.core.runner.run(
            "ssh.read_file", {"connector_id": self.connector_id, "path": self.path},
            agent="jarvis", execution_policy={"read_only": True})
        if not result.ok:
            return {"ok": False, "error": "SITE_READ_FAILED", "detail": result.output[:300],
                    "path": self.path, "records": []}
        content = result.output
        records = parse_site_php(content)
        if not records:
            return {"ok": False, "error": "SITE_SCHEMA_UNRECOGNISED", "path": self.path,
                    "records": []}
        import hashlib
        return {"ok": True, "path": self.path, "records": records,
                # Empreinte du magasin au moment de la lecture : c'est elle qui
                # detecte qu'il a bouge entre la comparaison et l'application.
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "fields": sorted(FIELD_MAPPING.values())}


# --- Matching ---------------------------------------------------------------
def _index(records: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        value = record.get(key) or ""
        if value:
            buckets.setdefault(value, []).append(record)
    return buckets


def match_records(sheet_records: list[dict[str, Any]],
                  site_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Échelle d'identité : id canonique, puis slug exact, puis nom normalisé.

    Aucun rapprochement approximatif : une ambiguïté devient un CONFLICT, elle
    ne devient jamais une modification.
    """
    pairs, conflicts = [], []
    matched_site: set[int] = set()
    ladders = [("canonical_id", "id"), ("slug", "slug"), ("normalized_name", "identity")]
    site_by = {name: _index(site_records, key) for name, key in ladders}
    sheet_by = {name: _index(sheet_records, key) for name, key in ladders}

    for record in sheet_records:
        hit = None
        for name, key in ladders:
            value = record.get(key) or ""
            if not value:
                continue
            candidates = site_by[name].get(value) or []
            same_side = sheet_by[name].get(value) or []
            if len(candidates) > 1 or len(same_side) > 1:
                conflicts.append({"sheet": record, "rule": name, "value": value,
                                  "site_candidates": candidates, "sheet_duplicates": same_side})
                hit = "conflict"
                break
            if candidates:
                hit = {"site": candidates[0], "rule": name}
                break
        if hit == "conflict":
            continue
        if hit:
            matched_site.add(id(hit["site"]))
            pairs.append({"sheet": record, "site": hit["site"], "rule": hit["rule"]})
        else:
            pairs.append({"sheet": record, "site": None, "rule": ""})
    orphans = [r for r in site_records if id(r) not in matched_site]
    return {"pairs": pairs, "conflicts": conflicts, "server_only": orphans}


# --- Diff -------------------------------------------------------------------
def _compare_field(field: str, sheet_value: Any, site_value: Any) -> dict[str, Any] | None:
    """Compare un champ mappé. None si les deux versions sont équivalentes."""
    sheet_blank, site_blank = _is_blank(sheet_value), _is_blank(site_value)
    if sheet_blank and site_blank:
        return None
    if sheet_blank and not site_blank:
        # Le Sheet ne dit rien : ce n'est pas l'ordre d'effacer la valeur du
        # site. On le signale comme donnée manquante côté Sheet, jamais comme
        # une mise à jour à appliquer.
        return {"field": field, "site_field": FIELD_MAPPING[field], "sheet": sheet_value,
                "site": site_value, "reason": "SHEET_VALUE_MISSING", "actionable": False}
    if field in NUMERIC_FIELDS:
        left, right = normalize_number(sheet_value), normalize_number(site_value)
        if left is None and not sheet_blank:
            return {"field": field, "site_field": FIELD_MAPPING[field], "sheet": sheet_value,
                    "site": site_value, "reason": "SHEET_VALUE_NOT_NUMERIC"}
        if left is not None and right is not None and left == right:
            return None
        if left is None and right is None:
            return None
    else:
        if normalize_text(sheet_value) == normalize_text(site_value):
            return None
    return {"field": field, "site_field": FIELD_MAPPING[field],
            "sheet": sheet_value, "site": site_value, "reason": ""}


def diff_records(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Produit un statut par enregistrement. Aucune suppression n'est proposée."""
    entries: list[dict[str, Any]] = []
    for pair in match["pairs"]:
        record, site = pair["sheet"], pair["site"]
        raw = record["raw"]
        identity = str(raw.get(IDENTITY_FIELD) or "").strip()
        base = {"identity": identity, "slug": record["slug"], "sheet_row": record["row"],
                "match_rule": pair["rule"],
                "sheet_values": {k: raw.get(k) for k in FIELD_MAPPING},
                "site_values": dict(site["raw"]) if site else {},
                "site_index": site["index"] if site else None,
                "site_line": site["line"] if site else None,
                "changed_fields": [],
                "evidence_sheet": f"ev::compare::sheet::{record['row']}",
                "evidence_site": f"ev::compare::site::{site['index']}" if site else ""}
        invalid = [f for f in NUMERIC_FIELDS
                   if not _is_blank(raw.get(f)) and normalize_number(raw.get(f)) is None]
        if invalid:
            entries.append({**base, "status": INVALID,
                            "reason": "Valeurs non numériques dans le Sheet : " + ", ".join(invalid),
                            "recommended_action": "Corriger la valeur dans le Sheet avant toute mise à jour."})
            continue
        if site is None:
            entries.append({**base, "status": CREATE, "reason": "Absent du site.",
                            "recommended_action": "Créer l'entrée sur le site (aucune écriture en V1)."})
            continue
        found = [c for c in (_compare_field(f, raw.get(f), site["raw"].get(FIELD_MAPPING[f]))
                             for f in COMPARED_FIELDS) if c]
        changed = [c for c in found if c.get("actionable", True)]
        missing = [c for c in found if not c.get("actionable", True)]
        base["missing_fields"] = missing
        if not changed:
            note = ("Identique sur les champs mappés." if not missing else
                    "Aucun écart à appliquer ; champs vides côté Sheet : "
                    + ", ".join(c["field"] for c in missing) + ".")
            entries.append({**base, "status": NO_CHANGE, "reason": note,
                            "recommended_action": "Rien à faire."
                            if not missing else "Compléter le Sheet si ces champs doivent y figurer."})
            continue
        entries.append({**base, "status": UPDATE, "changed_fields": changed,
                        "reason": "Écart sur : " + ", ".join(c["field"] for c in changed),
                        "recommended_action": "Mettre le site à jour depuis le Sheet (aucune écriture en V1)."})
    for conflict in match["conflicts"]:
        record = conflict["sheet"]
        raw = record["raw"]
        entries.append({
            "identity": str(raw.get(IDENTITY_FIELD) or "").strip(), "slug": record["slug"],
            "sheet_row": record["row"], "match_rule": conflict["rule"], "status": CONFLICT,
            "sheet_values": {k: raw.get(k) for k in FIELD_MAPPING},
            "site_values": {}, "site_index": None, "site_line": None, "changed_fields": [],
            "reason": (f"Identité ambiguë sur {conflict['rule']} = « {conflict['value']} » : "
                       f"{len(conflict['sheet_candidates'] if 'sheet_candidates' in conflict else conflict['sheet_duplicates'])} "
                       f"lignes Sheet, {len(conflict['site_candidates'])} entrées site."),
            "recommended_action": "Lever l'ambiguïté manuellement : aucune modification automatique.",
            "evidence_sheet": f"ev::compare::sheet::{record['row']}", "evidence_site": ""})
    for orphan in match["server_only"]:
        entries.append({
            "identity": orphan["raw"].get("name", ""), "slug": orphan["slug"],
            "sheet_row": None, "match_rule": "", "status": SERVER_ONLY,
            "sheet_values": {}, "site_values": dict(orphan["raw"]),
            "site_index": orphan["index"], "site_line": orphan["line"], "changed_fields": [],
            "reason": "Présent sur le site, absent de la table Sheet.",
            # Jamais de suppression automatique : le site peut légitimement
            # porter des entrées que le Sheet ne suit pas.
            "recommended_action": "À vérifier manuellement. Aucune suppression n'est proposée.",
            "evidence_sheet": "", "evidence_site": f"ev::compare::site::{orphan['index']}"})
    return entries


def compare(workbook: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
    """Pipeline complet : Sheet normalisé → matching → diff, 100 % déterministe."""
    sheet = read_sheet_records(workbook)
    if not sheet.get("ok"):
        return {"ok": False, "error": sheet.get("error"), "tab": sheet.get("tab"),
                "tab_requested": sheet.get("tab_requested"),
                "tab_resolution": sheet.get("tab_resolution"),
                "available_tabs": sheet.get("available_tabs", []), "entries": []}
    if not site.get("ok"):
        return {"ok": False, "error": site.get("error"), "detail": site.get("detail", ""),
                "tab": sheet.get("tab"), "tab_requested": sheet.get("tab_requested"),
                "tab_resolution": sheet.get("tab_resolution"),
                "available_tabs": sheet.get("available_tabs", []), "entries": []}
    match = match_records(sheet["records"], site["records"])
    entries = diff_records(match)
    counts = {status: sum(1 for e in entries if e["status"] == status)
              for status in (NO_CHANGE, CREATE, UPDATE, CONFLICT, INVALID, SERVER_ONLY)}
    return {"ok": True, "build": BUILD_ID,
            "sheet": {"tab": sheet["tab"], "table": sheet["table"],
                      "total": len(sheet["records"]), "headers": sheet["headers"]},
            "site": {"path": site["path"], "total": len(site["records"]),
                     "fields": site.get("fields", [])},
            "mapping": dict(FIELD_MAPPING), "counts": counts, "entries": entries,
            "write_performed": False}


# --- Catalogue d'images -----------------------------------------------------
def _catalog_page(page: int, *, timeout: float) -> dict[str, Any]:
    url = f"{IMAGE_CATALOG_URL}?limit={IMAGE_CATALOG_PAGE_SIZE}&page={page}"
    request = urllib.request.Request(url, headers={"User-Agent": "JARVIS/3.0",
                                                   "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read(4_000_000).decode("utf-8"))


def read_image_catalog(*, timeout: float = 20, max_pages: int = 12) -> dict[str, Any]:
    """Lit le catalogue public. Aucune image n'est téléchargée : seules les URL."""
    items: dict[str, dict[str, Any]] = {}
    try:
        page, pages = 1, 1
        while page <= pages and page <= max_pages:
            data = _catalog_page(page, timeout=timeout)
            if not data.get("ok"):
                return {"ok": False, "error": "IMAGE_CATALOG_REFUSED", "items": []}
            for group in data.get("groups", []):
                for item in group.get("items", []):
                    slug = str(item.get("slug") or "").strip()
                    if slug:
                        items[slug] = item
            pages = int((data.get("pagination") or {}).get("total_pages") or 1)
            page += 1
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            ValueError, UnicodeError) as exc:
        return {"ok": False, "error": "IMAGE_CATALOG_UNREACHABLE",
                "detail": str(exc)[:200], "items": []}
    return {"ok": True, "source": IMAGE_CATALOG_URL, "items": list(items.values())}


def _image_url(item: dict[str, Any]) -> str:
    """Transforme `img_path` en URL absolue. Une valeur vide ne donne pas d'URL."""
    path = str(item.get("img_path") or item.get("img") or "").strip()
    if not path or IMAGE_PLACEHOLDER_RE.search(path):
        return ""
    if re.match(r"^https?://", path, re.I):
        return path
    return SITE_PUBLIC_BASE + "/" + path.replace("\\", "/").lstrip("/")


def build_image_index(catalog: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Index par identifiant, slug et nom normalisé.

    Une clé revendiquée par deux entrées différentes est retirée : mieux vaut
    aucun visuel qu'un visuel faux.
    """
    index: dict[str, dict[str, str]] = {"id": {}, "slug": {}, "name": {}}
    collisions: dict[str, set[str]] = {"id": set(), "slug": set(), "name": set()}
    for item in catalog.get("items", []):
        url = _image_url(item)
        if not url:
            continue
        keys = {"id": str(item.get("id") or "").strip(),
                "slug": str(item.get("slug") or "").strip().casefold(),
                "name": normalize_text(item.get("name"))}
        for kind, key in keys.items():
            if not key:
                continue
            if key in index[kind] and index[kind][key] != url:
                collisions[kind].add(key)
            else:
                index[kind][key] = url
    for kind, keys in collisions.items():
        for key in keys:
            index[kind].pop(key, None)
    return index


def attach_images(comparison: dict[str, Any], index: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Ajoute `image_url` aux entrées. Échelle stricte, aucun rapprochement flou.

    Priorité : identifiant canonique, puis slug exact, puis nom normalisé exact.
    Sans correspondance certaine, le champ reste vide et l'interface retombe
    sur son monogramme.
    """
    if not comparison.get("ok"):
        return comparison
    matched = 0
    for entry in comparison.get("entries", []):
        identifier = str(entry.get("canonical_id") or "").strip()
        slug = str(entry.get("slug") or "").strip().casefold()
        name = normalize_text(entry.get("identity"))
        url = ""
        rule = ""
        for kind, key in (("id", identifier), ("slug", slug), ("name", name)):
            if key and index.get(kind, {}).get(key):
                url, rule = index[kind][key], kind
                break
        entry["image_url"] = url
        entry["image_match"] = rule
        if url:
            matched += 1
    comparison["images"] = {"source": IMAGE_CATALOG_URL, "matched": matched,
                            "catalog_size": len(index.get("slug", {}))}
    return comparison
