---
name: trader-budget
description: >-
  Budget Manager for NSE day trades. Sizes ONE pick from trader/plan.json to your
  budget and your max loss per trade, in whole shares, counting the round-trip
  charges (brokerage, STT, exchange fees, SEBI fee, stamp duty, GST) and slippage
  in the loss at the stop, so a small account gets fewer shares than the plain
  division suggests. Shows what the trade is worth after costs and how much of
  the reward the costs take. No leverage is assumed. Refuses a size of zero and
  a reward:risk under your minimum. Writes trader/sizing.json for /trader-risk.
  It produces a size, never an order, and places nothing. Use when the user says
  "how many shares", "size this trade", "what is my position size", "how much
  can I risk", or after /trader-exit, before /trader-risk.
argument-hint: "[--budget RUPEES] [--at PRICE] [--write]"
---

# trader-budget

The Budget Manager answers one question: how many shares fit your rules if the
stop is hit. It sizes one pick at a time from the Exit Planner's plan and says
the answer in rupees, including what the trade costs to make. It writes a size,
not an order: this tool never places anything.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                 # also save trader/sizing.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --at 102.50 --write     # size at an entry of Rs 102.50
python3 "${CLAUDE_SKILL_DIR}/size.py" --budget 50000 --json
```

Other flags: `--plan FILE`, `--config FILE`, `--prices FILE`, `--charges FILE`,
`--slippage-pct`, `--now "2026-11-23 10:15"` (Indian time).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan `/trader-exit` wrote: symbol, entry, stop, target, tick, flat-by, group. Must be for today |
| `trader/config.json` | your limits (all rupees, all optional; see below) |
| `trader/charges.json` | your broker's charges, if you have them; else placeholders marked unverified |
| `trader/prices.json` | an optional fresh price (under 15 minutes old) |
| `trader/sizing.json` | with `--write`: the size, the limits, the money after costs. Nothing is written when the pick is refused |

## How many shares

Whole shares, the largest count inside every limit:

- **max loss.** The loss if the stop is hit, **all-in** (the price move to the
  stop, plus round-trip charges, plus slippage on both fills), must stay within
  `max_loss_per_trade_inr`. It is found by search, so it is exact.
- **position cap.** `budget x max_position_pct / entry`.
- **max order.** `max_order_inr / entry`.
- **cash.** `(budget minus the cash buffer) / entry`. **No leverage is assumed:**
  the position must fit in your own cash.

The all-in rule is why a small account gets fewer shares: brokerage is a flat
amount per order, so charges are a bigger part of a small trade.

The entry it sizes at is `--at`, else the plan's entry, or a **fresh higher
price** from `trader/prices.json` (a price that ran up since the plan means a
bigger loss at the same stop).

## Config (placeholders, not advice: set your own)

`budget`, `cash_buffer_pct` (10), `max_position_pct` (75), `max_order_inr`
(75000), `min_order_inr` (1000), `max_loss_per_trade_inr` (500),
`min_reward_risk` (1.0), `slippage_pct` (0.05), `blocked` ([]). `config.example.json`
in this folder shows the shape.

The charge rates in `_common/data/charges.json` are **placeholders from memory,
marked `verified: false`**. Put your broker's published numbers in
`trader/charges.json` (same keys) and set `verified` to true after comparing
them. The output says which set it used.

## Steps

1. Run `size.py` after `/trader-exit`. Show the limits table and say which one
   binds, then the size and the money at the stop and at the target.
2. If it refuses (zero shares, or reward:risk too low), say why in one line and
   suggest a closer stop from `/trader-exit` or skipping the trade. Do not
   resize by hand to get around it.
3. Say how much of the reward the costs take. If it is a lot, that is the finding.
4. `--write` the sizing and hand off to `/trader-risk`.

## Example (SYNTHETIC data, from the test fixtures)

```
Limits (whole shares; the smallest one wins):
  max loss      Rs 500 all-in (move to the stop + charges + 0.05% slippage), Rs 1.60 a share before costs   276 shares  <- binds
  position cap  75% of Rs 1,00,000 = Rs 75,000 / 102.00                                                      735 shares
  cash          budget Rs 1,00,000 minus the Rs 10,000 cash buffer = Rs 90,000 / 102.00                       882 shares

Size: 276 shares of AAA = Rs 28,152 at Rs 102.00.
If the stop is hit you lose Rs 499.10 all-in: Rs 441.60 of price move + Rs 57.50 of charges and slippage.
Reward:risk left after costs: 1.65 (it was 2.00 before). Costs and slippage take 6.7% of the reward.
```

Plain division would have said 312 shares (500 / 1.60). Counting costs, it is 276.

## What it never does

It never places an order, never sets the stop (that is `/trader-exit`), never
borrows, and never sizes a short or an option. It does not know your broker's
margin rules.

## Safety

- Education and tooling, not financial advice. The default limits are placeholders, not a recommendation.
- This tool places no orders and holds no broker keys. Any trade is the user's own decision, in their own broker app.
- Costs and slippage are estimates. The charge rates are unverified until you replace them with your broker's.
