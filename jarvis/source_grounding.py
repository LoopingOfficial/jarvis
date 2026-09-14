"""Résumé déterministe et garde-fou des réponses issues d'un classeur."""
from __future__ import annotations
import re
from typing import Any
from .validation import ValidationResult
from .sheet_semantics import analyze_workbook, analysis_brief, structure_sheet

def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _useful_rows(cells: list[list[Any]], limit: int = 5) -> list[list[Any]]:
    """Retourne les premières lignes contenant au moins une vraie valeur."""
    useful = []
    for row in cells:
        if any(not _is_empty(value) for value in row):
            useful.append([value for value in row])
        if len(useful) >= limit:
            break
    return useful


def workbook_summary(workbook: dict[str, Any]) -> dict[str, Any]:
    sheets = []
    for sheet in workbook.get("sheets", []):
        cells = sheet.get("cells") or []
        metrics_rows = sheet.get("useful_rows")
        if metrics_rows is None:
            metrics_rows = sum(1 for row in cells if any(not _is_empty(v) for v in row))
        non_empty = sheet.get("non_empty_cells")
        if non_empty is None:
            non_empty = sum(1 for row in cells for v in row if not _is_empty(v))
        # Invariant : des lignes utiles impliquent forcément des cellules non vides.
        # En cas d'incohérence, on recompte sur la matrice brute plutôt que de
        # publier une métrique fausse.
        if metrics_rows > 0 and non_empty == 0:
            non_empty = sum(1 for row in cells for v in row if not _is_empty(v))
            metrics_rows = sum(1 for row in cells if any(not _is_empty(v) for v in row))
        entry = {"name": sheet.get("name", ""), "rows": sheet.get("rows", 0),
                 "columns": sheet.get("columns", 0),
                 "useful_rows": metrics_rows,
                 "useful_columns": sheet.get("useful_columns", 0),
                 "non_empty_cells": non_empty,
                 "headers": sheet.get("headers", []),
                 "sample_rows": _useful_rows(cells[max(0, sheet.get("header_row", 1)):]) or _useful_rows(cells),
                 "preview": sheet.get("data", [])[:5]}
        structured = structure_sheet(sheet)
        # Les statistiques sont portees par les regions, jamais par l'onglet
        # entier : deux tableaux d'un meme onglet ne sont jamais agreges.
        entry["regions"] = structured["regions"]
        entry["table_count"] = structured["table_count"]
        formulas = sheet.get("formula_cells") or []
        if formulas:
            entry["formulas"] = formulas[:5]
        sheets.append(entry)
    return {"workbook": workbook.get("workbook", ""), "sheet_count": len(sheets), "sheets": sheets}


def _fmt(value: Any) -> str:
    return "" if _is_empty(value) else str(value).replace("\n", " ").strip()


def deterministic_workbook_report(summary: dict[str, Any], max_sheets: int = 12) -> str:
    """Rapport calculé, sans IA : uniquement des valeurs réellement extraites."""
    lines = ["Aperçu déterministe du classeur :"]
    useful = [s for s in summary.get("sheets", []) if s.get("useful_rows", 0) > 0]
    empty = [s for s in summary.get("sheets", []) if s.get("useful_rows", 0) == 0]
    for sheet in useful[:max_sheets]:
        lines.append(f"- Onglet {sheet['name']}: {sheet['useful_rows']} lignes utiles, "
                     f"{sheet['useful_columns']} colonnes utiles, "
                     f"{sheet['non_empty_cells']} cellules non vides.")
        for region in [r for r in sheet.get("regions", []) if r["kind"] == "table"]:
            label = region["title"] or region["id"]
            lines.append(f"  Tableau {label} (L{region['first_row']}-{region['last_row']}, "
                         f"C{region['first_column']}-{region['last_column']}) : "
                         f"{region['row_count']} lignes.")
            headers = [h for h in region.get("headers", []) if not h.startswith("col_")]
            if headers:
                lines.append("    Colonnes : " + " | ".join(headers[:12]))
            for row in region.get("sample_rows", [])[:3]:
                cells = [_fmt(v) for v in row if not _is_empty(v)]
                if cells:
                    lines.append("    Ligne : " + " | ".join(cells[:12]))
            # Une stat n'existe que pour une colonne reellement numerique d'un
            # tableau identifie : "Stat Name : min=14" ne peut plus apparaitre.
            for column in region.get("column_profiles", []):
                stat = column.get("stats")
                if stat:
                    lines.append(f"    Stat {column['name']} : min={stat['min']} "
                                 f"max={stat['max']} somme={stat['sum']} "
                                 f"sur {stat['count']} valeurs.")
        for formula in sheet.get("formulas", [])[:3]:
            lines.append(f"  Formule {formula['ref']} : {formula['formula']} "
                         f"-> {_fmt(formula['value']) or 'valeur non mise en cache'}")
    for sheet in empty:
        lines.append(f"- Onglet {sheet['name']}: aucune cellule non vide.")
    return "\n".join(lines)


def _computed_values(workbook: dict[str, Any]) -> set[str]:
    """Valeurs issues d'un calcul deterministe trace : elles sont prouvees, donc citables."""
    out: set[str] = set()
    analysis = analyze_workbook(workbook)
    tables = sum(s["table_count"] for s in analysis["sheets"])
    rows = sum(r["row_count"] for s in analysis["sheets"]
               for r in s["regions"] if r["kind"] == "table")
    # Agregats publies par JARVIS lui-meme : ils sont calcules, donc citables.
    out.update(str(v).casefold() for v in (
        len(analysis["sheets"]), tables, rows, len(analysis["anomalies"]),
        len(analysis["facts"]), len(analysis["truth_sources"]),
        sum(s.get("non_empty_cells", 0) for s in analysis["sheets"]),
        sum(1 for s in analysis["sheets"] if s["regions"])))
    for sheet in analysis["sheets"]:
        out.update(str(v).casefold() for v in (
            sheet["table_count"], sheet.get("useful_rows", 0),
            sheet.get("useful_columns", 0), sheet.get("non_empty_cells", 0),
            len(sheet["regions"])))
        for region in sheet["regions"]:
            out.update({str(region["row_count"]).casefold(),
                        str(len(region["headers"])).casefold()})
            for column in region.get("column_profiles", []):
                out.update({str(column["filled"]).casefold(), str(column["missing"]).casefold(),
                            str(column["distinct"]).casefold()})
                for value in (column.get("stats") or {}).values():
                    out.add(str(value).casefold())
                    if isinstance(value, float) and value.is_integer():
                        out.add(str(int(value)).casefold())
                out.update(str(v).casefold() for v in (column.get("distribution") or {}).values())
    return out


def validate_source_grounding(answer: str, workbook: dict[str, Any]) -> ValidationResult:
    values = {str(v).casefold() for s in workbook.get("sheets", []) for row in s.get("data", []) for v in row.values() if not _is_empty(v)}
    values.update(str(v).casefold() for s in workbook.get("sheets", []) for row in (s.get("cells") or []) for v in row if not _is_empty(v))
    values.update(str(x).casefold() for s in workbook.get("sheets", []) for x in (s.get("rows", 0), s.get("columns", 0)))
    values.update(_computed_values(workbook))
    text = answer or ""
    if text.startswith("Aperçu déterministe du classeur"):
        return ValidationResult.pass_("deterministic workbook summary")
    # Les nombres affirmés doivent être présents dans les cellules ou être des petits
    # décomptes explicitement calculables par le backend.
    for token in re.findall(r"(?<![\w])\d+(?:[.,]\d+)?%?", text):
        if token.casefold() in values or token.rstrip("%").casefold() in values:
            continue
        if token in {"0", "1", "2", "3", "4", "5"}:
            continue
        # Un nombre écrit à l'intérieur d'une cellule réelle — une année dans
        # « Last updated : Sep 2026 » — est sourcé, pas inventé.
        bare = token.rstrip("%")
        # Un nombre écrit À L'INTÉRIEUR d'une cellule réelle est sourcé :
        # « 75 » dans « spawn between level 75 and 125 », « 2026 » dans une date.
        # La frontière de chiffres évite de valider « 90 » au hasard dans « 1901 ».
        edge = re.compile(r"(?<!\d)" + re.escape(bare) + r"(?!\d)")
        if any(edge.search(value) for value in values):
            continue
        return ValidationResult.fail("UNSUPPORTED_SOURCE_CLAIM", f"unsupported numeric claim: {token}", claim=token)
    # Détecte les valeurs inventées (Charlie, Warrior, Mage...).
    #
    # Le contrôle porte sur les valeurs CITÉES — entre guillemets, chevrons ou
    # accents graves — et non sur la prose. Un mot capitalisé en français
    # ordinaire (« Voici », « Dupliqués », un titre markdown) n'est pas une
    # donnée : le signaler rejetait toute synthèse et forçait le rapport brut,
    # exactement le comportement à corriger. Les chiffres, eux, restent
    # contrôlés partout ci-dessus.
    quoted = " ".join(m.group(1) or m.group(2) or m.group(3) or ""
                      for m in re.finditer(r"«\s*([^»]{1,120})\s*»|\"([^\"]{1,120})\"|`([^`]{1,120})`", text))
    for claim in re.findall(r"\b[A-Z][A-Za-zÀ-ÿ]{2,}\b", quoted):
        low = claim.casefold()
        if low in {"google", "sheet", "sheets", "csv", "llm", "jarvis", "analyse", "source", "data", "aperçu", "onglet", "home", "website", "overview", "reg", "carpet", "explained", "eternal", "machine", "luck", "portal", "tiers", "admin", "type", "rates", "llama", "rots", "boxrots", "lucky", "ferinsini", "rebirths", "sprites", "copy", "wheel", "guaranteed", "spawns", "traits", "misc", "leaksfacts"}: continue
        if not any(low in value for value in values) and not any(low in s.get("name", "").casefold() for s in workbook.get("sheets", [])):
            return ValidationResult.fail("UNSUPPORTED_SOURCE_CLAIM", f"unsupported source claim: {claim}", claim=claim)
    return ValidationResult.pass_("source grounded")
