---
name: trader-alerts
description: >-
  NSE price alerts and trade signals. Watches entry-related price levels,
  stop-loss, target, percentage moves, the NSE opening session and the
  trader-exit flat_by deadline. Uses IST and the shared NSE holiday/session
  calendar, including stock-group session hours. Fetches free delayed Yahoo
  Finance quotes for NSE (.NS) and BSE (.BO) symbols and writes
  trader/prices.json. Alerts are informational signals only; the user places
  and manages every order manually.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches configured levels and emits clear signals. It never
places, modifies or cancels an order. A stop, target or time alert tells the
user what happened; the user decides and executes manually through their broker.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                         # NSE session status in IST
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock --group other
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan               # stop, target, flat_by from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1450.00
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol TCS --target 4100
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:20 --label "flat by plan deadline"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3 --direction up
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                         # exit 0 = one or more signals
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE TCS --write    # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-11-26 15:20"` pins the clock in Indian time. A timestamp with
`Z` or an explicit UTC offset is interpreted as an absolute time. `--offline DIR`
reads `yahoo-<SYMBOL>.json` files instead of the network.

## What it reads and writes

| file | purpose |
| --- | --- |
| `trader/plan.json` | stop, target, group, date and `flat_by` from trader-exit |
| `trader/alerts.json` | alert rules (start from `alerts.example.json`) |
| `trader/state/alerts.json` | which rules have rung, preventing repeat alerts |
| `trader/prices.json` | latest quotes for trader-budget and trader-risk |
| `trader/portfolio.json` | symbols included by `quote --holdings` |

## Rule types

| type | signal condition | repeat behaviour |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | once until reset; optional `rearm` |
| `below` / `stop` | price at or below `price` | once until reset; optional `rearm` |
| `pct_move` | percentage move from previous close (or `ref`) reaches `pct` | at most once per market day |
| `time` | IST clock passes `at` on an NSE trading day | once per market day |
| `opening_bell` | continuous NSE session is open | once per market day |

A rule with `date` is watched only on that Indian market date. Plan-generated
rules inherit the plan's date and stock group. `flat_by` is taken directly from
`trader/plan.json`; if absent, the fallback is ten minutes before the continuous
session close for that stock group. A time rule marked `before_close` is capped
at that group's continuous-session close. It is not shifted by a US-style early
close adjustment.

## NSE clock and stock groups

The clock and trading-day logic come from the shared `common/in_market.py` and
its copied `_common` data. The usual continuous session opens at 09:15 IST.
The closing time depends on the effective session profile and the symbol group:
`cas`, `other`, or `any` (the earlier close, used when the group is unknown).
The same calendar and session profiles are used by trader-entry, trader-exit,
and trader-timemachine. If the holiday calendar does not cover a year, the
command warns rather than assuming the date is a trading day with a verified
calendar.

Examples:

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock --now "2026-11-26 10:00"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock --now "2026-11-26 10:00" --group other
```

## Quotes

- Yahoo Finance chart JSON is the supported quote source.
- Plain NSE symbols are automatically requested as `<SYMBOL>.NS`; explicit
  `.NS` and `.BO` suffixes are preserved.
- Quotes are free, delayed and unofficial. They are suitable for monitoring
  and preliminary checks, not for precise execution timing. Verify the price
  with the broker before acting.
- `trader/prices.json` stores the original symbol, price, quote timestamp,
  previous close, source and fetch timestamp. Currency is INR.
- `--offline DIR` expects files such as `yahoo-RELIANCE.json` for a plain
  `RELIANCE` symbol.

## Workflow

1. After trader-exit writes `trader/plan.json`, run `add --from-plan`. This
   creates three signals: stop-loss, target and the plan's `flat_by` deadline.
2. Run `check` periodically while monitoring the trade. Each check fetches
   only symbols needed by today's active rules. It is polling, not a live feed;
   a price can cross a level between checks.
3. Read the signals and verify the market price and position in the broker
   interface. If a stop or target is reached, the alert is a prompt to review
   and act. `TIME TO EXIT` means the plan's `flat_by` deadline has arrived.
4. The user executes all entry and exit actions manually.

## Example

A RELIANCE plan has entry ₹1,480, stop ₹1,450, target ₹1,540, group `other`,
trade date 2026-11-26 and `flat_by` 15:20. `add --from-plan` creates:

- `reliance-plan-stop` - **STOP HIT** when the quote is at or below ₹1,450.
- `reliance-plan-target` - **TARGET HIT** when the quote is at or above ₹1,540.
- `reliance-flat-by` - **TIME TO EXIT** at 15:20 IST if neither price alert has already prompted action.

## Rules and limitations

- An alert is a signal, never an order. This skill has no broker execution
  integration and must not tell the user that an order was placed or filled.
- Never describe a Yahoo quote as live or guaranteed. Delayed quotes can miss
  a fast move or a level crossed between polling intervals.
- Confirm every new rule back to the user: symbol, type, level and date.
- Use INR (₹) for all Indian share prices.
- This is an educational monitoring tool, not financial advice.
