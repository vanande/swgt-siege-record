# SWGT Siege Record

Turns your guild's SWGT siege logs into an interactive HTML report: win rate per offense
team, the enemy defenses you struggle with (and which team beats them), how your defenses
hold, and siege-by-siege results. Monster icons are embedded, so the report is a single
file you can open offline or send to anyone.

## Setup

1. Install Python 3.9 or newer (https://www.python.org). Nothing else is needed.
2. Put these files in one folder: `fetch_siege.py`, `fetch_guild.py`, `report_template.html`, `.env.example`.
3. Copy `.env.example` to `.env` and fill in your guild's SWGT subdomain, username and password.

## Run

Open a terminal in the folder, then:

```
python fetch_siege.py                       # your own report, last 365 days
python fetch_siege.py --player "Name"       # any member of your guild
python fetch_siege.py --days 90             # 30, 60, 90, 180, 365, or a season id like SSD_70
python fetch_guild.py                       # every member on one page, with a roster
```

Reports are written to `output/siege_report.html` (one player) and
`output/guild/siege_report.html` (whole guild), together with CSV files of every battle.

The first run downloads every siege match page, which takes a few minutes. Finished
sieges are kept in `cache/`, so later runs only download new ones. Use `--refresh`
to download everything again.

After each run the script compares its totals with SWGT's own summary and prints any
member whose numbers differ.

## Notes

- Your login stays on your computer: the script only sends it to your guild's swgt.io site.
- Don't share `.env`, `cache/` or `output/` unless you mean to: `output/` holds your
  guild's battle data, and `.env` holds your password.
- The script only sees sieges that your guild uploaded to SWGT. Battles fought under an
  older in-game name are counted under the player's current name.

## Development

The code is checked with [ruff](https://docs.astral.sh/ruff/) (settings in `pyproject.toml`),
which also runs on GitHub for every push:

```
pip install ruff
ruff check .
```

## License

MIT. See [LICENSE](LICENSE).
