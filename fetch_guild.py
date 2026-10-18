"""Fetch siege offense + defense history for every guild member into one HTML page.

Usage:
    python fetch_guild.py                 # all current members, last 365 days
    python fetch_guild.py --days 90
    python fetch_guild.py --skip-check    # skip the per-member SWGT total check (faster)

Uses the same login, cache and report template as fetch_siege.py.
Outputs go to ./output/guild by default.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_siege import (
    HERE,
    NO_SIEGES,
    SWGT,
    build_data,
    check_totals,
    common_args,
    guild_members,
    load_battles,
    match_ids,
    match_rows,
    member_page,
    run,
    write_outputs,
)


def main():
    ap = common_args(__doc__)
    ap.add_argument("--out", default=os.path.join(HERE, "output", "guild"))
    ap.add_argument("--skip-check", action="store_true",
                    help="don't load each member's SWGT tabs (match list then comes from your own tabs only)")
    args = ap.parse_args()

    swgt = SWGT(args.url)
    swgt.login()
    members = guild_members(member_page(swgt, "siegeOffenseTab", ""))
    print(f"{len(members)} guild members")

    matches, meta, pages = {}, {}, {}
    todo = members if not args.skip_check else [m for m in members if m[2]]
    for n, (focus, name, _) in enumerate(todo, 1):
        print(f"  [{n}/{len(todo)}] {name}")
        off = member_page(swgt, "siegeOffenseTab", focus, args.days)
        dfn = member_page(swgt, "siegeDefenseTab", focus, args.days)
        pages[name] = (off, dfn)
        matches.update(match_ids(off))
        matches.update(match_ids(dfn))
        meta.update(match_rows(dfn))
        meta.update(match_rows(off))
        time.sleep(0.5)
    print(f"{len(matches)} sieges in range")
    if not matches:
        sys.exit(NO_SIEGES)

    names = [m[1] for m in members]
    battles, icons = load_battles(swgt, matches, meta, set(names), args.refresh)
    if not args.skip_check:
        bad = [name for name, (off, dfn) in pages.items() if not check_totals(battles, name, off, dfn)]
        print("Totals match SWGT's summary for every member." if not bad
              else f"Totals differ for: {', '.join(bad)}")

    guild = next(iter(meta.values()))["guild"] if meta else ""
    data = build_data(names, guild, args.days, matches, meta, battles, icons)
    write_outputs(data, args.out, f"{guild} Siege Record")


if __name__ == "__main__":
    run(main)
