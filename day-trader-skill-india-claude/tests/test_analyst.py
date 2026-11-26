import json
import os
import tempfile
import unittest
from pathlib import Path

from _load import load

facts = load("trader-analyst", "facts")
FIXTURE = Path(__file__).parent / "fixtures" / "trader-analyst-india" / "synthetic-quarter.xml"


class IndianXbrlManifest(unittest.TestCase):
    def make_manifest(self, row):
        return [{"file": str(FIXTURE), "basis": "consolidated", "period_end": "2026-09-30",
                 "filed": "2026-11-01", "isin": "INE000A01000", **row}]

    def test_nse_ticker_and_isin_identity(self):
        reports, errors = facts.ingest(self.make_manifest({"ticker": "TEST", "exchange": ["NSE"]}))
        self.assertEqual(errors, [])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["ticker"], "TEST")
        self.assertEqual(reports[0]["company_id"], "INE000A01000")
        self.assertEqual(reports[0]["rows"][0]["revenue"], 1000000.0)
        self.assertEqual(reports[0]["rows"][0]["net_income"], 100000.0)

    def test_bse_only_company_without_nse_ticker(self):
        reports, errors = facts.ingest(self.make_manifest({
            "bse_scrip_code": "500325", "company_name": "Example BSE Ltd", "exchange": ["BSE"]
        }))
        self.assertEqual(errors, [])
        self.assertEqual(reports[0]["ticker"], "BSE:500325")
        self.assertIsNone(reports[0]["nse_symbol"])
        self.assertEqual(reports[0]["bse_scrip_code"], "500325")
        self.assertEqual(reports[0]["isin"], "INE000A01000")

    def test_isin_is_preferred_over_exchange_symbol_for_company_identity(self):
        rows = self.make_manifest({"ticker": "TEST", "exchange": ["NSE"]})
        rows += self.make_manifest({"bse_scrip_code": "500325", "exchange": ["BSE"]})
        reports, errors = facts.ingest(rows)
        self.assertEqual(errors, [])
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["company_id"], "INE000A01000")

    def test_manifest_rejects_rows_without_any_company_identifier(self):
        rows = [{"file": str(FIXTURE), "basis": "standalone", "period_end": "2026-09-30"}]
        reports, errors = facts.ingest(rows)
        self.assertEqual(reports, [])
        self.assertTrue(any("ticker/nse_symbol, bse_scrip_code or isin" in e for e in errors))

    def test_cli_can_filter_by_bse_scrip_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(json.dumps({"filings": self.make_manifest({
                "bse_scrip_code": "500325", "exchange": ["BSE"]
            })}))
            code = facts.main(["--manifest", str(manifest), "--ticker", "500325", "--json"])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
