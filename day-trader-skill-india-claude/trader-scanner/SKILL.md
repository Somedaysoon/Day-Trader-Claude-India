---
name: trader-scanner
description: >-
  Chart Scanner for NSE stocks. Builds the universe from NSE's equity list (EQ
  series only: trade-for-trade, SME and other series are dropped), then scans
  free Yahoo charts for setups. Before 9:15 AM IST it scans daily closes
  (20-day breakout, bull flag, 20-day average reclaim, oversold, oversold
  bounce). After the opening range it scans 5-minute bars for opening-range
  breakouts (9:15 to 9:30 AM IST) and shows VWAP, relative volume, previous-day
  high and strength against the Nifty 50 beside each hit. Reports how many
  charts were scanned, how many failed, and whether the result is partial.
  Writes trader/watchlist.json. It never places an order. Use when the user
  says "scan the market", "what is setting up", "opening range breakouts",
  "find me stocks to trade", "build my universe", or at the start of the
  day, before /trader-news and /trader-entry.
argument-hint: "[universe|daily|orb] [--symbols A,B] [--limit N]"
---

# trader-scanner

The Chart Scanner finds charts that match a rule. It does not find stocks to
buy: a match is where the next check starts, and most matches fail. Data is
Yahoo's free chart feed (unofficial, can lag), symbols are `SYMBOL.NS`, and
every time is Indian time (IST, UTC+05:30, no daylight saving).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe --equity-file EQUITY_L.csv --index-file ind_nifty500list.csv --fno-file fo_mktlots.csv
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                      # before 9:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                        # after 9:35 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15 --symbols RELIANCE,TCS,INFY
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --offline folder --now "2026-11-23 10:15" --min-price 0
```

Other flags: `--limit N`, `--min-price` (rupees, 20), `--min-turnover-cr` (crore
rupees traded: 5 for daily, 0.5 for orb), `--or-minutes` (15), `--max-confirm`
(200), `--watchlist FILE`, `--universe FILE`, `--json`, `--no-write`, `--show N`.

## The universe

`universe` reads NSE's equity list and keeps the **EQ series only**. The
trade-for-trade series (BE, BZ) cannot be traded intraday and SME stocks are out
of scope; the counts of what was dropped are printed. Pass an index constituent
list (for example the Nifty 500) with `--index-file` to keep only that index,
which is the sensible default for liquidity. Pass NSE's F&O lot-size file with
`--fno-file` to mark the stocks in the **closing auction session** (group `cas`,
reported as the F&O-eligible stocks); the rest are `other`.

The NSE file layouts and download addresses were written from memory and **could
not be checked**: the parsers read headers by name and fail with a plain error on
a file they do not understand, and if a download is refused the tool says to
fetch the file from NSE's website by hand and pass it with `--equity-file`. Read
the counts the first time you run it.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | one `SYMBOL|Name|group` line a stock |
| Yahoo spark and chart APIs | closes (20 symbols a request), then real highs and lows for candidates |
| `trader/watchlist.json` | the hits, plus the coverage: universe, scanned, succeeded, failed, unconfirmed, partial, source, as_of |

## The rules

- **Daily setups**, from closes only: `breakout_20d`, `bull_flag`, `reclaim_ma20`,
  `oversold` (RSI 14 under 30), `oversold_bounce`. Liquidity is price over
  `--min-price` and **traded value** (price x last volume) over
  `--min-turnover-cr`. Today's bar is dropped until the day is settled: after the
  closing auction, 15:35 or 15:40 IST, not 15:30.
- **Opening-range breakout.** The range is the first 15 minutes, the 9:15, 9:20
  and 9:25 bars. Stage 1 uses spark closes to find candidates; stage 2 fetches
  the real chart and calls a hit on the first completed bar that **closes**
  above the range high. The pre-open auction is never part of a candle.
- **Signals, never filters.** Each hit shows `vwap` / `above_vwap`,
  `rel_volume` (needs two earlier sessions in the chart), `prev_day_high` /
  `above_prev_day_high`, and `vs_nifty_pp` (the stock's move since the open minus
  the Nifty 50's, both through the trigger bar). A hit with weak signals is
  still a hit: they are for you to weigh.
- **Hours change by date.** Since 3 Aug 2026 the closing-auction stocks stop
  continuous trading at 15:15 (72 five-minute bars, last at 15:10); from 7 Dec
  2026 everything runs to 15:30 (75 bars). `_common/data/sessions.json` holds
  this by date, and the scanner uses the earlier end when a stock's group is
  unknown. These hours come from news and broker reports, not NSE's circulars.
- **Data checks.** A chart needs every 5-minute bar from 9:15 to the cutoff (a
  hole is a halt or missing data) and no impossible bar (high under low, close
  outside the bar); out-of-order candles are dropped too. Dropped charts are
  counted and named.
- **Coverage is always reported.** Fetched, usable, candidates, confirmed, failed,
  and `PARTIAL` when anything was skipped or not confirmed.

## Steps

1. Build the universe once (and when NSE's list changes). Hand-download the NSE
   files if the download fails.
2. Before 9:15 AM IST run `daily`; after 9:35 AM IST run `orb`. Tell the user
   the coverage line first, then the hits, and say when the result is PARTIAL.
3. Offer `/trader-news SYMBOL` for any hit they like, then `/trader-entry`.
4. NSE is closed on weekends and holidays: the tool says so and why. The holiday
   list covers 2026; for 2027 it says the list is not known yet.

## Example (SYNTHETIC data, from the test fixtures)

```
Opening-range breakout scan, Mon 2026-11-23 10:15 AM IST: 15-minute range (9:15 AM to 9:30 AM), 5-minute bars, Yahoo.
Fetched 1 of 1 charts; 1 usable; 1 candidates, 1 confirmed with real highs and lows.
1 of 1 charts closed above their opening-range high by 10:15 AM:
   9:45 AM  AAA               Rs 102.00  range 99.50 to 101.30  +0.69% over
            above VWAP, 1.2x volume, over prev-day high, +2.0 pp vs Nifty
```

## What it never does

It never places an order, never says a chart is a buy, never hides a failed
fetch, and never checks price bands, circuit limits, surveillance lists, spread
or depth: there is no free data for them here. Look at those in your own app.

## Safety

- Education and tooling, not financial advice. A match is a rules check, not a recommendation.
- The scanner reads public charts and writes files. It places no orders and holds no broker keys.
- Day trading loses most people money. Costs matter most on small positions: see `/trader-budget`.
