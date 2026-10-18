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
NOTIFIER = {"last_run": 0, "events": 0, "live": 0, "fetch_fail": 0, "error": ""}
LAST_ERRORS = {"edit": ""}
LAST_GOALS = {}

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


def show_photo(chat_id, msg, png, kb):
    message_id = msg.get("message_id")
    if msg.get("photo"):
        res = edit_photo(chat_id, message_id, png, kb)
        if not res.get("ok") and not is_not_modified(res) and not is_hard_edit_error(res):
            time.sleep(1)
            res = edit_photo(chat_id, message_id, png, kb)
        if res.get("ok") or is_not_modified(res):
            return
        note_edit_error(res)
        if not is_hard_edit_error(res):
            return
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
        [{"text": "🔄 Refresh Table", "callback_data": "rt:" + lid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def matches_kb(lid):
    return {"inline_keyboard": [
        [
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ],
        [{"text": "🔄 Refresh Matches", "callback_data": "rm:" + lid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def live_kb():
    return {"inline_keyboard": [
        [
            {"text": "🔄 Refresh", "callback_data": "rlive"},
            {"text": "📅 Today", "callback_data": "today"}
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


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
    width = 760
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

    col_y = head_h
    draw.rectangle([0, col_y, width, col_y + col_h - 1], fill=(34, 41, 60))
    head_color = (170, 180, 200)
    draw_right(draw, 48, col_y + 11, "#", f_head, head_color)
    draw.text((104, col_y + 11), "Team", font=f_head, fill=head_color)
    draw.text((392, col_y + 11), "Live", font=f_head, fill=head_color)
    draw_right(draw, 580, col_y + 11, "P", f_head, head_color)
    draw_right(draw, 650, col_y + 11, "GD", f_head, head_color)
    draw_right(draw, 725, col_y + 11, "Pts", f_head, head_color)

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
        elif i % 2 == 0:
            bg = (24, 29, 42)
            name_color = (235, 238, 245)
            num_color = (210, 215, 225)
        else:
            bg = (20, 25, 36)
            name_color = (235, 238, 245)
            num_color = (210, 215, 225)

        draw.rectangle([0, y, width, y + row_h - 1], fill=bg)
        draw_right(draw, 48, y + 9, str(i + 1), f_row, num_color)

        logo = logos.get(row["logo"])
        if logo is not None:
            lx = 62 + (LOGO_SIZE - logo.width) // 2
            ly = y + (row_h - logo.height) // 2
            img.paste(logo, (lx, ly), logo)
        else:
            draw.ellipse([64, y + 10, 88, y + 30], outline=(90, 100, 120))

        name = row["name"]
        while len(name) > 1 and text_w(draw, name, f_row) > 280:
            name = name[:-1]
        draw.text((104, y + 9), name, font=f_row, fill=name_color)

        if is_live:
            draw.rounded_rectangle([392, y + 7, 508, y + row_h - 7], radius=9, fill=(215, 38, 48))
            draw.text((402, y + 11), "LIVE " + score, font=f_pill, fill=(255, 255, 255))

        draw_right(draw, 580, y + 9, str(row["p"]), f_row, num_color)
        if row["gd"] > 0:
            gd_text = "+" + str(row["gd"])
        else:
            gd_text = str(row["gd"])
        draw_right(draw, 650, y + 9, gd_text, f_row, num_color)
        draw_right(draw, 725, y + 9, str(row["pts"]), f_bold, name_color)

    foot_y = top + row_h * len(rows) + 12
    draw.text((24, foot_y), "Updated " + stamp(), font=f_small, fill=(140, 150, 170))
    draw.text((520, foot_y), "Red row = playing now", font=f_small, fill=(255, 120, 120))

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
        logos[u] = LOGO_CACHE.get(u)
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
    info = {"hname": "Test FC", "aname": "Demo United"}
    text = build_goal_text(play, info, 1, 0) + "\n(this is a test alert)"
    res = send_text(chat_id, text)
    if not res.get("ok"):
        print("test alert failed: " + str(res.get("description")))


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

    elif data.startswith("tbl:") or data.startswith("rt:"):
        refresh = data.startswith("rt:")
        lid = data.split(":", 1)[1]
        if lid not in LEAGUE_IDS:
            answer_cb(cb_id)
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
        text = build_matches_text(lid)
        if refresh:
            show_text(chat_id, msg, text, matches_kb(lid))
        else:
            send_text(chat_id, text, matches_kb(lid))

    elif data == "live" or data == "rlive":
        answer_cb(cb_id, "Checking live games...")
        text = build_multi_text(True)
        if data == "rlive":
            show_text(chat_id, msg, text, live_kb())
        else:
            send_text(chat_id, text, live_kb())

    elif data == "today" or data == "rtoday":
        answer_cb(cb_id, "Loading today...")
        text = build_multi_text(False)
        if data == "rtoday":
            show_text(chat_id, msg, text, today_kb())
        else:
            send_text(chat_id, text, today_kb())

    elif data.startswith("st:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Loading stats...")
        text = build_stats_text(parts[1], parts[2])
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
        send_text(chat_id, "🔕 Goal alerts are OFF.\nSend /start to turn them on again.")
        return

    db_add_sub(chat_id)
    if low.startswith("/start") or low.startswith("/menu") or low == "menu":
        send_text(chat_id, START_TEXT, main_menu())
    elif low.startswith("/status"):
        send_text(chat_id, build_status_text(chat_id))
    elif low.startswith("/testalert"):
        send_test_alert(chat_id)
    elif low:
        send_text(chat_id, "Send /start to open the football menu.")


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


def broadcast(text, kb=None):
    for chat_id in db_get_subs():
        res = send_text(chat_id, text, kb)
        if not res.get("ok"):
            if res.get("error_code") == 403:
                db_remove_sub(chat_id)
            else:
                print("broadcast fail " + str(chat_id) + " : " + str(res.get("description")))
        time.sleep(0.05)


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
        text = build_goal_text(g, info, sc[0], sc[1])
        broadcast(text, stats_kb(info))
        print("GOAL PUSHED: " + text)
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
        text = build_goal_text(p, info, info["hs"], info["a_s"])
        broadcast(text, stats_kb(info))
        print("PENALTY MISS PUSHED: " + text)


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
    text = build_goal_text(play, info, info["hs"], info["a_s"])
    broadcast(text, stats_kb(info))
    print("VAR CANCEL PUSHED: " + text)


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
            text = build_goal_text({"kind": "full_time"}, info, info["hs"], info["a_s"])
            broadcast(text, stats_kb(info))
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
        text = build_goal_text({"kind": "half_time"}, info, info["hs"], info["a_s"])
        broadcast(text, stats_kb(info))

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
            text = build_goal_text(generic, info, info["hs"], info["a_s"])
            broadcast(text, stats_kb(info))
            print("GOAL PUSHED (no scorer): " + text)
            del PENDING[eid]


def collect_events():
    dates = scoreboard_dates()
    lids = []
    urls = []
    for lid in LEAGUE_IDS:
        for ds in dates:
            lids.append(lid)
            urls.append(scoreboard_url(lid, ds))
    results = list(fetch_pool.map(fetch_json, urls))
    fails = 0
    for r in results:
        if r is None:
            fails += 1
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
    events = collect_events()
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
