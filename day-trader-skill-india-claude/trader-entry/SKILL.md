---
name: trader-entry
description: >-
  Entry Timer for NSE stocks. Watches a setup bar by bar and tells you the
  moment a pick triggers. Reads free Yahoo 1-minute or 5-minute bars, builds the
  opening range (9:15 to 9:30 AM IST by default) and calls the entry on the first
  completed bar that CLOSES above the range high, or above a level you give it,
  with optional volume and above-VWAP conditions. Refuses to signal on a chart
  with a missing range bar or an impossible candle. Says waiting and how far,
  triggered with the time and price, expired, or no data, and writes
  trader/entry.json for /trader-exit. Long side only. It never places an order.
  Use when the user says "tell me when to get in", "watch RELIANCE", "has it
  broken out", "did it break the opening range", "entry signal", "check my
  watchlist", or after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m] [--group cas|other|any]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars and waits for each one to finish. A price that pokes above the high
in the middle of a bar is not an entry; a bar that CLOSES above it is. Then it
says the time and the price, writes them down for the Exit Planner, and stops.
It never buys anything. Day trading loses most people money, and a clean signal
does not change that. Times are Indian time (IST).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write          # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1450.50
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --interval 1m --min-volume-x 1.5 --above-vwap
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:45    # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                       # every hit in trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--group cas|other|any`, `--json`,
`--offline FILE` (a saved Yahoo chart JSON, or a folder of `<SYMBOL>.json`),
`--now "2026-11-23 10:15"` (Indian time) to replay a day. `watch` with `--now` or
`--offline` runs on a simulated clock and replays the day without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`, `hits[].group`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (by default 30 minutes before continuous trading ends). Optional |
| Yahoo chart API | today's bars for `SYMBOL.NS`, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, group, setup, status, time, bar, price, level, VWAP, opening range, and a note that the price can lag |

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the 9:15,
  9:20 and 9:25 five-minute bars (or fifteen 1-minute bars). The pre-open
  auction (9:00 to 9:15) is never part of a candle. The level to beat is the
  range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES above
  the level. A wick above is not a trigger; a close exactly at the level is not
  above it.
- **The time** is that bar's close time (the 9:40 bar closes at 9:45 AM). The
  price is its close, to the paisa. It is a Yahoo bar close, not a price you are
  sure to get: look at the live price in your own app before acting.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up (it waits a minute at most).
  Yahoo's live tick, like 10:49:45, is never a bar.
- **Data checks.** A range with a missing bar (a halt, or a gap in Yahoo's data)
  gives **no signal**: its high would be too low and would trigger early. A bar
  with an impossible high, low or close gives no signal either.
- **Volume and VWAP, if you want them.** `--min-volume-x 1.5` needs the trigger
  bar's volume at 1.5 times the range bars' average. `--above-vwap` needs the
  close above the session VWAP through that bar. The VWAP is shown on every
  trigger either way.
- **Hours depend on the date and the stock's group.** Since 3 Aug 2026 the
  closing-auction stocks (group `cas`, reported as the F&O-eligible ones) stop
  continuous trading at 15:15 and everything else at 15:30; from 7 Dec 2026 all
  run to 15:30. `any` takes the earlier end, the safe choice, and the watchlist
  carries each stock's group. The default last entry is 30 minutes before the
  end: 14:45 now, 15:00 from 7 Dec. These hours come from news and broker
  reports, not NSE's circulars.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends and NSE holidays: the market is closed.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line: waiting
   and how far, triggered at what time and price, or expired.
3. To wait for it, run `entry.py watch SYMBOL --until 14:45` in the background,
   or `entry.py check SYMBOL --write` on a `/loop` every 5 minutes. `watch` wakes
   just after each bar closes, prints one line per bar, and exits 0 on the
   trigger (it writes `trader/entry.json`) or 1 when the setup expires.
4. When it triggers, say the time and the price, that the price can lag, and
   that nothing was placed. If the trigger is more than a bar old, say that too:
   a late entry is a different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything is
   bought.

## Example (SYNTHETIC data, from the test fixtures)

At 9:43 the 9:40 bar is still open, so it waits:

```
WAITING  last close Rs 101.20 (9:35 bar), Rs 0.10 under the range high Rs 101.30 (-0.10%).
The 9:40 bar is still forming (Rs 102.00 so far, closes 9:45 AM); it does not count until it closes.
```

At 10:15 the bar has long closed:

```
TRIGGERED 9:45 AM at Rs 102.00: the 9:40 bar (bar 5) closed above the range high Rs 101.30 (+0.69%, above VWAP Rs 100.79).
That was 30 minutes ago; the last close is Rs 102.30 (10:10 bar). A late entry is a different trade.
Nothing was placed.
```

## Rules

- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Long side only. If the user wants a short, say this tool does not do that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that is
`/trader-exit`), never moves the level after the fact, and never counts a bar
that has not closed. It does not know price bands, circuit limits, spread or
depth: a stock stuck at its upper circuit can print a "breakout" you cannot buy.

## Safety

- Education and tooling, not financial advice. A trigger is a rules check, not a recommendation.
- This tool places no orders and holds no broker keys. Any trade is the user's own decision, made in their own broker app.
- The price is delayed and unofficial. Check the live price before acting on it.
