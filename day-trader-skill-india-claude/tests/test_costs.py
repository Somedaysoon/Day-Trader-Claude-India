"""common/in_costs.py. The charge RATES are placeholders (verified: false); these tests check the
arithmetic, with expected figures worked out by hand from those placeholder rates."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal as Dc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "common"))
import in_costs as c  # noqa: E402


class RoundTrip(unittest.TestCase):
    def test_a_small_trade_is_priced_by_the_percent_cap(self):
        # buy 100 x 100.00 = 10,000; sell 100 x 101.00 = 10,100
        r = c.round_trip(100.0, 101.0, 100)
        self.assertEqual(r["brokerage"], Dc("6.03"))        # 3.00 + 3.03 (0.03% of each leg, under the 20 cap)
        self.assertEqual(r["stt"], Dc("2.53"))              # 0.025% of 10,100 = 2.525
        self.assertEqual(r["exchange_txn"], Dc("0.60"))     # 0.00297% of 20,100 = 0.59697, half up
        self.assertEqual(r["sebi"], Dc("0.02"))
        self.assertEqual(r["stamp"], Dc("0.30"))            # 0.003% of 10,000
        self.assertEqual(r["gst"], Dc("1.20"))              # 18% of (6.03 + 0.60 + 0.02) = 1.197
        self.assertEqual(r["total"], Dc("10.68"))

    def test_a_large_trade_hits_the_flat_brokerage_cap(self):
        # buy 1,000 x 500 = 5,00,000; sell 1,000 x 505 = 5,05,000
        r = c.round_trip(500, 505, 1000)
        self.assertEqual(r["brokerage"], Dc("40.00"))       # 20 + 20: the percent would be 150 and 151.50
        self.assertEqual(r["stt"], Dc("126.25"))
        self.assertEqual(r["exchange_txn"], Dc("29.85"))    # 0.00297% of 10,05,000 = 29.8485
        self.assertEqual(r["sebi"], Dc("1.01"))             # Rs 10 per crore of 10,05,000 = 1.005, half up
        self.assertEqual(r["stamp"], Dc("15.00"))
        self.assertEqual(r["gst"], Dc("12.75"))             # 18% of 70.86 = 12.7548
        self.assertEqual(r["total"], Dc("224.86"))

    def test_flat_fee_dominates_a_small_order(self):
        # Rs 3,000 each way with a flat Rs 20 would be 1.3 percent: the percent cap keeps it at Rs 0.90 a leg.
        r = c.round_trip(100, 100, 30)
        self.assertEqual(r["brokerage"], Dc("1.80"))
        r = c.round_trip(2000, 2000, 30)                    # Rs 60,000 a leg: the flat cap bites (0.03% = 18)
        self.assertEqual(r["brokerage"], Dc("36.00"))

    def test_a_scratch_trade_still_costs_money(self):
        self.assertGreater(c.round_trip(100, 100, 100)["total"], 0)

    def test_quantity_must_be_positive(self):
        with self.assertRaises(c.CostError):
            c.round_trip(100, 101, 0)


class Net(unittest.TestCase):
    def test_slippage_and_charges_shrink_a_2r_trade(self):
        n = c.net_outcomes(100.0, 99.0, 102.0, 100, 0.05)
        # fills: buy 100.05, stop sells 98.9505, target sells 101.949
        self.assertEqual(n["loss_at_stop"], Dc("120.48"))       # 109.95 price loss + 10.53 charges
        self.assertEqual(n["gain_at_target"], Dc("179.17"))     # 189.90 price gain - 10.73 charges
        self.assertEqual(n["net_reward_risk"], Dc("1.49"))      # not the 2.00 on the screen
        self.assertEqual((n["charges_at_stop"], n["charges_at_target"]), (Dc("10.53"), Dc("10.73")))
        self.assertFalse(n["verified"])

    def test_without_slippage_the_gap_is_only_charges(self):
        n = c.net_outcomes(100.0, 99.0, 102.0, 100, 0)
        self.assertEqual(n["loss_at_stop"], Dc("100") + n["charges_at_stop"])
        self.assertLess(n["net_reward_risk"], Dc("2.00"))


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cwd = os.getcwd()
        self.addCleanup(os.chdir, self.cwd)

    def write(self, path, doc):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def test_the_bundled_file_is_marked_unverified(self):
        cfg = c.load()
        self.assertIs(cfg["verified"], False)
        self.assertIn("PLACEHOLDER", cfg["source"])

    def test_your_own_file_replaces_the_placeholders(self):
        base = c.load()
        mine = {k: v for k, v in base.items() if not k.startswith("_")}
        mine.update(verified=True, brokerage={"flat": 0.0, "pct": 0.0})
        os.chdir(self.tmp)
        self.write(os.path.join(self.tmp, "trader", "charges.json"), mine)
        cfg = c.load()
        self.assertTrue(cfg["verified"])
        self.assertEqual(c.round_trip(100, 101, 100, cfg)["brokerage"], Dc("0"))
        self.assertTrue(c.net_outcomes(100, 99, 102, 100, 0, cfg)["verified"])

    def test_an_explicit_path_wins_and_a_missing_key_is_named(self):
        p = os.path.join(self.tmp, "x.json")
        self.write(p, {"brokerage": {"flat": 20, "pct": 0.03}})
        with self.assertRaises(c.CostError) as cm:
            c.load(p)
        self.assertIn("stt_pct_sell", str(cm.exception))

    def test_unreadable_file(self):
        with self.assertRaises(c.CostError):
            c.load(os.path.join(self.tmp, "nope.json"))


if __name__ == "__main__":
    unittest.main()
