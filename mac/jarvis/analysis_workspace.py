"""AnalysisWorkspacePayload : l'objet structuré que le front rend tel quel.

Règle fondatrice : l'interface n'est JAMAIS construite à partir du texte final
du LLM. Chaque métrique, finding et table vient de l'analyse déterministe ; le
texte génératif n'occupe qu'un champ « narrative » explicitement marqué, et
aucun finding n'est publié sans evidence ou calcul tracé.

L'architecture est volontairement indépendante de la source : un Google Sheet,
un CSV ou un PDF produisent le même payload dès lors qu'un adaptateur fournit
des régions, des colonnes profilées et des anomalies.
"""
from __future__ import annotations

import re
import time
from typing import Any

from .sheet_errors import sheet_error_message, tab_resolution_suggestion

BUILD_ID = "JARVIS_ANALYSIS_WORKSPACE_V1"

GROUNDED, CALCULATED, WARNING, MISSING_DATA, CONFLICT = (
    "GROUNDED", "CALCULATED", "WARNING", "MISSING_DATA", "CONFLICT")

# L'intent pilote la vue ouverte par défaut et l'ordre des sections.
INTENT_PATTERNS = (
    ("anomalies", r"\b(incoh[ée]rence|anomalie|erreur|probl[èe]me|doublon|manquant|"
                  r"incomplet|v[ée]rifie|contr[ôo]le|audit)\w*\b"),
    ("comparison", r"\b(compare|comparaison|diff[ée]rence|versus|vs|[ée]cart)\w*\b"),
    ("sync", r"\b(synchronis|sync|mets? [àa] jour|importe|export)\w*\b"),
    ("statistics", r"\b(statistique|moyenne|somme|total|min|max|distribution|"
                   r"r[ée]partition|graphique|courbe)\w*\b"),
    ("search", r"\b(cherche|recherche|trouve|quel|quelle|combien|o[ùu] est|liste)\w*\b"),
)

SECTION_ORDER = ["request", "summary", "metrics", "findings", "warnings",
                 "recommendations", "evidence", "tables", "statistics"]
COMPARISON_SECTION = "site_comparison"
SYNC_SECTION = "site_sync"

SECTION_LABELS = {
    "request": "Demande utilisateur", "summary": "Résumé", "metrics": "Métriques clés",
    "findings": "Points importants", "warnings": "Anomalies et incohérences",
    "recommendations": "Recommandations", "evidence": "Données et preuves utilisées",
    "tables": "Tables détectées", "statistics": "Statistiques pertinentes",
    COMPARISON_SECTION: "Comparaison site",
    SYNC_SECTION: "Synchronisation",
}

# Badge porté par chaque statut de comparaison.
COMPARISON_BADGES = {
    "NO_CHANGE": GROUNDED, "CREATE": CALCULATED, "UPDATE": WARNING,
    "CONFLICT": CONFLICT, "INVALID": WARNING, "SERVER_ONLY": MISSING_DATA,
}

# Chaque intent met en avant une section sans jamais masquer les autres.
INTENT_FOCUS = {
    "overview": "summary", "anomalies": "warnings", "comparison": "tables",
    "search": "evidence", "statistics": "statistics", "sync": "findings",
}


def detect_intent(request: str) -> str:
    """Déduit la vue à ouvrir. « overview » est le défaut sûr."""
    text = request or ""
    for name, pattern in INTENT_PATTERNS:
        if re.search(pattern, text, re.I):
            return name
    return "overview"


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("\n", " ").strip()


def _evidence_from_region(sheet: dict[str, Any], region: dict[str, Any]) -> dict[str, Any]:
    """Preuve d'existence d'un tableau : ses bornes réelles et ses vraies lignes."""
    return {
        "id": f"ev::{region['id']}",
        "kind": "cells",
        "sheet": sheet["name"],
        "region": region["title"] or region["id"],
        "location": (f"lignes {region['first_row']}-{region['last_row']}, "
                     f"colonnes {region['first_column']}-{region['last_column']}"),
        "header_row": region.get("header_row"),
        "headers": region.get("headers", []),
        "rows": [[_cell(v) for v in row] for row in region.get("sample_rows", [])],
        "computation": "",
    }


def _evidence_from_stat(sheet: dict[str, Any], region: dict[str, Any],
                        column: dict[str, Any]) -> dict[str, Any]:
    """Preuve d'un chiffre : le calcul exact, sur la colonne exacte, d'une région exacte."""
    stat = column["stats"]
    return {
        "id": f"ev::{region['id']}::{column['column']}",
        "kind": "computation",
        "sheet": sheet["name"],
        "region": region["title"] or region["id"],
        "location": (f"colonne {column['column']} « {column['name']} », "
                     f"lignes {region['first_row']}-{region['last_row']}"),
        "header_row": region.get("header_row"),
        "headers": [column["name"]],
        "rows": [],
        "computation": (f"min/max/somme/moyenne sur les {stat['count']} valeurs "
                        f"numériques de « {column['name']} » : min={stat['min']}, "
                        f"max={stat['max']}, somme={stat['sum']}, moyenne={stat['mean']}"),
        "values": stat,
    }


def _metrics(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    sheets = analysis["sheets"]
    tables = sum(s["table_count"] for s in sheets)
    rows = sum(r["row_count"] for s in sheets for r in s["regions"] if r["kind"] == "table")
    cells = sum(s.get("non_empty_cells", 0) for s in sheets)
    populated = sum(1 for s in sheets if s["regions"])
    return [
        {"key": "sheets", "label": "Onglets", "value": len(sheets),
         "detail": f"{populated} contiennent des données", "badge": CALCULATED},
        {"key": "tables", "label": "Tableaux détectés", "value": tables,
         "detail": "régions indépendantes", "badge": CALCULATED},
        {"key": "rows", "label": "Lignes de données", "value": rows,
         "detail": "hors en-têtes et titres", "badge": CALCULATED},
        {"key": "cells", "label": "Cellules non vides", "value": cells,
         "detail": "lues dans la source", "badge": GROUNDED},
        {"key": "anomalies", "label": "Anomalies", "value": len(analysis["anomalies"]),
         "detail": "structurelles, vérifiées",
         "badge": WARNING if analysis["anomalies"] else GROUNDED},
    ]


def _tables(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    tables = []
    for sheet in analysis["sheets"]:
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            tables.append({
                "id": region["id"],
                "sheet": sheet["name"],
                "title": region["title"] or region["id"],
                "rows": region["row_count"],
                "columns": region["headers"],
                "location": (f"L{region['first_row']}-{region['last_row']} · "
                             f"C{region['first_column']}-{region['last_column']}"),
                "header_row": region.get("header_row"),
                "duplicate_rows": region.get("duplicate_rows", 0),
                "sample_rows": [[_cell(v) for v in row] for row in region.get("sample_rows", [])],
                "column_profiles": region.get("column_profiles", []),
                "evidence": f"ev::{region['id']}",
            })
    return tables


def _statistics(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Seules les colonnes réellement numériques d'un tableau identifié apparaissent."""
    stats = []
    for sheet in analysis["sheets"]:
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            for column in region["column_profiles"]:
                if "stats" not in column:
                    continue
                stats.append({
                    "id": f"{region['id']}::{column['column']}",
                    "sheet": sheet["name"],
                    "table": region["title"] or region["id"],
                    "column": column["name"],
                    "stats": column["stats"],
                    "badge": CALCULATED,
                    "evidence": f"ev::{region['id']}::{column['column']}",
                })
    return stats


def _distributions(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for sheet in analysis["sheets"]:
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            for column in region["column_profiles"]:
                if column.get("distribution"):
                    out.append({"id": f"{region['id']}::dist::{column['column']}",
                                "sheet": sheet["name"],
                                "table": region["title"] or region["id"],
                                "column": column["name"],
                                "distribution": column["distribution"],
                                "badge": CALCULATED,
                                "evidence": f"ev::{region['id']}"})
    return out


def _findings(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Un finding sans evidence n'est jamais produit : la boucle part des régions."""
    findings = []
    for sheet in analysis["sheets"]:
        if sheet["table_count"] > 1:
            names = [r["title"] or r["id"] for r in sheet["regions"] if r["kind"] == "table"]
            findings.append({
                "id": f"find::{sheet['name']}::regions",
                "badge": CALCULATED,
                "title": f"{sheet['name']} contient {sheet['table_count']} tableaux distincts",
                "detail": "Ils ne sont jamais agrégés entre eux : " + ", ".join(names),
                "evidence": f"ev::{sheet['regions'][0]['id']}",
                "sheet": sheet["name"],
            })
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            label = region["title"] or region["id"]
            findings.append({
                "id": f"find::{region['id']}",
                "badge": GROUNDED,
                "title": f"{label} : {region['row_count']} lignes sur {len(region['headers'])} colonnes",
                "detail": "Colonnes : " + ", ".join(region["headers"][:8]),
                "evidence": f"ev::{region['id']}",
                "sheet": sheet["name"],
            })
    return findings


def _warnings(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = []
    for sheet in analysis["sheets"]:
        if not sheet["regions"]:
            warnings.append({"id": f"warn::{sheet['name']}::empty", "badge": MISSING_DATA,
                             "title": f"Onglet {sheet['name']} vide",
                             "detail": "Aucune cellule non vide n'a été lue.",
                             "evidence": "", "sheet": sheet["name"]})
            continue
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            label = region["title"] or region["id"]
            if region.get("duplicate_rows"):
                warnings.append({
                    "id": f"warn::{region['id']}::dup", "badge": CONFLICT,
                    "title": f"{label} : {region['duplicate_rows']} lignes strictement dupliquées",
                    "detail": "Lignes identiques sur toutes les colonnes de la région.",
                    "evidence": f"ev::{region['id']}", "sheet": sheet["name"]})
            for column in region["column_profiles"]:
                if column["generic_header"] and column["filled"]:
                    warnings.append({
                        "id": f"warn::{region['id']}::{column['column']}::header",
                        "badge": WARNING,
                        "title": f"{label} : colonne {column['column']} sans en-tête",
                        "detail": f"{column['filled']} valeurs présentes mais aucun libellé.",
                        "evidence": f"ev::{region['id']}", "sheet": sheet["name"]})
                total = column["filled"] + column["missing"]
                if (column["missing"] and column["filled"] and not column.get("grouping")
                        and column["missing"] / max(1, total) >= 0.3):
                    warnings.append({
                        "id": f"warn::{region['id']}::{column['column']}::missing",
                        "badge": MISSING_DATA,
                        "title": f"{label} : « {column['name']} » incomplète",
                        "detail": f"{column['missing']} valeurs manquantes sur {total}.",
                        "evidence": f"ev::{region['id']}", "sheet": sheet["name"]})
    return warnings


def _recommendations(analysis: dict[str, Any], warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recommandations déduites des anomalies réelles, jamais inventées."""
    out = []
    by_badge = {w["badge"] for w in warnings}
    if MISSING_DATA in by_badge:
        out.append({"id": "reco::missing", "badge": MISSING_DATA,
                    "title": "Compléter les colonnes incomplètes",
                    "detail": "Les colonnes signalées ci-dessus perdent plus de 30 % de leurs valeurs ; "
                              "toute moyenne calculée sur elles resterait partielle."})
    if CONFLICT in by_badge:
        out.append({"id": "reco::dup", "badge": CONFLICT,
                    "title": "Dédupliquer les lignes identiques",
                    "detail": "Des lignes strictement identiques faussent les décomptes et les sommes."})
    if any(w["id"].endswith("::header") for w in warnings):
        out.append({"id": "reco::header", "badge": WARNING,
                    "title": "Nommer les colonnes sans en-tête",
                    "detail": "Sans libellé, une colonne ne peut être ni typée ni agrégée de façon sûre."})
    if analysis["truth_sources"]:
        out.append({"id": "reco::source", "badge": GROUNDED,
                    "title": "Utiliser ces tableaux comme source de vérité",
                    "detail": "Tableaux structurés et chiffrés : " +
                              " ; ".join(analysis["truth_sources"][:5])})
    if not out:
        out.append({"id": "reco::ok", "badge": GROUNDED,
                    "title": "Aucune correction structurelle nécessaire",
                    "detail": "Aucune anomalie structurelle n'a été détectée sur les régions lues."})
    return out


def build_payload(*, request: str, source: dict[str, Any], analysis: dict[str, Any],
                  narrative: str = "", grounding: dict[str, Any] | None = None,
                  duration_ms: int = 0, intent: str = "") -> dict[str, Any]:
    """Construit le payload rendu par le front. Source-agnostique."""
    intent = intent or detect_intent(request)
    evidence: list[dict[str, Any]] = []
    for sheet in analysis["sheets"]:
        for region in sheet["regions"]:
            if region["kind"] != "table":
                continue
            evidence.append(_evidence_from_region(sheet, region))
            for column in region["column_profiles"]:
                if "stats" in column:
                    evidence.append(_evidence_from_stat(sheet, region, column))

    findings = _findings(analysis)
    warnings = _warnings(analysis)
    known = {item["id"] for item in evidence}
    # Garde-fou : un finding dont la preuve a disparu n'est pas affichable.
    findings = [f for f in findings if f["evidence"] in known]
    warnings = [w for w in warnings if not w["evidence"] or w["evidence"] in known]
    statistics = _statistics(analysis)
    metrics = _metrics(analysis)

    sections = [{"id": key, "label": SECTION_LABELS[key]} for key in SECTION_ORDER]
    grounding = grounding or {}
    return {
        "build": BUILD_ID,
        "intent": intent,
        "focus": INTENT_FOCUS.get(intent, "summary"),
        "request": {"text": request, "intent": intent, "at": int(time.time())},
        "source": {
            "kind": source.get("kind", "google_sheet"),
            "label": source.get("label", ""),
            "url": source.get("url", ""),
            "sheets": [s["name"] for s in analysis["sheets"]],
            "sheet_count": len(analysis["sheets"]),
        },
        "summary": {
            # Marqué explicitement : le front l'affiche comme narration, et aucune
            # métrique de l'interface n'en est extraite.
            "narrative": narrative,
            "narrative_is_generative": True,
            "headline": f"{len(analysis['sheets'])} onglets, "
                        f"{sum(s['table_count'] for s in analysis['sheets'])} tableaux détectés, "
                        f"{len(warnings)} anomalies.",
            "badge": GROUNDED if grounding.get("ok", True) else WARNING,
        },
        "metrics": metrics,
        "findings": findings,
        "warnings": warnings,
        "recommendations": _recommendations(analysis, warnings),
        "sections": sections,
        "evidence": evidence,
        "tables": _tables(analysis),
        "statistics": statistics,
        "distributions": _distributions(analysis),
        "grounding": {"ok": bool(grounding.get("ok", True)),
                      "code": grounding.get("code", ""),
                      "label": "GROUNDED" if grounding.get("ok", True) else "NON VÉRIFIÉ"},
        "duration_ms": duration_ms,
        "actions": [
            {"id": "overview", "label": "Retour au résumé", "target": "summary"},
            {"id": "data", "label": "Voir les données", "target": "tables"},
            {"id": "evidence", "label": "Preuves", "target": "evidence"},
        ],
    }


def attach_comparison(payload: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
    """Greffe la vue « Comparaison site » sur un payload existant.

    Tout vient du diff déterministe : chaque ligne porte ses valeurs Sheet et
    site d'origine plus ses deux preuves. Aucune écriture n'est proposée.
    """
    if not comparison.get("ok"):
        payload["comparison"] = {
            "ok": False,
            "code": comparison.get("error", ""),
            "error": sheet_error_message(comparison.get("error")),
            "detail": comparison.get("detail", ""),
            "tab_requested": comparison.get("tab_requested", ""),
            "tab": comparison.get("tab", ""),
            "tab_resolution": comparison.get("tab_resolution", ""),
            "available_tabs": comparison.get("available_tabs", []),
            "suggestion": tab_resolution_suggestion(comparison.get("available_tabs"),
                                                    comparison.get("tab_requested", "")),
            "interrupted": True,
        }
        return payload

    counts = comparison["counts"]
    entries = []
    evidence = list(payload["evidence"])
    for entry in comparison["entries"]:
        # Preuve Sheet : la ligne réelle, avec ses valeurs telles qu'écrites.
        if entry["evidence_sheet"]:
            evidence.append({
                "id": entry["evidence_sheet"], "kind": "cells",
                "sheet": comparison["sheet"]["tab"], "region": comparison["sheet"]["table"],
                "location": f"ligne {entry['sheet_row']} du Google Sheet",
                "header_row": comparison["sheet"].get("header_row"),
                "headers": list(entry["sheet_values"].keys()),
                "rows": [[_cell(v) for v in entry["sheet_values"].values()]],
                "computation": ""})
        # Preuve site : la ligne réelle du fichier lu en SSH lecture seule.
        if entry["evidence_site"]:
            evidence.append({
                "id": entry["evidence_site"], "kind": "cells",
                "sheet": comparison["site"]["path"], "region": "all_brainrots_sheet_data.php",
                "location": f"entrée {entry['site_index']}, ligne {entry['site_line']} du fichier",
                "header_row": None,
                "headers": list(entry["site_values"].keys()),
                "rows": [[_cell(v) for v in entry["site_values"].values()]],
                "computation": ""})
        entries.append({**entry, "badge": COMPARISON_BADGES.get(entry["status"], WARNING)})

    payload["evidence"] = evidence
    payload["comparison"] = {
        "ok": True, "build": comparison["build"],
        "sheet": comparison["sheet"], "site": comparison["site"],
        "mapping": comparison["mapping"], "counts": counts, "entries": entries,
        # Couverture visuelle : combien d'entrees portent une image reelle.
        "images": comparison.get("images", {"source": "", "matched": 0, "catalog_size": 0}),
        "write_performed": False,
        "totals": {"sheet": comparison["sheet"]["total"], "site": comparison["site"]["total"]},
    }
    if not any(s["id"] == COMPARISON_SECTION for s in payload["sections"]):
        payload["sections"].insert(1, {"id": COMPARISON_SECTION,
                                       "label": SECTION_LABELS[COMPARISON_SECTION]})
    payload["focus"] = COMPARISON_SECTION
    payload["intent"] = "comparison"
    payload["actions"].insert(0, {"id": "comparison", "label": "Comparaison site",
                                  "target": COMPARISON_SECTION})
    return payload


def comparison_digest(comparison: dict[str, Any]) -> str:
    """Réponse courte en chat ; le détail vit dans le workspace.

    Une erreur technique n'est jamais collée brute dans le chat : elle est
    traduite vers son vrai sens (onglet non résolu ≠ accès refusé).
    """
    if not comparison.get("ok"):
        code = comparison.get("error", "")
        message = sheet_error_message(code)
        if code in ("SHEET_TAB_NOT_FOUND", "SHEETTAB_NOT_FOUND", "SHEETTABNOT_FOUND",
                    "SHEET_TABLE_NOT_FOUND"):
            message = ("⚠ COMPARAISON INTERROMPUE — onglet Google Sheet non identifié. "
                       + message)
            suggestion = tab_resolution_suggestion(comparison.get("available_tabs"),
                                                   comparison.get("tab_requested", ""))
            return message + " " + suggestion
        return "Comparaison impossible : " + message
    counts = comparison["counts"]
    return (f"Comparé {comparison['sheet']['total']} lignes du Sheet à "
            f"{comparison['site']['total']} entrées du site : "
            f"{counts['NO_CHANGE']} identiques, {counts['UPDATE']} à mettre à jour, "
            f"{counts['CREATE']} manquants sur le site, {counts['CONFLICT']} ambigus, "
            f"{counts['SERVER_ONLY']} présents uniquement sur le site.\n"
            "Aucune écriture n'a été faite. Détail et preuves dans l'Analysis Workspace.")


def deterministic_narrative(analysis: dict[str, Any]) -> str:
    """Synthèse rédigée sans IA, au format d'analyse attendu.

    C'est le filet de sécurité quand la réponse du modèle est rejetée par le
    grounding. Il tient le plan en six points à partir des seuls faits calculés :
    l'utilisateur reçoit une analyse lisible, jamais un dump de cellules.
    """
    sheets = analysis["sheets"]
    tables = [(s, r) for s in sheets for r in s["regions"] if r["kind"] == "table"]
    populated = [s for s in sheets if s["regions"]]
    rows = sum(r["row_count"] for _, r in tables)
    numeric = [(s, r, c) for s, r in tables for c in r["column_profiles"] if "stats" in c]
    multi = [s for s in sheets if s["table_count"] > 1]
    notes = [s for s in sheets if s["note_regions"]]

    lines = ["1. Résumé du classeur",
             f"Le classeur compte {len(sheets)} onglets, dont {len(populated)} contiennent des "
             f"données, pour {len(tables)} tableaux indépendants et {rows} lignes de données. "
             f"{len(numeric)} colonnes sont réellement numériques et exploitables en calcul.",
             "", "2. Ce qu'il contient"]
    for sheet in sheets:
        own = [r for r in sheet["regions"] if r["kind"] == "table"]
        if not own:
            lines.append(f"- {sheet['name']} : aucun tableau structuré.")
            continue
        detail = " ; ".join(f"{r['title'] or r['id']} ({r['row_count']} lignes : "
                            f"{', '.join(r['headers'][:4])})" for r in own[:3])
        lines.append(f"- {sheet['name']} : {detail}.")
    lines += ["", "3. Qualité des données"]
    missing = [w for w in _warnings(analysis) if w["badge"] == MISSING_DATA]
    headerless = [w for w in _warnings(analysis) if w["id"].endswith("::header")]
    lines.append(f"{len(analysis['anomalies'])} anomalies structurelles ont été relevées : "
                 f"{len(missing)} colonnes incomplètes et {len(headerless)} colonnes sans en-tête. "
                 f"{len(notes)} onglets contiennent des blocs de notes, non comptés comme données.")
    lines += ["", "4. Incohérences réelles"]
    for item in _warnings(analysis)[:8]:
        lines.append(f"- {item['title']} — {item['detail']}")
    if not analysis["anomalies"]:
        lines.append("- Aucune incohérence structurelle détectée.")
    lines += ["", "5. Points intéressants"]
    if multi:
        lines.append("- Onglets contenant plusieurs tableaux distincts (jamais agrégés entre eux) : "
                     + ", ".join(f"{s['name']} ({s['table_count']})" for s in multi[:6]) + ".")
    for sheet, region, column in numeric[:5]:
        stat = column["stats"]
        lines.append(f"- {sheet['name']} / {region['title'] or region['id']} : « {column['name']} » "
                     f"va de {stat['min']} à {stat['max']} sur {stat['count']} valeurs.")
    lines += ["", "6. Recommandations"]
    for reco in _recommendations(analysis, _warnings(analysis)):
        lines.append(f"- {reco['title']} : {reco['detail']}")
    return "\n".join(lines)


def chat_digest(payload: dict[str, Any]) -> str:
    """Réponse courte en chat : le détail vit dans le workspace."""
    source = payload["source"].get("label") or "la source"
    return (f"J'ai analysé {source} : {payload['summary']['headline']}\n"
            f"J'ouvre l'Analysis Workspace avec la synthèse, les tables détectées "
            f"et les preuves de chaque chiffre.")
