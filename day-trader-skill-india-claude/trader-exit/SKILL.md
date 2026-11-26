---
name: trader-exit
description: >-
  Exit Planner for NSE stocks. Sets the stop and the target BEFORE you enter, so
  you know when to get out. Reads trader/entry.json (or your own prices), builds
  the stop from the opening-range midpoint, the range low, a price, a percent
  or an ATR multiple, rounds it DOWN to the exchange tick (Rs 0.05), sets an R
  target, an optional trailing rule and a flat-by time that follows the
  session (the closing auction ends continuous trading at 15:15 for some
  stocks, 15:30 for others). With a share count it adds the money after
  charges and slippage. Refuses a stop too tight to survive the spread. Writes
  trader/plan.json for /trader-budget. Long side only. It never places an
  order. Use when the user says "set my stop", "where do I get out", "plan the
  exit", "what is my target", or after /trader-entry triggers.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|price:X] [--qty N] [--write]"
---

# trader-exit

The Exit Planner decides where you are wrong and where you take profit before
the trade exists, because a stop chosen after you are in is chosen by fear.
Prices are rupees on the exchange tick (Rs 0.05 for most NSE stocks). Times are
Indian time (IST).

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                                   # from trader/entry.json: or-mid stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-low --target 1.5R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1450.50 --or-high 1448 --or-low 1432.5
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5 --offline RELIANCE.json
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --qty 100 --trail pct:1         # adds the money after costs and a trailing rule
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                                   # the current trader/plan.json
```

Other flags: `--tick` (0.05), `--group cas|other|any`, `--flat-by HH:MM`,
`--slippage-pct` (0.05 with `--qty`), `--charges FILE`, `--json`, `--now`.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | what `/trader-entry` found: symbol, group, price, time, opening range. Only a triggered entry is planned, unless you give `--entry` |
| `trader/config.json` | `flat_by` (optional override) and `min_reward_risk` (1.0, a warning here) |
| `trader/charges.json` | your broker's charges, if you have them; else placeholders marked unverified |
| `trader/plan.json` | with `--write`: entry, stop, target, risk a share, reward:risk, tick, flat-by, group, trail, costs |

## The rules

- **Stops.** `or-mid` (the opening-range midpoint, the orb default), `or-low`,
  `price:X`, `pct:X`, `atr:K` (K times the 14-bar ATR on the session's bars to
  the entry; with fewer than 14 it says so). A level setup has no default: give
  a stop. **The stop rounds down to the tick**, away from the entry; the entry,
  the range levels and the target round to the nearest tick.
- **Targets.** `2R` (the default), `1R`, `1.5R`, or `price:X`.
- **Refused, with the reason.** A stop at or above the entry, a target at or
  below it, a risk under **two ticks or 0.05% of the price, whichever is more**
  (too tight to survive the spread), an entry that has not triggered, a short,
  and a day the NSE is closed.
- **Flat by.** Out by then, win or lose. By default 10 minutes before
  continuous trading ends for the stock's group: 15:05 for the closing-auction
  stocks until 4 Dec 2026, 15:20 from 7 Dec; `any` takes the earlier end. The
  group comes from `entry.json`. A configured time at or after the end is
  replaced with a note. These hours come from news and broker reports, not
  NSE's circulars.
- **Trailing rule (`--trail pct:X`).** Once a bar closes 1R above the entry, keep
  the stop X percent under the highest close, never lower. This tool does not
  watch the price: the rule is for you (or `/trader-alerts`).
- **Costs (`--qty N`).** Adds the loss if the stop is hit, the gain at the target
  and the reward:risk that is left, after round-trip charges and slippage on each
  fill. Brokerage is a flat amount per order, so small positions pay far more.
  The charge rates are **placeholders until you put your broker's in
  `trader/charges.json`**; the plan says which it used.

## Steps

1. Run `exit.py plan` straight after `/trader-entry` triggers. Show the entry,
   stop, target, risk a share and reward:risk.
2. If the user wants a different stop, change `--stop`; never move a stop further
   away after entry.
3. Add `--qty` once `/trader-budget` has a size, to see the money after costs.
4. Hand off to `/trader-timemachine` to replay it, then `/trader-budget`.

## Example (SYNTHETIC data, from the test fixtures)

```
Exit plan: AAA long, Mon 2026-11-23, opening-range breakout on 5-minute bars
  Entry         Rs 102.00 at 9:45 AM
  Stop          Rs 100.40  opening-range midpoint: (Rs 101.30 + Rs 99.50) / 2 = Rs 100.4
  Target        Rs 105.20  2R (two times the risk)
  Risk a share  Rs 1.60 (1.57% of the entry)
  Reward:risk   2.0
  Flat by       15:05 IST, win or lose: no overnight hold
```

With `--qty 100` the same plan reads: stop hit, lose Rs 180.83; target hit, gain
Rs 298.62; reward:risk left 1.65. Costs and slippage are why 2.0 on the screen is
not 2.0 in your account.

## What it never does

It never places an order or a stop order, and never watches the price. It cannot
know a price band or circuit limit: a stock at its lower circuit may not let you
sell at your stop at all.

## Safety

- Education and tooling, not financial advice. A plan is arithmetic, not a forecast.
- A stop is a plan, not a guarantee: a gap, a fast market or a circuit limit can fill past it, or leave you unable to sell.
- This tool places no orders and holds no broker keys. Any trade is the user's own decision, in their own broker app.
