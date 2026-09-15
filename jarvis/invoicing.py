"""Factures et devis : calcul déterministe, gabarit HTML, rendu PDF.

Le calcul avant tout
--------------------
Tout passe par `Decimal`, jamais par `float`. Une facture calculée en virgule
flottante finit par afficher 1 234,57 € là où le client attend 1 234,56 € :
l'écart est minuscule et l'erreur est comptable. L'arrondi est HALF_UP à deux
décimales, ligne par ligne, puis la TVA est calculée par taux sur des lignes
déjà arrondies — c'est la pratique française, et c'est reproductible.

Le rendu ensuite
----------------
Le PDF est produit par Playwright (Chromium headless), déjà présent dans le
projet : aucune dépendance supplémentaire, contrôle CSS complet, et le MÊME
HTML sert d'aperçu à l'écran. Deux règles tenues :

- une instance Playwright courte et SÉPARÉE, jamais la session partagée du
  BrowserManager : générer une facture ne doit pas détourner l'aperçu
  navigateur que l'utilisateur est en train de regarder ;
- si Playwright est absent, on le dit. On ne produit jamais un PDF de
  substitution qui ressemblerait à un vrai document.
"""
from __future__ import annotations

import html
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .config import DATA_DIR

EXPORT_DIR = Path(DATA_DIR) / "exports"

INVOICE = "invoice"
QUOTE = "quote"

KIND_LABELS = {INVOICE: "FACTURE", QUOTE: "DEVIS"}
DEFAULT_VAT_RATE = Decimal("20")
CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    """Décimal monétaire arrondi au centime (HALF_UP)."""
    if isinstance(value, Decimal):
        d = value
    else:
        # str() avant Decimal : Decimal(0.1) vaut 0.1000000000000000055…
        d = Decimal(str(value if value not in (None, "") else "0").replace(",", ".").strip())
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def rate(value: Any, default: Decimal = DEFAULT_VAT_RATE) -> Decimal:
    if value in (None, ""):
        return default
    return Decimal(str(value).replace(",", ".").strip())


def fmt_money(value: Decimal) -> str:
    """1234.5 → « 1 234,50 » (espace insécable fine, virgule décimale)."""
    sign = "-" if value < 0 else ""
    whole, _, dec = f"{abs(value):.2f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return f"{sign}{' '.join(groups)},{dec}"


def fmt_rate(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


@dataclass
class LineItem:
    description: str
    quantity: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0")
    vat_rate: Decimal = DEFAULT_VAT_RATE
    discount_pct: Decimal = Decimal("0")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LineItem":
        return cls(
            description=str(raw.get("description") or raw.get("label") or "Prestation").strip(),
            quantity=Decimal(str(raw.get("quantity", 1)).replace(",", ".")),
            unit_price=Decimal(str(raw.get("unit_price", raw.get("price", 0))).replace(",", ".")),
            vat_rate=rate(raw.get("vat_rate")),
            discount_pct=Decimal(str(raw.get("discount_pct", 0)).replace(",", ".")),
        )

    @property
    def gross_ht(self) -> Decimal:
        return money(self.quantity * self.unit_price)

    @property
    def discount(self) -> Decimal:
        return money(self.gross_ht * self.discount_pct / Decimal(100))

    @property
    def net_ht(self) -> Decimal:
        return money(self.gross_ht - self.discount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "quantity": str(self.quantity), "unit_price": str(money(self.unit_price)),
            "vat_rate": str(self.vat_rate), "discount_pct": str(self.discount_pct),
            "net_ht": str(self.net_ht),
        }


def compute_totals(lines: list[LineItem], global_discount_pct: Decimal = Decimal("0")) -> dict[str, Any]:
    """Sous-total HT, remise, TVA par taux, total TTC.

    La remise globale est répartie au prorata de chaque ligne AVANT le calcul
    de la TVA : une remise de 10 % doit réduire la TVA d'autant, sinon le
    total TTC est faux.
    """
    subtotal = money(sum((l.net_ht for l in lines), Decimal("0")))
    global_discount = money(subtotal * global_discount_pct / Decimal(100))
    net_ht = money(subtotal - global_discount)

    vat_by_rate: dict[str, Decimal] = {}
    base_by_rate: dict[str, Decimal] = {}
    for line in lines:
        key = str(line.vat_rate)
        base = line.net_ht
        if subtotal > 0 and global_discount > 0:
            base = money(base - money(global_discount * base / subtotal))
        base_by_rate[key] = base_by_rate.get(key, Decimal("0")) + base
    for key, base in base_by_rate.items():
        vat_by_rate[key] = money(base * Decimal(key) / Decimal(100))

    total_vat = money(sum(vat_by_rate.values(), Decimal("0")))
    # Le total TTC se recale sur la somme des bases réellement taxées : après
    # répartition de la remise, la somme des bases peut différer de net_ht
    # d'un centime, et c'est elle qui fait foi.
    taxed_base = money(sum(base_by_rate.values(), Decimal("0")))
    return {
        "subtotal_ht": subtotal,
        "discount": money(global_discount + money(sum((l.discount for l in lines), Decimal("0")))),
        "global_discount": global_discount,
        "net_ht": taxed_base,
        "base_by_rate": base_by_rate,
        "vat_by_rate": vat_by_rate,
        "total_vat": total_vat,
        "total_ttc": money(taxed_base + total_vat),
    }


@dataclass
class Document:
    kind: str = INVOICE
    number: str = ""
    issued_on: str = ""
    due_on: str = ""
    contact: dict[str, Any] = field(default_factory=dict)
    issuer: dict[str, Any] = field(default_factory=dict)
    lines: list[LineItem] = field(default_factory=list)
    global_discount_pct: Decimal = Decimal("0")
    notes: str = ""
    payment_terms_days: int = 30

    @property
    def totals(self) -> dict[str, Any]:
        return compute_totals(self.lines, self.global_discount_pct)

    def to_dict(self) -> dict[str, Any]:
        t = self.totals
        return {
            "kind": self.kind, "kind_label": KIND_LABELS.get(self.kind, self.kind),
            "number": self.number, "issued_on": self.issued_on, "due_on": self.due_on,
            "contact": self.contact, "lines": [l.to_dict() for l in self.lines],
            "subtotal_ht": str(t["subtotal_ht"]), "discount": str(t["discount"]),
            "net_ht": str(t["net_ht"]), "total_vat": str(t["total_vat"]),
            "total_ttc": str(t["total_ttc"]),
            "vat_by_rate": {k: str(v) for k, v in t["vat_by_rate"].items()},
        }


def next_number(kind: str, existing: list[str] | None = None) -> str:
    """Numérotation séquentielle par année : F2026-0001 / D2026-0001."""
    prefix = "F" if kind == INVOICE else "D"
    year = date.today().year
    head = f"{prefix}{year}-"
    used = [n for n in (existing or []) if str(n).startswith(head)]
    nums = []
    for n in used:
        tail = str(n)[len(head):]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{head}{(max(nums) + 1 if nums else 1):04d}"


def build_document(kind: str, contact: dict[str, Any], lines: list[dict[str, Any]], *,
                   number: str = "", issuer: dict[str, Any] | None = None,
                   global_discount_pct: Any = 0, notes: str = "",
                   payment_terms_days: int = 30, existing_numbers: list[str] | None = None) -> Document:
    items = [LineItem.from_dict(l) for l in (lines or []) if l]
    today = date.today()
    due = today + timedelta(days=max(0, int(payment_terms_days or 0)))
    return Document(
        kind=kind,
        number=number or next_number(kind, existing_numbers),
        issued_on=today.isoformat(),
        due_on=due.isoformat(),
        contact=dict(contact or {}),
        issuer=dict(issuer or {}),
        lines=items,
        global_discount_pct=Decimal(str(global_discount_pct or 0).replace(",", ".")),
        notes=notes or "",
        payment_terms_days=int(payment_terms_days or 0),
    )


# ---------------------------------------------------------------------------
# Gabarit HTML (aucune dépendance : pas de Jinja2 pour si peu)
# ---------------------------------------------------------------------------
def _fr_date(iso: str) -> str:
    try:
        y, m, d = str(iso).split("-")
        return f"{d}/{m}/{y}"
    except Exception:
        return str(iso)


def render_html(doc: Document) -> str:
    e = html.escape
    t = doc.totals
    issuer = doc.issuer or {}
    contact = doc.contact or {}
    label = KIND_LABELS.get(doc.kind, doc.kind)

    rows = "".join(
        f"<tr><td class='desc'>{e(l.description)}</td>"
        f"<td class='num'>{e(str(l.quantity.normalize()))}</td>"
        f"<td class='num'>{fmt_money(money(l.unit_price))} €</td>"
        f"<td class='num'>{fmt_rate(l.vat_rate)} %</td>"
        f"<td class='num'>{fmt_money(l.net_ht)} €</td></tr>"
        for l in doc.lines) or "<tr><td colspan='5' class='desc'>Aucune ligne.</td></tr>"

    vat_rows = "".join(
        f"<tr><td>TVA {fmt_rate(Decimal(k))} % sur {fmt_money(t['base_by_rate'][k])} €</td>"
        f"<td class='num'>{fmt_money(v)} €</td></tr>"
        for k, v in sorted(t["vat_by_rate"].items(), key=lambda kv: Decimal(kv[0])))

    discount_row = ""
    if t["discount"] > 0:
        discount_row = (f"<tr><td>Remise</td><td class='num'>-{fmt_money(t['discount'])} €</td></tr>")

    # Mentions légales : obligatoires sur une facture française.
    if doc.kind == INVOICE:
        legal = (
            f"Paiement à {doc.payment_terms_days} jours, échéance le {_fr_date(doc.due_on)}. "
            "En cas de retard de paiement, pénalités au taux de trois fois le taux d'intérêt légal, "
            "et indemnité forfaitaire pour frais de recouvrement de 40 €. Pas d'escompte pour "
            "paiement anticipé.")
    else:
        legal = (f"Devis valable 30 jours à compter du {_fr_date(doc.issued_on)}. "
                 "Bon pour accord : date, signature et mention « lu et approuvé ».")

    address_block = "<br>".join(e(part) for part in str(contact.get("address") or "").split("\n") if part)
    issuer_address = "<br>".join(e(part) for part in str(issuer.get("address") or "").split("\n") if part)

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>{e(label)} {e(doc.number)}</title>
<style>
  @page {{ size: A4; margin: 16mm 14mm; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color:#14202b;
          font-size: 10.5pt; line-height: 1.5; margin:0; }}
  .head {{ display:flex; justify-content:space-between; align-items:flex-start;
           border-bottom:2px solid #14202b; padding-bottom:12px; margin-bottom:18px; }}
  .kind {{ font-size:20pt; font-weight:700; letter-spacing:.06em; margin:0; }}
  .number {{ font-family:monospace; font-size:11pt; color:#4a6072; margin-top:2px; }}
  .issuer {{ text-align:right; font-size:9.5pt; color:#4a6072; }}
  .issuer b {{ color:#14202b; font-size:11pt; }}
  .parties {{ display:flex; justify-content:space-between; gap:24px; margin-bottom:20px; }}
  .box {{ flex:1; }}
  .box h3 {{ font-size:8.5pt; letter-spacing:.16em; text-transform:uppercase;
             color:#7d8fa0; margin:0 0 5px; font-weight:600; }}
  table.items {{ width:100%; border-collapse:collapse; margin-bottom:16px; }}
  table.items th {{ text-align:left; font-size:8.5pt; letter-spacing:.1em; text-transform:uppercase;
                    color:#7d8fa0; border-bottom:1px solid #d4dde5; padding:6px 8px; }}
  table.items td {{ padding:7px 8px; border-bottom:1px solid #eef2f6; vertical-align:top; }}
  .num {{ text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .desc {{ width:52%; }}
  .totals {{ width:52%; margin-left:auto; border-collapse:collapse; }}
  .totals td {{ padding:5px 8px; }}
  .totals td.num {{ font-variant-numeric:tabular-nums; }}
  .totals tr.grand td {{ border-top:2px solid #14202b; font-weight:700; font-size:12pt; padding-top:9px; }}
  .notes {{ margin-top:18px; padding:10px 12px; background:#f4f7fa; border-radius:4px; font-size:9.5pt; }}
  .legal {{ margin-top:16px; font-size:8pt; color:#7d8fa0; line-height:1.55; }}
</style></head><body>
<div class="head">
  <div><h1 class="kind">{e(label)}</h1><div class="number">{e(doc.number)}</div></div>
  <div class="issuer"><b>{e(str(issuer.get('name') or ''))}</b><br>{issuer_address}
    {('<br>' + e(str(issuer.get('email')))) if issuer.get('email') else ''}
    {('<br>TVA ' + e(str(issuer.get('vat_number')))) if issuer.get('vat_number') else ''}</div>
</div>
<div class="parties">
  <div class="box"><h3>Destinataire</h3>
    <b>{e(str(contact.get('name') or ''))}</b>
    {('<br>' + e(str(contact.get('company')))) if contact.get('company') else ''}
    {('<br>' + address_block) if address_block else ''}
    {('<br>' + e(str(contact.get('email')))) if contact.get('email') else ''}
    {('<br>TVA ' + e(str(contact.get('vat_number')))) if contact.get('vat_number') else ''}
  </div>
  <div class="box" style="text-align:right">
    <h3>Date d'émission</h3>{_fr_date(doc.issued_on)}
    {f"<h3 style='margin-top:10px'>Échéance</h3>{_fr_date(doc.due_on)}" if doc.kind == INVOICE else ''}
  </div>
</div>
<table class="items">
  <thead><tr><th class="desc">Désignation</th><th class="num">Qté</th><th class="num">P.U. HT</th>
    <th class="num">TVA</th><th class="num">Total HT</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<table class="totals">
  <tr><td>Sous-total HT</td><td class="num">{fmt_money(t['subtotal_ht'])} €</td></tr>
  {discount_row}
  <tr><td>Total HT</td><td class="num">{fmt_money(t['net_ht'])} €</td></tr>
  {vat_rows}
  <tr class="grand"><td>Total TTC</td><td class="num">{fmt_money(t['total_ttc'])} €</td></tr>
</table>
{f'<div class="notes">{e(doc.notes)}</div>' if doc.notes else ''}
<div class="legal">{e(legal)}</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Rendu PDF
# ---------------------------------------------------------------------------
def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def render_pdf(doc: Document, out_dir: Path | None = None) -> dict[str, Any]:
    """Écrit le PDF et renvoie ses métadonnées. Échec explicite si impossible."""
    target_dir = Path(out_dir or EXPORT_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    html_text = render_html(doc)
    stem = f"{doc.kind}_{doc.number}".replace("/", "-")
    html_path = target_dir / f"{stem}.html"
    html_path.write_text(html_text, encoding="utf-8")

    if not playwright_available():
        return {"ok": False,
                "error": ("Playwright n'est pas installé : impossible de produire le PDF. "
                          "Commande : .venv\\Scripts\\python.exe -m pip install playwright "
                          "puis .venv\\Scripts\\python.exe -m playwright install chromium."),
                "html_path": str(html_path)}

    pdf_path = target_dir / f"{stem}.pdf"
    try:
        from playwright.sync_api import sync_playwright

        # Instance courte et INDÉPENDANTE du BrowserManager : générer un
        # document ne doit jamais détourner l'aperçu navigateur en cours.
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(html_text, wait_until="load")
                page.pdf(path=str(pdf_path), format="A4", print_background=True)
            finally:
                browser.close()
    except Exception as exc:
        return {"ok": False, "error": f"Rendu PDF impossible : {exc}"[:300],
                "html_path": str(html_path)}

    t = doc.totals
    return {
        "ok": True,
        "path": str(pdf_path), "filename": pdf_path.name,
        "html_path": str(html_path),
        "bytes": pdf_path.stat().st_size,
        "kind": doc.kind, "kind_label": KIND_LABELS.get(doc.kind, doc.kind),
        "number": doc.number, "issued_on": doc.issued_on, "due_on": doc.due_on,
        "contact_name": str((doc.contact or {}).get("name") or ""),
        "contact_company": str((doc.contact or {}).get("company") or ""),
        "total_ht": str(t["net_ht"]), "total_vat": str(t["total_vat"]),
        "total_ttc": str(t["total_ttc"]),
        "total_ttc_label": fmt_money(t["total_ttc"]) + " €",
        "generated_at": time.time(),
    }
