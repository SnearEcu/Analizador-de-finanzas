from __future__ import annotations

import unittest
from pathlib import Path

from app.parsers import parse_statement


ROOT = Path(__file__).resolve().parent.parent


class ParserTests(unittest.TestCase):
    def test_diners_parser_extracts_summary_and_movements(self):
        parsed = parse_statement(ROOT / "Estado de Cuenta IDVIXXXXXXXX3325.pdf")
        self.assertEqual(parsed.institution, "diners")
        self.assertEqual(parsed.parser_name, "diners_pdf_text")
        self.assertEqual(str(parsed.period_start), "2026-02-05")
        self.assertEqual(str(parsed.period_end), "2026-03-04")
        self.assertEqual(str(parsed.payment_due_date), "2026-03-20")
        self.assertAlmostEqual(parsed.min_payment, 811.67, places=2)
        self.assertGreaterEqual(len(parsed.movements), 20)

    def test_internacional_parser_extracts_primary_and_additional_card(self):
        parsed = parse_statement(ROOT / "Estado_de_Cuenta_TC_Feb-2026.pdf")
        self.assertEqual(parsed.institution, "internacional")
        self.assertEqual(parsed.parser_name, "internacional_pdf_text")
        self.assertEqual(str(parsed.statement_date), "2026-03-17")
        self.assertAlmostEqual(parsed.min_payment, 10.0, places=2)
        self.assertGreaterEqual(len(parsed.movements), 10)
        owners = {movement.owner_name for movement in parsed.movements}
        self.assertIn("Bryan Andres Ortega Llanos", owners)
        self.assertIn("Sheerlaynataly Chiriboga Pozo", owners)

    def test_pacifico_parser_uses_ocr(self):
        parsed = parse_statement(ROOT / "ESTADOCUENTA202602.pdf")
        self.assertEqual(parsed.institution, "pacifico")
        self.assertEqual(parsed.parser_name, "pacifico_pdf_ocr")
        self.assertEqual(parsed.owner_name, "Bryan Ortega Llanos")
        self.assertAlmostEqual(parsed.total_payment, 3.65, places=2)
        self.assertGreaterEqual(len(parsed.movements), 1)


if __name__ == "__main__":
    unittest.main()
