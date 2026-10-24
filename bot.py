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
SB_TTL = 15
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
ESPN_ALT = "https://site.web.api.espn.com/apis/site/v2/sports/soccer/"
NOTIFY_TICK = 1
LIVE_POLL = 4
SOON_POLL = 15
IDLE_POLL = 60
EMPTY_POLL = 300
PEN_CHECK_SECONDS = 25
SCORER_WAIT_SECONDS = 300
SEND_GAP = 0.04
LINEUP_WINDOW = 95
LINEUP_CHECK_SECONDS = 45
DEFAULT_TZ = 60
START_TIME = time.time()

TZ_OPTIONS = [
    (-300, "UTC-5 New York"), (-240, "UTC-4 Caracas"), (-180, "UTC-3 Sao Paulo"),
    (0, "UTC+0 London"), (60, "UTC+1 Lagos / Paris"), (120, "UTC+2 Cairo / Joburg"),
    (180, "UTC+3 Istanbul / Nairobi"), (240, "UTC+4 Dubai"), (300, "UTC+5 Karachi"),
    (330, "UTC+5:30 India"), (480, "UTC+8 Singapore"), (540, "UTC+9 Tokyo")
]
DIGEST_HOURS = [6, 7, 8, 9, 10, 12]


def load_admin_ids():
    raw = os.environ.get("ADMIN_CHAT_IDS", "") or os.environ.get("ALERT_CHAT_IDS", "")
    return [x.strip() for x in raw.split(",") if x.strip()]


ADMIN_IDS = load_admin_ids()
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
    "⚙️ Settings: pick your time zone and a morning digest.\n"
    "📆 Fixtures: tomorrow, this week and each league's results.\n"
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
SEND_POOL = ThreadPoolExecutor(max_workers=12)

db_lock = threading.Lock()
conn = None

SB_CACHE = {}
LOGO_CACHE = {}
FONT_CACHE = {}
PENDING = {}
NOTIFIER = {"last_run": 0, "events": 0, "live": 0, "fetch_fail": 0, "polled": 0, "error": ""}
LAST_ERRORS = {"edit": "", "espn": ""}
LAST_GOALS = {}
FAILS = {}
TEAM_INDEX = {"time": 0, "teams": {}}
PENDING_INFO = {}
LAST_PEN = {}
LAST_SUMMARY = {}
DEC_CANDIDATE = {}
LEAGUE_NEXT = {}
LEAGUE_STATS = {}
ALERT_COUNT = {"sent": 0, "last": 0}
RATE_LOCK = threading.Lock()
RATE_STATE = {"next": 0.0}
TZ_LOCAL = threading.local()
GOAL_LOG = {}
LAST_LINEUP = {}
FT_TIME = {}
RED_LOG = {}
RANGE_CACHE = {}
SCORER_CACHE = {}
ADMIN_STATE = {"espn_down": False, "last_espn_alert": 0, "err_count": 0, "last_err_alert": 0}

TEAM_EMOJI = {
    "galatasaray": "🟡🔴", "fenerbah": "💛💙", "besiktas": "⚫⚪", "beşiktaş": "⚫⚪",
    "trabzonspor": "🔴🔵", "basaksehir": "🟠", "başakşehir": "🟠", "samsunspor": "🔴",
    "arsenal": "🔴", "chelsea": "🔵", "liverpool": "🔴", "manchester city": "🩵",
    "man city": "🩵", "manchester united": "🔴", "man united": "🔴", "tottenham": "⚪",
    "newcastle": "⚫⚪", "aston villa": "🟣", "west ham": "🟣", "everton": "🔵",
    "real madrid": "⚪", "barcelona": "🔵🔴", "atl": "🔴⚪", "sevilla": "⚪🔴",
    "real betis": "💚", "valencia": "⚪", "villarreal": "💛", "athletic": "🔴⚪",
    "real sociedad": "🔵⚪", "juventus": "⚪⚫", "milan": "🔴⚫", "inter": "🔵⚫",
    "napoli": "🔵", "roma": "🟡🔴", "lazio": "🩵", "atalanta": "🔵⚫",
    "bayern": "🔴", "dortmund": "🟡⚫", "leverkusen": "🔴⚫", "leipzig": "🔴⚪",
    "paris saint": "🔵🔴", "psg": "🔵🔴", "marseille": "🩵", "lyon": "🔵🔴",
    "monaco": "🔴⚪", "lille": "🔴", "benfica": "🔴", "porto": "🔵⚪",
    "sporting": "🟢", "ajax": "⚪🔴", "psv": "🔴⚪", "feyenoord": "🔴⚪",
    "celtic": "🟢", "club brugge": "🔵⚫", "salzburg": "🔴⚪", "shakhtar": "🟠⚫"
}


def load_club_emoji():
    data = {}
    raw = os.environ.get("CLUB_EMOJI", "")
    if raw:
        try:
            data.update(json.loads(raw))
        except Exception as err:
            print("CLUB_EMOJI is not valid JSON: " + str(err))
    if os.path.exists("club_emoji.json"):
        try:
            with open("club_emoji.json", encoding="utf-8") as fh:
                data.update(json.load(fh))
        except Exception as err:
            print("club_emoji.json error: " + str(err))
    out = {}
    for key, value in data.items():
        out[str(key).lower()] = str(value)
    return out


CLUB_EMOJI = load_club_emoji()

# ---------------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------------


def now_wat():
    return datetime.now(timezone.utc) + timedelta(hours=1)


def current_tz():
    return getattr(TZ_LOCAL, "minutes", DEFAULT_TZ)


def set_current_tz(minutes):
    TZ_LOCAL.minutes = minutes


def tz_label(minutes):
    if minutes == 60:
        return "WAT"
    sign = "+"
    m = minutes
    if m < 0:
        sign = "-"
        m = -m
    label = "UTC" + sign + str(m // 60)
    if m % 60:
        label += ":" + str(m % 60).zfill(2)
    return label


def stamp():
    local = datetime.now(timezone.utc) + timedelta(minutes=current_tz())
    return local.strftime("%H:%M:%S") + " " + tz_label(current_tz())


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
        dt = dt + timedelta(minutes=current_tz())
        return dt.strftime("%H:%M") + " " + tz_label(current_tz())
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
    db_exec("CREATE TABLE IF NOT EXISTS settings (chat_id TEXT PRIMARY KEY, tz INTEGER, digest_hour INTEGER, last_digest TEXT, last_seen TEXT)")
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


def db_touch(chat_id):
    seen = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    db_exec("INSERT OR IGNORE INTO settings (chat_id, tz, digest_hour, last_digest, last_seen) VALUES (?,?,?,?,?)", (str(chat_id), DEFAULT_TZ, -1, "", seen))
    db_exec("UPDATE settings SET last_seen=? WHERE chat_id=?", (seen, str(chat_id)))


def db_get_setting(chat_id):
    rows = db_exec("SELECT tz, digest_hour, last_digest FROM settings WHERE chat_id=?", (str(chat_id),))
    if rows:
        tz = rows[0][0]
        if tz is None:
            tz = DEFAULT_TZ
        return tz, rows[0][1], rows[0][2]
    return DEFAULT_TZ, -1, ""


def user_tz(chat_id):
    return db_get_setting(chat_id)[0]


def db_set_tz(chat_id, tz):
    db_touch(chat_id)
    db_exec("UPDATE settings SET tz=? WHERE chat_id=?", (int(tz), str(chat_id)))


def db_set_digest(chat_id, hour):
    db_touch(chat_id)
    db_exec("UPDATE settings SET digest_hour=?, last_digest=? WHERE chat_id=?", (int(hour), "", str(chat_id)))


def db_mark_digest(chat_id, day):
    db_exec("UPDATE settings SET last_digest=? WHERE chat_id=?", (day, str(chat_id)))


def db_digest_users():
    rows = db_exec("SELECT chat_id, tz, digest_hour, last_digest FROM settings WHERE digest_hour >= 0 AND chat_id IN (SELECT chat_id FROM subs)")
    return [(r[0], r[1] if r[1] is not None else DEFAULT_TZ, r[2], r[3] or "") for r in rows]


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
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as err:
        LAST_ERRORS["espn"] = str(err) + " | " + url[-70:] + " (" + stamp() + ")"
        print("fetch fail " + url + " : " + str(err))
        return None


def fetch_scoreboard(lid, date_str, today=True):
    urls = [
        ESPN + lid + "/scoreboard?dates=" + date_str,
        ESPN_ALT + lid + "/scoreboard?dates=" + date_str
    ]
    if today:
        urls.append(ESPN + lid + "/scoreboard")
    for url in urls:
        data = fetch_json(url)
        if data is not None:
            return data
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
        [
            {"text": "📆 Tomorrow", "callback_data": "tom"},
            {"text": "🗓 This Week", "callback_data": "week"}
        ],
        [
            {"text": "⭐ My Team", "callback_data": "my"},
            {"text": "⚙️ Settings", "callback_data": "set"}
        ]
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
    return {"inline_keyboard": [[
        {"text": "Match Stats", "callback_data": "st:" + lid + ":" + eid},
        {"text": "Lineups", "callback_data": "ln:" + lid + ":" + eid}
    ]]}


def stats_screen_kb(lid, eid):
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "rst:" + lid + ":" + eid}],
        [
            {"text": "👥 Lineups", "callback_data": "ln:" + lid + ":" + eid},
            {"text": "⬅️ Main Menu", "callback_data": "menu"}
        ]
    ]}


def lineups_screen_kb(lid, eid):
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "rln:" + lid + ":" + eid}],
        [
            {"text": "📊 Match Stats", "callback_data": "st:" + lid + ":" + eid},
            {"text": "⬅️ Main Menu", "callback_data": "menu"}
        ]
    ]}


def fx_kb(lid, mode, rows=None):
    kb_rows = []
    if rows:
        kb_rows.extend(match_buttons(rows, 6))
    kb_rows.append([
        {"text": "📅 Fixtures", "callback_data": "fx:" + lid},
        {"text": "✅ Results", "callback_data": "rs:" + lid}
    ])
    if mode == "fx":
        kb_rows.append([{"text": "🔄 Refresh", "callback_data": "rfx:" + lid}])
    else:
        kb_rows.append([{"text": "🔄 Refresh", "callback_data": "rrs:" + lid}])
    second = [{"text": "⚽ Today's Matches", "callback_data": "mat:" + lid}]
    if lid not in CUP_IDS:
        second.append({"text": "📊 Table", "callback_data": "tbl:" + lid})
    kb_rows.append(second)
    kb_rows.append([{"text": "⬅️ Main Menu", "callback_data": "menu"}])
    return {"inline_keyboard": kb_rows}


def range_kb(week):
    if week:
        refresh = "rweek"
    else:
        refresh = "rtom"
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": refresh}],
        [
            {"text": "📆 Tomorrow", "callback_data": "tom"},
            {"text": "🗓 This Week", "callback_data": "week"}
        ],
        [
            {"text": "📅 Today", "callback_data": "today"},
            {"text": "⬅️ Main Menu", "callback_data": "menu"}
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
        [{"text": "🏅 Top Scorers", "callback_data": "ts:" + lid}],
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
    kb_rows.append([
        {"text": "📅 Fixtures", "callback_data": "fx:" + lid},
        {"text": "✅ Results", "callback_data": "rs:" + lid}
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
    data = fetch_scoreboard(lid, today_str(), True)
    if data is not None:
        SB_CACHE[lid] = (now, data)
        return data
    if cached and (now - cached[0]) < 1800:
        return cached[1]
    return None


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
        value = team_obj.get("score", 0)
        if isinstance(value, dict):
            value = value.get("value", value.get("displayValue", 0))
        return int(float(value or 0))
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
        dt = dt + timedelta(minutes=current_tz())
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
    draw.text((20, 50), "Kickoff times are " + tz_label(current_tz()), font=f_small, fill=(140, 150, 170))

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


def text_from_groups(title, groups):
    text = "<b>" + esc(title) + "</b>\n\n"
    for group_title, rows in groups:
        text += "<b>" + esc(group_title) + "</b>\n"
        for r in rows:
            if r["state"] == "pre":
                mid = "vs"
                status = kickoff_hm(r["date"]) + " WAT"
            else:
                mid = str(r["hs"]) + "-" + str(r["a_s"])
                status = r["short"] or r["minute"]
            text += team_tag(r["hshort"], r["hid"]) + esc(r["hshort"]) + " " + mid + " " + team_tag(r["ashort"], r["aid"]) + esc(r["ashort"]) + " • " + esc(status) + "\n"
        text += "\n"
    text += "🔄 Updated " + stamp()
    return text[:4000]


def build_matches_view(lid):
    data = get_scoreboard(lid)
    if data is None:
        return None, "Could not load matches right now. Tap Refresh in a few seconds.", []
    rows = rows_from_data(lid, data, False)
    if not rows:
        return None, "⚽ <b>" + esc(LEAGUE_NAMES.get(lid, lid)) + "</b>\n\nNo matches today.\n\n🔄 Updated " + stamp(), []
    title = LEAGUE_TITLES.get(lid, lid) + " - Today"
    groups = [(LEAGUE_TITLES.get(lid, lid), rows)]
    try:
        png = render_matches_image(title, groups)
    except Exception as err:
        print("matches image failed: " + str(err))
        return None, text_from_groups(title, groups), rows
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
    try:
        png = render_matches_image(title, groups)
    except Exception as err:
        print("matches image failed: " + str(err))
        return None, text_from_groups(title, groups), all_rows
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
    if ALERT_COUNT["last"]:
        last_alert = str(int(time.time() - ALERT_COUNT["last"])) + "s ago"
    else:
        last_alert = "none yet"
    if str(chat_id) in subs:
        you = "yes"
    else:
        you = "no"
    waiting = 0
    for log in GOAL_LOG.values():
        for entry in log.values():
            if not entry["key"]:
                waiting += 1
    events_total = 0
    live_total = 0
    for stat in LEAGUE_STATS.values():
        events_total += stat[0]
        live_total += stat[1]
    lines = [
        "🛠 <b>Bot status</b>",
        "Your chat ID: <code>" + str(chat_id) + "</code>",
        "Subscribed chats: " + str(len(subs)),
        "Chats using My Team: " + str(len(db_all_favs())),
        "You get goal alerts: " + you,
        "Notifier last ran: " + ran,
        "Games found: " + str(events_total) + " (live now: " + str(live_total) + ")",
        "Leagues polled last cycle: " + str(NOTIFIER["polled"]) + ", failures: " + str(NOTIFIER["fetch_fail"]),
        "Alerts sent: " + str(ALERT_COUNT["sent"]) + " (last " + last_alert + ")",
        "Goals waiting for scorer name: " + str(waiting),
        "Last ESPN error: " + esc(LAST_ERRORS["espn"] or "none"),
        "Last notifier error: " + esc(NOTIFIER["error"] or "none"),
        "Last edit error: " + esc(LAST_ERRORS["edit"] or "none")
    ]
    return "\n".join(lines)


def send_test_alert(chat_id):
    play = {"kind": "goal", "minute": "88'", "team_name": "Test FC", "player": "Test Player"}
    info = {"lid": "eng.1", "id": "", "hname": "Test FC", "aname": "Demo United", "hid": "", "aid": ""}
    text = build_goal_text(play, info, 1, 0) + "\n(this is a test alert)"
    send_safe(chat_id, text, None)


# ---------- team page ----------


def parse_team_event(ev, lid):
    comps = ev.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    home, away = split_teams(comp)
    if home is None or away is None:
        return None
    stype = ((comp.get("status") or ev.get("status") or {}).get("type")) or {}
    state = stype.get("state", "")
    if not state:
        if stype.get("completed"):
            state = "post"
        else:
            state = "pre"
    hid = str(home.get("id") or (home.get("team") or {}).get("id", ""))
    aid = str(away.get("id") or (away.get("team") or {}).get("id", ""))
    return {
        "id": str(ev.get("id", "")),
        "date": str(ev.get("date") or comp.get("date") or ""),
        "lid": lid,
        "state": state,
        "hid": hid,
        "aid": aid,
        "hname": short_name(home),
        "aname": short_name(away),
        "hs": score_int(home),
        "a_s": score_int(away)
    }


def fetch_team_events(lid, tid):
    events = []
    data = fetch_json(ESPN + lid + "/teams/" + tid + "/schedule")
    if data:
        for ev in data.get("events") or []:
            e = parse_team_event(ev, lid)
            if e:
                events.append(e)
    has_next = False
    for e in events:
        if e["state"] != "post":
            has_next = True
    if not has_next:
        info = fetch_json(ESPN + lid + "/teams/" + tid)
        team = (info or {}).get("team") or {}
        for ev in team.get("nextEvent") or []:
            e = parse_team_event(ev, lid)
            if e:
                events.append(e)
    return events


def team_event_from_row(r):
    return {
        "id": r["id"], "date": r["date"], "lid": r["lid"], "state": r["state"],
        "hid": r["hid"], "aid": r["aid"], "hname": r["hshort"], "aname": r["ashort"],
        "hs": r["hs"], "a_s": r["a_s"]
    }


def team_events_job(job, tid):
    lid = job[0]
    kind = job[1]
    if kind == "schedule":
        return fetch_team_events(lid, tid)
    events = []
    for start_days, end_days in ((-45, 0), (1, 120)):
        rows = range_rows(lid, start_days, end_days)
        for r in rows or []:
            if r["hid"] == tid or r["aid"] == tid:
                events.append(team_event_from_row(r))
    return events


def team_events(tid):
    team = get_team_index().get(tid)
    if team is None:
        return []
    jobs = []
    for lid in team.get("lids") or []:
        jobs.append((lid, "range"))
        jobs.append((lid, "schedule"))
    results = list(fetch_pool.map(lambda job: team_events_job(job, tid), jobs))
    merged = []
    seen = set()
    for evs in results:
        for e in evs:
            if e["id"] in seen:
                continue
            seen.add(e["id"])
            merged.append(e)
    return merged


def build_team_list(tid, results):
    team = get_team_index().get(tid)
    if team is None:
        return "I could not find that club. Type its name again."
    events = team_events(tid)
    if results:
        title = "🗓 <b>" + esc(team["name"]) + " - Results</b>\n\n"
        items = sorted([e for e in events if e["state"] == "post"], key=lambda e: e["date"], reverse=True)
    else:
        title = "📅 <b>" + esc(team["name"]) + " - Fixtures</b>\n\n"
        items = sorted([e for e in events if e["state"] != "post"], key=lambda e: e["date"])
    if not items:
        return title + "Nothing found right now. Tap Refresh in a few seconds.\n\n🔄 Updated " + stamp()
    marks = {"W": "🟢", "D": "🟡", "L": "🔴"}
    lines = []
    for e in items[:30]:
        league = LEAGUE_TITLES.get(e["lid"], e["lid"])
        if results:
            lines.append(marks[team_result(e, tid)] + " " + esc(e["hname"]) + " " + str(e["hs"]) + "-" + str(e["a_s"]) + " " + esc(e["aname"]) + " (" + esc(league) + ") " + esc(local_date_title(e["date"])))
        else:
            lines.append(esc(when_text(e["date"])) + "\n" + esc(e["hname"]) + " vs " + esc(e["aname"]) + " (" + esc(league) + ")")
    text = title + "\n".join(lines) + "\n\n🔄 Updated " + stamp()
    return text[:3900]


def team_list_kb(tid, results):
    if results:
        refresh = "rtr:" + tid
        other = {"text": "📅 Fixtures", "callback_data": "tf:" + tid}
    else:
        refresh = "rtf:" + tid
        other = {"text": "🗓 Results", "callback_data": "tr:" + tid}
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": refresh}],
        [other, {"text": "📄 Team page", "callback_data": "tp:" + tid}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


def team_result(e, tid):
    if e["hid"] == tid:
        mine = e["hs"]
        theirs = e["a_s"]
    else:
        mine = e["a_s"]
        theirs = e["hs"]
    if mine > theirs:
        return "W"
    if mine < theirs:
        return "L"
    return "D"


def when_text(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        dt = dt + timedelta(minutes=current_tz())
        return dt.strftime("%a %d %b, %H:%M") + " " + tz_label(current_tz())
    except Exception:
        return ""


def build_team_page(tid):
    team = get_team_index().get(tid)
    if team is None:
        return "I could not find that club. Type its name again."
    events = team_events(tid)
    if not events:
        return "📄 <b>" + esc(team["name"]) + "</b>\n\nThe schedule is not available right now. Try Refresh.\n\n🔄 Updated " + stamp()
    past = sorted([e for e in events if e["state"] == "post"], key=lambda e: e["date"], reverse=True)
    coming = sorted([e for e in events if e["state"] != "post"], key=lambda e: e["date"])

    text = "📄 <b>" + esc(team["name"]) + "</b>\n\n"
    if coming:
        n = coming[0]
        league = LEAGUE_TITLES.get(n["lid"], n["lid"])
        if n["state"] == "in":
            text += "🔴 <b>Live now</b>\n" + esc(n["hname"]) + " " + str(n["hs"]) + " - " + str(n["a_s"]) + " " + esc(n["aname"]) + " (" + esc(league) + ")\n\n"
        else:
            text += "⏭ <b>Next match</b>\n" + esc(when_text(n["date"])) + "\n" + esc(n["hname"]) + " vs " + esc(n["aname"]) + " (" + esc(league) + ")\n\n"
    else:
        text += "⏭ <b>Next match</b>\nNo upcoming match found.\n\n"

    recent = past[:5]
    if recent:
        marks = {"W": "🟢", "D": "🟡", "L": "🔴"}
        letters = []
        for e in reversed(recent):
            letters.append(marks[team_result(e, tid)] + team_result(e, tid))
        text += "📈 <b>Form</b> (oldest to latest)\n" + " ".join(letters) + "\n\n"
        text += "🗓 <b>Recent results</b>\n"
        for e in recent:
            league = LEAGUE_TITLES.get(e["lid"], e["lid"])
            text += marks[team_result(e, tid)] + " " + esc(e["hname"]) + " " + str(e["hs"]) + "-" + str(e["a_s"]) + " " + esc(e["aname"]) + " (" + esc(league) + ")\n"
    else:
        text += "🗓 <b>Recent results</b>\nNo finished matches found.\n"
    text += "\n🔄 Updated " + stamp()
    return text[:3900]


def team_kb(tid):
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "rtp:" + tid}],
        [
            {"text": "📅 Fixtures", "callback_data": "tf:" + tid},
            {"text": "🗓 Results", "callback_data": "tr:" + tid}
        ],
        [
            {"text": "⭐ My Team", "callback_data": "my"},
            {"text": "⬅️ Main Menu", "callback_data": "menu"}
        ]
    ]}


# ---------- top scorers ----------


def stat_value(pos, vals, name):
    where = pos.get(name)
    if where is None:
        return None
    row = vals.get(where[0]) or []
    if where[1] >= len(row):
        return None
    try:
        return int(float(row[where[1]]))
    except Exception:
        return None


def scorers_from_stats(lid):
    url = ("https://site.web.api.espn.com/apis/common/v3/sports/soccer/" + lid +
           "/statistics/byathlete?region=us&lang=en&contentorigin=espn&isqualified=true&page=1&limit=25&sort=offensive.totalGoals%3Adesc")
    data = fetch_json(url)
    if not data:
        return None
    pos = {}
    for c in data.get("categories") or []:
        for i, n in enumerate(c.get("names") or []):
            pos[n] = (c.get("name"), i)
    players = []
    for a in data.get("athletes") or []:
        ath = a.get("athlete") or {}
        vals = {}
        for c in a.get("categories") or []:
            vals[c.get("name")] = c.get("values") or []
        goals = stat_value(pos, vals, "totalGoals")
        if goals is None:
            continue
        assists = stat_value(pos, vals, "goalAssists")
        players.append({
            "name": ath.get("displayName") or ath.get("shortName") or "?",
            "team": ath.get("teamShortName") or ath.get("teamName") or "",
            "goals": goals,
            "assists": assists
        })
    players.sort(key=lambda p: (-p["goals"], -(p["assists"] or 0)))
    return players[:10]


def season_start():
    now = datetime.now(timezone.utc)
    year = now.year
    if now.month < 7:
        year -= 1
    return datetime(year, 7, 20, tzinfo=timezone.utc)


def scorers_from_matches(lid):
    cached = SCORER_CACHE.get(lid)
    if cached and (time.time() - cached[0]) < 1800:
        return cached[1]
    now = datetime.now(timezone.utc)
    chunks = []
    cur = season_start()
    while cur < now:
        end = cur + timedelta(days=40)
        if end > now:
            end = now
        chunks.append((cur, end))
        cur = end + timedelta(days=1)

    def load(chunk):
        rng = chunk[0].strftime("%Y%m%d") + "-" + chunk[1].strftime("%Y%m%d")
        for base in (ESPN, ESPN_ALT):
            data = fetch_json(base + lid + "/scoreboard?dates=" + rng + "&limit=400")
            if data is not None:
                return data
        return None

    datas = list(fetch_pool.map(load, chunks))
    counts = {}
    team_names = {}
    seen_events = set()
    got = False
    for data in datas:
        if not data:
            continue
        got = True
        for ev in data.get("events") or []:
            ev_id = str(ev.get("id", ""))
            if ev_id and ev_id in seen_events:
                continue
            seen_events.add(ev_id)
            comps = ev.get("competitions") or []
            if not comps:
                continue
            comp = comps[0]
            names = {}
            for c in comp.get("competitors") or []:
                t = c.get("team") or {}
                names[str(t.get("id", ""))] = t.get("shortDisplayName") or t.get("displayName") or ""
            for d in comp.get("details") or []:
                if not d.get("scoringPlay") or d.get("ownGoal") or d.get("shootout"):
                    continue
                aths = d.get("athletesInvolved") or []
                if not aths:
                    continue
                name = name_from(aths[0])
                if not name:
                    continue
                tid = str((d.get("team") or {}).get("id", ""))
                key = (name, tid)
                counts[key] = counts.get(key, 0) + 1
                team_names[key] = names.get(tid, "")
    if not got:
        return None
    players = []
    for key, goals in counts.items():
        players.append({"name": key[0], "team": team_names.get(key, ""), "goals": goals, "assists": None})
    players.sort(key=lambda p: (-p["goals"], p["name"]))
    players = players[:10]
    SCORER_CACHE[lid] = (time.time(), players)
    return players


def get_top_scorers(lid):
    players = None
    try:
        players = scorers_from_stats(lid)
    except Exception as err:
        print("scorers stats failed: " + str(err))
    if players:
        return players
    try:
        return scorers_from_matches(lid)
    except Exception as err:
        print("scorers from matches failed: " + str(err))
        return None


def build_scorers_text(lid):
    title = esc(LEAGUE_NAMES.get(lid, lid))
    players = get_top_scorers(lid)
    if not players:
        return "🏅 <b>Top Scorers - " + title + "</b>\n\nTop scorers are not available right now.\n\n🔄 Updated " + stamp()
    text = "🏅 <b>Top Scorers - " + title + "</b>\n\n"
    for i, p in enumerate(players, 1):
        line = str(i) + ". " + esc(p["name"])
        if p["team"]:
            line += " (" + esc(p["team"]) + ")"
        line += " - " + str(p["goals"]) + " ⚽"
        if p["assists"] is not None:
            line += ", " + str(p["assists"]) + " assists"
        text += line + "\n"
    text += "\n🔄 Updated " + stamp()
    return text


def scorers_kb(lid):
    return {"inline_keyboard": [
        [{"text": "🔄 Refresh", "callback_data": "rts:" + lid}],
        [
            {"text": "📊 Table", "callback_data": "tbl:" + lid},
            {"text": "⚽ Matches", "callback_data": "mat:" + lid}
        ],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}


# ---------- settings: time zone and morning digest ----------


def send_settings(chat_id, prefix=""):
    tz, hour, last = db_get_setting(chat_id)
    if hour >= 0:
        digest = "ON at " + str(hour).zfill(2) + ":00 your time"
    else:
        digest = "OFF"
    text = prefix + "⚙️ <b>Settings</b>\n\n🌍 Time zone: " + tz_label(tz) + "\n☀️ Morning digest: " + digest
    kb = {"inline_keyboard": [
        [{"text": "🌍 Time zone", "callback_data": "tzm"}],
        [{"text": "☀️ Morning digest", "callback_data": "dgm"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}
    send_text(chat_id, text, kb)


def send_tz_menu(chat_id):
    rows = []
    pair = []
    for minutes, label in TZ_OPTIONS:
        pair.append({"text": label, "callback_data": "tz:" + str(minutes)})
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([{"text": "⬅️ Back", "callback_data": "set"}])
    text = "🌍 <b>Pick your time zone</b>\n\nTables, match times and reminders will use it.\nIt is a fixed offset, so change it again when your clocks change."
    send_text(chat_id, text, {"inline_keyboard": rows})


def send_digest_menu(chat_id):
    rows = []
    pair = []
    for hour in DIGEST_HOURS:
        pair.append({"text": str(hour).zfill(2) + ":00", "callback_data": "dg:" + str(hour)})
        if len(pair) == 3:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([{"text": "🔕 Turn off", "callback_data": "dg:off"}])
    rows.append([{"text": "⬅️ Back", "callback_data": "set"}])
    text = "☀️ <b>Morning digest</b>\n\nEvery morning you get today's matches in one message (only your teams if you use My Team).\nPick the time, in your own time zone."
    send_text(chat_id, text, {"inline_keyboard": rows})


def digest_payload(fav_ids, results_by_lid):
    groups = []
    count = 0
    for lid in LEAGUE_IDS:
        rows = rows_from_data(lid, results_by_lid.get(lid), False)
        if fav_ids:
            rows = [r for r in rows if r["hid"] in fav_ids or r["aid"] in fav_ids]
        if not rows:
            continue
        if count + len(rows) > 150:
            rows = rows[:150 - count]
        if not rows:
            break
        count += len(rows)
        groups.append((LEAGUE_TITLES.get(lid, lid), rows))
    if not groups:
        return None
    return render_matches_image("Today's Matches", groups)


def run_digests():
    now = datetime.now(timezone.utc)
    due = []
    for chat_id, tz, hour, last in db_digest_users():
        local = now + timedelta(minutes=tz)
        day = local.strftime("%Y-%m-%d")
        if local.hour == hour and last != day:
            due.append((chat_id, tz, day))
    if not due:
        return
    results = get_scoreboards(LEAGUE_IDS)
    if all(r is None for r in results):
        return
    results_by_lid = dict(zip(LEAGUE_IDS, results))
    favs = db_all_favs()
    cache = {}
    kb = {"inline_keyboard": [[
        {"text": "🔴 Live Now", "callback_data": "live"},
        {"text": "⭐ My Team", "callback_data": "my"}
    ]]}
    for chat_id, tz, day in due:
        fav_ids = favs.get(chat_id) or set()
        set_current_tz(tz)
        key = (tz, tuple(sorted(fav_ids)))
        if key not in cache:
            try:
                cache[key] = digest_payload(fav_ids, results_by_lid)
            except Exception as err:
                print("digest image failed: " + str(err))
                cache[key] = None
        png = cache[key]
        rate_wait()
        if png is None:
            if fav_ids:
                text = "☀️ <b>Good morning!</b>\n\nNo matches today for your teams."
            else:
                text = "☀️ <b>Good morning!</b>\n\nNo matches today in the top leagues."
            res = send_text(chat_id, text, kb)
        else:
            res = send_photo(chat_id, png, kb, "☀️ <b>Good morning!</b> Here are today's matches.")
        handle_send_failure(chat_id, res)
        db_mark_digest(chat_id, day)
    set_current_tz(DEFAULT_TZ)


def digest_loop():
    print("Morning digest loop started")
    while True:
        try:
            run_digests()
        except Exception as err:
            print("Digest error: " + str(err))
        time.sleep(30)


# ---------- admin tools ----------


def probe_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
        return "OK " + str(len(body) // 1024) + " KB"
    except Exception as err:
        return "FAIL " + str(err)


def build_probe_text():
    today = today_str()
    now = datetime.now(timezone.utc)
    rng = (now - timedelta(days=20)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")
    tests = [
        ("scoreboard today", ESPN + "eng.1/scoreboard?dates=" + today),
        ("scoreboard backup host", ESPN_ALT + "eng.1/scoreboard?dates=" + today),
        ("scoreboard date range", ESPN + "eng.1/scoreboard?dates=" + rng + "&limit=400"),
        ("standings", ESPN_V2 + "eng.1/standings"),
        ("team schedule", ESPN + "eng.1/teams/359/schedule"),
        ("scorers stats", "https://site.web.api.espn.com/apis/common/v3/sports/soccer/eng.1/statistics/byathlete?region=us&lang=en&contentorigin=espn&isqualified=true&page=1&limit=5&sort=offensive.totalGoals%3Adesc")
    ]
    results = list(fetch_pool.map(lambda t: probe_url(t[1]), tests))
    lines = ["🔬 <b>ESPN probe</b>"]
    for t, r in zip(tests, results):
        if r.startswith("OK"):
            mark = "✅"
        else:
            mark = "❌"
        lines.append(mark + " " + t[0] + ": " + esc(r))
    return "\n".join(lines)


def is_admin(chat_id):
    return str(chat_id) in ADMIN_IDS


def build_admin_stats():
    now = datetime.now(timezone.utc)
    c1 = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    c7 = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    total = db_exec("SELECT COUNT(*) FROM subs")[0][0]
    active1 = db_exec("SELECT COUNT(*) FROM settings WHERE last_seen >= ?", (c1,))[0][0]
    active7 = db_exec("SELECT COUNT(*) FROM settings WHERE last_seen >= ?", (c7,))[0][0]
    digest = db_exec("SELECT COUNT(*) FROM settings WHERE digest_hour >= 0")[0][0]
    zones = db_exec("SELECT tz, COUNT(*) FROM settings GROUP BY tz ORDER BY COUNT(*) DESC LIMIT 3")
    zone_text = ", ".join([tz_label(z[0]) + ": " + str(z[1]) for z in zones]) or "none"
    minutes = int((time.time() - START_TIME) // 60)
    lines = [
        "📊 <b>Bot stats</b>",
        "Users with alerts on: " + str(total),
        "Active last 24 hours: " + str(active1),
        "Active last 7 days: " + str(active7),
        "Using My Team: " + str(len(db_all_favs())),
        "Morning digest on: " + str(digest),
        "Top time zones: " + esc(zone_text),
        "Alerts sent since start: " + str(ALERT_COUNT["sent"]),
        "Running for: " + str(minutes) + " min (users reset on each Render redeploy)"
    ]
    return "\n".join(lines)


def admin_broadcast(admin_chat, body):
    subs = db_get_subs()
    send_text(admin_chat, "📣 Sending to " + str(len(subs)) + " chats...")
    jobs = []
    for chat_id in subs:
        jobs.append(SEND_POOL.submit(send_safe, chat_id, esc(body), None))
    ok = 0
    for fut in jobs:
        try:
            if fut.result(timeout=600).get("ok"):
                ok += 1
        except Exception as err:
            print("broadcast job error: " + str(err))
    send_text(admin_chat, "✅ Delivered to " + str(ok) + " of " + str(len(subs)) + " chats.")


def send_reminder(info, play):
    groups = {}
    for chat_id in recipients(info):
        tz = user_tz(chat_id)
        if tz not in groups:
            groups[tz] = []
        groups[tz].append(chat_id)
    kb = alert_kb(info)
    jobs = []
    for tz, chats in groups.items():
        set_current_tz(tz)
        text = build_goal_text(play, info, 0, 0)
        for chat_id in chats:
            jobs.append(SEND_POOL.submit(send_safe, chat_id, text, kb))
    set_current_tz(DEFAULT_TZ)
    for fut in jobs:
        try:
            fut.result(timeout=180)
        except Exception as err:
            print("reminder job error: " + str(err))
    ALERT_COUNT["sent"] += 1
    ALERT_COUNT["last"] = time.time()
    print("REMINDER PUSHED to " + str(len(jobs)) + " chats for " + info["hname"] + " vs " + info["aname"])


def local_date_title(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        dt = dt + timedelta(minutes=current_tz())
        return dt.strftime("%a %d %b")
    except Exception:
        return "Date"


def date_groups(rows):
    groups = []
    last = None
    for r in rows:
        title = local_date_title(r["date"])
        if title != last:
            groups.append((title, []))
            last = title
        groups[-1][1].append(r)
    return groups


def range_rows(lid, start_days, end_days):
    now = datetime.now(timezone.utc)
    start = (now + timedelta(days=start_days)).strftime("%Y%m%d")
    end = (now + timedelta(days=end_days)).strftime("%Y%m%d")
    key = (lid, start, end)
    cached = RANGE_CACHE.get(key)
    if cached and (time.time() - cached[0]) < 120:
        return cached[1]
    rng = start + "-" + end
    data = None
    for base in (ESPN, ESPN_ALT):
        data = fetch_json(base + lid + "/scoreboard?dates=" + rng + "&limit=400")
        if data is not None:
            break
    if data is None:
        return None
    rows = rows_from_data(lid, data, False)
    RANGE_CACHE[key] = (time.time(), rows)
    return rows


def render_or_text(title, groups):
    try:
        return render_matches_image(title, groups), ""
    except Exception as err:
        print("image failed: " + str(err))
        return None, text_from_groups(title, groups)


def build_fx_view(lid, results):
    if results:
        rows = range_rows(lid, -21, 0)
        label = "Results"
    else:
        rows = range_rows(lid, 0, 21)
        label = "Fixtures"
    league = LEAGUE_TITLES.get(lid, lid)
    if rows is None:
        return None, "Could not load " + label.lower() + " right now. Tap Refresh in a few seconds.", []
    if results:
        rows = [r for r in rows if r["state"] == "post"]
        rows.sort(key=lambda r: r["date"], reverse=True)
    else:
        rows = [r for r in rows if r["state"] != "post"]
        rows.sort(key=lambda r: r["date"])
    rows = rows[:40]
    if not rows:
        return None, "⚽ <b>" + esc(league) + " - " + label + "</b>\n\nNo " + label.lower() + " found.\n\n🔄 Updated " + stamp(), []
    png, text = render_or_text(league + " - " + label, date_groups(rows))
    return png, text, rows


def send_fx_view(chat_id, msg, lid, results, refresh):
    png, text, rows = build_fx_view(lid, results)
    if results:
        mode = "rs"
    else:
        mode = "fx"
    kb = fx_kb(lid, mode, rows)
    if png is None:
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)
    elif refresh:
        show_photo(chat_id, msg, png, kb)
    else:
        send_photo(chat_id, png, kb)


def build_range_view(week, chat_id):
    if week:
        start_days = 0
        end_days = 6
        title = "This Week"
    else:
        start_days = 1
        end_days = 1
        title = "Tomorrow"
    results = list(fetch_pool.map(lambda lid: range_rows(lid, start_days, end_days), LEAGUE_IDS))
    if all(r is None for r in results):
        return None, "Could not load fixtures right now. Tap Refresh in a few seconds."
    favs = db_all_favs().get(str(chat_id)) or set()
    entries = []
    for lid, rows in zip(LEAGUE_IDS, results):
        if not rows:
            continue
        for r in rows:
            if r["state"] == "post":
                continue
            if favs and not (r["hid"] in favs or r["aid"] in favs):
                continue
            entries.append((r["date"], lid, r))
    entries.sort(key=lambda e: e[0])
    groups = []
    index = {}
    for date_iso, lid, r in entries[:150]:
        key = (local_date_title(date_iso), lid)
        if key not in index:
            index[key] = []
            groups.append((key[0] + " - " + LEAGUE_TITLES.get(lid, lid), index[key]))
        index[key].append(r)
    if favs:
        title += " - My Team"
    if not groups:
        return None, "📆 <b>" + esc(title) + "</b>\n\nNo matches found.\n\n🔄 Updated " + stamp()
    return render_or_text(title, groups)


def send_range_view(chat_id, msg, week, refresh):
    png, text = build_range_view(week, chat_id)
    kb = range_kb(week)
    if png is None:
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)
    elif refresh:
        show_photo(chat_id, msg, png, kb)
    else:
        send_photo(chat_id, png, kb)


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
        icon_h = team_tag(info["hname"], info["hid"])
        icon_a = team_tag(info["aname"], info["aid"])
        if info["state"] == "pre":
            text = icon_h + esc(info["hname"]) + " vs " + icon_a + esc(info["aname"]) + "\nKickoff " + esc(kickoff_hm(info["date"])) + " WAT"
        else:
            text = icon_h + esc(info["hname"]) + " " + str(info["hs"]) + " - " + str(info["a_s"]) + " " + icon_a + esc(info["aname"]) + "\n"
            text += esc(info["short"]) + "\n\n"
            lines = goal_lines(info, data)
            if lines:
                text += "\n".join(lines)
            else:
                text += "No goals yet."
        text += "\n\n🔄 Updated " + stamp()
        return None, text[:3800]
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


def lineup_blocks(data):
    blocks = []
    for r in (data.get("rosters") or [])[:2]:
        starters = []
        bench = []
        for x in r.get("roster") or []:
            ath = x.get("athlete") or {}
            name = ath.get("displayName") or ath.get("shortName") or ""
            if not name:
                continue
            jersey = x.get("jersey") or ath.get("jersey") or ""
            pos = (x.get("position") or {}).get("abbreviation", "")
            if x.get("starter"):
                starters.append({"jersey": jersey, "name": name, "pos": pos, "off": bool(x.get("subbedOut"))})
            else:
                bench.append({"name": name, "on": bool(x.get("subbedIn"))})
        formation = r.get("formation")
        if isinstance(formation, dict):
            formation = formation.get("displayValue") or formation.get("name")
        blocks.append({
            "team": (r.get("team") or {}).get("displayName", ""),
            "formation": formation or "",
            "starters": starters,
            "bench": bench
        })
    return blocks


def format_lineups(blocks):
    text = ""
    for b in blocks:
        if not b["starters"]:
            continue
        text += "<b>" + esc(b["team"]) + "</b>"
        if b["formation"]:
            text += " (" + esc(b["formation"]) + ")"
        text += "\n"
        for p in b["starters"]:
            line = esc(p["jersey"]) + " " + esc(p["name"])
            if p["pos"]:
                line += " (" + esc(p["pos"]) + ")"
            if p["off"]:
                line += " 🔻"
            text += line + "\n"
        if b["bench"]:
            names = []
            for sub in b["bench"][:12]:
                n = esc(sub["name"])
                if sub["on"]:
                    n = "🔺" + n
                names.append(n)
            text += "Bench: " + ", ".join(names) + "\n"
        text += "\n"
    return text


def substitution_lines(data):
    items = data.get("commentary") or []
    if not items:
        items = data.get("keyEvents") or []
    lines = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        play = item.get("play")
        if not isinstance(play, dict):
            play = item
        ptype = play.get("type") or item.get("type") or {}
        if not isinstance(ptype, dict):
            continue
        kind_text = (str(ptype.get("text", "")) + " " + str(ptype.get("type", ""))).lower()
        if "substitution" not in kind_text:
            continue
        body = str(play.get("text") or item.get("text") or play.get("shortText") or "")
        if body.lower().startswith("substitution") and ". " in body:
            body = body.split(". ", 1)[1]
        minute = play_minute(play, item)
        tid, tname = play_team(play, item)
        line = "🔄 " + esc(minute) + " " + esc(body.strip())
        if tname:
            line += " (" + esc(tname) + ")"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return lines[-14:]


def build_lineups_text(lid, eid):
    data = fetch_json(ESPN + lid + "/summary?event=" + eid)
    if not data:
        return "Lineups are not available yet.\n\n🔄 Updated " + stamp()
    body = format_lineups(lineup_blocks(data))
    if not body:
        return "Lineups are not available yet.\n\n🔄 Updated " + stamp()
    text = "👥 <b>Lineups</b>\n\n" + body
    subs = substitution_lines(data)
    if subs:
        text += "<b>Substitutions</b>\n" + "\n".join(subs) + "\n\n"
    text += "🔄 Updated " + stamp()
    return text[:3900]


def check_lineup_alert(info):
    eid = info["id"]
    now = time.time()
    if now - LAST_LINEUP.get(eid, 0) < LINEUP_CHECK_SECONDS:
        return
    LAST_LINEUP[eid] = now
    data = fetch_json(ESPN + info["lid"] + "/summary?event=" + eid)
    if not data:
        return
    blocks = lineup_blocks(data)
    ready = [b for b in blocks if len(b["starters"]) >= 9]
    if len(ready) < 2:
        return
    db_mark_seen(eid, "mark:LU")
    text = match_head(info) + "\n📋 Lineups are out!\n\n" + format_lineups(blocks)
    sent = broadcast(text[:3900], alert_kb(info), info)
    ALERT_COUNT["sent"] += 1
    ALERT_COUNT["last"] = time.time()
    print("LINEUP ALERT to " + str(len(sent)) + " chats: " + info["hname"] + " vs " + info["aname"])


def get_team_index():
    now = time.time()
    if TEAM_INDEX["teams"] and (now - TEAM_INDEX["time"]) < 21600:
        return TEAM_INDEX["teams"]
    lids = [lid for lid in LEAGUE_IDS if lid not in CUP_IDS]
    results = list(fetch_pool.map(get_standings, lids))
    teams = {}
    for lid, entries in zip(lids, results):
        for e in entries:
            team = e.get("team") or {}
            tid = str(team.get("id", ""))
            if not tid:
                continue
            if tid not in teams:
                teams[tid] = {
                    "id": tid,
                    "name": team.get("displayName") or team.get("name") or "?",
                    "short": team.get("shortDisplayName") or "",
                    "abbr": team.get("abbreviation") or "",
                    "lids": []
                }
            if lid not in teams[tid]["lids"]:
                teams[tid]["lids"].append(lid)
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
            rows.append([
                {"text": "📄 " + name, "callback_data": "tp:" + tid},
                {"text": "❌", "callback_data": "uf:" + tid}
            ])
        text += "\nTap a team for its next match and form. Type another club name to add it (up to " + str(MAX_FAVS) + ")."
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

    elif data.startswith("st:") or data.startswith("rst:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        refresh = parts[0] == "rst"
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading stats...")
        text = build_stats_text(parts[1], parts[2])
        kb = stats_screen_kb(parts[1], parts[2])
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)

    elif data.startswith("ln:") or data.startswith("rln:"):
        parts = data.split(":", 2)
        if len(parts) != 3 or parts[1] not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        refresh = parts[0] == "rln"
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading lineups...")
        text = build_lineups_text(parts[1], parts[2])
        kb = lineups_screen_kb(parts[1], parts[2])
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)

    elif data.startswith("fx:") or data.startswith("rfx:") or data.startswith("rs:") or data.startswith("rrs:"):
        parts = data.split(":", 1)
        lid = parts[1]
        if lid not in LEAGUE_IDS:
            answer_cb(cb_id)
            return
        results = parts[0] in ("rs", "rrs")
        refresh = parts[0] in ("rfx", "rrs")
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        elif results:
            answer_cb(cb_id, "Loading results...")
        else:
            answer_cb(cb_id, "Loading fixtures...")
        send_fx_view(chat_id, msg, lid, results, refresh)

    elif data in ("tom", "rtom", "week", "rweek"):
        refresh = data.startswith("r")
        week = data in ("week", "rweek")
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading fixtures...")
        send_range_view(chat_id, msg, week, refresh)

    elif data.startswith("tf:") or data.startswith("rtf:") or data.startswith("tr:") or data.startswith("rtr:"):
        parts = data.split(":", 1)
        tid = parts[1]
        results = parts[0] in ("tr", "rtr")
        refresh = parts[0] in ("rtf", "rtr")
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading...")
        text = build_team_list(tid, results)
        kb = team_list_kb(tid, results)
        if refresh:
            show_text(chat_id, msg, text, kb)
        else:
            send_text(chat_id, text, kb)

    elif data == "set":
        answer_cb(cb_id)
        send_settings(chat_id)

    elif data == "tzm":
        answer_cb(cb_id)
        send_tz_menu(chat_id)

    elif data.startswith("tz:"):
        try:
            minutes = int(data[3:])
        except Exception:
            answer_cb(cb_id)
            return
        valid = [m for m, label in TZ_OPTIONS]
        if minutes not in valid:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Saved")
        db_set_tz(chat_id, minutes)
        set_current_tz(minutes)
        send_settings(chat_id, "✅ Time zone saved.\n\n")

    elif data == "dgm":
        answer_cb(cb_id)
        send_digest_menu(chat_id)

    elif data.startswith("dg:"):
        value = data[3:]
        if value == "off":
            answer_cb(cb_id, "Digest off")
            db_set_digest(chat_id, -1)
            send_settings(chat_id, "✅ Morning digest is off.\n\n")
            return
        try:
            hour = int(value)
        except Exception:
            answer_cb(cb_id)
            return
        if hour not in DIGEST_HOURS:
            answer_cb(cb_id)
            return
        answer_cb(cb_id, "Saved")
        db_set_digest(chat_id, hour)
        send_settings(chat_id, "✅ Morning digest saved.\n\n")

    elif data.startswith("tp:") or data.startswith("rtp:"):
        refresh = data.startswith("rtp:")
        tid = data.split(":", 1)[1]
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading team...")
        text = build_team_page(tid)
        if refresh:
            show_text(chat_id, msg, text, team_kb(tid))
        else:
            send_text(chat_id, text, team_kb(tid))

    elif data.startswith("ts:") or data.startswith("rts:"):
        refresh = data.startswith("rts:")
        lid = data.split(":", 1)[1]
        if lid not in LEAGUE_IDS or lid in CUP_IDS:
            answer_cb(cb_id)
            return
        if refresh:
            answer_cb(cb_id, "Refreshing...")
        else:
            answer_cb(cb_id, "Loading top scorers...")
        text = build_scorers_text(lid)
        if refresh:
            show_text(chat_id, msg, text, scorers_kb(lid))
        else:
            send_text(chat_id, text, scorers_kb(lid))

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
        text += "🔄 Updated " + stamp()
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
    elif low.startswith("/settings"):
        send_settings(chat_id)
    elif low.startswith("/timezone"):
        send_tz_menu(chat_id)
    elif low.startswith("/digest"):
        send_digest_menu(chat_id)
    elif low.startswith("/broadcast"):
        if not is_admin(chat_id):
            send_text(chat_id, "This command is only for the bot admin.")
        else:
            body = text[len("/broadcast"):].strip()
            if not body:
                send_text(chat_id, "Usage: /broadcast your message here")
            else:
                admin_broadcast(chat_id, body)
    elif low.startswith("/probe"):
        if is_admin(chat_id):
            send_text(chat_id, "🔬 Testing ESPN, please wait...")
            send_text(chat_id, build_probe_text())
        else:
            send_text(chat_id, "This command is only for the bot admin.")
    elif low.startswith("/status"):
        send_text(chat_id, build_status_text(chat_id))
    elif low.startswith("/stats"):
        if is_admin(chat_id):
            send_text(chat_id, build_admin_stats())
        else:
            send_text(chat_id, "This command is only for the bot admin.\nIf you own the bot, set ADMIN_CHAT_IDS on Render to your chat ID (send /status to see it).")
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
        chat_id = None
        if "callback_query" in up:
            m = up["callback_query"].get("message") or {}
            chat_id = (m.get("chat") or {}).get("id")
        elif "message" in up:
            chat_id = (up["message"].get("chat") or {}).get("id")
        if chat_id is not None:
            set_current_tz(user_tz(chat_id))
            db_touch(chat_id)
        else:
            set_current_tz(DEFAULT_TZ)
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


def handle_send_failure(chat_id, res):
    if res.get("ok"):
        return
    if res.get("error_code") == 403:
        db_remove_sub(chat_id)
    else:
        print("send fail " + str(chat_id) + " : " + str(res.get("description")))


def rate_wait():
    with RATE_LOCK:
        now = time.time()
        wait = RATE_STATE["next"] - now
        if wait < 0:
            wait = 0
        RATE_STATE["next"] = now + wait + SEND_GAP
    if wait > 0:
        time.sleep(wait)


def strip_tg_emoji(text):
    while "<tg-emoji" in text:
        a = text.find("<tg-emoji")
        b = text.find(">", a)
        c = text.find("</tg-emoji>", b)
        if b < 0 or c < 0:
            break
        text = text[:a] + text[b + 1:c] + text[c + len("</tg-emoji>"):]
    return text


def is_emoji_error(res):
    desc = str(res.get("description", "")).lower()
    return "emoji" in desc or "entit" in desc


def retry_after(res):
    try:
        return int((res.get("parameters") or {}).get("retry_after", 1))
    except Exception:
        return 1


def send_safe(chat_id, text, kb):
    rate_wait()
    res = send_text(chat_id, text, kb)
    if not res.get("ok"):
        if res.get("error_code") == 429:
            time.sleep(min(retry_after(res), 20) + 0.5)
            rate_wait()
            res = send_text(chat_id, text, kb)
        elif is_emoji_error(res):
            rate_wait()
            res = send_text(chat_id, strip_tg_emoji(text), kb)
    handle_send_failure(chat_id, res)
    return res


def edit_safe(chat_id, message_id, text, kb):
    rate_wait()
    res = edit_text(chat_id, message_id, text, kb)
    if not res.get("ok") and not is_not_modified(res):
        if res.get("error_code") == 429:
            time.sleep(min(retry_after(res), 20) + 0.5)
            rate_wait()
            res = edit_text(chat_id, message_id, text, kb)
        elif is_emoji_error(res):
            rate_wait()
            res = edit_text(chat_id, message_id, strip_tg_emoji(text), kb)
    return res


def broadcast(text, kb=None, info=None):
    jobs = []
    for chat_id in recipients(info):
        jobs.append((chat_id, SEND_POOL.submit(send_safe, chat_id, text, kb)))
    sent = {}
    for chat_id, fut in jobs:
        try:
            res = fut.result(timeout=180)
        except Exception as err:
            print("send job error: " + str(err))
            continue
        if res.get("ok"):
            try:
                sent[chat_id] = res["result"]["message_id"]
            except Exception:
                pass
    return sent


def edit_alert(entry, info, play):
    text = build_goal_text(play, info, entry["hs"], entry["a_s"])
    kb = alert_kb(info)
    jobs = []
    for chat_id, message_id in entry["msgs"].items():
        jobs.append(SEND_POOL.submit(edit_safe, chat_id, message_id, text, kb))
    for fut in jobs:
        try:
            fut.result(timeout=180)
        except Exception as err:
            print("edit job error: " + str(err))


def send_alert(info, play, hs, a_s):
    text = build_goal_text(play, info, hs, a_s)
    sent = broadcast(text, alert_kb(info), info)
    ALERT_COUNT["sent"] += 1
    ALERT_COUNT["last"] = time.time()
    print("ALERT PUSHED to " + str(len(sent)) + " chats: " + text)
    return sent


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
    if play.get("redCard"):
        return "red_card"
    if "red card" in type_l and "yellow" not in type_l:
        return "red_card"

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


def play_key(kind, minute, player):
    if kind == "red_card":
        return "red_card|" + (str(player).lower() or str(minute))
    return kind + "|" + str(minute) + "|" + str(player)


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
            "key": play_key(kind, minute, player),
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


def team_tag(name, tid=""):
    custom = CLUB_EMOJI.get(str(tid).lower()) or CLUB_EMOJI.get(str(name).lower())
    if custom:
        return "<tg-emoji emoji-id=\"" + custom + "\">⚽</tg-emoji> "
    low = str(name).lower()
    emoji = TEAM_EMOJI.get(low)
    if emoji:
        return emoji + " "
    for key in TEAM_EMOJI:
        if key in low:
            return TEAM_EMOJI[key] + " "
    return ""


def match_head(info):
    hname = info["hname"]
    aname = info["aname"]
    return team_tag(hname, info.get("hid", "")) + esc(hname) + " - " + team_tag(aname, info.get("aid", "")) + esc(aname) + ":"


def build_goal_text(play, info, hs, a_s):
    kind = play["kind"]
    hname = info["hname"]
    aname = info["aname"]
    head = match_head(info)
    score_now = str(hs) + " - " + str(a_s)

    if kind == "half_time":
        return head + "\nHalf Time. The score is " + score_now
    if kind == "full_time":
        return head + "\nFull Time. The score is " + score_now
    if kind == "kickoff":
        return head + "\n🟢 Kick off! The match has started."
    if kind == "reminder":
        return head + "\n⏰ Kickoff in " + str(play.get("mins", "")) + " minutes (" + esc(play.get("time") or kickoff_wat(info.get("date", ""))) + ")"

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

    if kind == "red_card":
        if who:
            return head + "\n" + prefix + "🟥 Red card " + who + " is sent off!"
        return head + "\n" + prefix + "🟥 Red card!"

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


def alert_missed_penalties(info, plays):
    eid = info["id"]
    for p in plays:
        if p["kind"] != "penalty_missed" or p["src"] != 1:
            continue
        if db_is_seen(eid, p["key"]):
            continue
        db_mark_seen(eid, p["key"])
        send_alert(info, p, info["hs"], info["a_s"])


def alert_red_cards(info, plays):
    eid = info["id"]
    for p in plays:
        if p["kind"] != "red_card":
            continue
        if db_is_seen(eid, p["key"]):
            continue
        db_mark_seen(eid, p["key"])
        when = minute_value(p["minute"])
        side = p.get("side", "")
        known = RED_LOG.get(eid) or []
        duplicate = False
        for old_when, old_side in known:
            if old_side == side and abs(old_when - when) <= 1.5:
                duplicate = True
        if duplicate:
            continue
        known.append((when, side))
        RED_LOG[eid] = known
        send_alert(info, p, info["hs"], info["a_s"])


def minute_value(text):
    nums = []
    cur = ""
    for ch in str(text):
        if ch.isdigit():
            cur += ch
        elif cur:
            nums.append(int(cur))
            cur = ""
    if cur:
        nums.append(int(cur))
    if not nums:
        return 0.0
    value = float(nums[0])
    if len(nums) > 1:
        value += nums[1] / 100.0
    return value


def chrono_goals(plays):
    goals = [p for p in plays if p["kind"] in GOAL_KINDS]
    goals.sort(key=lambda p: (minute_value(p["minute"]), p["order"]))
    return goals


def summary_goal_list(plays):
    for src in (1, 2):
        chosen = [p for p in plays if p["kind"] in GOAL_KINDS and p["src"] == src]
        if chosen:
            return chrono_goals(chosen)
    return []


def trusted_goal(play, side, info):
    if play is None:
        return False
    if play["kind"] != "own_goal":
        ps = scoring_side(play)
        if ps and ps != side:
            return False
    clock = minute_value(info["minute"])
    if clock > 0 and minute_value(play["minute"]) > clock + 3:
        return False
    return True


def new_goal(info, side, ch, ca):
    eid = info["id"]
    total = ch + ca
    if side == "h":
        team = info["hname"]
    else:
        team = info["aname"]
    goals = chrono_goals(detail_plays(info))
    play = None
    if len(goals) >= total:
        candidate = goals[total - 1]
        if trusted_goal(candidate, side, info):
            play = candidate
    if play is not None:
        announced = play
        key = play["key"]
    else:
        announced = {"kind": "goal", "minute": info["minute"], "team_name": team, "player": "", "side": side}
        key = ""
    sent = send_alert(info, announced, ch, ca)
    if eid not in GOAL_LOG:
        GOAL_LOG[eid] = {}
    GOAL_LOG[eid][total] = {
        "side": side, "hs": ch, "a_s": ca, "msgs": sent, "key": key,
        "play": announced, "cand": "", "cand_n": 0, "created": time.time()
    }


def refresh_goal_log(info, cycle_plays):
    eid = info["id"]
    log = GOAL_LOG.get(eid)
    if not log:
        return
    now = time.time()
    goals = chrono_goals(detail_plays(info))
    top = max(log.keys())
    waiting = False
    for entry in log.values():
        if not entry["key"]:
            waiting = True
    if len(goals) < top and waiting and (now - LAST_SUMMARY.get(eid, 0)) >= 10:
        LAST_SUMMARY[eid] = now
        sp = summary_plays(info, cycle_plays)
        if sp:
            alt = summary_goal_list(sp)
            if len(alt) > len(goals):
                goals = alt
    for total in sorted(log.keys()):
        entry = log[total]
        if len(goals) < total:
            continue
        cur = goals[total - 1]
        if not trusted_goal(cur, entry["side"], info):
            continue
        if not entry["key"]:
            entry["key"] = cur["key"]
            entry["play"] = cur
            edit_alert(entry, info, cur)
        elif cur["key"] != entry["key"]:
            if entry["cand"] == cur["key"]:
                entry["cand_n"] += 1
            else:
                entry["cand"] = cur["key"]
                entry["cand_n"] = 1
            if entry["cand_n"] >= 2:
                entry["key"] = cur["key"]
                entry["play"] = cur
                entry["cand"] = ""
                entry["cand_n"] = 0
                edit_alert(entry, info, cur)
        else:
            entry["cand"] = ""
            entry["cand_n"] = 0


def handle_var_cancel(info, side):
    eid = info["id"]
    log = GOAL_LOG.get(eid) or {}
    victim = None
    for total in sorted(log.keys(), reverse=True):
        if log[total]["side"] == side:
            victim = total
            break
    player = ""
    if victim is not None:
        player = log[victim]["play"].get("player", "")
        del log[victim]
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
        DEC_CANDIDATE.pop(eid, None)
        return
    if hs < old_h or a_s < old_a:
        cand = DEC_CANDIDATE.get(eid)
        if cand is None or cand[0] != hs or cand[1] != a_s:
            DEC_CANDIDATE[eid] = (hs, a_s)
            return
        del DEC_CANDIDATE[eid]
    db_set_score(eid, hs, a_s)
    cur_h = old_h
    cur_a = old_a
    while cur_h < hs:
        cur_h += 1
        new_goal(info, "h", cur_h, cur_a)
    while cur_a < a_s:
        cur_a += 1
        new_goal(info, "a", cur_h, cur_a)
    if hs < old_h:
        handle_var_cancel(info, "h")
    if a_s < old_a:
        handle_var_cancel(info, "a")


def handle_event(info, cycle_plays):
    eid = info["id"]
    state = info["state"]
    row = db_get_score(eid)

    if state == "pre":
        if row is None:
            db_set_score(eid, 0, 0)
        mins = minutes_to_kickoff(info["date"])
        if mins is not None and 0 <= mins <= REMINDER_MINUTES and not db_is_seen(eid, "mark:REM"):
            db_mark_seen(eid, "mark:REM")
            play = {"kind": "reminder", "mins": int(round(mins))}
            send_reminder(info, play)
        if mins is not None and -5 <= mins <= LINEUP_WINDOW and not db_is_seen(eid, "mark:LU"):
            check_lineup_alert(info)
        return

    if state == "post":
        if row is None:
            return
        process_score(info, row)
        refresh_goal_log(info, cycle_plays)
        alert_red_cards(info, detail_plays(info))
        name = info["status_name"]
        if ("FULL_TIME" in name or "FINAL" in name) and not db_is_seen(eid, "mark:FT"):
            db_mark_seen(eid, "mark:FT")
            FT_TIME[eid] = time.time()
            send_alert(info, {"kind": "full_time"}, info["hs"], info["a_s"])
        done = FT_TIME.get(eid)
        if done and (time.time() - done) > 900:
            GOAL_LOG.pop(eid, None)
            RED_LOG.pop(eid, None)
        return

    if state != "in":
        return

    if row is None:
        db_set_score(eid, info["hs"], info["a_s"])
        db_mark_seen(eid, "mark:KO")
        db_mark_seen(eid, "mark:LU")
        for p in detail_plays(info):
            db_mark_seen(eid, p["key"])
        plays = summary_plays(info, cycle_plays)
        if plays:
            for p in plays:
                db_mark_seen(eid, p["key"])
        return

    if not db_is_seen(eid, "mark:KO"):
        db_mark_seen(eid, "mark:KO")
        if minute_value(info["minute"]) <= 10:
            send_alert(info, {"kind": "kickoff"}, info["hs"], info["a_s"])

    process_score(info, row)
    refresh_goal_log(info, cycle_plays)
    alert_red_cards(info, detail_plays(info))

    if info["status_name"] == "STATUS_HALFTIME" and not db_is_seen(eid, "mark:HT"):
        db_mark_seen(eid, "mark:HT")
        send_alert(info, {"kind": "half_time"}, info["hs"], info["a_s"])

    now = time.time()
    if now - LAST_PEN.get(eid, 0) >= PEN_CHECK_SECONDS:
        LAST_PEN[eid] = now
        plays = summary_plays(info, cycle_plays)
        if plays:
            alert_missed_penalties(info, plays)
            alert_red_cards(info, plays)


def poll_league(lid, dates):
    events = []
    failed = False
    today = today_str()
    for ds in dates:
        is_today = (ds == today)
        data = fetch_scoreboard(lid, ds, is_today)
        if data is None:
            failed = True
            continue
        if is_today:
            SB_CACHE[lid] = (time.time(), data)
        for ev in data.get("events") or []:
            info = event_info(ev, lid)
            if info is not None:
                events.append(info)
    if failed and not events:
        return None
    return events


def next_interval(events):
    interval = EMPTY_POLL
    for info in events:
        state = info["state"]
        if state == "in":
            return LIVE_POLL
        if state == "pre":
            mins = minutes_to_kickoff(info["date"])
            if mins is None:
                interval = min(interval, IDLE_POLL)
            elif -10 < mins <= 3:
                return LIVE_POLL
            elif -180 < mins <= LINEUP_WINDOW:
                interval = min(interval, SOON_POLL)
            else:
                interval = min(interval, IDLE_POLL)
    return interval


def notify_admins(text):
    for chat_id in ADMIN_IDS:
        SEND_POOL.submit(send_safe, chat_id, text, None)


def check_health():
    failing = 0
    for lid, count in FAILS.items():
        if count >= 3:
            failing += 1
    now = time.time()
    if failing >= 6 and not ADMIN_STATE["espn_down"] and (now - ADMIN_STATE["last_espn_alert"]) > 600:
        ADMIN_STATE["espn_down"] = True
        ADMIN_STATE["last_espn_alert"] = now
        notify_admins("⚠️ <b>ESPN is failing</b> for " + str(failing) + " leagues.\nLast error: " + esc(LAST_ERRORS["espn"] or "unknown"))
    elif ADMIN_STATE["espn_down"] and failing < 6:
        ADMIN_STATE["espn_down"] = False
        notify_admins("✅ ESPN is working again.")


def check_cycle(cycle):
    now = time.time()
    dates = scoreboard_dates()
    due = []
    for lid in LEAGUE_IDS:
        if now >= LEAGUE_NEXT.get(lid, 0):
            due.append(lid)
    results = list(fetch_pool.map(lambda lid: poll_league(lid, dates), due))
    cycle_plays = {}
    seen_ids = set()
    fails = 0
    for lid, events in zip(due, results):
        if events is None:
            fails += 1
            FAILS[lid] = FAILS.get(lid, 0) + 1
            wait = 30 * FAILS[lid]
            if wait > 300:
                wait = 300
            LEAGUE_NEXT[lid] = now + wait
            continue
        FAILS[lid] = 0
        LEAGUE_NEXT[lid] = now + next_interval(events)
        live = 0
        for info in events:
            if info["state"] == "in":
                live += 1
        LEAGUE_STATS[lid] = (len(events), live)
        for info in events:
            if info["id"] in seen_ids:
                continue
            seen_ids.add(info["id"])
            try:
                handle_event(info, cycle_plays)
            except Exception as err:
                print("Event error: " + str(err))
    NOTIFIER["polled"] = len(due)
    NOTIFIER["fetch_fail"] = fails
    NOTIFIER["last_run"] = time.time()
    check_health()


def notifier_loop():
    print("Notifier started - fast adaptive polling")
    cycle = 0
    while True:
        cycle += 1
        started = time.time()
        try:
            check_cycle(cycle)
            NOTIFIER["error"] = ""
            ADMIN_STATE["err_count"] = 0
        except Exception as err:
            NOTIFIER["error"] = str(err)
            ADMIN_STATE["err_count"] += 1
            print("Notifier error: " + str(err))
            if ADMIN_STATE["err_count"] >= 3 and (time.time() - ADMIN_STATE["last_err_alert"]) > 900:
                ADMIN_STATE["last_err_alert"] = time.time()
                notify_admins("⚠️ <b>Notifier error</b> (3 in a row):\n" + esc(str(err)))
        wait = NOTIFY_TICK - (time.time() - started)
        if wait < 0.3:
            wait = 0.3
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
    notify_admins("🔄 <b>Bot restarted</b> at " + stamp() + ".")
    notifier = threading.Thread(target=notifier_loop, daemon=True)
    notifier.start()
    digest = threading.Thread(target=digest_loop, daemon=True)
    digest.start()
    poll_loop()


if __name__ == "__main__":
    main()
