# Indian day-trader skill pack: configuration checklist

This project only produces analysis and **entry/exit signals**. It does not connect to a broker or place, change, or cancel orders. You execute trades manually.

All runtime files belong in a `trader/` folder at the project root (the same level as `trader-entry/`, `trader-exit/`, etc.). Create it once:

```bash
mkdir -p trader/state filings downloads
```

## Configure these files before relying on the signals

| Priority | Create this runtime file | Copy this template/source | What to edit and why |
|---|---|---|---|
| Required | `trader/config.json` | `trader-budget/config.example.json` | Set your own rupee budget, cash buffer, maximum position/order value, maximum loss per trade, minimum order, reward:risk floor, slippage estimate, blocked symbols and entry-time preferences. These are risk limits, not recommendations. Do not leave the example ₹1 lakh budget / ₹500 loss limit unless they are actually yours. Optional `flat_by` and `no_entries_after` overrides may be added if you deliberately want to override session-derived times. |
| Required before sizing/risk checks | `trader/charges.json` | `common/data/charges.json` | Replace the placeholder brokerage, STT, exchange transaction charge, SEBI fee, stamp duty and GST rates with the current schedule for **your broker and NSE cash intraday segment**. These affect the all-in loss and reward:risk calculation. The bundled rates are explicitly unverified. |
| Required for news | `trader/news_feeds.json` | `trader-news/news_feeds.example.json` | RBI, SEBI and PIB RSS feeds are prefilled. Add the exact RSS URLs you choose from the official [NSE RSS directory](https://www.nseindia.in/static/rss-feed) to the blank NSE fields. Do not guess undocumented endpoints. |
| Required for insider/super-investor tracking | `trader/insider_watchlist.json` | `trader-insider/watchlist.example.json` | Edit investor names and aliases; add/remove people or entities you want monitored. Rakesh Jhunjhunwala is marked historical-only. This is a discovery watchlist, not proof of a current holding. |
| Required for alerts | `trader/alerts.json` | `trader-alerts/alerts.example.json` | Prefer generating stop, target and `flat_by` alerts from the current plan with `alerts.py add --from-plan`. If you copy the example, replace its sample symbol, prices and date first; sample levels are not live recommendations. |
| Required when using the earnings analyst | `trader/india-filings.json` | `trader-analyst/filings.manifest.example.json` | Add one manifest record for each official NSE/BSE/company XBRL attachment you downloaded. Record file path, company identity (ISIN where known), NSE symbol and/or BSE scrip code, standalone/consolidated basis, period end, filing date, revision status and source. Save original XBRL files under `filings/`. Do not mix standalone and consolidated records. |

### Example copy commands

Run these from the project root:

```bash
mkdir -p trader/state filings downloads
cp trader-budget/config.example.json trader/config.json
cp common/data/charges.json trader/charges.json
cp trader-news/news_feeds.example.json trader/news_feeds.json
cp trader-insider/watchlist.example.json trader/insider_watchlist.json
cp trader-alerts/alerts.example.json trader/alerts.json
cp trader-analyst/filings.manifest.example.json trader/india-filings.json
```

Then edit the copied files. Copying a template does not make its placeholder values accurate.

## Create these files by running skills; do not hand-write them initially

| Runtime file | Created by | Purpose |
|---|---|---|
| `trader/universe.txt` | `trader-scanner/scan.py universe` | NSE EQ-series universe and stock-group metadata. Download/verify the NSE equity list; optionally supply an index constituent file and the F&O lot-size file. The current parser's NSE file layouts and automatic download addresses still need a real-world check. |
| `trader/watchlist.json` | `trader-scanner/scan.py daily` or `orb` | Current scanner hits and scan-coverage information. |
| `trader/entry.json` | `trader-entry/entry.py` | Triggered entry signal. |
| `trader/plan.json` | `trader-exit/exit.py plan --write` | Stop, target, trailing-stop rules and `flat_by` deadline. |
| `trader/sizing.json` | `trader-budget/size.py --write` | Whole-share position size within configured limits. |
| `trader/risk.json` | `trader-risk/check.py` | PASS/BLOCK risk validation and warnings. |
| `trader/timemachine.json` | `trader-timemachine/timemachine.py run --write` | Simulation and historical replay results. |
| `trader/prices.json` | `trader-alerts/alerts.py quote ... --write` | Supplementary Yahoo quote snapshot. |
| `trader/metrics.json` | `trader-analyst/facts.py --metrics trader/metrics.json` | Normalised reported financial metrics for downstream use. |
| `trader/state/news.json` | `trader-news/news.py` | Deduplication state for previously seen news. |
| `trader/state/alerts.json` | `trader-alerts/alerts.py` | Which alerts have already fired. |
| `trader/state/insider_events.jsonl` | `trader-insider/insider.py` | Deduplication/event history for imported disclosures. |

`trader/portfolio.json` is optional and should contain only positions you choose to maintain manually. News and alerts can use it as an additional symbol source.

## Exchange calendar and market-session data: verify before live use

These are shipped with the project, not personal settings:

- `common/data/sessions.json` contains the date- and stock-group-specific continuous-session profiles. It is currently marked `verified: false`; the 15:15/15:30 group timings were not confirmed against the underlying NSE circulars when this package was prepared.
- `common/data/nse_holidays.json` contains the 2026 holiday list, but later circulars were not checked and 2027 is not covered in this package.

Before relying on time-sensitive entry, exit, alert or replay signals, compare both files with the latest official NSE circulars. If you change `common/`, run:

```bash
python3 tools/sync_common.py
python3 tools/sync_common.py --check
```

The first command copies the shared clock/calendar/cost modules into each skill's `_common/` folder; the second checks that those copies match.

## Source and execution cautions

- Yahoo Finance quotes/candles are free, unofficial and may be delayed or incomplete. Verify live price and market depth in your broker/exchange terminal.
- NSE/BSE filing downloads and the analyst's XBRL taxonomy coverage need broader real-company validation. The analyst does not automatically fetch every filing; use official exchange/company downloads and keep their original files.
- BSE corporate-announcement CSVs are imported manually by the news skill. Pass the downloaded file with `--bse-csv PATH`.
- The insider tracker distinguishes confirmed exchange disclosures from quarterly shareholding snapshots. A vanished disclosed holding does not prove an exact-date sale.
- The risk skill checks one proposed trade at a time. It does not know your actual fills and does not enforce cumulative daily loss or trade-count limits; the example config's `max_daily_loss_inr` and `max_trades_per_day` are not an execution ledger.
- Charges, slippage, holidays, session hours, price bands, circuit limits, surveillance lists, spreads and depth are not guaranteed by these tools. Review all warnings before acting.
