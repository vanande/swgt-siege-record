"""Fetch a player's siege offense + defense history from SWGT and build an HTML report.

Usage:
    python fetch_siege.py                      # logged-in player, last 365 days
    python fetch_siege.py --days 90
    python fetch_siege.py --player "Vanne2.0"  # any member of your guild
    python fetch_siege.py --refresh            # re-download every match page

For every guild member on one page, use fetch_guild.py.

Settings come from a .env file next to this script (see .env.example):
    SWGT_URL=https://reisfarmer.swgt.io   # your guild's SWGT address
    SWGT_USER=your_username
    SWGT_PASSWORD=your_password
Anything missing is asked for when the script runs.

Outputs (in --out, default ./output):
    siege_data.json     everything parsed
    attacks.csv         one row per attack
    defenses.csv        one row per time a defense was hit
    siege_report.html   interactive report, self-contained (monster icons embedded as one image)
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
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) swgt-siege-export"
ICON_BASE = "https://do9d4mpqk497d.cloudfront.net/common/images/monsters36/"
NO_SIEGES = ("No sieges found in this date range. SWGT only has sieges your guild uploaded "
             "(with the SWGT plugin in the Summoners War Exporter); try a longer --days range.")
MATCH_RE = re.compile(r"siegeBattle/specific&siegeID=(\d+)&matchID=(\d+)&guildID=(\d+)")


def read_file(path, binary=False):
    if binary:
        with open(path, "rb") as f:
            return f.read()
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def write_file(path, content):
    if isinstance(content, bytes):
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def load_env():
    """Read KEY=VALUE lines from .env beside the script; real environment variables win."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        if os.path.exists(path + ".txt"):
            print('Found ".env.txt" but no ".env". Rename it to ".env" (Notepad adds ".txt" when saving).')
        return
    for line in read_file(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


def setting(name, prompt, secret=False):
    """A value from .env / the environment, or typed in by the user when it's missing."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        value = getpass.getpass(prompt) if secret else input(prompt)
    except EOFError:
        value = ""
    if not value.strip():
        sys.exit(f"{name} is missing. Add it to the .env file next to the script (see .env.example).")
    return value.strip()


def parse_swgt_url(raw):
    """Turn the guild's SWGT address into https://<guild>.swgt.io, or exit with a clear message."""
    example = "for example: SWGT_URL=https://reisfarmer.swgt.io"
    raw = (raw or "").strip().strip("'\"")
    if not raw:
        sys.exit(f"SWGT_URL is missing. Put your guild's SWGT address in .env, {example}")
    if "://" not in raw:
        if re.fullmatch(r"[A-Za-z0-9-]+", raw):
            sys.exit(f"SWGT_URL must be the full address, not just the guild part. "
                     f"Did you mean SWGT_URL=https://{raw.lower()}.swgt.io ?")
        raw = "https://" + raw
    parts = urllib.parse.urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not re.fullmatch(r"[a-z0-9-]+\.swgt\.io", host):
        sys.exit(f"SWGT_URL {raw!r} is not an SWGT guild address. It should look like "
                 f"https://<your guild>.swgt.io ({example}).")
    # Pages like .../login.jsp are fine too: only the site address is kept.
    return f"https://{host}"


class SWGT:
    def __init__(self, url=None):
        self.base = parse_swgt_url(url or setting("SWGT_URL", "Your guild's SWGT address "
                                                  "(e.g. https://reisfarmer.swgt.io): "))
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.opener.addheaders = [("User-Agent", UA)]

    def get(self, path, params=None):
        return self.get_with_url(path, params)[0]

    def get_with_url(self, path, params=None):
        """Return (page text, final URL after redirects)."""
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with self.opener.open(url, timeout=120) as r:
                return r.read().decode("utf-8", errors="replace"), r.geturl()
        except urllib.error.URLError as e:
            sys.exit(f"Could not reach {self.base}: {getattr(e, 'reason', e)}. Check your internet connection.")

    def login(self):
        _, landed = self.get_with_url("/")
        if "noSiteSetup" in landed:
            sys.exit(f"There is no SWGT site at {self.base}. Check SWGT_URL in your .env: copy the address "
                     "from your browser's address bar while you're on your guild's SWGT site.")
        user = setting("SWGT_USER", "SWGT username: ")
        password = setting("SWGT_PASSWORD", "SWGT password: ", secret=True)
        data = urllib.parse.urlencode({"username": user, "password": password,
                                       "oldBrowser": "Y", "keepMeLoggedIn": "Y"}).encode()
        # parse_swgt_url only ever returns https://<guild>.swgt.io, so no file: or custom schemes.
        req = urllib.request.Request(self.base + "/controllers/account/login", data=data,  # noqa: S310
                                     headers={"Referer": self.base + "/login.jsp"})
        with self.opener.open(req, timeout=60) as r:
            r.read()
        if "/controllers/account/logout" not in self.get("/"):
            sys.exit("Login failed: check SWGT_USER and SWGT_PASSWORD in your .env "
                     "(they are the ones you use on the SWGT website, not your game login).")
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
    if not members:
        sys.exit("No guild members found. Your SWGT account must belong to the guild on this SWGT site.")
    for value, label, selected in members:
        if (name and label.lower() == name.lower()) or (not name and selected):
            return value, label
    sys.exit(f"Player {name!r} not found. Members: " + ", ".join(m[1] for m in members))


def summary_totals(page, table_id):
    """W/L totals from SWGT's own per-siege table, used to cross-check the parse."""
    start = page.find(f'id="{table_id}"')
    body = page[start:page.find("</table>", start)]
    wins = losses = 0
    # Only per-siege rows carry a data-sort date; this skips the table's totals row.
    for row in re.findall(r"<tr>(\s*<td[^>]*data-sort=.*?)</tr>", body, re.S):
        cells = re.findall(r'<td class="text-center">\s*(\d*)\s*</td>', row)
        if len(cells) >= 2:
            wins += int(cells[0] or 0)
            losses += int(cells[1] or 0)
    return wins, losses


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
    pairs = re.findall(r'<td[^>]*>([^<>\n]+?)\s*<a href="/controllers/guildMembers/specific\?wizardID=(\d+)', page)
    return {wid: html.unescape(name).strip() for name, wid in pairs}


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
            write_file(path, page)
            time.sleep(1)
        paths.append((match_id, path))

    # Players can rename; the newest match shows each account's current name.
    latest = {}
    for _match_id, path in sorted(paths, key=lambda x: meta.get(x[0], {}).get("ts", 0)):
        latest.update(wizard_names(read_file(path)))
    ids = {wid: name for wid, name in latest.items() if name in players}

    battles, icons = [], {}
    for match_id, path in paths:
        battles.extend(parse_battle_logs(read_file(path), players, match_id, icons, ids))

    seen, unique = set(), []
    for b in sorted(battles, key=lambda b: b["ts"]):
        key = (b["player"], b["type"], b["ts"], tuple(b["team"]), b["opp"])
        if key not in seen:
            seen.add(key)
            unique.append(b)
    return unique, icons


ICON_SIZE = 36   # SWGT's monster icons are 36x36
SPRITE_COLS = 24


def png_chunks(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield kind, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def png_to_rgba(data):
    """Decode an 8-bit RGB or RGBA, non-interlaced PNG (what SWGT serves) to (width, height, RGBA rows)."""
    header, idat = None, b""
    for kind, body in png_chunks(data):
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    width, height, depth, color, _, _, interlace = header
    if depth != 8 or color not in (2, 6) or interlace:
        raise ValueError(f"unsupported PNG format (bit depth {depth}, color type {color})")
    bpp = 4 if color == 6 else 3
    stride, raw, rows, prev = width * bpp, zlib.decompress(idat), [], bytearray(width * bpp)
    for y in range(height):
        start = y * (stride + 1)
        kind, row = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up, up_left = prev[i], prev[i - bpp] if i >= bpp else 0
            if kind == 1:
                row[i] = (row[i] + left) & 255
            elif kind == 2:
                row[i] = (row[i] + up) & 255
            elif kind == 3:
                row[i] = (row[i] + (left + up) // 2) & 255
            elif kind == 4:
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                row[i] = (row[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else up_left)) & 255
        prev = row
        if bpp == 3:
            rgba = bytearray(width * 4)
            rgba[0::4], rgba[1::4], rgba[2::4] = row[0::3], row[1::3], row[2::3]
            rgba[3::4] = b"\xff" * width
            row = rgba
        rows.append(bytes(row))
    return width, height, rows


def rgba_to_png(width, height, rows):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    # PNG "Up" filter (each byte minus the one above it) compresses icon sheets best.
    prev, filtered = bytes(width * 4), []
    for row in rows:
        filtered.append(b"\x02" + bytes((a - b) & 255 for a, b in zip(row, prev)))
        prev = row
    raw = b"".join(filtered)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def embed_icons(icons):
    """Download each monster icon once (cached on disk) and pack them all into one sprite sheet.

    One image instead of hundreds keeps the report small and shareable. Returns
    {"uri": PNG data URI, "cols": columns, "cell": icon size, "index": {name: position}}."""
    folder = os.path.join(CACHE, "icons")
    os.makedirs(folder, exist_ok=True)

    def fetch(item):
        name, file = item
        path = os.path.join(folder, file)
        try:
            if not os.path.exists(path):
                # ICON_BASE is a fixed https:// address.
                req = urllib.request.Request(ICON_BASE + file, headers={"User-Agent": UA})  # noqa: S310
                with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
                    write_file(path, r.read())
            return name, png_to_rgba(read_file(path, binary=True))
        except (OSError, ValueError, zlib.error) as e:
            print(f"  icon for {name} skipped: {e}")
            return name, None

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        decoded = [(name, img) for name, img in pool.map(fetch, sorted(icons.items())) if img]
    if not decoded:
        return None

    cell, cols = ICON_SIZE, SPRITE_COLS
    sheet_rows = -(-len(decoded) // cols)
    canvas = [bytearray(cols * cell * 4) for _ in range(sheet_rows * cell)]
    index = {}
    for n, (name, (_width, _height, rows)) in enumerate(decoded):
        x, y = (n % cols) * cell, (n // cols) * cell
        for dy, row in enumerate(rows[:cell]):
            line = row[:cell * 4]
            canvas[y + dy][x * 4:x * 4 + len(line)] = line
        index[name] = n
    png = rgba_to_png(cols * cell, sheet_rows * cell, [bytes(r) for r in canvas])
    return {"uri": "data:image/png;base64," + base64.b64encode(png).decode(), "cols": cols, "cell": cell,
            "index": index}


def check_totals(battles, player, off_page, def_page):
    ok = True
    for kind, page, table in (("offense", off_page, "siegeOffenseList"), ("defense", def_page, "siegeDefenseList")):
        got = [b for b in battles if b["type"] == kind and b["player"] == player]
        wins, losses = sum(b["result"] == "W" for b in got), sum(b["result"] == "L" for b in got)
        sw, sl = summary_totals(page, table)
        ok &= (wins, losses) == (sw, sl)
        if (wins, losses) != (sw, sl):
            print(f"  {player} {kind}: parsed {wins}W-{losses}L but SWGT shows {sw}W-{sl}L")
    return ok


def write_outputs(data, out, title):
    os.makedirs(out, exist_ok=True)
    battles = data["battles"]
    with open(os.path.join(out, "siege_data.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if k != "sprite"}, f, ensure_ascii=False, indent=1)
    for kind, fname in (("offense", "attacks.csv"), ("defense", "defenses.csv")):
        with open(os.path.join(out, fname), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["player", "date", "match", "opp_guild", "team", "opp_player", "opp_team", "result"])
            for b in battles:
                if b["type"] == kind:
                    w.writerow([b["player"], b["date"], b["match"], b["oppGuild"], " / ".join(b["team"]),
                                b["opp"], " / ".join(b["oppTeam"]), b["result"]])

    template = read_file(os.path.join(HERE, "report_template.html"))
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
        "sprite": embed_icons({n: f for n, f in icons.items() if n != "null"}),
    }


def common_args(description):
    # Member and guild names often use symbols the Windows console code page can't print.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    ap = argparse.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="guild SWGT address, e.g. https://reisfarmer.swgt.io (default: SWGT_URL from .env)")
    ap.add_argument("--days", default="365", help="30, 60, 90, 180 or 365 (default 365), or an SSD_xx season id")
    ap.add_argument("--refresh", action="store_true", help="ignore cached match pages")
    return ap


def main():
    ap = common_args(__doc__)
    ap.add_argument("--player", help="guild member name (default: the logged-in player)")
    ap.add_argument("--out", default=os.path.join(HERE, "output"))
    args = ap.parse_args()

    swgt = SWGT(args.url)
    swgt.login()
    focus, player = find_player(member_page(swgt, "siegeOffenseTab", ""), args.player)
    off_page = member_page(swgt, "siegeOffenseTab", focus, args.days)
    def_page = member_page(swgt, "siegeDefenseTab", focus, args.days)

    matches = {**match_ids(off_page), **match_ids(def_page)}
    meta = {**match_rows(def_page), **match_rows(off_page)}
    print(f"{player}: {len(matches)} sieges in range")
    if not matches:
        sys.exit(NO_SIEGES)

    battles, icons = load_battles(swgt, matches, meta, {player}, args.refresh)
    if check_totals(battles, player, off_page, def_page):
        print("Totals match SWGT's summary.")
    guild = next(iter(meta.values()))["guild"] if meta else ""
    data = build_data([player], guild, args.days, matches, meta, battles, icons)
    write_outputs(data, args.out, f"{player} Siege Record")


def run(entry):
    try:
        entry()
    except KeyboardInterrupt:
        sys.exit("\nStopped. Downloaded sieges are kept in cache/, so the next run picks up where this one left off.")


if __name__ == "__main__":
    run(main)
