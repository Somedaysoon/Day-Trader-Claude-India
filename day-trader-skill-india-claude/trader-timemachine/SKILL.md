---
name: trader-timemachine
description: >-
  Time Machine for NSE cash-market trade signals. Replays an entry/stop/target
  plan from trader-exit using 10,000 Monte Carlo paths built from the stock's
  own recent 5-minute bars, then checks the same opening-range rule against
  complete historical sessions. Uses IST, the shared NSE holiday/session
  calendar, and the stock group's session length. Uses the plan's flat_by as
  the default replay endpoint. Reports target-first, stop-first and neither
  odds, price ranges, average R, historical outcomes and a verdict. It never
  places orders. Use for "run the time machine", "replay this trade", "what
  are the odds", "simulate this trade", "Monte Carlo", or after trader-exit
  prepares an entry/exit signal.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine checks a proposed Indian-market trade signal before the user
acts on it. `trader-exit` supplies the entry, stop, target, stock group and
`flat_by` time. This skill asks: using the stock's own recent 5-minute bars,
how often would the target be hit first, the stop be hit first, or would the
position still be open at the planned exit deadline? It also replays the same
opening-range rule over complete past sessions. This is a simulation, not a
prediction.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:00
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-11-25 \
    --entry 1480 --stop 1465 --target 1510 --entry-time "10:15 AM" --group other
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`), and
`--now "2026-11-26 10:15"` (IST) to pin the clock. `--group` is only needed
when supplying a plan through command-line flags; a plan from trader-exit
already contains its group.

## What it reads and writes

| file/source | purpose |
| --- | --- |
| `trader/plan.json` | Plan from trader-exit: symbol, date, group, entry, entry time, stop, target, target R and `flat_by` |
| Yahoo chart data | 60 days of 5-minute bars for the symbol; unofficial and may lag |
| `common/in_market.py` and `common/data/` | Shared IST conversion, NSE holidays and date/group-specific continuous-session hours |
| `trader/timemachine.json` | With `--write`: plan, Monte Carlo results, history, warnings and verdict |

## How it works

1. **Session-aware data.** Converts Yahoo timestamps to IST and keeps only
   complete 5-minute bars inside the NSE continuous session for the plan's
   stock group. `cas`, `other` and `any` use the shared market calendar.
   `any` is the conservative group when the stock's group is unknown.
2. **The pool.** Keeps only complete prior sessions with the same expected
   number of bars as the plan date and group. A normal 15:30 close has 75
   five-minute bars; a 15:15 close has 72. Sessions with missing bars, gaps,
   holidays or a different session length are not mixed into the pool.
3. **One replay.** Starts at the planned entry price. For each remaining bar,
   it samples a historical bar from approximately the same time of day
   (plus or minus the configured window), scales that bar to the current
   price, and checks stop before target. If one bar touches both, it counts
   as the stop (conservative treatment).
4. **10,000 replays** estimate target first, stop first, neither by the
   replay endpoint, and green at that endpoint. The endpoint defaults to
   `flat_by` from trader-exit's plan. An explicit `--until` overrides it.
   If the plan has no `flat_by`, the continuous-session close is used.
5. **R and price range.** Reports median and 5th–95th percentile endpoint
   prices, plus average R. Target earns the plan's target R, stop is -1R,
   and a trade still open at the endpoint is marked to its endpoint price.
   A gap through the stop is still represented as -1R; a real fill can be
   worse.
6. **History.** For an opening-range breakout plan, replays the same opening
   range, stop rule and target R on every eligible past full session. Other
   setup types are reported as not supported by this historical-rule replay;
   their Monte Carlo simulation still runs.
7. **Verdict.** Compares target share among replays that hit either stop or
   target against the break-even share implied by the target R. It does not
   disguise a weak or inconclusive result.

## Steps

1. Confirm `trader/plan.json` is the intended current entry/exit signal from
   trader-exit.
2. Run `timemachine.py run --write`. It uses the plan's `flat_by` automatically.
   Use `--until HH:MM` only when intentionally testing a different endpoint.
3. Report target-first, stop-first and neither odds; endpoint price range;
   average R; historical outcomes; verdict and any caveats. Always state seed.
4. If the simulation and historical replay disagree, say so clearly. A
   simulation is not a reason by itself to take a trade.
5. Check trader-news for relevant announcements before the user decides.

## Rules and limitations

- 5-minute bars only. A live/incomplete bar is never used.
- Requires at least 20 eligible complete historical sessions.
- Same plan, same data and same seed produce the same results.
- The shared session calendar is the single source of truth. Its session
  profiles and holiday coverage must be checked against current NSE notices;
  the repository currently marks some session-hour data as unverified.
- Yahoo chart data is unofficial, may lag, and may be incomplete. Earnings,
  news, corporate actions, circuit limits and halts can create behaviour the
  recent sample does not contain.
- The historical replay currently understands the opening-range breakout
  (`orb`) setup only.
- No order placement, broker integration, position sizing or automatic trade
  execution. This skill only analyses a proposed signal; the user decides
  whether to enter or exit manually.
- Education and tooling, not financial advice. A favourable replay is not a
  guarantee or a recommendation.
