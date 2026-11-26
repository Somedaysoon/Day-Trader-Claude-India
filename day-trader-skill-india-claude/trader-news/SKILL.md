---
name: trader-news
description: >-
  Indian Market News Monitor. Tracks official RBI, SEBI, PIB and configured NSE
  RSS feeds, imports manually downloaded BSE corporate-announcement CSV files,
  matches news to the NSE watchlist and active trade plan, tags important events,
  and adds supplementary price-move context. Use for watchlist news, market
  briefings, regulatory developments, corporate announcements and event checks.
argument-hint: "[symbols]"
---

# trader-news

A chart can look technically sound while a result, exchange filing, regulatory
order or policy announcement changes the context. This skill checks configured
Indian public feeds, matches company names and symbols against your watchlist,
and reports only items that are new since the previous check.

It is a **signal-support tool only**. It does not place, modify or cancel orders.
You make all trading decisions and execute them manually.

## First-time setup

Copy the example feed configuration:

```bash
cp trader-news/news_feeds.example.json trader/news_feeds.json
```

RBI, SEBI and PIB feeds are preconfigured. Open the official NSE RSS directory
at <https://www.nseindia.in/static/rss-feed>, copy the exact RSS URLs you want,
and paste them into the blank NSE `url` fields in `trader/news_feeds.json`.

BSE corporate announcements are imported from a CSV downloaded manually from
the official BSE corporate-announcements page. No undocumented exchange API,
cookie replay or access-control workaround is used.

## Run

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS INFY --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --bse-csv downloads/bse_announcements.csv
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-moves --json
```

The default watchlist is collected from:

- `trader/watchlist.json` (scanner hits)
- `trader/plan.json` (active proposed trade)
- `trader/portfolio.json` (held positions, if maintained manually)

## Sources and output

| Source | Purpose |
|---|---|
| RBI Press Releases and Notifications RSS | Monetary policy, banking and financial regulation |
| SEBI RSS | Press releases, circulars, orders and rulings |
| PIB Press Releases RSS | Government policy and macroeconomic announcements |
| NSE RSS feeds configured by the user | Corporate announcements, results, board meetings and circulars |
| BSE CSV downloaded by the user | Supplementary corporate announcements |
| Yahoo Finance chart endpoint | Supplementary last-price/previous-close context; unofficial and may lag |

The tool writes `trader/state/news.json`, retaining seen item fingerprints for 30
days. It reports the item source, timestamp in IST, matching watchlist symbols,
event tags, summary and original link. Price moves are shown in INR and flagged
`MOVING` when the absolute move reaches the configured threshold (default 3%).

## Rules

- **Original disclosures beat commentary.** Open the linked exchange, regulator
  or company filing before relying on a headline or short RSS summary.
- **Do not over-attribute.** A broad RBI/PIB/SEBI item is not company-specific
  unless the company name or symbol is actually matched.
- **No news is not a signal.** Missing feeds and failed downloads are shown as
  notes; they are never represented as a clean bill of health.
- **News is not an entry/exit trigger by itself.** Explain why an event may
  matter to a setup, but leave the entry/exit decision to the relevant skills
  and the user.
- `--dry-run` does not update the seen-item state; `--all` includes previously
  shown items; `--hours` controls the lookback; `--json` emits machine-readable
  output; `--no-moves` skips supplementary quotes.
- For BSE CSV, preserve the original downloaded file and pass its path with
  `--bse-csv`. The importer supports common header variants; verify imported
  rows if BSE changes its export format.

## During the session

Run after `trader-scanner` finds setups, before finalizing an entry, and when
checking a proposed trade for new event risk. A periodic check (for example,
every 15 minutes during the session) is useful only if feed freshness and
exchange update cadence are understood.

## Safety

Education and decision support, not financial advice. This skill reads public
information and supplementary quotes only. It never places or modifies orders.
