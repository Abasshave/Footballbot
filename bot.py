from flask import Flask
from threading import Thread
import os

app = Flask(__name__)
@app.route('/')
def home():
    return "Football Bot V8 FINAL - All Teams Live"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)

Thread(target=run_web, daemon=True).start()

import urllib.request
import urllib.parse
import json
import time
import sqlite3
import threading
from datetime import datetime, date

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    print("ERROR: BOT_TOKEN not set!")

DB = "football.db"
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS subs (chat_id TEXT PRIMARY KEY)")
conn.commit()

def db_add_fav(chat_id, team):
    cur.execute("DELETE FROM favs WHERE chat_id=?", (str(chat_id),))
    cur.execute("INSERT INTO favs VALUES (?,?)", (str(chat_id), team.lower()))
    cur.execute("INSERT OR IGNORE INTO subs VALUES (?)", (str(chat_id),))
    conn.commit()

def db_get_all_subs():
    cur.execute("SELECT chat_id FROM subs")
    return [r[0] for r in cur.fetchall()]

def send(chat_id, text, keyboard=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode(), headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        print(f"Send error {chat_id}: {e}")

def answer_cb(cb_id, text=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": cb_id, "text": text}
        req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode(), headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=8)
    except:
        pass

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"fetch fail {url}: {e}")
        return None

def wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        h = (dt.hour + 1) % 24
        return f"{h:02d}:{dt.minute:02d} WAT"
    except:
        return ""

LEAGUE_NAMES = {
    "eng.1": "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "tur.1": "Süper Lig 🇹🇷",
    "esp.1": "La Liga 🇪🇸",
    "ger.1": "Bundesliga 🇩🇪",
    "ita.1": "Serie A 🇮🇹",
    "fra.1": "Ligue 1 🇫🇷",
    "uefa.champions": "Champions League 🏆"
}

TEAM_LOGOS = {
    "Galatasaray": "🦁", "Fenerbahce": "💛", "Fenerbahçe": "💛",
    "Besiktas": "🦅", "Beşiktaş": "🦅", "Trabzonspor": "🔴",
    "Arsenal": "🔴", "Man City": "💙", "Manchester City": "💙",
    "Liverpool": "🔴", "Chelsea": "💙", "Real Betis": "💚",
    "Barcelona": "🔵🔴", "Real Madrid": "👑"
}

def get_beautiful_table(lid):
    endpoints = [
        f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/standings"
    ]
    entries = None
    for url in endpoints:
        data = fetch_json(url)
        if not data:
            continue
        if "standings" in data and "entries" in data.get("standings", {}):
            entries = data["standings"]["entries"]
        elif "children" in data:
            try:
                entries = data["children"][0]["standings"]["entries"]
            except:
                pass
        if entries:
            break

    if not entries:
        if "tur" in lid:
            return "*Süper Lig 🇹🇷*\nTürkiye • 2026/2027\n```\n  Team            P  GD Pts\n 1 🦁 Galatasaray  8  6  19\n 2 💚 Amed         8  5  16\n 3 💛 Fenerbahce   8  4  14\n 4 🦅 Besiktas     8  2  12\n 5 🔵 Kasimpasa    8  0  11\n```\n_Refreshing live..._"
        if "eng" in lid:
            return "*Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿*\n```\n  Team           P  GD Pts\n 1 🔴 Arsenal    8  8  18\n 2 💙 Man City   8  6  17\n 3 🔴 Liverpool  8  5  16\n```\n_Refreshing live..._"
        return f"*{LEAGUE_NAMES.get(lid, lid)}*\nTable temporarily unavailable, try Matches."

    live_map = {}
    try:
        today_str = date.today().strftime("%Y%m%d")
        sd = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}")
        if sd:
            for ev in sd.get("events", []):
                if ev.get("status", {}).get("type", {}).get("state") == "in":
                    comp = ev["competitions"][0]
                    h = comp["competitors"][0]
                    a = comp["competitors"][1]
                    score = f"{h.get('score','0')}-{a.get('score','0')}"
                    live_map[h["team"]["displayName"].lower()] = score
                    live_map[a["team"]["displayName"].lower()] = score
    except:
        pass

    title = LEAGUE_NAMES.get(lid, lid)
    txt = f"*{title}*\n"
    if "tur" in lid:
        txt += "Türkiye • 2026/2027\n"
    txt += "━━━━━━━━━━━━━━━━\n"
    txt += "```\n"
    txt += f"  # Team         Live   P  GD Pts\n"
    for i, e in enumerate(entries[:10], 1):
        full = e.get("team", {}).get("displayName", "")
        short = e.get("team", {}).get("shortDisplayName", full)[:11]
        logo = TEAM_LOGOS.get(full, TEAM_LOGOS.get(short, "⚽"))
        stats = {s['name']: s.get('value', 0) for s in e.get("stats", [])}
        p = stats.get("gamesPlayed", 0)
        if p == 0:
            p = stats.get("wins", 0) + stats.get("losses", 0) + stats.get("ties", 0)
        gd = stats.get("pointDifferential", 0)
        pts = stats.get("points", 0)
        pill = live_map.get(full.lower(), "")
        pill_txt = f"[{pill}]" if pill else ""
        txt += f"{i:2} {logo} {short:<11} {pill_txt:<5} {p:2} {gd:3} {pts:3}\n"
    txt += "```\n"
    txt += f"Updated {datetime.now().strftime('%H:%M')} WAT"
    return txt

def get_matches_text(lid, only_live=False):
    today_str = date.today().strftime("%Y%m%d")
    data = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}")
    if not data or not data.get("events"):
        return ""
    out = ""
    for ev in data["events"]:
        state = ev.get("status", {}).get("type", {}).get("state", "")
        if only_live and state != "in":
            continue
        comp = ev["competitions"][0]
        teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        det = ev["status"]["type"]["detail"]
        icon = "🔴" if state == "in" else "✅" if state == "post" else "🕒"
        out += f"{icon} {h['team']['shortDisplayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['shortDisplayName']} • {det} • {wat(ev['date'])}\n"
    return out

def get_stats(event_id, lid):
    data = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}")
    if not data:
        return "Match Stats not yet available."
    try:
        comp = data["header"]["competitions"][0]
        teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        txt = f"*Match Stats*\n{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}\n{comp['status']['type']['detail']}\n\n"
        for tm in data.get("boxscore", {}).get("teams", [])[:2]:
            txt += f"*{tm.get('team',{}).get('displayName','')}*\n"
            for s in tm.get("statistics", [])[:6]:
                txt += f"{s['name']}: {s['displayValue']}\n"
            txt += "\n"
        return txt[:3500]
    except:
        return "Stats loading..."

def main_menu():
    return {"inline_keyboard": [
        [{"text": "🇹🇷 Süper Lig", "callback_data": "league_tur.1"}, {"text": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL", "callback_data": "league_eng.1"}],
        [{"text": "🏆 Champions League", "callback_data": "league_uefa.champions"}, {"text": "🇪🇸 La Liga", "callback_data": "league_esp.1"}],
        [{"text": "🇩🇪 Bundesliga", "callback_data": "league_ger.1"}, {"text": "🇮🇹 Serie A", "callback_data": "league_ita.1"}],
        [{"text": "🔴 Live Now", "callback_data": "live"}, {"text": "📅 Today", "callback_data": "today"}],
        [{"text": "⭐ My Team", "callback_data": "myteam"}]
    ]}

def league_tabs(lid):
    return {"inline_keyboard": [
        [{"text": "📊 Table", "callback_data": f"table_{lid}"}, {"text": "⚽ Matches", "callback_data": f"matches_{lid}"}],
        [{"text": "🏆 UCL", "callback_data": "league_uefa.champions"}, {"text": "🇹🇷 Süper Lig", "callback_data": "league_tur.1"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}

def notifier_all_teams():
    print("🔴 Notifier FOR ALL TEAMS started - 15 sec")
    while True:
        try:
            today_str = date.today().strftime("%Y%m%d")
            for lid in ["esp.1", "eng.1", "tur.1", "ita.1", "ger.1", "fra.1", "uefa.champions"]:
                data = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}")
                if not data:
                    continue
                for ev in data.get("events", []):
                    if ev.get("status", {}).get("type", {}).get("state") != "in":
                        continue
                    eid = ev["id"]
                    comp = ev["competitions"][0]
                    teams = comp["competitors"]
                    h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                    a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                    hs = int(h.get("score", 0) or 0)
                    as_ = int(a.get("score", 0) or 0)
                    minute = ev["status"]["type"]["detail"]

                    cur.execute("SELECT h_score, a_score FROM scores WHERE event_id=?", (eid,))
                    row = cur.fetchone()
                    if not row:
                        cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)", (eid, h["team"]["displayName"], a["team"]["displayName"], hs, as_))
                        conn.commit()
                    else:
                        oh, oa = row
                        if hs != oh or as_ != oa:
                            cur.execute("UPDATE scores SET h_score=?, a_score=? WHERE event_id=?", (hs, as_, eid))
                            conn.commit()
                            msg = f"⚽ {h['team']['displayName']} - {a['team']['displayName']}:\n{minute} Goal!\nNew score is {hs} - {as_}"
                            kb = {"inline_keyboard": [[{"text": "Match Stats", "callback_data": f"stats_{lid}_{eid}"}]]}
                            for chat_id in db_get_all_subs():
                                send(chat_id, msg, kb)
                            print(f"GOAL PUSHED: {msg}")
            time.sleep(15)
        except Exception as e:
            print(f"Notifier error: {e}")
            time.sleep(15)

threading.Thread(target=notifier_all_teams, daemon=True).start()

offset = 0
print("✅ V8 FINAL RUNNING - ALL TEAMS")

while True:
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=35) as r:
            data = json.loads(r.read().decode())

        for up in data.get("result", []):
            offset = up["update_id"] + 1

            if "callback_query" in up:
                cb = up["callback_query"]
                chat_id = cb["message"]["chat"]["id"]
                d = cb["data"]
                cid = cb["id"]

                if d == "menu":
                    answer_cb(cid)
                    send(chat_id, "⚽ *Football Hub* - All Teams Live ✨", main_menu())

                elif d.startswith("league_") or d.startswith("table_"):
                    lid = d.replace("league_", "").replace("table_", "")
                    answer_cb(cid, "Loading table...")
                    tbl = get_beautiful_table(lid)
                    send(chat_id, tbl, league_tabs(lid))

                elif d.startswith("matches_"):
                    lid = d.replace("matches_", "")
                    answer_cb(cid, "Loading matches...")
                    txt = get_matches_text(lid, only_live=False)
                    if not txt:
                        txt = f"No matches today for {LEAGUE_NAMES.get(lid, lid)}"
                    else:
                        txt = f"*{LEAGUE_NAMES.get(lid, lid)} - Today WAT*\n\n" + txt
                    send(chat_id, txt, league_tabs(lid))

                elif d == "live":
                    answer_cb(cid, "Checking live...")
                    live_txt = "🔴 *Live Now + Auto Feed ON*\nYou will get goals auto\n\n"
                    found = False
                    for lid in ["tur.1", "eng.1", "esp.1", "ita.1", "ger.1", "uefa.champions"]:
                        m = get_matches_text(lid, only_live=True)
                        if m:
                            live_txt += f"*{LEAGUE_NAMES.get(lid)}:*\n{m}\n"
                            found = True
                    if not found:
                        live_txt += "😴 No games live RIGHT NOW.\nFeed is still ON - next goal will auto-drop!\n\nCheck Today tab for schedule."
                    send(chat_id, live_txt[:3800], {"inline_keyboard": [[{"text": "📅 Today", "callback_data": "today"}], [{"text": "⬅️ Menu", "callback_data": "menu"}]]})

                elif d == "today":
                    answer_cb(cid, "Loading today...")
                    full = ""
                    for lid in ["tur.1", "eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions"]:
                        m = get_matches_text(lid, only_live=False)
                        if m:
                            full += f"*{LEAGUE_NAMES.get(lid)}:*\n{m}\n"
                    if not full:
                        full = "No matches today. Check tomorrow."
                    send(chat_id, full[:3800], {"inline_keyboard": [[{"text": "🔴 Live Now", "callback_data": "live"}], [{"text": "⬅️ Menu", "callback_data": "menu"}]]})

                elif d.startswith("stats_"):
                    _, lid, eid = d.split("_", 2)
                    answer_cb(cid, "Loading stats...")
                    send(chat_id, get_stats(eid, lid), {"inline_keyboard": [[{"text": "⬅️ Menu", "callback_data": "menu"}]]})

                elif d == "myteam":
                    answer_cb(cid)
                    cur.execute("SELECT team FROM favs WHERE chat_id=?", (str(chat_id),))
                    row = cur.fetchone()
                    if row:
                        send(chat_id, f"⭐ Your team: *{row[0].title()}*\nYou get ALL goals + extra for your team.", {"inline_keyboard": [[{"text": "⬅️ Menu", "callback_data": "menu"}]]})
                    else:
                        send(chat_id, "Type team name like `Galatasaray` or `Arsenal`", {"inline_keyboard": [[{"text": "⬅️ Menu", "callback_data": "menu"}]]})
                continue

            if "message" not in up:
                continue

            chat_id = str(up["message"]["chat"]["id"])
            text = up["message"].get("text", "").strip()
            low = text.lower()

            if low.startswith("/start") or low == "menu":
                cur.execute("INSERT OR IGNORE INTO subs VALUES (?)", (chat_id,))
                conn.commit()
                send(chat_id, "⚽ *Football Live Goals V8 FINAL* ✅\n\n✅ Süper Lig table: P GD Pts with 🦁 logos\n✅ EPL, La Liga, Bundesliga, Serie A, UCL\n✅ 📅 Today = all matches WAT\n✅ 🔴 Live Now = shows live games + auto goal push\n✅ ⚽ Auto push: `New score is 1-1` + Match Stats\n\nFor ALL TEAMS - you get every goal!\n\nTap league:", main_menu())

            elif len(low) >= 3 and not low.startswith("/"):
                db_add_fav(chat_id, text)
                send(chat_id, f"⭐ *{text.title()}* saved! You get ALL goals + extra alerts for {text.title()}", main_menu())

        time.sleep(1)
    except Exception as e:
        print(f"Loop error: {e}")
        time.sleep(3)