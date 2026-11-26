# Day Trader skill for the Indian market

A portable set of ten decision-support skills for NSE intraday trading. The pack scans the market, checks news and disclosures, signals entries and exits, estimates position size, validates risk and replays proposed trades. It uses free Yahoo chart data plus official exchange/regulator filing sources where available.

**It does not connect to a broker or place, modify, or cancel orders.** It only produces analysis and signals. The project places no orders. You execute trades manually.

This is an India adaptation of [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), originally built for US stocks. The original MIT license and attribution are retained. The India version changes market conventions, clock/calendar, currency/cost calculations, data sources and disclosure workflows.

## Status

The India port contains ten skills; see the table below for their current scope.

## Included skills

| Skill | Purpose |
|---|---|
| `trader-scanner` | NSE EQ universe and daily/intraday setup scans |
| `trader-news` | RBI/SEBI/PIB RSS, configured NSE RSS, BSE CSV import and watchlist event matching |
| `trader-entry` | NSE opening-range and price-level entry signals |
| `trader-exit` | Stop, target, trailing-stop and `flat_by` exit signals |
| `trader-budget` | Position sizing against all-in risk limits and estimated charges |
| `trader-risk` | Proposed-trade validation and PASS/BLOCK verdict |
| `trader-timemachine` | Indian-session Monte Carlo and historical replay |
| `trader-alerts` | Entry/exit/stop/target/time alerts in IST |
| `trader-analyst` | Indian exchange/company financial filing and XBRL analysis |
| `trader-insider` | Indian insider, promoter, bulk/block and super-investor disclosures |

## Workflow

`scanner → news/context → entry signal → exit plan → budget/size → risk check → time-machine review → alerts`

Use the skills individually as needed. The workflow is advisory: a PASS or signal is not a command to trade. Claude invocation names: `/trader-scanner`, `/trader-news`, `/trader-entry`, `/trader-exit`, `/trader-budget`, `/trader-risk`, `/trader-timemachine`, `/trader-alerts`, `/trader-analyst`, `/trader-insider`. The ChatGPT package uses the corresponding `@trader-*` skill names.

## Start here

1. Read [`CONFIGURATION.md`](CONFIGURATION.md) and create the runtime `trader/` folder.
2. Configure your own limits in `trader/config.json` and your broker's actual charges in `trader/charges.json`.
3. Configure official news feeds in `trader/news_feeds.json` and your investor watchlist in `trader/insider_watchlist.json`.
4. Verify NSE holidays and session hours before relying on intraday timing.
5. Run the included tests and compare the first live-day outputs with your broker/exchange terminal.

## Platform packages

- **Claude package:** retains `.claude-plugin/` metadata and the skill folders at the repository root.
- **ChatGPT/Aru package:** uses the portable Agent Plugins `plugin.json` manifest and places the same ten skill folders under `skills/`.

Both packages share the same trading code, rules, data sources, examples and behaviour. Only platform packaging, skill metadata and invocation/path conventions differ. The ChatGPT package contains no MCP server and no broker integration; script execution requires an environment that can access the package files and run Python.

## Important limitations before live use

- `common/data/sessions.json` is explicitly unverified against the underlying NSE circulars. The reported 15:15/15:30 session profiles must be checked.
- `common/data/nse_holidays.json` covers 2026 only; later circulars were not checked and 2027 is not included.
- `common/data/charges.json` contains unverified placeholder rates. Replace them with your broker's published charges.
- Yahoo quotes/candles are unofficial and may be delayed or incomplete.
- NSE scanner file layouts and download addresses need real-world verification.
- The analyst's Indian XBRL parser is a first implementation: taxonomy mapping and real-company coverage need broader validation. It does not automatically download all filings.
- The risk skill checks one proposed trade at a time. It does not track actual fills or enforce daily loss/trade-count limits.
- Price bands, circuits, surveillance lists, spread, depth and broker margin checks are not covered.

See `CONFIGURATION.md` for exact paths, setup commands and reasons for each setting. See each skill's `DATA_SOURCES.md` for source-specific details.

## Runtime requirements

Python 3.8+ and standard library only for the bundled scripts. Internet access is needed for the Yahoo chart/feed fetches. Official exchange filings and BSE CSV exports may need manual download.

## Developer checks

```bash
python3 tools/sync_common.py --check
python3 -m unittest discover -s tests
```

`common/` is the source of truth for shared Indian clock/calendar/cost/data-quality modules. After editing it, run `python3 tools/sync_common.py` to refresh each skill's `_common/` copy.

## Fine print

The scripts use Python standard-library modules only and require Python 3.8 or newer.

## Disclaimer

Educational tooling, not financial advice. Day trading can result in substantial losses. A signal or PASS verdict is not a promise of profit or a reason to trade. Verify current prices, costs, exchange rules and broker conditions yourself.
