"""common/in_instruments.py and in_fmt.py. The NSE-format files here are SYNTHETIC: they follow the
published layouts from memory, not a real download."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import in_fmt as fmt  # noqa: E402
import in_instruments as ins  # noqa: E402

EQUITY = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "AAA,Alpha Industries Limited,EQ,01-JAN-1995,10,1,INE000A01011,10\n"
    "BBB,Beta  Motors Ltd,EQ,02-FEB-2001,2,1,INE000B01012,2\n"
    "M&M,Mahindra and Mahindra Ltd,EQ,03-MAR-1990,5,1,INE000C01013,5\n"
    "CCC,Gamma Trade-for-Trade Ltd,BE,04-APR-2010,10,1,INE000D01014,10\n"
    "DDD,Delta SME Ltd,SM,05-MAY-2022,10,1000,INE000E01015,10\n"
    ",blank symbol,EQ,,,,,\n"
)
INDEX = (
    "Company Name,Industry,Symbol,Series,ISIN Code\n"
    "Alpha Industries Limited,Metals,AAA,EQ,INE000A01011\n"
    "Mahindra and Mahindra Ltd,Auto,M&M,EQ,INE000C01013\n"
    "Gamma Trade-for-Trade Ltd,Misc,CCC,BE,INE000D01014\n"
)
FNO = (
    "Contract Details,,,,\n"
    "UNDERLYING,SYMBOL,JAN-26,FEB-26,MAR-26\n"
    "Nifty 50,NIFTY,75,75,75\n"
    "Alpha Industries Limited,AAA,500,500,500\n"
    "Mahindra and Mahindra Ltd,M&M,350,350,350\n"
)


class Symbols(unittest.TestCase):
    def test_yahoo_symbol_round_trip(self):
        self.assertEqual(ins.yahoo_symbol("reliance"), "RELIANCE.NS")
        self.assertEqual(ins.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(ins.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(ins.yahoo_symbol("TCS", "BSE"), "TCS.BO")
        self.assertEqual(ins.plain_yahoo("m&m"), "M&M.NS")
        self.assertEqual(ins.from_yahoo("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(ins.from_yahoo("m&m.ns"), "M&M")
        self.assertEqual(ins.from_yahoo("^NSEI"), "^NSEI")


class Files(unittest.TestCase):
    def test_equity_list_keeps_only_eq_and_counts_the_rest(self):
        kept, counts = ins.parse_equity_list(EQUITY)
        self.assertEqual([k["symbol"] for k in kept], ["AAA", "BBB", "M&M"])
        self.assertEqual(counts, {"rows": 5, "EQ": 3, "BE": 1, "SM": 1})
        self.assertEqual(kept[1]["name"], "Beta Motors Ltd")      # double space collapsed
        self.assertEqual(kept[0]["isin"], "INE000A01011")

    def test_header_spaces_and_a_bom_are_tolerated(self):
        kept, _ = ins.parse_equity_list("\ufeff" + EQUITY)
        self.assertEqual(len(kept), 3)

    def test_wrong_file_is_a_plain_error(self):
        with self.assertRaises(ins.InstrumentError) as cm:
            ins.parse_equity_list("<html>blocked</html>")
        self.assertIn("equity list", str(cm.exception))

    def test_index_list_drops_non_eq(self):
        self.assertEqual(ins.parse_index_list(INDEX), {"AAA", "M&M"})

    def test_fno_list_reads_the_symbol_column_under_a_title_row(self):
        self.assertEqual(ins.parse_fno_list(FNO), {"NIFTY", "AAA", "M&M"})

    def test_group_follows_the_fno_list(self):
        fno = ins.parse_fno_list(FNO)
        self.assertEqual(ins.group_of("AAA", fno), "cas")
        self.assertEqual(ins.group_of("BBB", fno), "other")
        self.assertEqual(ins.group_of("BBB", None), "any")


class Ticks(unittest.TestCase):
    def test_nearest_up_down_on_a_5_paise_tick(self):
        self.assertEqual(ins.round_to_tick(100.02), 100.0)
        self.assertEqual(ins.round_to_tick(100.03), 100.05)
        self.assertEqual(ins.round_to_tick(100.01, direction="up"), 100.05)
        self.assertEqual(ins.round_to_tick(100.04, direction="down"), 100.0)

    def test_exact_ticks_do_not_drift_in_floating_point(self):
        for p in (0.35, 1234.55, 99.95, 2999.85):
            self.assertEqual(ins.round_to_tick(p, direction="up"), p)
            self.assertEqual(ins.round_to_tick(p, direction="down"), p)

    def test_other_ticks(self):
        self.assertEqual(ins.round_to_tick(10.123, 0.01), 10.12)
        with self.assertRaises(ins.InstrumentError):
            ins.round_to_tick(10, 0)


class Formatting(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(fmt.inr(999), "Rs 999.00")
        self.assertEqual(fmt.inr(1000), "Rs 1,000.00")
        self.assertEqual(fmt.inr(100000), "Rs 1,00,000.00")
        self.assertEqual(fmt.inr(1234567.5), "Rs 12,34,567.50")
        self.assertEqual(fmt.inr(-2500.5), "-Rs 2,500.50")
        self.assertEqual(fmt.inr(12345678, 0), "Rs 1,23,45,678")

    def test_crore_lakh_pct(self):
        self.assertEqual(fmt.crore(50000000), "5.00 crore")
        self.assertEqual(fmt.lakh(250000), "2.50 lakh")
        self.assertEqual(fmt.pct(0.553), "+0.55%")
        self.assertEqual(fmt.pct(-1.2, 1), "-1.2%")


if __name__ == "__main__":
    unittest.main()
