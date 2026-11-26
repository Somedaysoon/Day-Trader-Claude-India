# CONFIGURATION.md — Indian Day-Trader Skill Pack

> **Where to put this file:** Save this document as `trader/state/CONFIGURATION.md`.
> All source and destination paths mentioned below are relative to the extracted project root (the folder containing `trader-entry`, `trader-exit`, `common`, etc.), not relative to `trader/state`.
> This is a setup guide, not a runtime JSON configuration file.

This guide lists **every example/configuration file shipped in this package**, its exact starter values, where it belongs, what each value means, and why it matters.

The system produces research, analysis and **entry/exit signals only**. It does not place, modify or cancel broker orders. You execute trades manually.

> **Important:** Values marked as placeholders or illustrative must not be treated as trading advice, verified broker charges, live prices or actual investor transactions.

## 1. Create the runtime folders once

Run these commands from the extracted project root (the folder containing `trader-entry`, `trader-exit`, `common`, etc.):

```bash
mkdir -p trader/state filings downloads
```

The `trader/` folder is deliberately **not pre-created** in the ZIP. Create it at the project root, alongside the skill folders—not inside any skill folder.

- `trader/`: your personal configuration and generated runtime data.
- `trader/state/`: deduplication and alert state.
- `filings/`: original XBRL attachments downloaded from official exchange/company sources.
- `downloads/`: manually downloaded BSE CSVs and other source exports.

## 2. Personal configuration files

Copy each source file to the destination shown below, then edit the destination. Keep the original example file unchanged.

| Runtime file to create | Copy this file | When it is needed |
|---|---|---|
| `trader/config.json` | `trader-budget/config.example.json` | Before sizing/risk checks and for shared entry/exit timing limits |
| `trader/charges.json` | `common/data/charges.json` | Before relying on net loss, reward:risk or position sizing |
| `trader/news_feeds.json` | `trader-news/news_feeds.example.json` | When using News |
| `trader/insider_watchlist.json` | `trader-insider/watchlist.example.json` | When using Insider / Super-Investor tracking |
| `trader/alerts.json` | `trader-alerts/alerts.example.json` | When configuring manual price/time alerts; preferably generate from a real plan |
| `trader/india-filings.json` | `trader-analyst/filings.manifest.example.json` | When using the earnings analyst with downloaded XBRL files |

Copy commands:

```bash
cp trader-budget/config.example.json trader/config.json
cp common/data/charges.json trader/charges.json
cp trader-news/news_feeds.example.json trader/news_feeds.json
cp trader-insider/watchlist.example.json trader/insider_watchlist.json
cp trader-alerts/alerts.example.json trader/alerts.json
cp trader-analyst/filings.manifest.example.json trader/india-filings.json
```

### 2.1 `trader/config.json` — budget, risk and time preferences

The starter file `trader-budget/config.example.json` contains:

```json
{
  "_note": "PLACEHOLDER limits in rupees, not advice. Set your own. flat_by and no_entries_after are left out on purpose: by default they follow the session (flat 10 minutes before continuous trading ends, no entries in the last 30). Put them here to override.",
  "budget": 100000,
  "cash_buffer_pct": 10,
  "max_position_pct": 75,
  "max_order_inr": 75000,
  "min_order_inr": 1000,
  "max_loss_per_trade_inr": 500,
  "max_daily_loss_inr": 1500,
  "max_trades_per_day": 3,
  "min_reward_risk": 1.0,
  "max_cost_pct_of_reward": 25,
  "slippage_pct": 0.05,
  "no_entries_before": "09:30",
  "blocked": [],
  "allow_options": false,
  "allow_margin": false,
  "allow_short": false
}
```

| Key | Starter value | Meaning / what you should do |
|---|---:|---|
| `_note` | Explanatory text | Leave as a comment-like JSON string; it warns that these are placeholders. |
| `budget` | `100000` | Total rupee capital allocated to this workflow. Replace with the amount you actually intend to make available. |
| `cash_buffer_pct` | `10` | Keep this percentage of the budget uncommitted. |
| `max_position_pct` | `75` | Maximum position value as a percentage of budget. |
| `max_order_inr` | `75000` | Absolute cap on one order's rupee value. |
| `min_order_inr` | `1000` | Reject an order value below this amount. |
| `max_loss_per_trade_inr` | `500` | Maximum planned loss for one trade. Choose your own risk limit. |
| `max_daily_loss_inr` | `1500` | Intended daily loss threshold. **The current risk skill does not enforce cumulative daily loss**, because it does not record your manually executed fills. |
| `max_trades_per_day` | `3` | Intended daily trade-count limit. **Not enforced as an executed-trade counter** in the current workflow. |
| `min_reward_risk` | `1.0` | Minimum reward-to-risk ratio expected by sizing/risk checks. |
| `max_cost_pct_of_reward` | `25` | Reject/warn when estimated trading costs consume more than this percentage of gross reward. |
| `slippage_pct` | `0.05` | Assumed slippage percentage used in estimates. Replace with a conservative assumption appropriate to the stocks you trade. |
| `no_entries_before` | `"09:30"` | Earliest permitted entry time, IST. |
| `blocked` | `[]` | Symbols you do not want the system to consider; add NSE symbols as strings, e.g. `["ABC", "XYZ"]`. |
| `allow_options` | `false` | Options are disabled. Leave false unless the strategy and risk code are deliberately extended/configured for them. |
| `allow_margin` | `false` | Margin use is disabled. |
| `allow_short` | `false` | Short trades are disabled. |

`flat_by` and `no_entries_after` are intentionally absent. By default, the system derives these from the relevant stock-group session: it plans to be flat before the continuous session ends and stops accepting late entries. Add either key only if you deliberately want to override that shared session rule. Times are Indian local time (`HH:MM`).

**Why this file matters:** It is the shared source of your capital and risk constraints. The budget, exit, entry and risk skills read relevant values from the same file. Do not leave the ₹1,00,000 budget or ₹500 per-trade loss limit unless those are genuinely your chosen limits.

### 2.2 `trader/charges.json` — your broker's actual charges

The source is `common/data/charges.json`. Its current contents are:

```json
{
  "schema_version": 1,
  "currency": "INR",
  "segment": "equity intraday (NSE cash, MIS)",
  "verified": false,
  "effective_from": null,
  "source": "PLACEHOLDER rates from a typical discount-broker schedule, written from memory and NOT checked against any broker's or exchange's current published charges. Put your own broker's numbers in trader/charges.json (same keys) and set verified to true once you have compared them.",
  "brokerage": {
    "flat": 20.0,
    "pct": 0.03,
    "note": "per executed order: the lower of the flat amount and pct percent of the order value"
  },
  "stt_pct_sell": 0.025,
  "exchange_txn_pct": 0.00297,
  "sebi_per_crore": 10.0,
  "stamp_pct_buy": 0.003,
  "gst_pct": 18.0,
  "gst_applies_to": [
    "brokerage",
    "exchange_txn",
    "sebi"
  ]
}
```

| Key | Bundled value | Meaning / required action |
|---|---:|---|
| `schema_version` | `1` | Configuration schema version; do not change. |
| `currency` | `"INR"` | Rupee denomination; do not change for this India pack. |
| `segment` | `"equity intraday (NSE cash, MIS)"` | Describes the segment for which these rates are intended. Verify that it matches your broker product/segment. |
| `verified` | `false` | Must remain false until you compare every applicable rate with your broker's current tariff. Set true only after verification. |
| `effective_from` | `null` | Enter the date the tariff became effective, if known, e.g. `"2026-11-01"`. |
| `source` | Placeholder warning | Replace with the broker tariff URL/name and verification note after checking it. |
| `brokerage.flat` | `20.0` | Illustrative flat brokerage amount per executed order. |
| `brokerage.pct` | `0.03` | Illustrative percentage brokerage rate; the note says the lower of flat amount and percentage of order value. Confirm the broker's exact method and units. |
| `stt_pct_sell` | `0.025` | Illustrative sell-side STT percentage. Verify current rate and applicable transaction type. |
| `exchange_txn_pct` | `0.00297` | Illustrative exchange transaction charge percentage. Verify the current NSE cash rate. |
| `sebi_per_crore` | `10.0` | Illustrative SEBI turnover fee per crore of turnover. Verify current rate. |
| `stamp_pct_buy` | `0.003` | Illustrative buy-side stamp-duty percentage. Verify current rate and applicability. |
| `gst_pct` | `18.0` | Illustrative GST percentage. |
| `gst_applies_to` | `["brokerage", "exchange_txn", "sebi"]` | Components on which the cost calculator applies GST. Confirm against the current tax treatment and calculator implementation. |

**Why this file matters:** These charges affect estimated net loss at the stop, net reward at target and reward:risk. The bundled rates are explicitly unverified placeholders—not a broker tariff. Replace them with the rates for your broker and the exact NSE cash intraday product you use. The same `trader/charges.json` is shared by the skills; do not edit each skill's internal `_common/data/charges.json` copy separately.

### 2.3 `trader/news_feeds.json` — regulator and exchange news

The source is `trader-news/news_feeds.example.json`:

```json
{
  "version": 1,
  "timezone": "Asia/Kolkata",
  "notes": [
    "RBI, SEBI and PIB URLs below are official public RSS feeds.",
    "Copy the exact NSE RSS URLs you want from https://www.nseindia.in/static/rss-feed into the blank url fields below.",
    "BSE corporate announcements are imported from a CSV downloaded manually from the BSE website; no undocumented BSE API is used."
  ],
  "feeds": [
    {
      "name": "RBI Press Releases",
      "publisher": "Reserve Bank of India",
      "category": "macro",
      "url": "https://rbi.org.in/pressreleases_rss.xml",
      "enabled": true
    },
    {
      "name": "RBI Notifications",
      "publisher": "Reserve Bank of India",
      "category": "regulatory",
      "url": "https://rbi.org.in/notifications_rss.xml",
      "enabled": true
    },
    {
      "name": "SEBI RSS",
      "publisher": "Securities and Exchange Board of India",
      "category": "regulatory",
      "url": "https://www.sebi.gov.in/sebirss.xml",
      "enabled": true
    },
    {
      "name": "PIB Press Releases",
      "publisher": "Press Information Bureau",
      "category": "macro",
      "url": "https://pib.gov.in/RssMain.aspx?ModId=6&Lang=1&Regid=1",
      "enabled": true
    },
    {
      "name": "NSE Corporate Announcements",
      "publisher": "National Stock Exchange of India",
      "category": "exchange",
      "url": "",
      "enabled": true
    },
    {
      "name": "NSE Financial Results",
      "publisher": "National Stock Exchange of India",
      "category": "filings",
      "url": "",
      "enabled": true
    }
  ]
}
```

| Feed | Starter status | What to do / why |
|---|---|---|
| RBI Press Releases | Enabled; URL prefilled | Supplies RBI policy and macro/financial-sector announcements. |
| RBI Notifications | Enabled; URL prefilled | Supplies regulatory/operational notifications. |
| SEBI RSS | Enabled; URL prefilled | Supplies securities-market regulation, circulars, orders and announcements. |
| PIB Press Releases | Enabled; URL prefilled | Supplies government policy and macroeconomic announcements. |
| NSE Corporate Announcements | Enabled; URL blank | Open the official [NSE RSS directory](https://www.nseindia.in/static/rss-feed), copy the exact feed URL for corporate announcements, and paste it into this `url` field. |
| NSE Financial Results | Enabled; URL blank | Copy the exact official financial-results RSS URL into this `url` field if you want that feed. |

Do not invent or guess NSE URLs. If you do not want one of the feeds, set its `enabled` value to `false`. Keep `timezone` as `"Asia/Kolkata"`.

**BSE workflow:** BSE announcements are not an RSS URL in this example. Download the announcement CSV manually from BSE, keep the original under `downloads/`, then run:

```bash
python3 trader-news/news.py --bse-csv downloads/bse_announcements.csv
```

Useful options include `--hours 48`, `--all`, `--dry-run`, `--no-moves`, `--move-pct 3`, and `--json`. The news tool uses `trader/watchlist.json`, `trader/plan.json`, and optional `trader/portfolio.json` to match relevant symbols. It stores seen-item state in `trader/state/news.json`.

**Why this file matters:** It determines which official sources are polled. A missing/failed feed is reported as a source issue; it must not be interpreted as “there is no relevant news.”

### 2.4 `trader/insider_watchlist.json` — major-investor names and aliases

The source is `trader-insider/watchlist.example.json`:

```json
{
  "investors": [
    {
      "name": "Vijay Kedia",
      "aliases": [
        "Kedia Securities",
        "Kedia Securities Pvt Ltd"
      ]
    },
    {
      "name": "Rakesh Jhunjhunwala",
      "aliases": [
        "Rare Enterprises"
      ],
      "historical_only": true
    },
    {
      "name": "Rekha Jhunjhunwala",
      "aliases": []
    },
    {
      "name": "Dolly Khanna",
      "aliases": [
        "Rajiv Khanna"
      ]
    },
    {
      "name": "Ashish Kacholia",
      "aliases": []
    },
    {
      "name": "Mukul Agrawal",
      "aliases": [
        "Param Capital"
      ]
    }
  ]
}
```

| Name | Aliases in starter file | Note |
|---|---|---|
| Vijay Kedia | `Kedia Securities`; `Kedia Securities Pvt Ltd` | Keep aliases only where an official filing supports the association. |
| Rakesh Jhunjhunwala | `Rare Enterprises` | `historical_only: true`; legacy/historical tracking, not a current personal-trade feed. |
| Rekha Jhunjhunwala | None | Add only documented aliases/entities. |
| Dolly Khanna | `Rajiv Khanna` | Do not assume an entity association beyond what filings establish. |
| Ashish Kacholia | None | Add only verified aliases. |
| Mukul Agrawal | `Param Capital` | Add only verified aliases. |

You can add/remove entries and aliases. The script also supports `watchlist --add "Investor Name"`.

**Why this file matters:** It tells the tracker which names and associated entities to look for in imported disclosures and shareholding snapshots. A match is a research lead, not proof that the investor personally owns the shares. Quarterly shareholding is a dated snapshot, not a trade blotter; disappearance from a snapshot does not prove a sale or complete exit.

The example CSV files shipped with this skill are **illustrative input layouts**, not real disclosures:

`trader-insider/insider.example.csv`:

```csv
Company Name,Symbol,ISIN,Name of Person,Category of Person,Type,No. of Shares,Value of Securities,Date of Acquisition,Submission Date,Regulation,Source URL,Revision Remarks
Example Ltd,EXAMPLE,INE000A01000,Example Director,Director,Buy,10000,1250000,10/10/2026,12/10/2026,SEBI PIT Regulation 7(2),https://www.nseindia.com/,Illustrative only
```

`trader-insider/shareholding.example.csv`:

```csv
Company Name,Symbol,ISIN,Name of Shareholder,No. of Shares,% of Shareholding,As on Date,Submission Date,Source URL
Example Ltd,EXAMPLE,INE000A01000,Example Investor,250000,2.5,30/09/2026,20/10/2026,https://www.nseindia.com/
```

The names, symbol, ISIN and figures in both CSVs are dummy illustrative values. Do not import them as actual market disclosures.

For actual files, use the official NSE/BSE downloads and preserve source URL, exchange, transaction date, filing date and revision information. Typical imports:

```bash
python3 trader-insider/insider.py import --kind insider --exchange NSE --file downloads/insider.csv
python3 trader-insider/insider.py import --kind sast --exchange BSE --file downloads/sast.csv
python3 trader-insider/insider.py import --kind bulk --exchange NSE --file downloads/bulk.csv
python3 trader-insider/insider.py import --kind block --exchange NSE --file downloads/block.csv
python3 trader-insider/insider.py import --kind shareholding --exchange NSE --file downloads/shareholding.csv
```

### 2.5 `trader/alerts.json` — price and time alert rules

The source is `trader-alerts/alerts.example.json`:

```json
{
  "rules": [
    {
      "id": "opening-bell",
      "type": "opening_bell"
    },
    {
      "id": "reliance-plan-stop",
      "symbol": "RELIANCE",
      "type": "stop",
      "price": 1450.0,
      "note": "plan stop",
      "group": "other",
      "date": "2026-11-26"
    },
    {
      "id": "reliance-plan-target",
      "symbol": "RELIANCE",
      "type": "target",
      "price": 1540.0,
      "note": "plan target",
      "group": "other",
      "date": "2026-11-26"
    },
    {
      "id": "reliance-flat-by",
      "symbol": "RELIANCE",
      "type": "time",
      "at": "15:20",
      "label": "TIME TO EXIT - flat by plan deadline",
      "before_close": true,
      "group": "other",
      "date": "2026-11-26"
    },
    {
      "id": "reliance-move-3",
      "symbol": "RELIANCE",
      "type": "pct_move",
      "pct": 3,
      "group": "other"
    }
  ]
}
```

| Rule | Starter values | Meaning / edit |
|---|---|---|
| `opening-bell` | `type: opening_bell` | Signals when the continuous NSE session is open. |
| `reliance-plan-stop` | RELIANCE, stop `1450.0`, group `other`, date `2026-11-26` | Signals when price is at/below the stop. Replace symbol, price, group and date with a real plan—or preferably generate from `trader/plan.json`. |
| `reliance-plan-target` | RELIANCE, target `1540.0`, group `other`, date `2026-11-26` | Signals when price is at/above target. Replace sample values. |
| `reliance-flat-by` | `15:20`, `before_close: true`, date `2026-11-26` | Time-based TIME TO EXIT signal. Use the actual plan's `flat_by`; do not keep the sample date/time for a later session. |
| `reliance-move-3` | RELIANCE, `pct: 3` | Flags a move of 3% from previous close (or configured reference); edit symbol and threshold as desired. |

The `date` fields are one-day-only examples. They become stale after that date. Do not use the sample stop/target prices as live levels.

**Recommended method:** After `trader-exit` writes a valid `trader/plan.json`, create the stop, target and `flat_by` alert rules from that plan:

```bash
python3 trader-alerts/alerts.py add --from-plan
python3 trader-alerts/alerts.py check
python3 trader-alerts/alerts.py list
```

Alerts are signals only. Quotes come from Yahoo Finance and can be delayed/incomplete; verify the price in your broker terminal. The alert state is stored in `trader/state/alerts.json`.

### 2.6 `trader/india-filings.json` — analyst XBRL manifest

The source `trader-analyst/filings.manifest.example.json` is intentionally empty:

```json
{
  "filings": []
}
```

You add one object for each official XBRL file you have downloaded. The parser requires:

- `file`: path to the downloaded XBRL attachment (relative paths are resolved from the project working directory, or use an absolute path).
- `basis`: exactly `"standalone"` or `"consolidated"`.
- `period_end`: reporting period end date, preferably `YYYY-MM-DD`.
- At least one identity: `ticker` or `nse_symbol`, `bse_scrip_code`, or `isin`.

Useful optional metadata fields include `company_name`, `isin`, `nse_symbol`, `bse_scrip_code`, `exchange` (array such as `["NSE"]` or `["BSE"]`), `filed` (filing date), `revision` (e.g. `"original"` or `"revised"`), and `source` (official source description/URL).

Illustrative shape (replace every value with your own downloaded filing; this is not an actual filing):

```json
{
  "filings": [
    {
      "file": "filings/COMPANY_2026-09-30_consolidated.xml",
      "company_name": "Example Limited",
      "isin": "INE000A01000",
      "nse_symbol": "EXAMPLE",
      "bse_scrip_code": "500000",
      "exchange": ["NSE", "BSE"],
      "basis": "consolidated",
      "period_end": "2026-09-30",
      "filed": "2026-11-10",
      "revision": "original",
      "source": "Official NSE Integrated Filing"
    }
  ]
}
```

Use official NSE Integrated Filing / Financial Results, BSE corporate financial results or the issuer's investor-relations site. Download the original XBRL attachment and keep it under `filings/`. Keep standalone and consolidated filings as separate manifest rows. Add a revised filing as a separate row with its actual filing date and `revision: "revised"`; do not overwrite the original attachment.

Run the analyst with:

```bash
python3 trader-analyst/facts.py --manifest trader/india-filings.json --metrics trader/metrics.json
```

**Why this file matters:** The manifest tells the parser which local XBRL files belong to which company, reporting period and accounting basis. The parser does not automatically fetch every company's filing. The shipped `trader-analyst/metrics.example.json` is an **illustrative generated-output example**, not a runtime configuration file. It shows two sample RELIANCE records (one consolidated, one standalone), ISIN `INE002A01018`, NSE symbol `RELIANCE`, BSE scrip code `500325`, period end `2026-09-30`, and source `NSE Integrated Filing`; its `rows` arrays are empty. Do not copy it into the manifest or treat its sample company data as a current report.

## 3. Files generated by the skills — do not create these by hand

| Runtime path | Produced by | What it contains / why |
|---|---|---|
| `trader/universe.txt` | `trader-scanner/scan.py universe` | NSE EQ-series universe and stock-group metadata. |
| `trader/watchlist.json` | `trader-scanner/scan.py daily` or `orb` | Scanner hits and scan coverage; feeds News and Entry. |
| `trader/entry.json` | `trader-entry/entry.py` | Entry trigger status, price and rationale. |
| `trader/plan.json` | `trader-exit/exit.py plan --write` | Planned entry, stop, target, trailing rules and `flat_by`. |
| `trader/sizing.json` | `trader-budget/size.py --write` | Whole-share position size within configured limits. |
| `trader/risk.json` | `trader-risk/check.py` | PASS/BLOCK risk assessment and warnings. |
| `trader/timemachine.json` | `trader-timemachine/timemachine.py run --write` | Simulation and historical replay results. |
| `trader/prices.json` | `trader-alerts/alerts.py quote ... --write` | Supplementary Yahoo quote snapshot. |
| `trader/metrics.json` | `trader-analyst/facts.py` | Normalised reported financial metrics. |
| `trader/state/news.json` | `trader-news/news.py` | News deduplication state. |
| `trader/state/alerts.json` | `trader-alerts/alerts.py` | Which alert rules have already fired. |
| `trader/state/insider_events.jsonl` | `trader-insider/insider.py` | Imported disclosure event history and deduplication. |

`trader/portfolio.json` is optional. Maintain it manually if you want News and quote alerts to include your existing holdings. It is not a broker sync.

## 4. Market data files shipped with the pack — verify, don't copy

These are project-wide reference data, not personal `trader/` settings:

- `common/data/sessions.json`: date- and stock-group-specific continuous-session profiles. It is marked `verified: false`; the 15:15/15:30 group timings were not confirmed against the underlying NSE circulars during package preparation.
- `common/data/nse_holidays.json`: includes the 2026 holiday list; later calendars are not covered.
- `common/data/charges.json`: bundled fallback charge assumptions described above; unverified.

Before relying on time-sensitive entry, exit, alert or replay signals, compare session and holiday data with the latest official NSE circulars. If you modify shared code/data, synchronize the per-skill copies and check consistency:

```bash
python3 tools/sync_common.py
python3 tools/sync_common.py --check
```

## 5. Data-source and operational cautions

- Yahoo Finance quotes/candles are free, unofficial and may be delayed, incomplete or missing. Verify price and market depth in your broker terminal.
- NSE/BSE filing downloads and analyst XBRL taxonomy coverage need wider real-company validation. The analyst does not automatically download every filing.
- BSE corporate-announcement CSVs are imported manually with `--bse-csv`; preserve the original CSV.
- Insider tracking distinguishes confirmed transaction disclosures from quarterly shareholding snapshots. A missing name in a later snapshot does not prove a sale.
- Risk checks evaluate one proposed trade at a time. The current workflow does not know your manual fills and therefore does not enforce cumulative daily loss or executed trade-count limits.
- Charges, slippage, holidays, session hours, price bands, circuit limits, surveillance lists, spreads and depth are not guaranteed by these tools.
- Review warnings and independently verify the setup before acting. The software only signals; you remain responsible for every entry and exit.

## 6. Quick first-run checklist

- [ ] Extract ZIP and create `trader/`, `trader/state/`, `filings/`, `downloads/` at project root.
- [ ] Copy templates to the six runtime paths in Section 2.
- [ ] Set your own budget and per-trade maximum loss in `trader/config.json`.
- [ ] Replace all placeholder charge rates in `trader/charges.json` with your broker's current tariff; verify units and set `verified: true` only after checking.
- [ ] Add exact NSE RSS URLs to the blank feed fields in `trader/news_feeds.json`.
- [ ] Review investor names and aliases in `trader/insider_watchlist.json`.
- [ ] Remove/replace sample RELIANCE alerts and the example date, or generate alerts from a real `trader/plan.json`.
- [ ] Add only real downloaded XBRL files to `trader/india-filings.json`.
- [ ] Verify NSE session profiles and holiday calendar against current official circulars.
- [ ] Run a dry/test workflow and compare every price, cost and signal with your broker terminal before relying on it.
