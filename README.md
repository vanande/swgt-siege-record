# SWGT Siege Record

Turns your guild's [SWGT](https://swgt.io) siege logs into an interactive HTML report:

- the enemy defenses you struggle with, and which of your teams beats them
- the win rate of each of your offense teams
- how often your siege defenses hold
- siege-by-siege results, and every battle in one searchable list

`fetch_guild.py` puts every guild member on one page, with a roster to compare everyone.
Monster icons are built into the report, so it is a single file you can open offline or send to anyone.

## Before you start

You need:

- **An SWGT account** that is a member of your guild's SWGT site, for example `https://reisfarmer.swgt.io`.
  This is the login you use on the SWGT website, not your Summoners War login.
- **A guild that uploads its sieges to SWGT.** This happens through the SWGT plugin in the
  Summoners War Exporter (SWEX). If your guild's sieges show up on SWGT's Siege pages, you're set.
- **Python 3.9 or newer.** Get it from https://www.python.org/downloads/. On Windows, tick
  **"Add python.exe to PATH"** on the first screen of the installer.
  Nothing else needs to be installed.

## 1. Download

Either:

- On the GitHub page, click **Code → Download ZIP**, then unzip it somewhere, for example your Desktop.
- Or, if you use git: `git clone https://github.com/vanande/swgt-siege-record.git`

## 2. Open a terminal in the folder

- **Windows:** open the `swgt-siege-record` folder in File Explorer, click the address bar,
  type `cmd` and press Enter.
- **Mac:** right-click the folder in Finder and choose **New Terminal at Folder**.

Check that Python works by typing `python --version` and pressing Enter.
It should print `Python 3.9` or higher. On Windows, if that fails, try `py --version`
and use `py` instead of `python` in the commands below. On a Mac, use `python3`.

## 3. Add your login

Make a copy of `.env.example` named `.env`:

| Windows (cmd)                 | Mac / Linux               |
|-------------------------------|---------------------------|
| `copy .env.example .env`      | `cp .env.example .env`    |

Open `.env` in any text editor (on Windows: `notepad .env`) and fill in your details:

```
SWGT_URL=https://reisfarmer.swgt.io
SWGT_USER=your_swgt_username
SWGT_PASSWORD="your_swgt_password"
```

`SWGT_URL` is your guild's SWGT address: open your guild's SWGT site in your browser and copy
it from the address bar. Anything after `.swgt.io` (like `/login.jsp`) is ignored.
`SWGT_USER` and `SWGT_PASSWORD` are your SWGT website login. Keep the quotes around the password.

If you skip this step, the script asks for these three values each time it runs instead.

## 4. Run it

```
python fetch_siege.py
```

The first run downloads every siege from the last year, which takes a few minutes.
It prints its progress and ends like this:

```
Logged in.
Vanande: 23 sieges in range
  [1/23] downloading match 2025120101000044
  ...
Totals match SWGT's summary.
Wrote C:\...\swgt-siege-record\output\siege_report.html (918 KB)
```

Open `output/siege_report.html` in your browser (double-click it). That's your report.

For the whole guild on one page:

```
python fetch_guild.py
```

That writes `output/guild/siege_report.html`. It takes longer because it also checks each
member's totals against SWGT.

## Options

| Command | What it does |
|---|---|
| `python fetch_siege.py --player "Name"` | Report for another member of your guild. Use their exact in-game name. |
| `python fetch_siege.py --days 90` | Only the last 30, 60, 90, 180 or 365 days (default 365). |
| `python fetch_siege.py --days SSD_70` | One SWGT season. The season ids are in the date dropdown on SWGT's Siege Offense page. |
| `python fetch_siege.py --refresh` | Download every siege again instead of reusing saved ones. |
| `python fetch_siege.py --url https://other.swgt.io` | Use another guild's SWGT site for this run, instead of `SWGT_URL` from `.env`. |
| `python fetch_siege.py --out some_folder` | Write the report somewhere else, for example one folder per player. |
| `python fetch_guild.py --skip-check` | Faster guild run that doesn't compare each member's totals with SWGT. |

Add `--help` to either script to see all options.

Next to the report, each run also writes `attacks.csv` and `defenses.csv` (open them in Excel
or Google Sheets) and `siege_data.json`.

Finished sieges are saved in `cache/`, so later runs only download new ones and finish in seconds.

## Checking the numbers

After each run the script compares its totals with SWGT's own summary. `Totals match SWGT's summary`
means the report agrees with SWGT. If a player differs by a battle or two, it's usually a battle right at
the edge of the date range. A big difference means something didn't read correctly: please open an issue
on GitHub and include the message the script printed.

## Troubleshooting

| Message | What to do |
|---|---|
| `'python' is not recognized` | Python isn't installed or isn't on PATH. Reinstall it and tick "Add python.exe to PATH", or use `py` instead of `python`. |
| `SWGT_URL is missing`, `SWGT_USER is missing`… | That line is missing or empty in `.env`. Compare your file with `.env.example`. |
| `SWGT_URL must be the full address` | Use the whole address, like `https://reisfarmer.swgt.io`, not just `reisfarmer`. |
| `… is not an SWGT guild address` | `SWGT_URL` must end in `.swgt.io`. Copy it from the address bar when you're on your guild's SWGT site. |
| `There is no SWGT site at https://….swgt.io` | The address is misspelled. Copy it from the address bar when you're on SWGT. |
| `Login failed` | Check `SWGT_USER` and `SWGT_PASSWORD`. Try logging in on the SWGT website with the same details. |
| `Found ".env.txt" but no ".env"` | Notepad saved the file as `.env.txt`. Rename it to `.env`. In File Explorer, turn on **View → File name extensions** to see the real name. |
| `No guild members found` | Your SWGT account isn't a member of the guild on that SWGT site. |
| `No sieges found in this date range` | Your guild hasn't uploaded sieges for that period. Try `--days 365`. |
| `Player 'X' not found` | The message lists every member; copy the name exactly as shown. |
| `Could not reach …` | Check your internet connection, or try again later if SWGT is down. |
| `Stopped.` | You pressed Ctrl+C. Run the command again: sieges already downloaded are reused. |

## Privacy

- Your login stays on your computer. It is only sent to your guild's swgt.io site.
- **Never share your `.env` file**: it contains your password.
- `output/` and `cache/` contain your guild's battle data. Share the report HTML if you want to,
  but think before posting the whole folder.

The script only reads what your account can already see on SWGT. It waits between downloads
to keep the load on SWGT low.

## Updating

If you used **Download ZIP**, download it again and copy your `.env` into the new folder.
If you used git, run `git pull` in the folder.

## Development

The code is checked with [ruff](https://docs.astral.sh/ruff/) (settings in `pyproject.toml`),
which also runs on GitHub for every push:

```
pip install ruff
ruff check .
```

## License

MIT. See [LICENSE](LICENSE).
