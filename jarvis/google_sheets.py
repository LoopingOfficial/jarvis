"""Lecture générique, publique et strictement read-only de Google Sheets."""
from __future__ import annotations

import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

SHEET_URL_RE = re.compile(r"https?://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)(?:/[^\s<>]*)?", re.I)


def sheet_url_gid(url: str, default: str = "0") -> str:
    """Extrait le `gid`, qu'il soit dans la query (`?gid=N`) ou le fragment (`#gid=N`).

    GitHub/Slack réécrivent parfois `#gid=0` pendant que d'autres URLs portent
    `?gid=0` : c'est le cas particulier de Google Sheets, pas un attribut du gid.
    """
    split = urllib.parse.urlsplit(url or "")
    query = urllib.parse.parse_qs(split.query)
    if query.get("gid"):
        return query["gid"][0]
    fragment_gid = re.search(r"(?:^|[&#])gid=(\d+)", split.fragment or "")
    if fragment_gid:
        return fragment_gid.group(1)
    return default


def parse_sheet_url(url: str) -> dict[str, str] | None:
    match = SHEET_URL_RE.search(url or "")
    if not match:
        return None
    # `gid` vaut "0" par defaut : une URL SANS gid est donc indiscernable d'un
    # `?gid=0` explicite. `gid_explicit` leve cette ambiguite pour que les
    # consommateurs ne prennent pas le premier onglet pour un choix de l'utilisateur.
    explicit = sheet_url_gid(match.group(0), default="") != ""
    return {"sheet_id": match.group(1), "gid": sheet_url_gid(match.group(0)),
            "gid_explicit": explicit}


def _col_index(ref: str) -> int:
    """Convertit la référence A1/AB12 en index de colonne 0-based."""
    letters = re.match(r"[A-Z]+", (ref or "").upper())
    col = 0
    for char in (letters.group(0) if letters else "A"):
        col = col * 26 + ord(char) - 64
    return col - 1


def _is_empty(value: Any) -> bool:
    """Une cellule est vide si elle vaut None ou une chaîne sans contenu."""
    return value is None or (isinstance(value, str) and value.strip() == "")


def _xlsx_value(cell: ET.Element, shared: list[str]) -> Any:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    inline = cell.find(f"{ns}is")
    if inline is not None:
        return "".join(t.text or "" for t in inline.iter(f"{ns}t"))
    value = cell.findtext(f"{ns}v")
    if value is None:
        return ""
    kind = cell.get("t")
    if kind == "s":
        try:
            index = int(value)
        except ValueError:
            return ""
        return shared[index] if 0 <= index < len(shared) else ""
    if kind == "b":
        return value == "1"
    if kind in ("str", "e", "inlineStr"):
        return value
    try:
        return float(value) if "." in value or "e" in value.lower() else int(value)
    except ValueError:
        return value


def _cell_type(value: Any, formula: str | None) -> str:
    if formula is not None:
        return "formula"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and re.match(r"https?://", value.strip(), re.I):
        return "url"
    return "text"


def _unique_headers(raw: list[Any], width: int) -> list[str]:
    """Produit des en-têtes uniques et non vides.

    Sans cela, ``dict(zip(headers, row))`` écrase toutes les colonnes qui
    partagent un en-tête vide ou dupliqué, et le contenu réel disparaît.
    """
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index in range(width):
        label = str(raw[index]).strip() if index < len(raw) and not _is_empty(raw[index]) else ""
        if not label:
            label = f"col_{index + 1}"
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 1
        headers.append(label)
    return headers


def _filled_columns(row: list[Any]) -> set[int]:
    return {index for index, value in enumerate(row) if not _is_empty(value)}


def _header_row_index(matrix: list[list[Any]], scan: int = 20) -> int:
    """Choisit la vraie ligne d'en-tête plutôt qu'une bannière de titre.

    Un en-tête est une ligne de libellés textuels dont les colonnes sont
    reprises par plusieurs lignes de données en dessous. Les bandeaux de
    navigation ("HOME") et les titres fusionnés ne remplissent pas ce critère.
    """
    for index, row in enumerate(matrix[:scan]):
        columns = _filled_columns(row)
        if len(columns) < 2 or any(not isinstance(row[c], str) for c in columns):
            continue
        aligned = sum(1 for following in matrix[index + 1:index + 8]
                      if len(_filled_columns(following) & columns) >= 2)
        if aligned >= 2:
            return index
    for index, row in enumerate(matrix[:scan]):
        if len(_filled_columns(row)) >= 2:
            return index
    return 0


def _sheet_metrics(matrix: list[list[Any]], width: int) -> dict[str, int]:
    useful_rows = 0
    non_empty_cells = 0
    used_columns: set[int] = set()
    for row in matrix:
        filled = [index for index, value in enumerate(row) if not _is_empty(value)]
        if filled:
            useful_rows += 1
            non_empty_cells += len(filled)
            used_columns.update(filled)
    return {"useful_rows": useful_rows, "non_empty_cells": non_empty_cells,
            "useful_columns": len(used_columns), "width": width}


CELL_REF_RE = re.compile(r"^([A-Z]+)(\d+)$", re.I)


def _merge_ranges(sheet_xml: ET.Element, ns: str) -> list[dict[str, int]]:
    """Plages fusionnées de l'onglet, en coordonnées 1-based du tableur.

    Une rareté écrite une fois pour toute une section vit dans une fusion
    verticale : sans ces plages, impossible de savoir à quelles lignes elle
    s'applique — et toute comparaison de ce champ serait une supposition.
    """
    ranges: list[dict[str, int]] = []
    for merge in sheet_xml.findall(f".//{ns}mergeCells/{ns}mergeCell"):
        ref = merge.get("ref") or ""
        if ":" not in ref:
            continue
        start, end = ref.split(":", 1)
        first, last = CELL_REF_RE.match(start.strip()), CELL_REF_RE.match(end.strip())
        if not first or not last:
            continue
        ranges.append({"first_row": int(first.group(2)), "last_row": int(last.group(2)),
                       "first_col": _col_index(first.group(1)) + 1,
                       "last_col": _col_index(last.group(1)) + 1})
    return ranges


def _read_xlsx(raw: bytes, sheet_limit: int = 100_000) -> list[dict[str, Any]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        shared: list[str] = []
        try:
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{ns}t")) for si in root.findall(f"{ns}si")]
        except KeyError:
            pass
        wb = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        relmap = {r.get("Id"): r.get("Target") for r in rels}
        sheets = []
        for item in wb.findall(f"{ns}sheets/{ns}sheet"):
            rid = item.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            gid = item.get("sheetId", "")
            target = (relmap.get(rid) or "").lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            try:
                sheet_xml = ET.fromstring(book.read(target))
            except KeyError:
                continue
            values: dict[int, list[Any]] = {}
            formula_cells: list[dict[str, Any]] = []
            max_col = 0
            for row_index, row in enumerate(sheet_xml.findall(f".//{ns}row"), start=1):
                vals: list[Any] = []
                cursor = 0
                for cell in row.findall(f"{ns}c"):
                    ref = cell.get("r")
                    col = _col_index(ref) if ref else cursor
                    cursor = col + 1
                    while len(vals) <= col:
                        vals.append("")
                    value = _xlsx_value(cell, shared)
                    formula = cell.findtext(f"{ns}f")
                    vals[col] = value
                    if formula is not None:
                        # openpyxl data_only=True perd la formule et rend None quand
                        # aucune valeur n'est mise en cache : on garde les deux.
                        formula_cells.append({
                            "ref": ref or f"R{row_index}C{col + 1}",
                            "value": None if _is_empty(value) else value,
                            "formula": "=" + formula if not formula.startswith("=") else formula,
                            "type": "formula"})
                    max_col = max(max_col, col + 1)
                # Les cellules fusionnées ne portent une valeur que dans leur coin
                # supérieur gauche : rien n'est propagé, donc aucun faux décompte.
                if any(not _is_empty(v) for v in vals):
                    values[int(row.get("r") or row_index)] = vals
                if len(values) >= sheet_limit:
                    break
            width = max_col
            ordered = sorted(values.items())
            matrix = [v + [""] * (width - len(v)) for _, v in ordered]
            # Les lignes entièrement vides ne sont pas conservées : l'index de
            # `matrix` ne vaut donc pas le numéro de ligne du tableur. On garde
            # la correspondance, seule façon d'exploiter les fusions ensuite.
            row_numbers = [number for number, _ in ordered]
            merges = _merge_ranges(sheet_xml, ns)
            metrics = _sheet_metrics(matrix, width)
            header_index = _header_row_index(matrix)
            headers = _unique_headers(matrix[header_index] if matrix else [], width)
            body = matrix[header_index + 1:]
            data = [dict(zip(headers, row)) for row in body]
            sheets.append({"name": item.get("name", ""), "gid": str(gid),
                           "rows": len(data), "columns": width,
                           "headers": headers,
                           "header_row": header_index + 1,
                           "raw_headers": matrix[header_index] if matrix else [],
                           "cells": matrix,
                           "useful_rows": metrics["useful_rows"],
                           "useful_columns": metrics["useful_columns"],
                           "non_empty_cells": metrics["non_empty_cells"],
                           "formula_cells": formula_cells[:200],
                           "merges": merges,
                           "row_numbers": row_numbers,
                           "data": data})
    return sheets


def read_public_sheet(url: str, *, timeout: float = 30, max_bytes: int = 32_000_000) -> dict[str, Any]:
    """Lit un Google Sheet public en XLSX, résout le gid, et renvoie le classeur complet.

    Résolution gid → onglet : le gid réel est traduit vers le vrai titre de
    l'onglet (propriété ``sheetId`` du XLSX). L'export XLSX est toujours
    utilisé (même si un gid est fourni) car c'est le seul format qui renvoie
    les titres d'onglet : sans eux, ``read_sheet_records`` ne peut pas résoudre
    le nom attendu (par ex. "ALL BRAINROTS") et la comparaison échoue.
    """
    parsed = parse_sheet_url(url)
    if not parsed:
        return {"ok": False, "error": "SHEET_NOT_FOUND",
                "detail": "L'URL fournie n'est pas une URL Google Sheets valide."}
    export = f"https://docs.google.com/spreadsheets/d/{parsed['sheet_id']}/export?format=xlsx"
    try:
        req = urllib.request.Request(export, headers={"User-Agent": "JARVIS/3.0"}, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _t0 = time.perf_counter()
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
        _t1 = time.perf_counter()
        if len(raw) > max_bytes:
            return {"ok": False, "error": "GOOGLE_SHEET_TOO_LARGE"}
        sheets = _read_xlsx(raw)
        _t2 = time.perf_counter()
        timings = {"download_ms": int((_t1 - _t0) * 1000),
                   "xlsx_parse_ms": int((_t2 - _t1) * 1000),
                   "bytes": len(raw)}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
            UnicodeDecodeError, zipfile.BadZipFile) as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403):
            code = "SHEET_ACCESS_DENIED"
        elif isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError)):
            code = "NETWORK_ERROR"
        else:
            code = "SHEET_PARSE_ERROR"
        return {"ok": False, "error": code, "detail": str(exc)[:300]}
    if not sheets:
        return {"ok": False, "error": "SHEET_EMPTY",
                "detail": "Aucun onglet n'a été trouvé dans le classeur.",
                "sheet_id": parsed["sheet_id"], "gid": parsed["gid"]}
    if re.search(r"<html|<!doctype", (raw[:2000] if isinstance(raw, (bytes, bytearray))
                                      else b"").decode("utf-8", errors="replace")[:2000], re.I):
        return {"ok": False, "error": "SHEET_ACCESS_DENIED",
                "detail": "Le serveur a renvoyé une page HTML au lieu du fichier."}
    gid = parsed["gid"]
    target = next((s for s in sheets if str(s.get("gid")) == str(gid)), None)
    if gid != "0" and target is None:
        return {"ok": False, "error": "SHEET_TAB_NOT_FOUND",
                "detail": f"Aucun onglet n'a le numéro de série {gid}.",
                "sheet_id": parsed["sheet_id"], "gid": gid,
                "available_tabs": [{"name": s.get("name", ""), "gid": s.get("gid", "")} for s in sheets]}
    selected_name = (target["name"] if target
                     else next((s["name"] for s in sheets if s.get("useful_rows", 0) > 0),
                               sheets[0]["name"]))
    result: dict[str, Any] = {
        "ok": True, "workbook": parsed["sheet_id"], "sheet_count": len(sheets),
        "sheets": sheets, "source_url": export,
        "selected_tab": selected_name,
        # Vrai uniquement si l'URL portait reellement un gid : sinon
        # `selected_tab` est un defaut de lecture, pas une demande explicite.
        "selected_tab_explicit": bool(parsed.get("gid_explicit")) and target is not None,
        # Cout reel de la lecture, mesure et non estime.
        "timings": timings,
    }
    target = next((s for s in sheets if s["name"] == selected_name), None) or sheets[0]
    result["sheet_id"] = parsed["sheet_id"]
    result["gid"] = gid
    result["tab"] = target["name"]
    result["columns"] = target.get("headers", [])
    result["row_count"] = target.get("rows", 0)
    result["column_count"] = target.get("columns", 0)
    return result
