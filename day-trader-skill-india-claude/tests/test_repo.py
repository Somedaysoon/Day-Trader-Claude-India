import json
import os
import re
import sys
import unittest

from _load import ROOT

sys.path.insert(0, os.path.join(ROOT, "tools"))
import sync_common  # noqa: E402

# The India adaptation picks trades and places no orders. Disclosure monitoring is local-import based.
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-alerts", "trader-analyst", "trader-insider"]
# Skills already ported to the NSE. Add a skill here when its port lands: it is then held to the no-US-leftovers rule.
PORTED = ["trader-scanner", "trader-entry", "trader-exit", "trader-budget", "trader-risk", "trader-timemachine", "trader-alerts"]
US_TERMS = ["NYSE", "Nasdaq", "NASDAQ", "Alpaca", "New York", "Eastern", "SEC EDGAR", "place_stock_order",
            "order_class", "PDT", "pattern day"]
V1_ONLY = ["trader-copycat", "trader-screener", "trader-tax", "trader-rebalance"]
NO_ORDER_WORDS = ("places no order", "place no order", "never places an order", "never places", "places nothing",
                  "no orders")


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml")) or f == "LICENSE":
                yield os.path.join(base, f)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Repo(unittest.TestCase):
    def test_the_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_there_is_no_broker_skill(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker")))

    def test_root_layout(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git"))
        self.assertEqual(root, sorted([".claude-plugin", "CONFIGURATION.md", "LICENSE", "README.md", "common", "tests", "tools"] + SKILLS))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_every_skill_carries_the_safety_rule(self):
        for s in SKILLS:
            low = " ".join(read(os.path.join(ROOT, s, "SKILL.md")).lower().split())
            self.assertIn("not financial advice", low, s)

    def test_ported_skills_say_they_place_no_orders(self):
        for s in PORTED:
            low = " ".join(read(os.path.join(ROOT, s, "SKILL.md")).lower().split())
            self.assertTrue(any(w in low for w in NO_ORDER_WORDS), s)

    def test_ported_skills_have_no_us_market_leftovers(self):
        for s in PORTED:
            folder = os.path.join(ROOT, s)
            for base, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for f in files:
                    if not f.endswith((".py", ".md", ".json")):
                        continue
                    text = read(os.path.join(base, f))
                    for term in US_TERMS:
                        self.assertNotIn(term, text, "%s: %s" % (os.path.relpath(os.path.join(base, f), ROOT), term))
                    self.assertNotRegex(text, r"\$\d", "%s has a dollar amount" % f)

    def test_plugin_manifest_points_at_the_root_skills(self):
        plugin = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(plugin["name"], "day-trader-skill-india")
        self.assertEqual(plugin["skills"], "./")
        self.assertIn("NSE", plugin["description"])
        self.assertIn("places no orders", plugin["description"])
        market = json.loads(read(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(market["name"], "day-trader-skill-india")
        self.assertEqual(market["plugins"][0]["name"], "day-trader-skill-india")

    def test_readme_credits_the_original_and_states_the_status(self):
        readme = " ".join(read(os.path.join(ROOT, "README.md")).split())
        self.assertTrue(readme.startswith("# Day Trader skill for the Indian market"))
        for line in ("Jakeschincariol/day-trader-skill", "MIT", "places no orders", "## Status", "## Fine print",
                     "not financial advice"):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            text = read(path)
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn("\u2014", text, rel)
            self.assertNotIn("jakeschincariol" + "@" + "gmail.com", text, rel)

    def test_no_v1_only_skill_is_left_behind(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            if rel.startswith("tests" + os.sep) or rel == "README.md":
                continue
            text = read(path)
            for old in V1_ONLY:
                self.assertNotIn(old, text, rel)

    def test_every_tool_runs_on_python_3_8(self):
        folders = [os.path.join(ROOT, s) for s in SKILLS] + [os.path.join(ROOT, s, "_common") for s in sync_common.USERS]
        folders += [os.path.join(ROOT, "common"), os.path.join(ROOT, "tools")]
        for folder in folders:
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                src = read(os.path.join(folder, f))
                where = os.path.relpath(os.path.join(folder, f), ROOT)
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(", ":=", "match ",
                            "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", where)
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), where)
                    else:
                        self.assertNotIn(bad, src, where)


class SharedModules(unittest.TestCase):
    def test_every_copy_of_common_matches_the_source(self):
        self.assertEqual(sync_common.check(), [], "run: python3 tools/sync_common.py")

    def test_every_skill_that_imports_common_is_synced(self):
        importers = set()
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if f.endswith(".py") and "from _common import" in read(os.path.join(folder, f)):
                    importers.add(s)
        self.assertEqual(sorted(importers), sorted(sync_common.USERS))

    def test_the_ported_skills_are_exactly_the_synced_ones(self):
        self.assertEqual(sorted(PORTED), sorted(sync_common.USERS))

    def test_the_data_files_are_valid_and_dated(self):
        for name in ("nse_holidays.json", "sessions.json", "charges.json"):
            doc = json.loads(read(os.path.join(ROOT, "common", "data", name)))
            self.assertEqual(doc["schema_version"], 1, name)
        self.assertIs(json.loads(read(os.path.join(ROOT, "common", "data", "charges.json")))["verified"], False)
        self.assertIs(json.loads(read(os.path.join(ROOT, "common", "data", "sessions.json")))["verified"], False)


class Config(unittest.TestCase):
    def cfg(self):
        return json.loads(read(os.path.join(ROOT, "trader-budget", "config.example.json")))

    def test_day_trading_limits_ship_in_the_example_config(self):
        cfg = self.cfg()
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_trades_per_day", "min_reward_risk",
                  "max_cost_pct_of_reward", "slippage_pct"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)
        self.assertIs(cfg.get("allow_options", False), False)

    def test_the_example_config_has_no_broker_or_live_trading_keys(self):
        cfg = self.cfg()
        for k in ("paper", "broker", "pdt_guard", "max_order_usd", "sec_user_agent", "require_stop"):
            self.assertNotIn(k, cfg, k)


if __name__ == "__main__":
    unittest.main()
