import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
import io

from _load import load

insider = load("trader-insider", "insider")


class Normalize(unittest.TestCase):
    def test_insider_csv_columns_normalize(self):
        row = {
            "Company Name": "Example Ltd", "Symbol": "EXAMPLE", "Name of Person": "A Director",
            "Category of Person": "Director", "Type": "Buy", "No. of Shares": "10,000",
            "Value of Securities": "₹12,50,000", "Date of Acquisition": "10/10/2026",
            "Submission Date": "12/10/2026", "Source URL": "https://example.com/filing"
        }
        e = insider.normalize(row, "insider", "NSE")
        self.assertEqual(e["kind"], "insider_transaction")
        self.assertEqual(e["symbol"], "EXAMPLE")
        self.assertEqual(e["quantity"], 10000)
        self.assertEqual(e["value"], 1250000)
        self.assertEqual(e["trade_date"], "2026-10-10")
        self.assertEqual(e["filing_date"], "2026-10-12")
        self.assertEqual(e["exchange"], "NSE")

    def test_shareholding_date_is_snapshot_date(self):
        e = insider.normalize({"Name of Shareholder": "Vijay Kedia", "Symbol": "ABC",
                               "Date": "30/09/2026", "Holding %": "2.5", "No. of Shares": "250000"}, "shareholding")
        self.assertEqual(e["as_on_date"], "2026-09-30")
        self.assertIsNone(e["trade_date"])
        self.assertEqual(e["holding_pct"], 2.5)
        self.assertEqual(e["holding_shares"], 250000)
        self.assertIsNone(e["quantity"])

    def test_duplicate_fingerprint_ignores_source_url(self):
        a = insider.normalize({"Symbol": "ABC", "Person": "X", "Date": "10/10/2026",
                               "Action": "Buy", "Quantity": "100", "Source URL": "https://a"}, "bulk")
        b = insider.normalize({"Symbol": "ABC", "Person": "X", "Date": "10/10/2026",
                               "Action": "Buy", "Quantity": "100", "Source URL": "https://b"}, "bulk")
        self.assertEqual(a["event_id"], b["event_id"])


class Storage(unittest.TestCase):
    def test_import_deduplicates_and_report_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "events.csv")
            with open(source, "w", encoding="utf-8") as f:
                f.write("Person,Company,Symbol,Action,Quantity,Trade Date,Source URL\n")
                f.write("Vijay Kedia,Example Ltd,ABC,Buy,1000,10/10/2026,https://example.com\n")
            log = os.path.join(tmp, "events.jsonl")
            class Args: pass
            args = Args(); args.file = source; args.kind = "bulk"; args.exchange = "NSE"; args.log = log
            insider.import_rows(args)
            insider.import_rows(args)
            rows = insider.load_log(log)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["person"], "Vijay Kedia")

    def test_watchlist_starter_set_and_rakesh_is_historical(self):
        watch = insider.DEFAULT_WATCH
        names = {x["name"] for x in watch}
        self.assertTrue({"Vijay Kedia", "Rakesh Jhunjhunwala", "Rekha Jhunjhunwala",
                         "Dolly Khanna", "Ashish Kacholia", "Mukul Agrawal"}.issubset(names))
        r = next(x for x in watch if x["name"] == "Rakesh Jhunjhunwala")
        self.assertTrue(r["historical_only"])

    def test_snapshot_changes_and_overlap_are_labelled_as_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "events.jsonl")
            watch = os.path.join(tmp, "watch.json")
            with open(watch, "w", encoding="utf-8") as f:
                json.dump({"investors": [{"name": "Vijay Kedia", "aliases": []},
                                          {"name": "Ashish Kacholia", "aliases": []}]}, f)
            records = [
                insider.normalize({"Person": "Vijay Kedia", "Symbol": "ABC", "Date": "30/06/2026",
                                   "No. of Shares": "100000", "Holding %": "1.0"}, "shareholding"),
                insider.normalize({"Person": "Vijay Kedia", "Symbol": "ABC", "Date": "30/09/2026",
                                   "No. of Shares": "150000", "Holding %": "1.5"}, "shareholding"),
                insider.normalize({"Person": "Ashish Kacholia", "Symbol": "ABC", "Date": "30/09/2026",
                                   "No. of Shares": "200000", "Holding %": "2.0"}, "shareholding"),
            ]
            with open(log, "w", encoding="utf-8") as f:
                for row in records: f.write(json.dumps(row) + "\n")
            class Args: pass
            args = Args(); args.log = log; args.watchlist = watch; args.minimum = 2
            out = io.StringIO()
            with redirect_stdout(out): insider.changes(args)
            self.assertIn("+50000", out.getvalue())
            self.assertIn("Snapshot comparison only", out.getvalue())
            out = io.StringIO()
            with redirect_stdout(out): insider.overlaps(args)
            self.assertIn("Vijay Kedia", out.getvalue())
            self.assertIn("Ashish Kacholia", out.getvalue())
            self.assertIn("not evidence of same-day buying", out.getvalue())


if __name__ == "__main__":
    unittest.main()
