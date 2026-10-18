"""Fetch a player's siege offense + defense history from SWGT and build an HTML report.

Usage:
    python fetch_siege.py                      # logged-in player, last 365 days
    python fetch_siege.py --days 90
    python fetch_siege.py --player "Vanne2.0"  # any member of your guild
    python fetch_siege.py --refresh            # re-download every match page

For every guild member on one page, use fetch_guild.py.

Settings come from a .env file next to this script (see .env.example):
    SWGT_SUBDOMAIN=reisfarmer      # the part before .swgt.io in your guild's SWGT address
    SWGT_USER=your_username
    SWGT_PASSWORD=your_password
Anything missing is asked for when the script runs.

Outputs (in --out, default ./output):
    siege_data.json     everything parsed
    attacks.csv         one row per attack
    defenses.csv        one row per time a defense was hit
    siege_report.html   interactive report, self-contained (monster icons embedded)
"""
import argparse
import base64
import concurrent.futures
import csv
import datetime
import getpass
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) swgt-siege-export"
ICON_BASE = "https://do9d4mpqk497d.cloudfront.net/common/images/monsters36/"
MATCH_RE = re.compile(r"siegeBattle/specific&siegeID=(\d+)&matchID=(\d+)&guildID=(\d+)")


def load_env():
    """Read KEY=VALUE lines from .env beside the script; real environment variables win."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


class SWGT:
    def __init__(self, subdomain=None):
        subdomain = subdomain or os.environ.get("SWGT_SUBDOMAIN") or input("SWGT subdomain (the part before .swgt.io): ")
        # Accept "reisfarmer", "reisfarmer.swgt.io" or a full https:// address.
        subdomain = re.sub(r"^https?://|\.swgt\.io.*$", "", subdomain.strip().lower()).strip("/")
        self.base = f"https://{subdomain}.swgt.io"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener.addheaders = [("User-Agent", UA)]

    def get(self, path, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        with self.opener.open(url, timeout=120) as r:
            return r.read().decode("utf-8", errors="replace")

    def login(self):
        user = os.environ.get("SWGT_USER") or input("SWGT username: ")
        password = os.environ.get("SWGT_PASSWORD") or getpass.getpass("SWGT password: ")
        data = urllib.parse.urlencode({"username": user, "password": password,
                                       "oldBrowser": "Y", "keepMeLoggedIn": "Y"}).encode()
        req = urllib.request.Request(self.base + "/controllers/account/login", data=data,
                                     headers={"Referer": self.base + "/login.jsp"})
        self.opener.open(req, timeout=60).read()
        if 'id="password"' in self.get("/"):
            sys.exit("Login failed: check your SWGT username and password.")
        print("Logged in.")


def member_page(swgt, tab, focus, days=None):
    params = {"selectedTab": tab, "selectedGuildMemberFocus": focus or ""}
    if days:
        params["siegeDateCriteria"] = days
    return swgt.get("/controllers/guildMembers/specific", params)


def guild_members(page):
    """[(focusID, name, is_selected)] from the guild member dropdown."""
    opts = re.findall(r'<option value="(P_\d+)"\s*(selected="selected")?\s*>([^<\n]*)', page)
    return [(v, html.unescape(label).strip(), bool(sel)) for v, sel, label in opts]


def find_player(page, name):
    members = guild_members(page)
    for value, label, selected in members:
        if (name and label.lower() == name.lower()) or (not name and selected):
            return value, label
    sys.exit(f"Player {name!r} not found. Members: " + ", ".join(m[1] for m in members))


def summary_totals(page, table_id):
    """W/L totals from SWGT's own per-siege table, used to cross-check the parse."""
    start = page.find(f'id="{table_id}"')
    body = page[start:page.find("</table>", start)]
    w = l = 0
    # Only per-siege rows carry a data-sort date; this skips the table's totals row.
    for row in re.findall(r"<tr>(\s*<td[^>]*data-sort=.*?)</tr>", body, re.S):
        cells = re.findall(r'<td class="text-center">\s*(\d*)\s*</td>', row)
        if len(cells) >= 2:
            w += int(cells[0] or 0)
            l += int(cells[1] or 0)
    return w, l


def match_rows(page):
    """Date + opponent guilds per match, from a member siege tab."""
    out = {}
    for m in re.finditer(r'data-sort="(\d+)">.*?matchID=(\d+).*?</td>\s*<td[^>]*>(.*?)</td>', page, re.S):
        names = [html.unescape(re.sub(r"<[^>]+>", "", x)).strip()
                 for x in re.split(r"<span[^>]*>VS</span>", m.group(3))]
        out[m.group(2)] = {"ts": int(m.group(1)), "guilds": [n for n in names[1:] if n], "guild": names[0]}
    return out


def match_ids(page):
    return {match_id: (siege_id, guild_id) for siege_id, match_id, guild_id in MATCH_RE.findall(page)}


def monsters(fragment, icons):
    """Monster names from <img alt=...> tags; remembers each name's icon file in `icons`."""
    names = []
    for tag in re.findall(r"<img[^>]*>", fragment):
        alt = re.search(r'alt=\\?"([^"\\]*)', tag)
        src = re.search(r"monsters36\\?/(unit_icon_[\w]+\.png)", tag)
        if alt:
            names.append(alt.group(1))
            if src and alt.group(1) not in icons:
                icons[alt.group(1)] = src.group(1)
    return names


def wizard_names(page):
    """{wizardID: name} from the guild member tables on a match page (names as they were then)."""
    return dict((wid, html.unescape(name).strip()) for name, wid in
                re.findall(r'<td[^>]*>([^<>\n]+?)\s*<a href="/controllers/guildMembers/specific\?wizardID=(\d+)', page))


def section(chunk, name, nxt):
    i = chunk.find(f'"{name}":')
    j = chunk.find(f'"{nxt}":', i) if nxt else len(chunk)
    return chunk[i:j] if i >= 0 else ""


def parse_battle_logs(page, players, match_id, icons, ids):
    """Yield offense/defense logs from a match page for the given set of player names.

    `ids` maps wizardID -> current name, so battles fought under an old name still count."""
    for chunk in page.split('"logType":')[1:]:
        chunk = '"logType":' + chunk
        kind = re.search(r"\"forSorting\":\s*'(\w+)'", section(chunk, "logType", "player"))
        if not kind or kind.group(1) not in ("OFFENSE", "DEFENSE"):
            continue
        p = section(chunk, "player", "oppPlayer")
        name = re.search(r"\('(?:<div[^>]*>)?([^<']*)", p)
        player = html.unescape(name.group(1)).strip() if name else ""
        wid = re.search(r"SIEGE_BATTLE_LOG_(\d+)_", section(chunk, "notes", "hitCount"))
        player = ids.get(wid.group(1), player) if wid else player
        if player not in players:
            continue
        o = section(chunk, "oppPlayer", "oppGuildName")
        ts = int(re.search(r'"forSorting":\s*"(\d+)"', section(chunk, "battleDateTime", "battleResult")).group(1))
        res = re.search(r'"forSorting":\s*"([^"]*)"', section(chunk, "battleResult", "notes")).group(1)
        guild = re.search(r"'(.*)'", section(chunk, "oppGuildName", "battleDateTime"))
        yield {
            "player": player,
            "type": kind.group(1).lower(),
            "match": match_id,
            "ts": ts,
            "date": datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
            "team": monsters(p, icons),
            "opp": html.unescape(re.search(r"\('([^<']*)", o).group(1)).strip(),
            "oppTeam": monsters(o, icons),
            "oppGuild": html.unescape(re.sub(r"<[^>]+>", "", guild.group(1) if guild else "")).strip(),
            # Result is always from the player's point of view: on defense, "W" means the defense held.
            "result": "W" if res.startswith("Victory") else "L" if res.startswith("Defeat") else "D",
        }


def load_battles(swgt, matches, meta, players, refresh=False):
    """Download (or reuse cached) match pages and parse every battle for `players`."""
    os.makedirs(CACHE, exist_ok=True)
    now, paths = time.time(), []
    for n, (match_id, (siege_id, guild_id)) in enumerate(sorted(matches.items()), 1):
        path = os.path.join(CACHE, f"{match_id}.html")
        # Pages for sieges older than 8 days are final, so cache them.
        final = os.path.exists(path) and now - meta.get(match_id, {}).get("ts", now) > 8 * 86400
        if refresh or not final:
            print(f"  [{n}/{len(matches)}] downloading match {match_id}")
            page = swgt.get("/controllers/siegeBattle/specific",
                            {"siegeID": siege_id, "matchID": match_id, "guildID": guild_id})
            open(path, "w", encoding="utf-8").write(page)
            time.sleep(1)
        paths.append((match_id, path))

    # Players can rename; the newest match shows each account's current name.
    latest = {}
    for match_id, path in sorted(paths, key=lambda x: meta.get(x[0], {}).get("ts", 0)):
        latest.update(wizard_names(open(path, encoding="utf-8").read()))
    ids = {wid: name for wid, name in latest.items() if name in players}

    battles, icons = [], {}
    for match_id, path in paths:
        battles.extend(parse_battle_logs(open(path, encoding="utf-8").read(), players, match_id, icons, ids))

    seen, unique = set(), []
    for b in sorted(battles, key=lambda b: b["ts"]):
        key = (b["player"], b["type"], b["ts"], tuple(b["team"]), b["opp"])
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique, icons


def embed_icons(icons):
    """Download each monster icon once (cached on disk) and return {name: data URI}."""
    folder = os.path.join(CACHE, "icons")
    os.makedirs(folder, exist_ok=True)

    def fetch(item):
        name, file = item
        path = os.path.join(folder, file)
        if not os.path.exists(path):
            try:
                req = urllib.request.Request(ICON_BASE + file, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    open(path, "wb").write(r.read())
            except OSError as e:
                print(f"  icon for {name} not downloaded: {e}")
                return name, None
        return name, "data:image/png;base64," + base64.b64encode(open(path, "rb").read()).decode()

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        return {name: uri for name, uri in pool.map(fetch, icons.items()) if uri}


def check_totals(battles, player, off_page, def_page):
    ok = True
    for kind, page, table in (("offense", off_page, "siegeOffenseList"), ("defense", def_page, "siegeDefenseList")):
        got = [b for b in battles if b["type"] == kind and b["player"] == player]
        w, l = sum(b["result"] == "W" for b in got), sum(b["result"] == "L" for b in got)
        sw, sl = summary_totals(page, table)
        ok &= (w, l) == (sw, sl)
        if (w, l) != (sw, sl):
            print(f"  {player} {kind}: parsed {w}W-{l}L but SWGT shows {sw}W-{sl}L")
    return ok


def write_outputs(data, out, title):
    os.makedirs(out, exist_ok=True)
    battles = data["battles"]
    with open(os.path.join(out, "siege_data.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if k != "icons"}, f, ensure_ascii=False, indent=1)
    for kind, fname in (("offense", "attacks.csv"), ("defense", "defenses.csv")):
        with open(os.path.join(out, fname), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["player", "date", "match", "opp_guild", "team", "opp_player", "opp_team", "result"])
            for b in battles:
                if b["type"] == kind:
                    w.writerow([b["player"], b["date"], b["match"], b["oppGuild"], " / ".join(b["team"]),
                                b["opp"], " / ".join(b["oppTeam"]), b["result"]])

    template = open(os.path.join(HERE, "report_template.html"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path = os.path.join(out, "siege_report.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(template.replace("/*__SIEGE_DATA__*/null", payload)
                .replace("<title>Siege Record</title>", f"<title>{html.escape(title)}</title>"))
    print(f"Wrote {path} ({os.path.getsize(path) // 1024} KB)")


def build_data(player_names, guild, days, matches, meta, battles, icons):
    return {
        "players": player_names,
        "guild": guild,
        "range": days,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sieges": [{"match": m, "date": datetime.datetime.fromtimestamp(v["ts"]).strftime("%Y-%m-%d"),
                    "opponents": v["guilds"]} for m, v in sorted(meta.items(), key=lambda x: x[1]["ts"])
                   if m in matches],
        "battles": battles,
        "icons": embed_icons({n: f for n, f in icons.items() if n != "null"}),
    }


def common_args(description):
    # Member and guild names often use symbols the Windows console code page can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subdomain", help="SWGT subdomain, e.g. reisfarmer (default: SWGT_SUBDOMAIN from .env)")
    ap.add_argument("--days", default="365", help="30, 60, 90, 180 or 365 (default 365), or an SSD_xx season id")
    ap.add_argument("--refresh", action="store_true", help="ignore cached match pages")
    return ap


def main():
    ap = common_args(__doc__)
    ap.add_argument("--player", help="guild member name (default: the logged-in player)")
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    args = ap.parse_args()

    swgt = SWGT(args.subdomain)
    swgt.login()
    focus, player = find_player(member_page(swgt, "siegeOffenseTab", ""), args.player)
    off_page = member_page(swgt, "siegeOffenseTab", focus, args.days)
    def_page = member_page(swgt, "siegeDefenseTab", focus, args.days)

    matches = {**match_ids(off_page), **match_ids(def_page)}
    meta = {**match_rows(def_page), **match_rows(off_page)}
    print(f"{player}: {len(matches)} sieges in range")

    battles, icons = load_battles(swgt, matches, meta, {player}, args.refresh)
    if check_totals(battles, player, off_page, def_page):
        print("Totals match SWGT's summary.")
    guild = next(iter(meta.values()))["guild"] if meta else ""
    data = build_data([player], guild, args.days, matches, meta, battles, icons)
    write_outputs(data, args.out, f"{player} Siege Record")


if __name__ == "__main__":
    main()
