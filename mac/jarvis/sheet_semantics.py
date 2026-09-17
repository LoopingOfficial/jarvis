"""Structuration sémantique d'un classeur : régions, colonnes typées, faits calculés.

Un onglet n'est pas un tableau : il peut contenir un tableau principal, un encart
de calcul, deux blocs côte à côte et des notes. Agréger tout cela produit des
statistiques absurdes ("Stat Name : min=14 max=67"). Ce module découpe d'abord
les régions, puis ne calcule que ce qui est réellement calculable.
"""
from __future__ import annotations

import re
from typing import Any

# En-têtes dont les valeurs sont des identifiants : même stockées en nombre,
# une somme ou un min/max n'y a aucun sens.
LABEL_HEADER_RE = re.compile(
    r"\b(name|nom|title|titre|label|libell\w*|type|category|cat[ée]gorie|rarity|raret[ée]|"
    r"tier|id|code|ref|r[ée]f[ée]rence|status|statut|owner|author|auteur|"
    r"description|note|notes|comment|commentaire|link|lien|url|emoji|icon|image)\b",
    re.I)
# Les classeurs écrivent aussi « 1.5K », « 12M », « 1 250 » ou « 3,5 % » :
# ce sont de vrais nombres, refuser de les lire fausserait tout décompte.
NUMBER_RE = re.compile(
    r"^[-+]?\s*[\d  ,]{1,24}(?:\.\d+)?\s*(?:[KkMmBbTt])?\s*[%$€x]?$")
SUFFIX_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _as_number(value: Any) -> float | None:
    """Convertit une cellule en nombre réel, sinon None. Les booléens ne comptent pas."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not NUMBER_RE.match(value.strip()):
        return None
    cleaned = value.strip().rstrip("%$€x").strip()
    scale = 1
    if cleaned and cleaned[-1].lower() in SUFFIX_SCALE:
        scale = SUFFIX_SCALE[cleaned[-1].lower()]
        cleaned = cleaned[:-1].strip()
    cleaned = cleaned.replace(" ", "").replace(" ", "")
    # La virgule sépare les milliers dès qu'un point décimal est présent ou
    # qu'elle ne précède pas exactement deux ou trois décimales.
    if "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") > 1 or re.search(r",\d{3}$", cleaned):
        cleaned = cleaned.replace(",", "")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned) * scale
    except ValueError:
        return None


def _filled(row: list[Any]) -> set[int]:
    return {i for i, v in enumerate(row) if not _is_empty(v)}


# --- Détection de régions ---------------------------------------------------
def _row_bands(cells: list[list[Any]], rows: list[int]) -> list[list[int]]:
    """Découpe une liste d'indices de lignes sur les lignes entièrement vides."""
    bands: list[list[int]] = []
    current: list[int] = []
    for index in rows:
        if _filled(cells[index]):
            current.append(index)
        elif current:
            bands.append(current)
            current = []
    if current:
        bands.append(current)
    return bands


def _column_groups(cells: list[list[Any]], rows: list[int]) -> list[list[int]]:
    """Sépare les blocs côte à côte : une colonne vide sur toute la bande coupe."""
    used = sorted({c for r in rows for c in _filled(cells[r])})
    groups: list[list[int]] = []
    for column in used:
        if groups and column == groups[-1][-1] + 1:
            groups[-1].append(column)
        else:
            groups.append([column])
    return _merge_spacer_gaps(cells, rows, groups)


def _merge_spacer_gaps(cells: list[list[Any]], rows: list[int],
                       groups: list[list[int]]) -> list[list[int]]:
    """Recolle un tableau coupé par une colonne d'espacement vide.

    Une colonne vide n'est une frontière que si les deux côtés sont chacun un
    tableau autonome. Quand un seul côté porte l'en-tête — cas courant d'une
    colonne de séparation visuelle entre « Rarity | Name » et « Income | Price »
    — les deux morceaux appartiennent au même tableau et doivent être recollés.
    """
    merged: list[list[int]] = []
    for group in groups:
        if not merged:
            merged.append(list(group))
            continue
        previous = merged[-1]
        gap = group[0] - previous[-1] - 1
        standalone = (_header_row(cells, rows, previous) is not None and len(previous) >= 2
                      and _header_row(cells, rows, group) is not None and len(group) >= 2)
        span = list(range(previous[0], group[-1] + 1))
        if gap == 1 and not standalone and _header_row(cells, rows, span) is not None:
            merged[-1] = span
        else:
            merged.append(list(group))
    return merged


def _is_label(value: Any) -> bool:
    """Un en-tête est un libellé court, pas une phrase de documentation."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(text) and len(text) <= 48 and len(text.split()) <= 6 and not text.endswith(".")


def _header_row(cells: list[list[Any]], rows: list[int], columns: list[int]) -> int | None:
    """La vraie ligne d'en-tête : des libellés texte repris par les lignes du dessous."""
    wanted = set(columns)
    for position, index in enumerate(rows):
        filled = _filled(cells[index]) & wanted
        if len(filled) < 2 or any(not isinstance(cells[index][c], str) for c in filled):
            continue
        # Une ligne de prose n'est jamais un en-tête : sans ce filtre, une note
        # de documentation devient un tableau aux colonnes-phrases.
        if not all(_is_label(cells[index][c]) for c in filled):
            continue
        if any(_as_number(cells[index][c]) is not None for c in filled):
            continue
        aligned = sum(1 for follow in rows[position + 1:position + 9]
                      if len(_filled(cells[follow]) & filled) >= max(2, len(filled) // 2))
        if aligned >= 2:
            return index
    return None


def _titles_above(cells: list[list[Any]], rows: list[int], header: int, columns: list[int]) -> list[str]:
    """Les lignes mono-cellule au-dessus d'un en-tête sont des titres, pas des données."""
    titles = []
    for index in rows:
        if index >= header:
            break
        filled = _filled(cells[index]) & set(columns)
        if len(filled) != 1:
            continue
        value = str(cells[index][min(filled)]).strip()
        # Une phrase de documentation placée au-dessus d'un tableau n'est pas
        # son titre : sans ce filtre, la région s'appelle « Additionally, … ».
        if value and len(value) <= 60 and len(value.split()) <= 8 and not value.endswith("."):
            titles.append(value)
    return titles


def _region(cells: list[list[Any]], rows: list[int], columns: list[int],
            sheet: str, ordinal: int) -> dict[str, Any]:
    header = _header_row(cells, rows, columns)
    titles = _titles_above(cells, rows, header, columns) if header is not None else []
    body = [r for r in rows if header is None or r > header]
    header_values = ([str(cells[header][c]).strip() if c < len(cells[header]) else "" for c in columns]
                     if header is not None else [])
    density = (sum(len(_filled(cells[r]) & set(columns)) for r in rows)
               / max(1, len(rows) * len(columns)))
    # Une colonne unique sans en-tête, ou des lignes de prose, ne sont pas un tableau.
    prose = all(len(_filled(cells[r]) & set(columns)) <= 1 for r in body) if body else True
    if header is not None and len(body) >= 2 and len(columns) >= 2:
        kind = "table"
    elif prose or len(columns) < 2:
        kind = "notes"
    else:
        kind = "fragment"
    return {"sheet": sheet, "id": f"{sheet}#{ordinal}", "kind": kind,
            "titles": titles,
            "title": titles[-1] if titles else "",
            "first_row": rows[0] + 1, "last_row": rows[-1] + 1,
            "first_column": columns[0] + 1, "last_column": columns[-1] + 1,
            "header_row": None if header is None else header + 1,
            "columns": columns, "raw_headers": header_values,
            "row_indexes": body, "row_count": len(body),
            "density": round(density, 3)}


def detect_regions(cells: list[list[Any]], sheet_name: str = "") -> list[dict[str, Any]]:
    """Découpe un onglet en régions indépendantes (tableaux, encarts, notes)."""
    if not cells:
        return []
    regions: list[dict[str, Any]] = []
    for band in _row_bands(cells, list(range(len(cells)))):
        for columns in _column_groups(cells, band):
            rows = [r for r in band if _filled(cells[r]) & set(columns)]
            if rows:
                regions.append(_region(cells, rows, columns, sheet_name, len(regions) + 1))
    return regions


# --- Colonnes et statistiques ----------------------------------------------
def _unique(labels: list[str], columns: list[int]) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for position, column in enumerate(columns):
        label = (labels[position] if position < len(labels) else "").strip()
        if not label:
            label = f"col_{column + 1}"
        if label in seen:
            seen[label] += 1
            label = f"{label} ({seen[label]})"
        else:
            seen[label] = 1
        out.append(label)
    return out


def region_columns(cells: list[list[Any]], region: dict[str, Any]) -> list[dict[str, Any]]:
    """Profil réel de chaque colonne d'une région : remplissage, type, stats si légitimes."""
    names = _unique(region.get("raw_headers", []), region["columns"])
    profiles = []
    for position, column in enumerate(region["columns"]):
        values = [cells[r][column] for r in region["row_indexes"]
                  if column < len(cells[r]) and not _is_empty(cells[r][column])]
        numbers = [n for n in (_as_number(v) for v in values) if n is not None]
        total = len(region["row_indexes"])
        label_like = bool(LABEL_HEADER_RE.search(names[position]))
        # Stat seulement si : colonne d'un tableau identifié, majorité de vraies
        # valeurs numériques, et en-tête compatible avec des nombres.
        numeric = (region["kind"] == "table" and not label_like
                   and len(numbers) >= 3 and len(numbers) >= 0.6 * max(1, len(values)))
        distinct = len({str(v).strip().casefold() for v in values})
        # Une colonne de regroupement n'est écrite qu'une fois par section :
        # ses cases vides sont une convention de mise en forme, pas un trou.
        grouping = (bool(values) and total >= 10 and len(values) <= 0.25 * total
                    and distinct <= 30 and distinct < len(values) + 1)
        profile = {"name": names[position], "column": column + 1,
                   "filled": len(values), "missing": max(0, total - len(values)),
                   "distinct": distinct, "grouping": grouping,
                   "kind": "number" if numeric else (
                       "grouping" if grouping else ("label" if label_like else "text")),
                   "generic_header": names[position].startswith("col_")}
        if numeric:
            profile["stats"] = {"count": len(numbers), "min": min(numbers), "max": max(numbers),
                                "sum": round(sum(numbers), 6),
                                "mean": round(sum(numbers) / len(numbers), 4)}
        elif values and profile["distinct"] <= min(12, max(2, len(values) // 2)):
            top: dict[str, int] = {}
            for value in values:
                key = str(value).strip()
                top[key] = top.get(key, 0) + 1
            profile["distribution"] = dict(sorted(top.items(), key=lambda kv: -kv[1])[:8])
        profiles.append(profile)
    return profiles


def _duplicates(cells: list[list[Any]], region: dict[str, Any]) -> int:
    seen: set[tuple[str, ...]] = set()
    duplicates = 0
    for r in region["row_indexes"]:
        key = tuple(str(cells[r][c]).strip().casefold() if c < len(cells[r]) else ""
                    for c in region["columns"])
        if not any(key):
            continue
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def structure_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """StructuredSheet : l'onglet décomposé en régions typées et profilées."""
    cells = sheet.get("cells") or []
    name = sheet.get("name", "")
    regions = []
    for region in detect_regions(cells, name):
        columns = region_columns(cells, region)
        # Une colonne d'espacement entièrement vide n'est pas une colonne de
        # données : elle ne doit ni s'afficher ni produire d'anomalie.
        kept = [i for i, c in enumerate(columns) if c["filled"] or region["kind"] != "table"]
        entry = {k: v for k, v in region.items()
                 if k not in ("row_indexes", "columns", "raw_headers")}
        entry["column_profiles"] = [columns[i] for i in kept]
        entry["headers"] = [columns[i]["name"] for i in kept]
        entry["duplicate_rows"] = _duplicates(cells, region) if region["kind"] == "table" else 0
        entry["sample_rows"] = [[cells[r][region["columns"][i]]
                                 if region["columns"][i] < len(cells[r]) else ""
                                 for i in kept]
                                for r in region["row_indexes"][:3]]
        regions.append(entry)
    tables = [r for r in regions if r["kind"] == "table"]
    return {"name": name, "regions": regions, "table_count": len(tables),
            "note_regions": [r["id"] for r in regions if r["kind"] == "notes"],
            "useful_rows": sheet.get("useful_rows", 0),
            "useful_columns": sheet.get("useful_columns", 0),
            "non_empty_cells": sheet.get("non_empty_cells", 0),
            "formulas": (sheet.get("formula_cells") or [])[:5]}


# --- Analyse déterministe ---------------------------------------------------
def analyze_workbook(workbook: dict[str, Any]) -> dict[str, Any]:
    """Faits vérifiés : chaque élément provient d'une cellule ou d'un calcul tracé."""
    sheets = [structure_sheet(s) for s in workbook.get("sheets", [])]
    facts: list[str] = []
    anomalies: list[str] = []
    sources: list[str] = []
    for sheet in sheets:
        if not sheet["regions"]:
            anomalies.append(f"Onglet {sheet['name']} : aucune cellule non vide.")
            continue
        if sheet["table_count"] > 1:
            titles = ", ".join(r["title"] or r["id"] for r in sheet["regions"] if r["kind"] == "table")
            facts.append(f"Onglet {sheet['name']} : {sheet['table_count']} tableaux distincts ({titles}).")
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            label = region["title"] or region["id"]
            facts.append(f"{sheet['name']} / {label} : {region['row_count']} lignes, "
                         f"{len(region['headers'])} colonnes ({', '.join(region['headers'][:8])}).")
            if region["duplicate_rows"]:
                anomalies.append(f"{sheet['name']} / {label} : {region['duplicate_rows']} "
                                 "lignes strictement dupliquées.")
            for column in region["column_profiles"]:
                if column["generic_header"] and column["filled"]:
                    anomalies.append(f"{sheet['name']} / {label} : colonne {column['column']} "
                                     f"sans en-tête ({column['filled']} valeurs).")
                if column["missing"] and column["filled"] and not column.get("grouping"):
                    ratio = column["missing"] / max(1, column["missing"] + column["filled"])
                    if ratio >= 0.3:
                        anomalies.append(f"{sheet['name']} / {label} : colonne « {column['name']} » "
                                         f"incomplète ({column['missing']} valeurs manquantes).")
            if region["row_count"] >= 5 and any(c["kind"] == "number" for c in region["column_profiles"]):
                sources.append(f"{sheet['name']} / {label}")
    return {"workbook": workbook.get("workbook", ""), "sheet_count": len(sheets),
            "sheets": sheets, "facts": facts, "anomalies": anomalies,
            "truth_sources": sources}


def analysis_brief(analysis: dict[str, Any], max_lines: int = 80) -> str:
    """Contexte compact remis au modèle : des faits, pas un dump de cellules."""
    lines = ["FAITS_VERIFIES (calculés depuis les cellules réelles) :"]
    lines += [f"- {fact}" for fact in analysis["facts"]]
    for sheet in analysis["sheets"]:
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            for column in region["column_profiles"]:
                if "stats" in column:
                    stat = column["stats"]
                    lines.append(f"- {region['id']} « {column['name']} » : min={stat['min']} "
                                 f"max={stat['max']} somme={stat['sum']} sur {stat['count']} valeurs.")
                elif "distribution" in column:
                    parts = ", ".join(f"{k}={v}" for k, v in column["distribution"].items())
                    lines.append(f"- {region['id']} « {column['name']} » : {parts}.")
    if analysis["anomalies"]:
        lines.append("ANOMALIES_STRUCTURELLES :")
        lines += [f"- {item}" for item in analysis["anomalies"]]
    if analysis["truth_sources"]:
        lines.append("SOURCES_DE_VERITE : " + " ; ".join(analysis["truth_sources"][:10]))
    return "\n".join(lines[:max_lines])


def deterministic_analysis_report(analysis: dict[str, Any]) -> str:
    """Rapport calculé sans IA : dernier filet si le grounding échoue."""
    lines = ["Aperçu déterministe du classeur :"]
    lines.append(f"- {analysis['sheet_count']} onglets analysés, "
                 f"{sum(s['table_count'] for s in analysis['sheets'])} tableaux détectés.")
    lines.append(analysis_brief(analysis))
    return "\n".join(lines)
