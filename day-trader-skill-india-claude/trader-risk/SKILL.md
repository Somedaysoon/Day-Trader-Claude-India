---
name: trader-risk
description: >-
  Risk Manager for NSE day-trade picks. Reads the sized pick (trader/sizing.json)
  and checks it against your rules: the pick is for today, the NSE is in
  continuous trading and inside your entry window and flat-by time (not the
  pre-open or closing auction), a stop and target on the right sides, order and
  position and cash limits, the ALL-IN loss at the stop against your max loss
  (charges and slippage included), reward:risk, and a cost gate that blocks a
  pick whose charges and slippage eat too much of the reward. Worked out again
  from the numbers, not copied. Says PASS or BLOCK with the reasons, lists what
  it cannot check, and writes trader/risk.json. It never places an order. Use
  when the user says "is this trade OK", "check my risk", "does this pass",
  or after /trader-budget.
argument-hint: "[--sizing FILE] [--prices FILE] [--json]"
---

# trader-risk

The Risk Manager is a second pair of eyes on one sized pick. It does not trust
the sizing file: it takes the entry, stop, target and quantity and works the
money out again, so a hand-edited file or a stale price cannot slip through. The
answer is PASS or BLOCK with the reasons. A BLOCK means do not take this pick as
it stands. A PASS is not a reason to take it.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                         # trader/sizing.json, writes trader/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-11-23 10:15"
python3 "${CLAUDE_SKILL_DIR}/check.py" --json --no-write
```

Other flags: `--config FILE`, `--prices FILE`, `--charges FILE`, `--out FILE`.
Exit code 0 = PASS, 1 = BLOCK or an error.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/sizing.json` | the pick `/trader-budget` sized: symbol, group, qty, entry, stop, target, date, flat-by |
| `trader/config.json` | your rules (the same file `/trader-budget` reads) |
| `trader/prices.json` | an optional latest price; used only if under 15 minutes old |
| `trader/charges.json` | your broker's charges, if you have them; else placeholders marked unverified |
| `trader/risk.json` | the verdict, every check, the blocks, the warnings, and what was not checked |

## What blocks a pick

- **pick.** A missing or impossible symbol, side, quantity, entry, stop or target;
  anything but a long; a symbol on your `blocked` list. It stops there.
- **today.** The pick is for another day.
- **window.** The NSE is closed (weekend, holiday, outside continuous trading),
  the pre-open or closing auction is running, it is before `no_entries_before`,
  at or after the last entry time (30 minutes before continuous trading ends for
  the stock's group), or past the flat-by time. The hours depend on the date and
  the group: the closing-auction stocks stop at 15:15 until 4 Dec 2026, and all
  stocks run to 15:30 from 7 Dec. These hours come from news and broker reports,
  not NSE's circulars.
- **stop.** A stop at or above the entry, or a target at or below it. A pick
  always needs a stop: there is no setting to turn that off.
- **size.** Under `min_order_inr`, over `max_order_inr`, over `max_position_pct`
  of the budget, or more than your cash after the buffer. No leverage is assumed.
- **loss.** The **all-in** loss at the stop (price move, round-trip charges,
  slippage on both fills) over `max_loss_per_trade_inr`. The entry used is the
  higher of the pick's entry and a fresh price.
- **reward:risk.** Under `min_reward_risk`.
- **costs.** Charges and slippage take more than `max_cost_pct_of_reward` (25) of
  the gain at the target. A tight target can be worth less than zero after
  costs, and this says so.
- **price.** A fresh price already at or under the stop, or at or over the target.

Warnings never block: no price, or one too old to use; a signal older than 30
minutes; placeholder charge rates; a year the holiday list does not cover.

## Not checked (every output says so)

Price bands and circuit limits (a stop cannot fill at a lower circuit),
surveillance and trade-for-trade lists, bid-ask spread and market depth, margin
and your broker's own risk checks. There is no free data for these here. Tell the
user to look at them in their own broker app before acting.

## Steps

1. Run `check.py` after `/trader-budget --write`. Say PASS or BLOCK first.
2. For a BLOCK, give each reason in plain words and what would fix it (a closer
   stop, a smaller size, waiting for the window, skipping the trade). Do not
   loosen the user's limits to turn a BLOCK into a PASS.
3. For a PASS, repeat the warnings and the not-checked list. Do not say the trade
   is good.
4. Remind the user it only checks: nothing was placed.

## Example (SYNTHETIC data, from the test fixtures)

```
Risk check for AAA, 2026-11-23 10:15 IST: PASS
  PASS  window       open; entries until 14:45 IST, flat by 15:05 IST
  WARN  price        no price to check against: checked on the pick's own entry 102.00 only.
  PASS  loss         the loss at the stop is Rs 499.10 all-in, inside your Rs 500 limit
  PASS  reward:risk  reward:risk 2.00 before costs, 1.65 after
  PASS  costs        charges and slippage take 6.7% of the reward (limit 25%)
  WARN  charges      the charge rates are placeholders, not checked against any broker's schedule
```

At 3:20 PM the same pick is a BLOCK: the closing auction session is running.

## What it never does

It never places an order, never loosens a rule, and never says a pick is safe. It
cannot see price bands, circuits, spread or depth, and it keeps no record of
what you traded, so it has no daily-loss or trades-per-day limits.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- This tool places no orders and holds no broker keys. Any trade is the user's own decision, in their own broker app.
- Limits are the user's own and the defaults are placeholders. Never change a limit to make a pick pass.
