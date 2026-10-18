import os
import io
import json
import time
import sqlite3
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from flask import Flask
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------

TOKEN = os.environ.get("BOT_TOKEN", "")
API = "https://api.telegram.org/bot" + TOKEN + "/"
UA = "Mozilla/5.0"

ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/"
ESPN_V2 = "https://site.api.espn.com/apis/v2/sports/soccer/"
ESPN_V2_ALT = "https://site.web.api.espn.com/apis/v2/sports/soccer/"

DB_PATH = "football_v9.db"
NOTIFY_INTERVAL = 10
PEN_CHECK_EVERY = 3
MAX_PENDING_TRIES = 8
SB_TTL = 8
LOGO_SIZE = 30

LEAGUE_IDS = ["tur.1", "eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions"]

LEAGUE_NAMES = {
    "tur.1": "Süper Lig 🇹🇷",
    "eng.1": "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "esp.1": "La Liga 🇪🇸",
    "ger.1": "Bundesliga 🇩🇪",
    "ita.1": "Serie A 🇮🇹",
    "fra.1": "Ligue 1 🇫🇷",
    "uefa.champions": "Champions League 🏆"
}

LEAGUE_TITLES = {
    "tur.1": "Süper Lig",
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "uefa.champions": "Champions League"
}

GOAL_KINDS = ["goal", "penalty", "own_goal"]

START_TEXT = (
    "⚽ <b>Football Live</b>\n\n"
    "Pick a league to see its table, or tap Live Now / Today.\n\n"
    "🔔 Goal alerts are ON for every league.\n"
    "Send /stop to turn them off."
)

# ---------------------------------------------------------------
# WEB SERVER (keeps Render happy)
# ---------------------------------------------------------------

app = Flask(__name__)


@app.route("/")
def home():
    return "Football Bot V9 is running"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ---------------------------------------------------------------
# GLOBALS
# ---------------------------------------------------------------

fetch_pool = ThreadPoolExecutor(max_workers=8)
update_pool = ThreadPoolExecutor(max_workers=8)

db_lock = threading.Lock()
conn = None

SB_CACHE = {}
LOGO_CACHE = {}
FONT_CACHE = {}
PENDING = {}

# ---------------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------------


def now_wat():
    return datetime.now(timezone.utc) + timedelta(hours=1)


def stamp():
    return now_wat().strftime("%H:%M:%S") + " WAT"


def today_str():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def scoreboard_dates():
    now = datetime.now(timezone.utc)
    dates = [now.strftime("%Y%m%d")]
    if now.hour < 3:
        dates.insert(0, (now - timedelta(days=1)).strftime("%Y%m%d"))
    return dates


def kickoff_wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        dt = dt + timedelta(hours=1)
        return dt.strftime("%H:%M") + " WAT"
    except Exception:
        return ""


def esc(text):
    text = str(text)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


# ---------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------


def init_db():
    global conn
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    db_exec("CREATE TABLE IF NOT EXISTS subs (chat_id TEXT PRIMARY KEY)")
    db_exec("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, h_score INTEGER, a_score INTEGER)")
    db_exec("CREATE TABLE IF NOT EXISTS alerted (event_id TEXT, play_key TEXT, PRIMARY KEY (event_id, play_key))")


def db_exec(sql, params=()):
    with db_lock:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.fetchall()


def db_add_sub(chat_id):
    db_exec("INSERT OR IGNORE INTO subs (chat_id) VALUES (?)", (str(chat_id),))


def db_remove_sub(chat_id):
    db_exec("DELETE FROM subs WHERE chat_id=?", (str(chat_id),))


def db_get_subs():
    rows = db_exec("SELECT chat_id FROM subs")
    return [r[0] for r in rows]


def db_get_score(event_id):
    rows = db_exec("SELECT h_score, a_score FROM scores WHERE event_id=?", (event_id,))
    if rows:
        return rows[0]
    return None


def db_set_score(event_id, hs, a_s):
    db_exec("INSERT OR REPLACE INTO scores (event_id, h_score, a_score) VALUES (?,?,?)", (event_id, hs, a_s))


def db_is_seen(event_id, key):
    rows = db_exec("SELECT 1 FROM alerted WHERE event_id=? AND play_key=?", (event_id, key))
    return len(rows) > 0


def db_mark_seen(event_id, key):
    db_exec("INSERT OR IGNORE INTO alerted (event_id, play_key) VALUES (?,?)", (event_id, key))


# ---------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------


def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        print("fetch fail " + url + " : " + str(err))
        return None


def fetch_bytes(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read()
    except Exception as err:
        print("fetch bytes fail " + url + " : " + str(err))
        return None


def encode_multipart(fields, files):
    boundary = "----fbbot" + os.urandom(8).hex()
    parts = []
    for name, value in fields.items():
        parts.append(("--" + boundary + "\r\n").encode("utf-8"))
        parts.append(('Content-Disposition: form-data; name="' + name + '"\r\n\r\n').encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    for name, item in files.items():
        filename = item[0]
        content = item[1]
        ctype = item[2]
        parts.append(("--" + boundary + "\r\n").encode("utf-8"))
        parts.append(('Content-Disposition: form-data; name="' + name + '"; filename="' + filename + '"\r\n').encode("utf-8"))
        parts.append(("Content-Type: " + ctype + "\r\n\r\n").encode("utf-8"))
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(("--" + boundary + "--\r\n").encode("utf-8"))
    body = b"".join(parts)
    return body, "multipart/form-data; boundary=" + boundary


def tg_call(method, params=None, files=None, timeout=30):
    if params is None:
        params = {}
    if files:
        body, ctype = encode_multipart(params, files)
    else:
        body = urllib.parse.urlencode(params).encode("utf-8")
        ctype = "application/x-www-form-urlencoded"
    req = urllib.request.Request(API + method, data=body, headers={"Content-Type": ctype, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            return json.loads(err.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error_code": err.code, "description": "HTTP error " + str(err.code)}
    except Exception as err:
        return {"ok": False, "error_code": 0, "description": str(err)}


def is_not_modified(res):
    return "not modified" in str(res.get("description", "")).lower()


def send_text(chat_id, text, kb=None):
    params = {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    if kb:
        params["reply_markup"] = json.dumps(kb)
    return tg_call("sendMessage", params)


def edit_text(chat_id, message_id, text, kb=None):
    params = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true"
    }
    if kb:
        params["reply_markup"] = json.dumps(kb)
    return tg_call("editMessageText", params)


def send_photo(chat_id, png, kb=None):
    params = {"chat_id": str(chat_id)}
    if kb:
        params["reply_markup"] = json.dumps(kb)
    files = {"photo": ("table.png", png, "image/png")}
    return tg_call("sendPhoto", params, files, 60)


def edit_photo(chat_id, message_id, png, kb=None):
    media = {"type": "photo", "media": "attach://photo"}
    params = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "media": json.dumps(media)
    }
    if kb:
        params["reply_markup"] = json.dumps(kb)
    files = {"photo": ("table.png", png, "image/png")}
    return tg_call("editMessageMedia", params, files, 60)


def delete_msg(chat_id, message_id):
    return tg_call("deleteMessage", {"chat_id": str(chat_id), "message_id": str(message_id)}, None, 15)


def answer_cb(cb_id, text=""):
    return tg_call("answerCallbackQuery", {"callback_query_id": str(cb_id), "text": text}, None, 10)


def show_text(chat_id, msg, text, kb):
    message_id = msg.get("message_id")
    if msg.get("photo"):
        res = send_text(chat_id, text, kb)
        if res.get("ok"):
            delete_msg(chat_id, message_id)
        else:
            print("send text failed: " + str(res.get("description")))
        return
    res = edit_text(chat_id, message_id, text, kb)
    if res.get("ok") or is_not_modified(res):
        return
    print("edit text failed: " + str(res.get("description")))
    send_text(chat_id, text, kb)


def show_photo(chat_id, msg, png, kb):
    message_id = msg.get("message_id")
    if msg.get("photo"):
        res = edit_photo(chat_id, message_id, png, kb)
        if res.get("ok") or is_not_modified(res):
            return
        print("edit photo failed: " + str(res.get("description")))
    res = send_photo(chat_id, png, kb)
    if res.get("ok"):
        delete_msg(chat_id, message_id)
    else:
        print("send photo failed: " + str(res.get("description")))


# ---------------------------------------------------------------
# KEYBOARDS
# ---------------------------------------------------------------


def main_menu():
    return {"inline_keyboard": [
        [
            {"text": "🇹🇷 Süper Lig", "callback_data": "tbl:tur.1"},
            {"text": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL", "callback_data": "tbl:eng.1"}
        ],
        [
            {"text": "🇪🇸 La Liga", "callback_data": "tbl:esp.1"},
            {"text": "🇩🇪 Bundesliga", "callback_data": "tbl:ger.1"}
        ],
        [
            {"text": "🇮🇹 Serie A", "callback_data": "tbl:ita.1"},
            {"text": "🏆 Champions League", "callback_data": "tbl:uefa.champions"}
        ],
        [
            {"text": "🔴 Live Now", "callback_data": "live"},
            {"text": "📅 Today", "callback_data": "today"}
        ]
    ]}


def table_kb(lid):
    return {"inline_keyboard": [
        [
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ],
        [{"text": "🔄 Refresh Table", "callback_data": "tbl:" + lid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def matches_kb(lid):
    return {"inline_keyboard": [
        [
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ],
        [{"text": "🔄 Refresh Matches", "callback_data": "mat:" + lid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def live_kb():
    return {"inline_keyboard": [
        [
            {"text": "🔄 Refresh", "callback_data": "live"},
            {"text": "📅 Today", "callback_data": "today"}
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def today_kb():
    return {"inline_keyboard": [
        [
            {"text": "🔄 Refresh", "callback_data": "today"},
            {"text": "🔴 Live Now", "callback_data": "live"}
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


# ---------------------------------------------------------------
# SCOREBOARD + MATCH TEXT
# ---------------------------------------------------------------


def scoreboard_url(lid, date_str):
    return ESPN + lid + "/scoreboard?dates=" + date_str


def get_scoreboard(lid, fresh=False):
    now = time.time()
    cached = SB_CACHE.get(lid)
    if cached and not fresh and (now - cached[0]) < SB_TTL:
        return cached[1]
    data = fetch_json(scoreboard_url(lid, today_str()))
    if data is not None:
        SB_CACHE[lid] = (now, data)
    return data


def get_scoreboards(lids):
    return list(fetch_pool.map(get_scoreboard, lids))


def split_teams(comp):
    teams = comp.get("competitors") or []
    home = None
    away = None
    for t in teams:
        if t.get("homeAway") == "home":
            home = t
        elif t.get("homeAway") == "away":
            away = t
    if home is None and len(teams) > 0:
        home = teams[0]
    if away is None and len(teams) > 1:
        away = teams[1]
    return home, away


def score_int(team_obj):
    try:
        return int(team_obj.get("score", 0) or 0)
    except Exception:
        return 0


def short_name(team_obj):
    team = team_obj.get("team") or {}
    return team.get("shortDisplayName") or team.get("displayName") or team.get("name") or "?"


def match_line(ev):
    comps = ev.get("competitions") or []
    if not comps:
        return ""
    home, away = split_teams(comps[0])
    if home is None or away is None:
        return ""
    stype = (ev.get("status") or {}).get("type") or {}
    state = stype.get("state", "")
    if state == "pre":
        icon = "🕒"
        score = "vs"
        info = kickoff_wat(ev.get("date", ""))
    elif state == "in":
        icon = "🔴"
        score = str(score_int(home)) + "-" + str(score_int(away))
        info = stype.get("shortDetail") or stype.get("detail") or "LIVE"
    else:
        icon = "✅"
        score = str(score_int(home)) + "-" + str(score_int(away))
        info = stype.get("shortDetail") or stype.get("detail") or "FT"
    return icon + " " + esc(short_name(home)) + " " + score + " " + esc(short_name(away)) + " • " + esc(info)


def league_matches(data, only_live):
    lines = []
    if not data:
        return lines
    for ev in data.get("events") or []:
        stype = (ev.get("status") or {}).get("type") or {}
        if only_live and stype.get("state") != "in":
            continue
        line = match_line(ev)
        if line:
            lines.append(line)
    return lines


def build_matches_text(lid):
    data = get_scoreboard(lid)
    lines = league_matches(data, False)
    text = "⚽ <b>" + esc(LEAGUE_NAMES.get(lid, lid)) + "</b> - Today (WAT)\n\n"
    if data is None:
        text += "Could not load matches. Tap Refresh."
    elif not lines:
        text += "No matches today."
    else:
        text += "\n".join(lines)
    text += "\n\n🔄 Updated " + stamp()
    return text[:4000]


def build_multi_text(only_live):
    results = get_scoreboards(LEAGUE_IDS)
    blocks = []
    for lid, data in zip(LEAGUE_IDS, results):
        lines = league_matches(data, only_live)
        if lines:
            blocks.append("<b>" + esc(LEAGUE_NAMES[lid]) + "</b>\n" + "\n".join(lines))
    if only_live:
        text = "🔴 <b>Live Now</b>\n\n"
        empty = "😴 No games are live right now.\nGoal alerts stay ON, you will be notified automatically."
    else:
        text = "📅 <b>Today's Matches</b> (WAT)\n\n"
        empty = "No matches today in the top leagues."
    if not blocks:
        text += empty + "\n\n"
    for block in blocks:
        if len(text) + len(block) > 3800:
            text += "…more matches not shown\n\n"
            break
        text += block + "\n\n"
    text += "🔄 Updated " + stamp()
    return text


# ---------------------------------------------------------------
# TABLE IMAGE
# ---------------------------------------------------------------


def get_standings(lid):
    urls = [ESPN_V2 + lid + "/standings", ESPN_V2_ALT + lid + "/standings"]
    for url in urls:
        data = fetch_json(url)
        if not data:
            continue
        entries = []
        children = data.get("children")
        if children:
            for child in children:
                entries.extend((child.get("standings") or {}).get("entries") or [])
        elif data.get("standings"):
            entries = data["standings"].get("entries") or []
        if entries:
            return entries
    return []


def num(stats, names):
    for name in names:
        value = stats.get(name)
        if value is None:
            continue
        try:
            return int(float(value))
        except Exception:
            continue
    return 0


def get_table_rows(lid):
    entries = get_standings(lid)
    rows = []
    for e in entries:
        team = e.get("team") or {}
        stats = {}
        for s in e.get("stats") or []:
            if isinstance(s, dict):
                stats[s.get("name")] = s.get("value")
        played = num(stats, ["gamesPlayed"])
        if played == 0:
            played = num(stats, ["wins"]) + num(stats, ["losses"]) + num(stats, ["ties", "draws"])
        logo = ""
        logos = team.get("logos") or []
        if logos and isinstance(logos[0], dict):
            logo = logos[0].get("href", "")
        full = team.get("displayName") or team.get("name") or "?"
        rows.append({
            "id": str(team.get("id", "")),
            "full": full,
            "name": team.get("shortDisplayName") or full,
            "logo": logo,
            "p": played,
            "gd": num(stats, ["pointDifferential", "goalDifferential"]),
            "pts": num(stats, ["points"]),
            "rank": num(stats, ["rank"])
        })
    if rows and all(r["rank"] > 0 for r in rows):
        rows.sort(key=lambda r: r["rank"])
    else:
        rows.sort(key=lambda r: (-r["pts"], -r["gd"]))
    return rows


def get_live_map(lid):
    live = {}
    data = get_scoreboard(lid)
    if not data:
        return live
    for ev in data.get("events") or []:
        stype = (ev.get("status") or {}).get("type") or {}
        if stype.get("state") != "in":
            continue
        comps = ev.get("competitions") or []
        if not comps:
            continue
        home, away = split_teams(comps[0])
        if home is None or away is None:
            continue
        hs = score_int(home)
        a_s = score_int(away)
        hteam = home.get("team") or {}
        ateam = away.get("team") or {}
        home_score = str(hs) + "-" + str(a_s)
        away_score = str(a_s) + "-" + str(hs)
        live[str(hteam.get("id", ""))] = home_score
        live[str(ateam.get("id", ""))] = away_score
        live[str(hteam.get("displayName", "")).lower()] = home_score
        live[str(ateam.get("displayName", "")).lower()] = away_score
    return live


def load_logo(url):
    if not url:
        return None
    if url in LOGO_CACHE:
        return LOGO_CACHE[url]
    raw = fetch_bytes(url)
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img.thumbnail((LOGO_SIZE, LOGO_SIZE))
    except Exception as err:
        print("logo decode fail: " + str(err))
        return None
    LOGO_CACHE[url] = img
    return img


def get_font(size, bold=False):
    key = (size, bold)
    if key in FONT_CACHE:
        return FONT_CACHE[key]
    if bold:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "DejaVuSans-Bold.ttf",
            "arialbd.ttf"
        ]
    else:
        candidates 