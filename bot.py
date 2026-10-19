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

# Optional: permanent alert receivers (survive Render restarts).
# Set env var ALERT_CHAT_IDS to your chat id, e.g. 123456789 or 123,456
EXTRA_CHAT_IDS = [x.strip() for x in os.environ.get("ALERT_CHAT_IDS", "").split(",") if x.strip()]
HARD_EDIT_ERRORS = ["message to edit not found", "can't be edited", "message can't be edited"]

LEAGUE_IDS = [
    "tur.1", "eng.1", "esp.1", "ger.1", "ita.1", "fra.1",
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "ned.1", "por.1",
    "eng.fa", "eng.league_cup", "esp.copa_del_rey", "ger.dfb_pokal", "ita.coppa_italia", "fra.coupe_de_france"
]

CUP_IDS = ["eng.fa", "eng.league_cup", "esp.copa_del_rey", "ger.dfb_pokal", "ita.coppa_italia", "fra.coupe_de_france"]

MORE_LEAGUE_IDS = [
    "ned.1", "por.1", "eng.fa", "eng.league_cup",
    "esp.copa_del_rey", "ger.dfb_pokal", "ita.coppa_italia", "fra.coupe_de_france"
]

LEAGUE_NAMES = {
    "tur.1": "Süper Lig 🇹🇷",
    "eng.1": "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "esp.1": "La Liga 🇪🇸",
    "ger.1": "Bundesliga 🇩🇪",
    "ita.1": "Serie A 🇮🇹",
    "fra.1": "Ligue 1 🇫🇷",
    "uefa.champions": "Champions League 🏆",
    "uefa.europa": "Europa League 🟠",
    "uefa.europa.conf": "Conference League 🟢",
    "ned.1": "Eredivisie 🇳🇱",
    "por.1": "Liga Portugal 🇵🇹",
    "eng.fa": "FA Cup 🏆",
    "eng.league_cup": "League Cup 🏆",
    "esp.copa_del_rey": "Copa del Rey 🏆",
    "ger.dfb_pokal": "DFB-Pokal 🏆",
    "ita.coppa_italia": "Coppa Italia 🏆",
    "fra.coupe_de_france": "Coupe de France 🏆"
}

LEAGUE_TITLES = {
    "tur.1": "Süper Lig",
    "eng.1": "Premier League",
    "esp.1": "La Liga",
    "ger.1": "Bundesliga",
    "ita.1": "Serie A",
    "fra.1": "Ligue 1",
    "uefa.champions": "Champions League",
    "uefa.europa": "Europa League",
    "uefa.europa.conf": "Conference League",
    "ned.1": "Eredivisie",
    "por.1": "Liga Portugal",
    "eng.fa": "FA Cup",
    "eng.league_cup": "League Cup",
    "esp.copa_del_rey": "Copa del Rey",
    "ger.dfb_pokal": "DFB-Pokal",
    "ita.coppa_italia": "Coppa Italia",
    "fra.coupe_de_france": "Coupe de France"
}

REMINDER_MINUTES = 30
MAX_FAVS = 5

ALERT_STYLE = {
    "goal": ("GOAL", (40, 160, 70)),
    "penalty": ("PENALTY GOAL", (40, 160, 70)),
    "own_goal": ("OWN GOAL", (225, 140, 30)),
    "penalty_missed": ("PENALTY MISSED", (210, 45, 55)),
    "var_cancel": ("GOAL CANCELED - VAR", (210, 45, 55)),
    "half_time": ("HALF TIME", (90, 110, 165)),
    "full_time": ("FULL TIME", (90, 110, 165)),
    "reminder": ("KICKOFF SOON", (55, 125, 220))
}

GOAL_KINDS = ["goal", "penalty", "own_goal"]

START_TEXT = (
    "⚽ <b>Football Live</b>\n\n"
    "Pick a league to see its table, or tap Live Now / Today.\n\n"
    "🔔 Goal, VAR, half time, full time and kickoff alerts are ON.\n"
    "⭐ Use My Team to get alerts only for your club.\n"
    "Send /stop to turn alerts off."
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

fetch_pool = ThreadPoolExecutor(max_workers=12)
update_pool = ThreadPoolExecutor(max_workers=8)

db_lock = threading.Lock()
conn = None

SB_CACHE = {}
LOGO_CACHE = {}
FONT_CACHE = {}
PENDING = {}
NOTIFIER = {"last_run": 0, "events": 0, "live": 0, "fetch_fail": 0, "error": ""}
LAST_ERRORS = {"edit": ""}
LAST_GOALS = {}
FAILS = {}
TEAM_INDEX = {"time": 0, "teams": {}}

TEAM_EMOJI = {
    "galatasaray": "🦁", "fenerbahce": "💛", "fenerbahçe": "💛",
    "besiktas": "🦅", "beşiktaş": "🦅", "trabzonspor": "🔴",
    "arsenal": "🔴", "manchester city": "💙", "man city": "💙",
    "liverpool": "🔴", "chelsea": "💙", "real betis": "💚",
    "barcelona": "🔵🔴", "real madrid": "👑"
}

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
    db_exec("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team_id TEXT, team_name TEXT, PRIMARY KEY (chat_id, team_id))")
    migrate_old_subs()


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
    subs = [r[0] for r in rows]
    for cid in EXTRA_CHAT_IDS:
        if cid not in subs:
            subs.append(cid)
    return subs


def db_add_fav(chat_id, team_id, team_name):
    db_exec("INSERT OR REPLACE INTO favs (chat_id, team_id, team_name) VALUES (?,?,?)", (str(chat_id), str(team_id), team_name))


def db_remove_fav(chat_id, team_id):
    db_exec("DELETE FROM favs WHERE chat_id=? AND team_id=?", (str(chat_id), str(team_id)))


def db_clear_favs(chat_id):
    db_exec("DELETE FROM favs WHERE chat_id=?", (str(chat_id),))


def db_get_favs(chat_id):
    rows = db_exec("SELECT team_id, team_name FROM favs WHERE chat_id=?", (str(chat_id),))
    return [(r[0], r[1]) for r in rows]


def db_all_favs():
    rows = db_exec("SELECT chat_id, team_id FROM favs")
    out = {}
    for r in rows:
        if r[0] not in out:
            out[r[0]] = set()
        out[r[0]].add(r[1])
    return out


def migrate_old_subs():
    if not os.path.exists("football.db"):
        return
    try:
        old = sqlite3.connect("football.db")
        rows = old.execute("SELECT chat_id FROM subs").fetchall()
        old.close()
        for r in rows:
            db_add_sub(r[0])
    except Exception as err:
        print("old db import fail: " + str(err))


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


def send_photo(chat_id, photo, kb=None, caption=None):
    params = {"chat_id": str(chat_id)}
    if caption:
        params["caption"] = caption
        params["parse_mode"] = "HTML"
    if kb:
        params["reply_markup"] = json.dumps(kb)
    if isinstance(photo, bytes):
        files = {"photo": ("image.png", photo, "image/png")}
        return tg_call("sendPhoto", params, files, 60)
    params["photo"] = str(photo)
    return tg_call("sendPhoto", params, None, 30)


def edit_photo(chat_id, message_id, png, kb=None, caption=None):
    media = {"type": "photo", "media": "attach://photo"}
    if caption:
        media["caption"] = caption
        media["parse_mode"] = "HTML"
    params = {
        "chat_id": str(chat_id),
        "message_id": str(message_id),
        "media": json.dumps(media)
    }
    if kb:
        params["reply_markup"] = json.dumps(kb)
    files = {"photo": ("image.png", png, "image/png")}
    return tg_call("editMessageMedia", params, files, 60)


def delete_msg(chat_id, message_id):
    return tg_call("deleteMessage", {"chat_id": str(chat_id), "message_id": str(message_id)}, None, 15)


def answer_cb(cb_id, text=""):
    return tg_call("answerCallbackQuery", {"callback_query_id": str(cb_id), "text": text}, None, 10)


def is_hard_edit_error(res):
    desc = str(res.get("description", "")).lower()
    for w in HARD_EDIT_ERRORS:
        if w in desc:
            return True
    return False


def note_edit_error(res):
    LAST_ERRORS["edit"] = str(res.get("description", "unknown")) + " (" + stamp() + ")"
    print("edit failed: " + LAST_ERRORS["edit"])


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
    if not res.get("ok") and not is_not_modified(res) and not is_hard_edit_error(res):
        time.sleep(1)
        res = edit_text(chat_id, message_id, text, kb)
    if res.get("ok") or is_not_modified(res):
        return
    note_edit_error(res)
    if is_hard_edit_error(res):
        send_text(chat_id, text, kb)


def show_photo(chat_id, msg, png, kb, caption=None):
    message_id = msg.get("message_id")
    if msg.get("photo"):
        res = edit_photo(chat_id, message_id, png, kb, caption)
        if not res.get("ok") and not is_not_modified(res) and not is_hard_edit_error(res):
            time.sleep(1)
            res = edit_photo(chat_id, message_id, png, kb, caption)
        if res.get("ok") or is_not_modified(res):
            return
        note_edit_error(res)
        if not is_hard_edit_error(res):
            return
    res = send_photo(chat_id, png, kb, caption)
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
            {"text": "🇫🇷 Ligue 1", "callback_data": "tbl:fra.1"}
        ],
        [
            {"text": "🏆 Champions League", "callback_data": "tbl:uefa.champions"},
            {"text": "🟠 Europa League", "callback_data": "tbl:uefa.europa"}
        ],
        [
            {"text": "🟢 Conference League", "callback_data": "tbl:uefa.europa.conf"},
            {"text": "🌍 More Leagues", "callback_data": "more"}
        ],
        [
            {"text": "🔴 Live Now", "callback_data": "live"},
            {"text": "📅 Today", "callback_data": "today"}
        ],
        [{"text": "⭐ My Team", "callback_data": "my"}]
    ]}


def more_menu():
    rows = []
    pair = []
    for lid in MORE_LEAGUE_IDS:
        pair.append({"text": LEAGUE_NAMES[lid], "callback_data": "tbl:" + lid})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    return {"inline_keyboard": rows}


def match_buttons(rows, limit):
    out = []
    pair = []
    for r in rows[:limit]:
        if r["state"] == "pre":
            label = r["hshort"] + " v " + r["ashort"]
        else:
            label = r["hshort"] + " " + str(r["hs"]) + "-" + str(r["a_s"]) + " " + r["ashort"]
        pair.append({"text": label[:30], "callback_data": "mc:" + r["lid"] + ":" + r["id"]})
        if len(pair) == 2:
            out.append(pair)
            pair = []
    if pair:
        out.append(pair)
    return out


def alert_kb(info):
    lid = info.get("lid")
    eid = info.get("id")
    if not lid or not eid:
        return None
    return {"inline_keyboard": [
        [{"text": "🏟 Match Center", "callback_data": "mc:" + lid + ":" + eid}],
        [
            {"text": "📊 Stats", "callback_data": "st:" + lid + ":" + eid},
            {"text": "👥 Lineups", "callback_data": "ln:" + lid + ":" + eid}
        ]
    ]}


def mc_kb(lid, eid):
    return {"inline_keyboard": [
        [
            {"text": "📊 Stats", "callback_data": "st:" + lid + ":" + eid},
            {"text": "👥 Lineups", "callback_data": "ln:" + lid + ":" + eid}
        ],
        [{"text": "🔄 Refresh", "callback_data": "rmc:" + lid + ":" + eid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def table_kb(lid):
    return {"inline_keyboard": [
        [
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ],
        [{"text": "🔄 Refresh Table", "callback_data": "rt:" + lid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def matches_kb(lid, rows=None):
    kb_rows = []
    if rows:
        kb_rows.extend(match_buttons(rows, 10))
    if lid in CUP_IDS:
        kb_rows.append([{"text": "⚽ Matches", "callback_data": "mat:" + lid}])
    else:
        kb_rows.append([
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ])
    kb_rows.append([{"text": "🔄 Refresh Matches", "callback_data": "rm:" + lid}])
    kb_rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    return {"inline_keyboard": kb_rows}


def live_kb(rows=None):
    kb_rows = []
    if rows:
        kb_rows.extend(match_buttons(rows, 8))
    kb_rows.append([
        {"text": "🔄 Refresh", "callback_data": "rlive"},
        {"text": "📅 Today", "callback_data": "today"}
    ])
    kb_rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    return {"inline_keyboard": kb_rows}


def today_kb():
    return {"inline_keyboard": [
        [
            {"text": "🔄 Refresh", "callback_data": "rtoday"},
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


def kickoff_hm(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        dt = dt + timedelta(hours=1)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def minutes_to_kickoff(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        return None


def match_status(info):
    if info["state"] == "pre":
        return kickoff_hm(info["date"])
    return info["short"] or info["minute"] or "-"


def rows_from_data(lid, data, only_live):
    rows = []
    if not data:
        return rows
    for ev in data.get("events") or []:
        info = event_info(ev, lid)
        if info is None:
            continue
        if only_live and info["state"] != "in":
            continue
        rows.append(info)
    rows.sort(key=lambda r: r["date"])
    return rows


def render_matches_image(title, groups):
    width = 760
    head_h = 72
    group_h = 36
    row_h = 48
    foot_h = 40
    total_rows = 0
    for g in groups:
        total_rows += len(g[1])
    height = head_h + group_h * len(groups) + row_h * total_rows + foot_h

    urls = []
    for g in groups:
        for r in g[1]:
            if r["hlogo"]:
                urls.append(r["hlogo"])
            if r["alogo"]:
                urls.append(r["alogo"])
    list(fetch_pool.map(lambda u: load_logo(u, 32), urls))

    img = Image.new("RGB", (width, height), (16, 20, 30))
    draw = ImageDraw.Draw(img)
    f_title = get_font(28, True)
    f_group = get_font(16, True)
    f_name = get_font(18)
    f_score = get_font(22, True)
    f_stat = get_font(16, True)
    f_small = get_font(14)

    draw.text((20, 14), title, font=f_title, fill=(255, 255, 255))
    draw.text((20, 50), "Kickoff times are WAT", font=f_small, fill=(140, 150, 170))

    y = head_h
    for group_title, rows in groups:
        draw.rectangle([0, y, width, y + group_h - 1], fill=(34, 41, 60))
        draw.text((20, y + 9), group_title, font=f_group, fill=(200, 210, 230))
        y += group_h
        for i, r in enumerate(rows):
            live = r["state"] == "in"
            if live:
                bg = (88, 22, 30)
                stat_color = (255, 110, 110)
            elif i % 2 == 0:
                bg = (24, 29, 42)
                stat_color = (150, 160, 180)
            else:
                bg = (20, 25, 36)
                stat_color = (150, 160, 180)
            draw.rectangle([0, y, width, y + row_h - 1], fill=bg)
            draw_center(draw, 55, y + 14, match_status(r), f_stat, stat_color)
            hname = fit_text(draw, r["hshort"], f_name, 220)
            aname = fit_text(draw, r["ashort"], f_name, 220)
            draw_right(draw, 338, y + 13, hname, f_name, (235, 238, 245))
            paste_logo(img, draw, r["hlogo"], 346, y + 8, 32)
            if r["state"] == "pre":
                draw_center(draw, 415, y + 12, "vs", f_stat, (150, 160, 180))
            else:
                draw_center(draw, 415, y + 11, str(r["hs"]) + " - " + str(r["a_s"]), f_score, (255, 255, 255))
            paste_logo(img, draw, r["alogo"], 452, y + 8, 32)
            draw.text((494, y + 13), aname, font=f_name, fill=(235, 238, 245))
            y += row_h

    draw.text((20, y + 12), "Updated " + stamp(), font=f_small, fill=(140, 150, 170))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_matches_view(lid):
    data = get_scoreboard(lid)
    if data is None:
        return None, "Could not load matches. Tap Refresh.", []
    rows = rows_from_data(lid, data, False)
    if not rows:
        return None, "⚽ <b>" + esc(LEAGUE_NAMES.get(lid, lid)) + "</b>\n\nNo matches today.\n\n🔄 Updated " + stamp(), []
    title = LEAGUE_TITLES.get(lid, lid) + " - Today"
    png = render_matches_image(title, [(LEAGUE_TITLES.get(lid, lid), rows)])
    return png, "", rows


def build_multi_view(only_live):
    results = get_scoreboards(LEAGUE_IDS)
    groups = []
    all_rows = []
    count = 0
    for lid, data in zip(LEAGUE_IDS, results):
        rows = rows_from_data(lid, data, only_live)
        if not rows:
            continue
        if count + len(rows) > 150:
            rows = rows[:150 - count]
        if not rows:
            break
        count += len(rows)
        groups.append((LEAGUE_TITLES.get(lid, lid), rows))
        all_rows.extend(rows)
    if only_live:
        title = "Live Now"
        empty = "🔴 <b>Live Now</b>\n\n😴 No games are live right now.\nAlerts stay ON, you will be notified automatically.\n\n🔄 Updated " + stamp()
    else:
        title = "Today's Matches"
        empty = "📅 <b>Today's Matches</b>\n\nNo matches today.\n\n🔄 Updated " + stamp()
    if not groups:
        return None, empty, []
    png = render_matches_image(title, groups)
    return png, "", all_rows


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
            "w": num(stats, ["wins"]),
            "d": num(stats, ["ties", "draws"]),
            "l": num(stats, ["losses"]),
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


def logo_of(team):
    if not isinstance(team, dict):
        return ""
    href = team.get("logo")
    if href:
        return str(href)
    logos = team.get("logos") or []
    if logos and isinstance(logos[0], dict):
        return str(logos[0].get("href", ""))
    return ""


def load_logo(url, size=LOGO_SIZE):
    if not url:
        return None
    key = (url, size)
    if key in LOGO_CACHE:
        return LOGO_CACHE[key]
    raw = fetch_bytes(url)
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img.thumbnail((size, size))
    except Exception as err:
        print("logo decode fail: " + str(err))
        return None
    LOGO_CACHE[key] = img
    return img


def paste_logo(img, draw, url, x, y, size):
    logo = load_logo(url, size)
    if logo is None:
        draw.ellipse([x + 3, y + 3, x + size - 3, y + size - 3], outline=(90, 100, 120))
        return
    img.paste(logo, (x + (size - logo.width) // 2, y + (size - logo.height) // 2), logo)


def fit_text(draw, text, font, max_w):
    text = str(text)
    while len(text) > 1 and text_w(draw, text, font) > max_w:
        text = text[:-1]
    return text


def draw_center(draw, cx, y, text, font, fill):
    width = text_w(draw, text, font)
    draw.text((cx - width / 2, y), text, font=font, fill=fill)


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
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "DejaVuSans.ttf",
            "arial.ttf"
        ]
    font = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            font = None
    if font is None:
        try:
            font = ImageFont.load_default(size)
        except Exception:
            font = ImageFont.load_default()
    FONT_CACHE[key] = font
    return font


def text_w(draw, text, font):
    try:
        return draw.textlength(text, font=font)
    except Exception:
        return len(text) * 9


def draw_right(draw, x_right, y, text, font, fill):
    width = text_w(draw, text, font)
    draw.text((x_right - width, y), text, font=font, fill=fill)


def render_table(lid, rows, live_map, logos):
    title = LEAGUE_TITLES.get(lid, lid)
    width = 820
    head_h = 84
    col_h = 38
    row_h = 40
    foot_h = 44
    height = head_h + col_h + row_h * len(rows) + foot_h

    img = Image.new("RGB", (width, height), (16, 20, 30))
    draw = ImageDraw.Draw(img)

    f_title = get_font(30, True)
    f_head = get_font(15, True)
    f_row = get_font(19)
    f_bold = get_font(19, True)
    f_pill = get_font(15, True)
    f_small = get_font(14)

    draw.text((24, 16), title, font=f_title, fill=(255, 255, 255))
    draw.text((24, 56), "League table", font=f_small, fill=(140, 150, 170))

    x_rank = 44
    x_name = 98
    x_pill = 320
    x_p = 478
    x_w = 530
    x_d = 582
    x_l = 634
    x_gd = 706
    x_pts = 780

    col_y = head_h
    draw.rectangle([0, col_y, width, col_y + col_h - 1], fill=(34, 41, 60))
    head_color = (170, 180, 200)
    draw_right(draw, x_rank, col_y + 11, "#", f_head, head_color)
    draw.text((x_name, col_y + 11), "Team", font=f_head, fill=head_color)
    draw.text((x_pill, col_y + 11), "Live", font=f_head, fill=head_color)
    draw_right(draw, x_p, col_y + 11, "P", f_head, head_color)
    draw_right(draw, x_w, col_y + 11, "W", f_head, head_color)
    draw_right(draw, x_d, col_y + 11, "D", f_head, head_color)
    draw_right(draw, x_l, col_y + 11, "L", f_head, head_color)
    draw_right(draw, x_gd, col_y + 11, "GD", f_head, head_color)
    draw_right(draw, x_pts, col_y + 11, "Pts", f_head, head_color)

    top = head_h + col_h
    for i, row in enumerate(rows):
        y = top + row_h * i
        score = live_map.get(row["id"])
        if score is None:
            score = live_map.get(row["full"].lower())
        is_live = score is not None

        if is_live:
            bg = (88, 22, 30)
            name_color = (255, 120, 120)
            num_color = (255, 150, 150)
            w_color = num_color
            l_color = num_color
        elif i % 2 == 0:
            bg = (24, 29, 42)
            name_color = (235, 238, 245)
            num_color = (210, 215, 225)
            w_color = (120, 205, 130)
            l_color = (235, 125, 125)
        else:
            bg = (20, 25, 36)
            name_color = (235, 238, 245)
            num_color = (210, 215, 225)
            w_color = (120, 205, 130)
            l_color = (235, 125, 125)

        draw.rectangle([0, y, width, y + row_h - 1], fill=bg)
        draw_right(draw, x_rank, y + 9, str(i + 1), f_row, num_color)

        logo = logos.get(row["logo"])
        if logo is not None:
            lx = 58 + (LOGO_SIZE - logo.width) // 2
            ly = y + (row_h - logo.height) // 2
            img.paste(logo, (lx, ly), logo)
        else:
            draw.ellipse([60, y + 10, 84, y + 30], outline=(90, 100, 120))

        name = fit_text(draw, row["name"], f_row, 215)
        draw.text((x_name, y + 9), name, font=f_row, fill=name_color)

        if is_live:
            draw.rounded_rectangle([x_pill, y + 7, x_pill + 96, y + row_h - 7], radius=9, fill=(215, 38, 48))
            draw.text((x_pill + 9, y + 11), "LIVE " + score, font=f_pill, fill=(255, 255, 255))

        draw_right(draw, x_p, y + 9, str(row["p"]), f_row, num_color)
        draw_right(draw, x_w, y + 9, str(row["w"]), f_row, w_color)
        draw_right(draw, x_d, y + 9, str(row["d"]), f_row, num_color)
        draw_right(draw, x_l, y + 9, str(row["l"]), f_row, l_color)
        if row["gd"] > 0:
            gd_text = "+" + str(row["gd"])
        else:
            gd_text = str(row["gd"])
        draw_right(draw, x_gd, y + 9, gd_text, f_row, num_color)
        draw_right(draw, x_pts, y + 9, str(row["pts"]), f_bold, name_color)

    foot_y = top + row_h * len(rows) + 12
    draw.text((24, foot_y), "Updated " + stamp(), font=f_small, fill=(140, 150, 170))
    draw.text((590, foot_y), "Red row = playing now", font=f_small, fill=(255, 120, 120))

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_table_image(lid):
    rows = get_table_rows(lid)
    if not rows:
        return None
    live_map = get_live_map(lid)
    urls = []
    for r in rows:
        if r["logo"]:
            urls.append(r["logo"])
    list(fetch_pool.map(load_logo, urls))
    logos = {}
    for u in urls:
        logos[u] = LOGO_CACHE.get((u, LOGO_SIZE))
    return render_table(lid, rows, live_map, logos)


# ---------------------------------------------------------------
# TELEGRAM HANDLERS
# ---------------------------------------------------------------


def build_status_text(chat_id):
    subs = db_get_subs()
    if NOTIFIER["last_run"]:
        ran = str(int(time.time() - NOTIFIER["last_run"])) + "s ago"
    else:
        ran = "not yet"
    if str(chat_id) in subs:
        you = "yes"
    else:
        you = "no"
    lines = [
        "🛠 <b>Bot status</b>",
        "Your chat ID: <code>" + str(chat_id) + "</code>",
        "Subscribed chats: " + str(len(subs)),
        "Chats using My Team: " + str(len(db_all_favs())),
        "You get goal alerts: " + you,
        "Notifier last ran: " + ran,
        "Games found today: " + str(NOTIFIER["events"]) + " (live now: " + str(NOTIFIER["live"]) + ")",
        "ESPN fetch failures last cycle: " + str(NOTIFIER["fetch_fail"]),
        "Waiting for scorer name: " + str(len(PENDING)),
        "Last notifier error: " + esc(NOTIFIER["error"] or "none"),
        "Last edit error: " + esc(LAST_ERRORS["edit"] or "none")
    ]
    return "\n".join(lines)


def send_test_alert(chat_id):
    play = {"kind": "goal", "minute": "88'", "team_name": "Test FC", "player": "Test Player"}
    info = {"lid": "eng.1", "id": "", "hname": "Test FC", "aname": "Demo United",
            "hshort": "Test FC", "ashort": "Demo United", "hlogo": "", "alogo": ""}
    text = build_goal_text(play, info, 1, 0) + "\n(this is a test alert)"
    label, color = ALERT_STYLE["goal"]
    try:
        png = render_alert_card(info, 1, 0, label, color, "88'")
        res = send_photo(chat_id, png, None, text)
    except Exception as err:
        print("test card failed: " + str(err))
        res = send_text(chat_id, text)
    if not res.get("ok"):
        print("test alert failed: " + str(res.get("description")))


def send_matches_view(chat_id, msg, lid, refresh):
    png, text, rows = build_matches_view(lid)
    kb = matches_kb(lid, rows)
    if png is None:
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)
    elif refresh:
        show_photo(chat_id, msg, png, kb)
    else:
        send_photo(chat_id, png, kb)


def send_multi_view(chat_id, msg, only_live, refresh):
    png, text, rows = build_multi_view(only_live)
    if only_live:
        kb = live_kb(rows)
    else:
        kb = today_kb()
    if png is None:
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)
    elif refresh:
        show_photo(chat_id, msg, png, kb)
    else:
        send_photo(chat_id, png, kb)


def goal_lines(info, data):
    goals = [p for p in parse_items(info.get("details") or [], info, 0) if p["kind"] in GOAL_KINDS]
    if not goals:
        allp = extract_plays(data, info)
        for src in (1, 2):
            goals = [p for p in allp if p["kind"] in GOAL_KINDS and p["src"] == src]
            if goals:
                break
    goals.sort(key=lambda p: p["order"])
    lines = []
    seen = set()
    for g in goals:
        if g["key"] in seen:
            continue
        seen.add(g["key"])
        line = "⚽ " + esc(g["minute"]) + " " + esc(g["player"] or "Goal")
        if g["team_name"]:
            line += " (" + esc(g["team_name"]) + ")"
        if g["kind"] == "penalty":
            line += " - pen"
        elif g["kind"] == "own_goal":
            line += " - own goal"
        lines.append(line)
    return lines


def build_match_center(lid, eid):
    data = fetch_json(ESPN + lid + "/summary?event=" + eid)
    if not data:
        return None, "Match details are not available yet."
    try:
        comp = ((data.get("header") or {}).get("competitions") or [])[0]
        ev = {"id": eid, "competitions": [comp], "status": comp.get("status") or {}, "date": comp.get("date", "")}
        info = event_info(ev, lid)
        if info is None:
            return None, "Match details are not available yet."
        state = info["state"]
        if state == "pre":
            label = "UPCOMING"
            color = (55, 125, 220)
            footer = kickoff_hm(info["date"]) + " WAT"
            shown_h = None
        elif state == "in":
            label = "LIVE"
            color = (210, 45, 55)
            footer = info["short"] or info["minute"]
            shown_h = info["hs"]
        else:
            label = "FULL TIME"
            color = (90, 110, 165)
            footer = info["short"]
            shown_h = info["hs"]
        png = render_alert_card(info, shown_h, info["a_s"], label, color, footer)
        if state == "pre":
            caption = "<b>" + esc(info["hname"]) + " vs " + esc(info["aname"]) + "</b>\nKickoff " + esc(kickoff_hm(info["date"])) + " WAT"
        else:
            caption = "<b>" + esc(info["hname"]) + " " + str(info["hs"]) + " - " + str(info["a_s"]) + " " + esc(info["aname"]) + "</b>\n"
            lines = goal_lines(info, data)
            if lines:
                caption += "\n".join(lines)
            else:
                caption += "No goals yet."
        caption += "\n\n🔄 Updated " + stamp()
        return png, caption[:1000]
    except Exception as err:
        print("match center error: " + str(err))
        return None, "Match details are not available yet."


def send_match_center(chat_id, msg, lid, eid, refresh):
    png, caption = build_match_center(lid, eid)
    kb = mc_kb(lid, eid)
    if png is None:
        if refresh:
            show_text(chat_id, msg, caption, kb)
        else:
            send_text(chat_id, caption, kb)
    elif refresh:
        show_photo(chat_id, msg, png, kb, caption)
    else:
        send_photo(chat_id, png, kb, caption)


def build_lineups_text(lid, eid):
    data = fetch_json(ESPN + lid + "/summary?event=" + eid)
    if not data:
        return "Lineups are not available yet."
    rosters = data.get("rosters") or []
    text = "👥 <b>Lineups</b>\n\n"
    found = False
    for r in rosters[:2]:
        starters = []
        for x in r.get("roster") or []:
            if x.get("starter"):
                starters.append(x)
        if not starters:
            continue
        found = True
        team = (r.get("team") or {}).get("displayName", "")
        formation = r.get("formation")
        if isinstance(formation, dict):
            formation = formation.get("displayValue") or formation.get("name")
        text += "<b>" + esc(team) + "</b>"
        if formation:
            text += " (" + esc(formation) + ")"
        text += "\n"
        for x in starters:
            name = (x.get("athlete") or {}).get("displayName", "")
            jersey = x.get("jersey") or (x.get("athlete") or {}).get("jersey") or ""
            pos = (x.get("position") or {}).get("abbreviation", "")
            text += esc(jersey) + " " + esc(name)
            if pos:
                text += " (" + esc(pos) + ")"
            text += "\n"
        text += "\n"
    if not found:
        return "Lineups are not available yet."
    return text[:3800]


def get_team_index():
    now = time.time()
    if TEAM_INDEX["teams"] and (now - TEAM_INDEX["time"]) < 21600:
        return TEAM_INDEX["teams"]
    lids = [lid for lid in LEAGUE_IDS if lid not in CUP_IDS]
    results = list(fetch_pool.map(get_standings, lids))
    teams = {}
    for entries in results:
        for e in entries:
            team = e.get("team") or {}
            tid = str(team.get("id", ""))
            if not tid:
                continue
            teams[tid] = {
                "id": tid,
                "name": team.get("displayName") or team.get("name") or "?",
                "short": team.get("shortDisplayName") or "",
                "abbr": team.get("abbreviation") or ""
            }
    if teams:
        TEAM_INDEX["teams"] = teams
        TEAM_INDEX["time"] = now
    return TEAM_INDEX["teams"]


def search_teams(query):
    index = get_team_index()
    q = query.lower().strip()
    scored = []
    for t in index.values():
        best = 0
        for n in (t["name"].lower(), t["short"].lower(), t["abbr"].lower()):
            if not n:
                continue
            if n == q:
                best = max(best, 3)
            elif n.startswith(q):
                best = max(best, 2)
            elif q in n:
                best = max(best, 1)
        if best > 0:
            scored.append((best, t["name"], t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:8]]


def add_favorite(chat_id, team):
    favs = db_get_favs(chat_id)
    ids = [f[0] for f in favs]
    if team["id"] not in ids and len(favs) >= MAX_FAVS:
        return False
    db_add_fav(chat_id, team["id"], team["name"])
    return True


def send_my_team(chat_id, prefix=""):
    favs = db_get_favs(chat_id)
    text = prefix + "⭐ <b>My Team</b>\n\n"
    rows = []
    if favs:
        text += "You only get alerts for matches of:\n"
        for tid, name in favs:
            text += "• " + esc(name) + "\n"
            rows.append([{"text": "❌ Remove " + name, "callback_data": "uf:" + tid}])
        text += "\nType another club name to add it (up to " + str(MAX_FAVS) + ")."
        rows.append([{"text": "🗑 Clear all (get every match)", "callback_data": "clr"}])
    else:
        text += "You get alerts for every match.\n\nType a club name (for example Galatasaray or Arsenal) to get alerts only for that team."
    rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    send_text(chat_id, text, {"inline_keyboard": rows})


def handle_team_search(chat_id, query):
    teams = search_teams(query)
    if not teams:
        kb = {"inline_keyboard": [[{"text": "⬅️ Main Menu", "callback_data": "menu"}]]}
        send_text(chat_id, "I could not find a club called " + esc(query) + ". Check the spelling or try another name.", kb)
        return
    exact = []
    for t in teams:
        if t["name"].lower() == query.lower() or t["short"].lower() == query.lower():
            exact.append(t)
    if len(teams) == 1 or exact:
        if exact:
            team = exact[0]
        else:
            team = teams[0]
        if add_favorite(chat_id, team):
            send_my_team(chat_id, "✅ " + esc(team["name"]) + " added.\n\n")
        else:
            send_my_team(chat_id, "You can follow up to " + str(MAX_FAVS) + " teams. Remove one first.\n\n")
        return
    rows = []
    for t in teams[:6]:
        rows.append([{"text": t["name"], "callback_data": "fav:" + t["id"]}])
    rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    send_text(chat_id, "Which club did you mean?", {"inline_keyboard": rows})


def handle_callback(cb):
    cb_id = cb.get("id")
    data = cb.get("data") or ""
    msg = cb.get("message")
    if not msg:
        answer_cb(cb_id)
        return
    chat_id = msg["chat"]["id"]
    db_add_sub(chat_id)

    if data == "menu":
        answer_cb(cb_id)
        send_text(chat_id, START_TEXT, main_menu())

    elif data == "more":
        answer_cb(cb_id)
        send_text(chat_id, "🌍 <b>More leagues and cups</b>", more_menu())

    elif data == "my":
        answer_cb(cb_id)
        send_my_team(chat_id)

    elif data.startswith("fav:"):
        answer_cb(cb_id)
        team = get_team_index().get(data[4:])
        if team is None:
            send_text(chat_id, "I could not find that club. Type its name again.")
        elif add_favorite(chat_id, team):
            send_my_team(chat_id, "✅ " + esc(team["name"]) + " added.\n\n")
        else:
            send_my_team(chat_id, "You can follow up to " + str(MAX_FAVS) + " teams. Remove one first.\n\n")

    elif data.startswith("uf:"):
        answer_cb(cb_id, "Removed")
        db_remove_fav(chat_id, data[3:])
        send_my_team(chat_id)

    elif data == "clr":
        answer_cb(cb_id, "Cleared")
        db_clear_favs(chat_id)
        send_my_team(chat_id)

    elif data.startswith("tbl:") or data.startswith("rt:"):
        refresh = data.startswith("rt:")
        lid = data.split(":", 1)[1]
        if lid not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        if lid in CUP_IDS:
            answer_cb(cb_id, "Loading matches...")
            send_matches_view(chat_id, msg, lid, refresh)
            return
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading table...")
        png = build_table_image(lid)
        if png is None:
            if not refresh:
                text = "📊 The table for " + esc(LEAGUE_NAMES.get(lid, lid)) + " is not available right now.\nTry Matches or Refresh."
                send_text(chat_id, text, table_kb(lid))
        elif refresh:
            show_photo(chat_id, msg, png, table_kb(lid))
        else:
            send_photo(chat_id, png, table_kb(lid))

    elif data.startswith("mat:") or data.startswith("rm:"):
        refresh = data.startswith("rm:")
        lid = data.split(":", 1)[1]
        if lid not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading matches...")
        send_matches_view(chat_id, msg, lid, refresh)

    elif data == "live" or data == "rlive":
        answer_cb(cb_id, "Checking live games...")
        send_multi_view(chat_id, msg, True, data == "rlive")

    elif data == "today" or data == "rtoday":
        answer_cb(cb_id, "Loading today...")
        send_multi_view(chat_id, msg, False, data == "rtoday")

    elif data.startswith("mc:") or data.startswith("rmc:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Loading match...")
        send_match_center(chat_id, msg, parts[1], parts[2], parts[0] == "rmc")

    elif data.startswith("st:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Loading stats...")
        text = build_stats_text(parts[1], parts[2])
        kb = {"inline_keyboard": [[{"text": "⬅️ Main Menu", "callback_data": "menu"}]]}
        send_text(chat_id, text, kb)

    elif data.startswith("ln:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Loading lineups...")
        text = build_lineups_text(parts[1], parts[2])
        kb = {"inline_keyboard": [[{"text": "⬅️ Main Menu", "callback_data": "menu"}]]}
        send_text(chat_id, text, kb)

    else:
        answer_cb(cb_id)


def build_stats_text(lid, eid):
    data = fetch_json(ESPN + lid + "/summary?event=" + eid)
    if not data:
        return "Match stats are not available yet."
    try:
        comp = data["header"]["competitions"][0]
        home, away = split_teams(comp)
        status = (comp.get("status") or {}).get("type") or {}
        text = "<b>Match Stats</b>\n"
        text += esc(home["team"]["displayName"]) + " " + str(score_int(home)) + " - " + str(score_int(away)) + " " + esc(away["team"]["displayName"]) + "\n"
        text += esc(status.get("detail") or "") + "\n\n"
        boxes = (data.get("boxscore") or {}).get("teams") or []
        for tm in boxes[:2]:
            text += "<b>" + esc((tm.get("team") or {}).get("displayName", "")) + "</b>\n"
            for st in (tm.get("statistics") or [])[:8]:
                label = st.get("label") or st.get("name") or ""
                text += esc(label) + ": " + esc(st.get("displayValue", "")) + "\n"
            text += "\n"
        return text[:3800]
    except Exception as err:
        print("stats error: " + str(err))
        return "Match stats are not available yet."


def handle_message(message):
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = (message.get("text") or "").strip()
    low = text.lower()

    if low.startswith("/stop"):
        db_remove_sub(chat_id)
        send_text(chat_id, "🔕 Alerts are OFF.\nSend /start to turn them on again.")
        return

    db_add_sub(chat_id)
    if low.startswith("/start") or low.startswith("/menu") or low == "menu":
        send_text(chat_id, START_TEXT, main_menu())
    elif low.startswith("/myteam"):
        send_my_team(chat_id)
    elif low.startswith("/status"):
        send_text(chat_id, build_status_text(chat_id))
    elif low.startswith("/testalert"):
        send_test_alert(chat_id)
    elif low.startswith("/"):
        send_text(chat_id, "Send /start to open the football menu.")
    elif len(low) >= 3:
        handle_team_search(chat_id, text)
    elif low:
        send_text(chat_id, "Type at least 3 letters of a club name, or send /start.")


def handle_update(up):
    try:
        if "callback_query" in up:
            handle_callback(up["callback_query"])
        elif "message" in up:
            handle_message(up["message"])
    except Exception as err:
        print("Update error: " + str(err))


def poll_loop():
    offset = 0
    tg_call("deleteWebhook", {"drop_pending_updates": "false"}, None, 15)
    print("Football Bot V9 polling started")
    while True:
        params = {
            "offset": str(offset),
            "timeout": "30",
            "allowed_updates": json.dumps(["message", "callback_query"])
        }
        res = tg_call("getUpdates", params, None, 40)
        if not res.get("ok"):
            print("getUpdates error: " + str(res.get("description")))
            time.sleep(5)
            continue
        for up in res.get("result") or []:
            offset = up["update_id"] + 1
            update_pool.submit(handle_update, up)


# ---------------------------------------------------------------
# GOAL NOTIFIER
# ---------------------------------------------------------------


def recipients(info):
    subs = db_get_subs()
    if not info:
        return subs
    favs = db_all_favs()
    ids = set([str(info.get("hid", "")), str(info.get("aid", ""))])
    out = []
    for chat_id in subs:
        mine = favs.get(chat_id)
        if not mine or (mine & ids):
            out.append(chat_id)
    return out


def photo_file_id(res):
    try:
        photos = res["result"]["photo"]
        return photos[-1]["file_id"]
    except Exception:
        return ""


def handle_send_failure(chat_id, res):
    if res.get("ok"):
        return
    if res.get("error_code") == 403:
        db_remove_sub(chat_id)
    else:
        print("send fail " + str(chat_id) + " : " + str(res.get("description")))


def broadcast(text, kb=None, info=None):
    for chat_id in recipients(info):
        res = send_text(chat_id, text, kb)
        handle_send_failure(chat_id, res)
        time.sleep(0.05)


def broadcast_photo(info, png, caption, kb):
    file_id = ""
    for chat_id in recipients(info):
        if file_id:
            res = send_photo(chat_id, file_id, kb, caption)
        else:
            res = send_photo(chat_id, png, kb, caption)
            if res.get("ok"):
                file_id = photo_file_id(res)
        handle_send_failure(chat_id, res)
        time.sleep(0.05)


def render_alert_card(info, hs, a_s, label, color, footer=""):
    width = 720
    height = 290
    img = Image.new("RGB", (width, height), (16, 20, 30))
    draw = ImageDraw.Draw(img)
    f_small = get_font(16)
    f_pill = get_font(16, True)
    f_score = get_font(64, True)
    f_foot = get_font(24, True)
    f_name = get_font(22, True)

    draw.rectangle([0, 0, width, 56], fill=(24, 29, 42))
    draw.text((20, 17), LEAGUE_TITLES.get(info.get("lid", ""), ""), font=f_small, fill=(170, 180, 200))
    pill_w = text_w(draw, label, f_pill) + 28
    draw.rounded_rectangle([width - 20 - pill_w, 12, width - 20, 44], radius=14, fill=color)
    draw.text((width - 20 - pill_w + 14, 19), label, font=f_pill, fill=(255, 255, 255))

    urls = [info.get("hlogo", ""), info.get("alogo", "")]
    list(fetch_pool.map(lambda u: load_logo(u, 110), urls))
    paste_logo(img, draw, urls[0], 75, 72, 110)
    paste_logo(img, draw, urls[1], 535, 72, 110)

    if hs is None:
        score_text = "vs"
    else:
        score_text = str(hs) + " - " + str(a_s)
    draw_center(draw, 360, 92, score_text, f_score, (255, 255, 255))
    if footer:
        draw_center(draw, 360, 176, footer, f_foot, (200, 205, 215))
    draw_center(draw, 130, 200, fit_text(draw, info.get("hshort", ""), f_name, 240), f_name, (235, 238, 245))
    draw_center(draw, 590, 200, fit_text(draw, info.get("ashort", ""), f_name, 240), f_name, (235, 238, 245))
    draw.rectangle([0, height - 8, width, height], fill=color)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def send_alert(info, play, hs, a_s):
    text = build_goal_text(play, info, hs, a_s)
    kb = alert_kb(info)
    label, color = ALERT_STYLE.get(play["kind"], ("UPDATE", (90, 110, 165)))
    footer = play.get("minute") or ""
    shown_h = hs
    if play["kind"] == "reminder":
        shown_h = None
        footer = play.get("time", "")
    try:
        png = render_alert_card(info, shown_h, a_s, label, color, footer)
        broadcast_photo(info, png, text, kb)
    except Exception as err:
        print("card failed: " + str(err))
        broadcast(text, kb, info)
    print("ALERT PUSHED: " + text)


def event_info(ev, lid):
    comps = ev.get("competitions") or []
    if not comps:
        return None
    home, away = split_teams(comps[0])
    if home is None or away is None:
        return None
    status = ev.get("status") or {}
    stype = status.get("type") or {}
    hteam = home.get("team") or {}
    ateam = away.get("team") or {}
    minute = status.get("displayClock") or stype.get("shortDetail") or ""
    return {
        "id": str(ev.get("id", "")),
        "lid": lid,
        "state": stype.get("state", ""),
        "status_name": str(stype.get("name", "")),
        "date": str(ev.get("date", "")),
        "short": str(stype.get("shortDetail", "")),
        "hshort": hteam.get("shortDisplayName") or hteam.get("displayName") or "Home",
        "ashort": ateam.get("shortDisplayName") or ateam.get("displayName") or "Away",
        "hlogo": logo_of(hteam),
        "alogo": logo_of(ateam),
        "details": comps[0].get("details") or [],
        "minute": str(minute),
        "hname": hteam.get("displayName") or hteam.get("shortDisplayName") or "Home",
        "aname": ateam.get("displayName") or ateam.get("shortDisplayName") or "Away",
        "hid": str(hteam.get("id", "")),
        "aid": str(ateam.get("id", "")),
        "hs": score_int(home),
        "a_s": score_int(away)
    }


def name_from(obj):
    if not isinstance(obj, dict):
        return ""
    return obj.get("displayName") or obj.get("fullName") or obj.get("shortName") or obj.get("name") or ""


def play_players(play, item):
    names = []
    for src in (play, item):
        for a in src.get("athletesInvolved") or []:
            n = name_from(a)
            if n and n not in names:
                names.append(n)
    if names:
        return names
    for src in (play, item):
        for p in src.get("participants") or []:
            if isinstance(p, dict):
                n = name_from(p.get("athlete") or p)
                if n and n not in names:
                    names.append(n)
    return names


def play_minute(play, item):
    for src, key in ((play, "clock"), (item, "time"), (item, "clock"), (play, "time")):
        obj = src.get(key)
        if isinstance(obj, dict):
            val = obj.get("displayValue")
            if val:
                return str(val)
    return ""


def play_team(play, item):
    for src in (play, item):
        team = src.get("team")
        if isinstance(team, dict):
            tid = str(team.get("id", ""))
            tname = team.get("displayName") or team.get("name") or ""
            return tid, tname
    return "", ""


def play_side(tid, tname, info):
    if tid and tid == info["hid"]:
        return "h"
    if tid and tid == info["aid"]:
        return "a"
    low = tname.lower()
    if low and low == info["hname"].lower():
        return "h"
    if low and low == info["aname"].lower():
        return "a"
    return ""


def classify(play, item):
    ptype = play.get("type")
    if not isinstance(ptype, dict):
        ptype = item.get("type")
    if not isinstance(ptype, dict):
        ptype = {}
    type_l = (str(ptype.get("text", "")) + " " + str(ptype.get("type", ""))).lower()
    text_l = (str(play.get("text", "")) + " " + str(item.get("text", "")) + " " + str(play.get("shortText", ""))).lower()
    scoring = bool(play.get("scoringPlay") or item.get("scoringPlay"))

    if play.get("shootout"):
        return None
    if play.get("ownGoal"):
        return "own_goal"

    own_words = ["own goal", "own-goal", "owngoal", "own_goal"]
    for w in own_words:
        if w in type_l:
            return "own_goal"
    if scoring and "own goal" in text_l:
        return "own_goal"

    miss_words = ["miss", "saved", "wide", "woodwork", "post", "crossbar", "off target", "over the bar"]
    if "penalty" in type_l:
        for w in miss_words:
            if w in type_l:
                return "penalty_missed"
    if not scoring:
        if "penalty missed" in text_l or "penalty saved" in text_l or "misses the penalty" in text_l:
            return "penalty_missed"

    bad_words = ["kick", "disallow", "no goal", "offside", "cancel", "keeper"]
    is_goal = scoring
    if not is_goal and "goal" in type_l:
        is_goal = True
        for w in bad_words:
            if w in type_l:
                is_goal = False
    if not is_goal and "penalty" in type_l and "scored" in type_l:
        is_goal = True
    if is_goal:
        if "penalty" in type_l or "converts the penalty" in text_l or play.get("penaltyKick"):
            return "penalty"
        return "goal"
    return None


def parse_items(items, info, src):
    plays = []
    index = 0
    for item in items:
        index += 1
        if not isinstance(item, dict):
            continue
        play = item.get("play")
        if not isinstance(play, dict):
            play = item
        kind = classify(play, item)
        if kind is None:
            continue
        if kind == "penalty_missed" and src != 1:
            continue
        players = play_players(play, item)
        player = ""
        if players:
            player = players[0]
        minute = play_minute(play, item)
        tid, tname = play_team(play, item)
        side = play_side(tid, tname, info)
        if kind == "own_goal":
            athletes = play.get("athletesInvolved") or item.get("athletesInvolved") or []
            if athletes and isinstance(athletes[0], dict) and isinstance(athletes[0].get("team"), dict):
                own_id = str(athletes[0]["team"].get("id", ""))
                own_side = play_side(own_id, "", info)
                if own_side:
                    side = own_side
        if side == "h":
            tname = info["hname"]
        elif side == "a":
            tname = info["aname"]
        order = index
        try:
            order = int(float(item.get("sequence")))
        except Exception:
            order = index
        plays.append({
            "kind": kind,
            "key": kind + "|" + minute + "|" + player,
            "minute": minute,
            "player": player,
            "team_name": tname,
            "side": side,
            "order": order,
            "src": src
        })
    return plays


def extract_plays(data, info):
    plays = []
    plays.extend(parse_items(data.get("commentary") or [], info, 1))
    plays.extend(parse_items(data.get("keyEvents") or [], info, 2))
    return plays


def detail_plays(info):
    return parse_items(info.get("details") or [], info, 0)


def summary_plays(info, cycle_plays):
    eid = info["id"]
    if eid in cycle_plays:
        return cycle_plays[eid]
    data = fetch_json(ESPN + info["lid"] + "/summary?event=" + eid)
    plays = None
    if data:
        plays = extract_plays(data, info)
    cycle_plays[eid] = plays
    return plays


def team_tag(name):
    emoji = TEAM_EMOJI.get(str(name).lower(), "")
    if emoji:
        return emoji + " "
    return ""


def stats_kb(info):
    lid = info.get("lid")
    eid = info.get("id")
    if not lid or not eid:
        return None
    return {"inline_keyboard": [[{"text": "Match Stats", "callback_data": "st:" + lid + ":" + eid}]]}


def build_goal_text(play, info, hs, a_s):
    kind = play["kind"]
    hname = info["hname"]
    aname = info["aname"]
    head = "<b>" + team_tag(hname) + esc(hname) + " - " + team_tag(aname) + esc(aname) + ":</b>"
    score_now = str(hs) + " - " + str(a_s)

    if kind == "half_time":
        return head + "\nHalf Time. The score is " + score_now
    if kind == "full_time":
        return head + "\nFull Time. The score is " + score_now
    if kind == "reminder":
        return head + "\n⏰ Kickoff in " + str(play.get("mins", "")) + " minutes (" + esc(play.get("time", "")) + ")"

    minute = play.get("minute") or ""
    prefix = ""
    if minute:
        prefix = esc(minute) + ": "
    player = play.get("player") or ""
    club = play.get("team_name") or ""
    who = ""
    if player and club:
        who = esc(player) + " (" + esc(club) + ")"
    elif player:
        who = esc(player)
    elif club:
        who = esc(club)

    score_line = "New score is " + score_now
    if kind == "var_cancel":
        if who:
            line = prefix + "❌ Goal " + who + " canceled after VAR review"
        else:
            line = prefix + "❌ Goal canceled after VAR review"
    elif kind == "penalty_missed":
        score_line = "The score is " + score_now
        if who:
            line = prefix + "❌ " + who + " misses a penalty!"
        else:
            line = prefix + "❌ Penalty missed!"
    elif kind == "own_goal":
        if who:
            line = prefix + "⚽ " + who + " scores an own goal!"
        else:
            line = prefix + "⚽ Own goal!"
    elif kind == "penalty":
        if who:
            line = prefix + "⚽ " + who + " scores from the penalty spot!"
        else:
            line = prefix + "⚽ Penalty goal!"
    else:
        if who:
            line = prefix + "⚽ " + who + " scores!"
        else:
            line = prefix + "⚽ Goal!"
    return head + "\n" + line + "\n" + score_line


def scoring_side(play):
    side = play.get("side", "")
    if play["kind"] == "own_goal":
        if side == "h":
            return "a"
        if side == "a":
            return "h"
        return ""
    return side


def send_pending_goals(info, pend, plays):
    eid = info["id"]
    goals = []
    for p in plays:
        if p["kind"] in GOAL_KINDS and not db_is_seen(eid, p["key"]):
            goals.append(p)
    chosen = []
    for src in (0, 1, 2):
        chosen = [p for p in goals if p["src"] == src]
        if chosen:
            break
    goals = chosen
    goals.sort(key=lambda p: p["order"])
    n = pend["n"]
    if len(goals) > n:
        goals = goals[len(goals) - n:]

    scores = []
    sh = info["hs"]
    sa = info["a_s"]
    for g in reversed(goals):
        scores.insert(0, (sh, sa))
        side = scoring_side(g)
        if side == "h":
            sh -= 1
        elif side == "a":
            sa -= 1

    for g, sc in zip(goals, scores):
        send_alert(info, g, sc[0], sc[1])
        side = scoring_side(g)
        if side and g.get("player"):
            LAST_GOALS[(eid, side)] = g["player"]

    for p in plays:
        if p["kind"] in GOAL_KINDS:
            db_mark_seen(eid, p["key"])
    return len(goals)


def alert_missed_penalties(info, plays):
    eid = info["id"]
    for p in plays:
        if p["kind"] != "penalty_missed" or p["src"] != 1:
            continue
        if db_is_seen(eid, p["key"]):
            continue
        db_mark_seen(eid, p["key"])
        send_alert(info, p, info["hs"], info["a_s"])


def handle_var_cancel(info, side, count):
    eid = info["id"]
    pend = PENDING.get(eid)
    if pend is not None and pend["n"] > 0:
        pend["n"] -= count
        if pend["n"] <= 0:
            del PENDING[eid]
        return
    player = LAST_GOALS.get((eid, side), "")
    if side == "h":
        club = info["hname"]
    else:
        club = info["aname"]
    play = {"kind": "var_cancel", "minute": info["minute"], "player": player, "team_name": club}
    send_alert(info, play, info["hs"], info["a_s"])


def process_score(info, row):
    eid = info["id"]
    hs = info["hs"]
    a_s = info["a_s"]
    old_h = row[0]
    old_a = row[1]
    if hs == old_h and a_s == old_a:
        return
    db_set_score(eid, hs, a_s)
    added = 0
    if hs > old_h:
        added += hs - old_h
    if a_s > old_a:
        added += a_s - old_a
    if added > 0:
        pend = PENDING.get(eid)
        if pend is None:
            PENDING[eid] = {"info": info, "n": added, "tries": 0, "minute": info["minute"]}
        else:
            pend["n"] += added
    if hs < old_h:
        handle_var_cancel(info, "h", old_h - hs)
    if a_s < old_a:
        handle_var_cancel(info, "a", old_a - a_s)


def handle_event(info, cycle, cycle_plays):
    eid = info["id"]
    pend = PENDING.get(eid)
    if pend is not None:
        pend["info"] = info

    state = info["state"]
    row = db_get_score(eid)

    if state == "pre":
        if row is None:
            db_set_score(eid, 0, 0)
        mins = minutes_to_kickoff(info["date"])
        if mins is not None and 0 <= mins <= REMINDER_MINUTES and not db_is_seen(eid, "mark:REM"):
            db_mark_seen(eid, "mark:REM")
            play = {"kind": "reminder", "mins": int(round(mins)), "time": kickoff_wat(info["date"])}
            send_alert(info, play, 0, 0)
        return

    if state == "post":
        if row is None:
            return
        process_score(info, row)
        if eid in PENDING:
            return
        name = info["status_name"]
        if ("FULL_TIME" in name or "FINAL" in name) and not db_is_seen(eid, "mark:FT"):
            db_mark_seen(eid, "mark:FT")
            send_alert(info, {"kind": "full_time"}, info["hs"], info["a_s"])
        return

    if state != "in":
        return

    if row is None:
        db_set_score(eid, info["hs"], info["a_s"])
        for p in detail_plays(info):
            db_mark_seen(eid, p["key"])
        plays = summary_plays(info, cycle_plays)
        if plays:
            for p in plays:
                db_mark_seen(eid, p["key"])
        return

    process_score(info, row)

    if info["status_name"] == "STATUS_HALFTIME" and eid not in PENDING and not db_is_seen(eid, "mark:HT"):
        db_mark_seen(eid, "mark:HT")
        send_alert(info, {"kind": "half_time"}, info["hs"], info["a_s"])

    if cycle % PEN_CHECK_EVERY == 0:
        plays = summary_plays(info, cycle_plays)
        if plays:
            alert_missed_penalties(info, plays)


def resolve_pending(cycle_plays):
    for eid in list(PENDING.keys()):
        pend = PENDING[eid]
        info = pend["info"]
        pend["tries"] += 1
        details = detail_plays(info)
        sent = send_pending_goals(info, pend, details)
        if sent == 0:
            plays = summary_plays(info, cycle_plays)
            if plays:
                sent = send_pending_goals(info, pend, details + plays)
                alert_missed_penalties(info, plays)
        pend["n"] -= sent
        if pend["n"] <= 0:
            del PENDING[eid]
        elif pend["tries"] >= MAX_PENDING_TRIES:
            generic = {"kind": "goal", "minute": pend["minute"], "team_name": "", "player": ""}
            send_alert(info, generic, info["hs"], info["a_s"])
            del PENDING[eid]


def collect_events(cycle):
    dates = scoreboard_dates()
    lids = []
    urls = []
    for lid in LEAGUE_IDS:
        if FAILS.get(lid, 0) >= 5 and cycle % 30 != 0:
            continue
        for ds in dates:
            lids.append(lid)
            urls.append(scoreboard_url(lid, ds))
    results = list(fetch_pool.map(fetch_json, urls))
    fails = 0
    for lid, r in zip(lids, results):
        if r is None:
            fails += 1
            FAILS[lid] = FAILS.get(lid, 0) + 1
        else:
            FAILS[lid] = 0
    NOTIFIER["fetch_fail"] = fails
    events = []
    seen_ids = set()
    for lid, data in zip(lids, results):
        if not data:
            continue
        for ev in data.get("events") or []:
            info = event_info(ev, lid)
            if info is None:
                continue
            if info["id"] in seen_ids:
                continue
            seen_ids.add(info["id"])
            events.append(info)
    return events


def check_cycle(cycle):
    cycle_plays = {}
    events = collect_events(cycle)
    live_count = 0
    for info in events:
        if info["state"] == "in":
            live_count += 1
        try:
            handle_event(info, cycle, cycle_plays)
        except Exception as err:
            print("Event error: " + str(err))
    resolve_pending(cycle_plays)
    NOTIFIER["events"] = len(events)
    NOTIFIER["live"] = live_count
    NOTIFIER["last_run"] = time.time()


def notifier_loop():
    print("Notifier started - every " + str(NOTIFY_INTERVAL) + " sec")
    cycle = 0
    while True:
        cycle += 1
        started = time.time()
        try:
            check_cycle(cycle)
            NOTIFIER["error"] = ""
        except Exception as err:
            NOTIFIER["error"] = str(err)
            print("Notifier error: " + str(err))
        elapsed = time.time() - started
        wait = NOTIFY_INTERVAL - elapsed
        if wait < 1:
            wait = 1
        time.sleep(wait)


# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------


def main():
    web = threading.Thread(target=run_web, daemon=True)
    web.start()

    if not TOKEN:
        print("ERROR: BOT_TOKEN is not set")
        while True:
            time.sleep(60)

    init_db()
    notifier = threading.Thread(target=notifier_loop, daemon=True)
    notifier.start()
    poll_loop()


if __name__ == "__main__":
    main()
