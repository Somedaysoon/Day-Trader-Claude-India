import csv
import datetime as dt
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from _load import load

news = load("trader-news", "news")


class IndianNewsTests(unittest.TestCase):
    def test_ist_is_fixed_offset(self):
        utc = dt.datetime(2026, 11, 26, 4, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(news.to_ist(utc).strftime("%Y-%m-%d %H:%M %Z"), "2026-11-26 09:30 IST")

    def test_parse_rss_and_atom(self):
        rss = b'''<?xml version="1.0"?><rss><channel><item><title>ABC Ltd announces quarterly results</title><link>https://example.test/abc</link><description>Revenue and profit update</description><pubDate>Thu, 26 Nov 2026 09:00:00 +0530</pubDate><guid>abc-1</guid></item></channel></rss>'''
        items = news.parse_feed(rss, "NSE Announcements", "exchange")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "ABC Ltd announces quarterly results")
        self.assertEqual(items[0]["time"].isoformat(), "2026-11-26T03:30:00+00:00")
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>SEBI issues order</title><id>sebi-1</id><updated>2026-11-26T04:00:00Z</updated><link href="https://example.test/sebi" /></entry></feed>'''
        self.assertEqual(news.parse_feed(atom, "SEBI", "regulatory")[0]["link"], "https://example.test/sebi")

    def test_watchlist_matching_and_tags(self):
        items = [{"id": "1", "source": "NSE", "kind": "announcement", "category": "exchange",
                  "ticker": "", "title": "ABC Industries wins a major contract and declares dividend",
                  "summary": "Board meeting to consider quarterly results", "link": "", "time": None}]
        tagged = news.tag(items, {"ABC": "ABC Industries", "TCS": "Tata Consultancy Services"})[0]
        self.assertEqual(tagged["touches"], ["ABC"])
        for label in ("deal", "capital", "earnings"):
            self.assertIn(label, tagged["tags"])
        self.assertTrue(tagged["important"])

    def test_broad_macro_regulatory_items_are_kept_without_symbol_match(self):
        item = {"id": "x", "source": "RBI", "kind": "headline", "category": "macro",
                "ticker": "", "title": "RBI announces repo rate decision", "summary": "Monetary policy", "link": "", "time": None}
        tagged = news.tag([item], {"RELIANCE": "Reliance Industries"})
        selected = news.select(tagged, {}, dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["touches"], [])

    def test_bse_csv_import_and_ist_naive_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bse.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["Scrip Code", "Company Name", "Subject", "Category", "Broadcast Date"])
                writer.writerow(["500325", "Reliance Industries", "Board meeting for results", "Board Meeting", "2026-11-25 18:30:00"])
            items = news.parse_bse_csv(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["bse_scrip_code"], "500325")
        self.assertEqual(items[0]["time"].isoformat(), "2026-11-25T13:00:00+00:00")
        self.assertIn("Reliance Industries", items[0]["title"])

    def test_nse_and_bse_yahoo_symbol_mapping(self):
        self.assertEqual(news.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("500325.BO"), "500325.BO")

    def test_chart_price_parser(self):
        data = json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 1250.0,
            "chartPreviousClose": 1200.0, "regularMarketTime": 1795662000}}]}}).encode()
        move = news.parse_chart(data, "ABC")
        self.assertEqual(move["pct"], 4.17)
        self.assertEqual(move["ticker"], "ABC")

    def test_dedupe_and_state(self):
        a = {"id": "one", "link": "https://example.test/a", "title": "ABC Results", "time": None, "touches": ["ABC"], "tags": ["earnings"]}
        b = dict(a, id="two", touches=["TCS"], tags=["macro"])
        merged = news.dedupe([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["touches"], ["ABC", "TCS"])
        state = {"seen": {"old": "2026-01-01T00:00:00+00:00"}}
        now = dt.datetime(2026, 11, 26, tzinfo=dt.timezone.utc)
        news.remember([], state, now)
        self.assertNotIn("old", state["seen"])

    def test_main_offline_new_items_once(self):
        with tempfile.TemporaryDirectory() as td:
            offline = os.path.join(td, "offline")
            os.makedirs(offline)
            with open(os.path.join(offline, "feed-nse_announcements.xml"), "wb") as fh:
                fh.write(b'''<rss><channel><item><title>ABC Industries quarterly results declared</title><link>https://example.test/abc</link><description>Company results</description><pubDate>Thu, 26 Nov 2026 09:00:00 +0530</pubDate><guid>abc-1</guid></item></channel></rss>''')
            config = os.path.join(td, "feeds.json")
            with open(config, "w") as fh:
                json.dump({"feeds": [{"name": "NSE Announcements", "category": "exchange", "url": "https://example.test/feed"}]}, fh)
            state = os.path.join(td, "state", "news.json")
            common = ["--tickers", "ABC", "--feeds", config, "--offline", offline,
                      "--state", state, "--no-moves", "--dry-run", "--now", "2026-11-26T04:00:00Z"]
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = news.main(common + ["--json"])
            self.assertEqual(code, 0, err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["items"][0]["touches"], ["ABC"])
            self.assertIn("earnings", payload["items"][0]["tags"])
            # Non-dry run remembers the item; second check shows nothing new.
            args = [x for x in common if x != "--dry-run"]
            out2 = StringIO()
            with redirect_stdout(out2), redirect_stderr(StringIO()):
                self.assertEqual(news.main(args), 0)
            out3 = StringIO()
            with redirect_stdout(out3), redirect_stderr(StringIO()):
                self.assertEqual(news.main(args), 1)
            self.assertIn("Nothing new", out3.getvalue())

    def test_missing_feed_config_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            err = StringIO()
            with redirect_stderr(err):
                code = news.main(["--tickers", "ABC", "--feeds", os.path.join(td, "missing.json"), "--no-moves"])
            self.assertEqual(code, 2)
            self.assertIn("copy trader-news/news_feeds.example.json", err.getvalue())


if __name__ == "__main__":
    unittest.main()
