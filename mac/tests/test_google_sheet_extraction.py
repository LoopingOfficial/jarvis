"""T1-T7 : extraction réelle du contenu XLSX (BUILD JARVIS_GOOGLE_SHEETS_CONTENT_EXTRACTION_V5)."""
import io
import unittest
import zipfile

from jarvis.google_sheets import _read_xlsx
from jarvis.source_grounding import workbook_summary, deterministic_workbook_report

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def build_xlsx(sheets):
    """Construit un vrai .xlsx minimal. sheets = [(nom, [[(ref, xml_cell)]])]."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as book:
        names = "".join(
            f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>'
            for i, (n, _) in enumerate(sheets))
        book.writestr("xl/workbook.xml",
                      f'<workbook xmlns="{NS}" xmlns:r="{RNS}"><sheets>{names}</sheets></workbook>')
        rels = "".join(
            f'<Relationship Id="rId{i+1}" Target="worksheets/sheet{i+1}.xml"/>'
            for i in range(len(sheets)))
        book.writestr("xl/_rels/workbook.xml.rels",
                      f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, (_, rows) in enumerate(sheets):
            body = "".join(
                f'<row r="{r+1}">' + "".join(cells) + "</row>"
                for r, cells in enumerate(rows))
            book.writestr(f"xl/worksheets/sheet{i+1}.xml",
                          f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>')
    return out.getvalue()


def text(ref, value):
    return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'


def num(ref, value):
    return f'<c r="{ref}"><v>{value}</v></c>'


def empty(ref):
    return f'<c r="{ref}"/>'


NAV = [text("A1", "HOME")] + [empty(c + "1") for c in "BCD"]


class ContentExtractionTests(unittest.TestCase):
    def sheet(self, raw, name):
        return next(s for s in _read_xlsx(raw) if s["name"] == name)

    def test_t1_all_brainrots_exposes_real_values(self):
        rows = [NAV,
                [text("A2", "Name"), text("B2", "Rarity"), text("C2", "Income"), empty("D2")],
                [text("A3", "Tralalero"), text("B3", "Secret"), num("C3", "5000"), empty("D3")],
                [text("A4", "Bombardiro"), text("B4", "Epic"), num("C4", "250"), empty("D4")]]
        sheet = self.sheet(build_xlsx([("ALL BRAINROTS", rows)]), "ALL BRAINROTS")
        self.assertEqual(sheet["useful_rows"], 4)
        self.assertEqual(sheet["non_empty_cells"], 10)
        self.assertEqual(sheet["useful_columns"], 3)
        self.assertEqual(sheet["headers"][:3], ["Name", "Rarity", "Income"])
        self.assertEqual(sheet["data"][0]["Name"], "Tralalero")
        self.assertEqual(sheet["data"][1]["Income"], 250)
        flat = [v for row in sheet["cells"] for v in row]
        self.assertIn("Bombardiro", flat)
        report = deterministic_workbook_report(workbook_summary({"sheets": [sheet]}))
        self.assertIn("Tralalero", report)
        self.assertIn("Bombardiro", report)

    def test_t2_t3_t4_metrics_are_coherent(self):
        specs = {"Traits": 3, "Wheel": 4, "Type Rates": 5}
        sheets = [(name, [NAV] + [[text(f"A{r+2}", f"{name}-{r}"), num(f"B{r+2}", r)]
                                  for r in range(count)])
                  for name, count in specs.items()]
        raw = build_xlsx(sheets)
        for name, count in specs.items():
            with self.subTest(name):
                sheet = self.sheet(raw, name)
                self.assertEqual(sheet["useful_rows"], count + 1)
                self.assertEqual(sheet["non_empty_cells"], 2 * count + 1)
                self.assertGreater(sheet["non_empty_cells"], 0)
                self.assertEqual(sheet["useful_columns"], 2)

    def test_t5_formula_keeps_formula_and_value(self):
        rows = [[text("A1", "Total"), text("B1", "Vide")],
                ['<c r="A2"><f>SUM(C1:C9)</f><v>42</v></c>',
                 '<c r="B2"><f>SUM(D1:D9)</f></c>']]
        sheet = self.sheet(build_xlsx([("Calc", rows)]), "Calc")
        cached, uncached = sheet["formula_cells"]
        self.assertEqual((cached["formula"], cached["value"], cached["type"]),
                         ("=SUM(C1:C9)", 42, "formula"))
        self.assertEqual(uncached["formula"], "=SUM(D1:D9)")
        self.assertIsNone(uncached["value"])
        self.assertIn("=SUM(C1:C9)", deterministic_workbook_report(
            workbook_summary({"sheets": [sheet]})))

    def test_t6_merged_cells_are_not_double_counted(self):
        # Dans un XLSX, seule la cellule haut-gauche d'une fusion porte la valeur.
        rows = [[text("A1", "Titre fusionne"), empty("B1"), empty("C1")],
                [text("A2", "x"), empty("B2"), empty("C2")]]
        sheet = self.sheet(build_xlsx([("Merged", rows)]), "Merged")
        self.assertEqual(sheet["non_empty_cells"], 2)
        self.assertEqual(sheet["useful_columns"], 1)
        self.assertEqual(sheet["useful_rows"], 2)

    def test_t7_empty_sheet_is_zero(self):
        rows = [[empty("A1"), empty("B1")], ['<c r="A2" t="inlineStr"><is><t>   </t></is></c>']]
        sheet = self.sheet(build_xlsx([("Vide", rows)]), "Vide")
        self.assertEqual(sheet["useful_rows"], 0)
        self.assertEqual(sheet["non_empty_cells"], 0)
        self.assertEqual(sheet["useful_columns"], 0)

    def test_no_rows_without_cells_invariant(self):
        rows = [NAV, [text("A2", "a"), text("B2", "b")]]
        raw = build_xlsx([("S", rows)])
        for sheet in workbook_summary({"sheets": _read_xlsx(raw)})["sheets"]:
            if sheet["useful_rows"] > 0:
                self.assertGreater(sheet["non_empty_cells"], 0)


if __name__ == "__main__":
    unittest.main()
