from flask import Flask
from threading import Thread
import os

app = Flask(__name__)
@app.route('/')
def home():
    return "Football Bot V5 Beautiful - Live"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading
from datetime import datetime, date

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    print("ERROR: Set BOT_TOKEN in Render Environment!")

DB = "football.db"
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
conn.commit()

def db_add_fav(chat_id, team):
    cur.execute("DELETE FROM favs WHERE chat_id=?", (str(chat_id),))
    cur.execute("INSERT INTO favs VALUES (?,?)", (str(chat_id), team.lower()))
    conn.commit()
def db_get_favs():
    cur.execute("SELECT chat_id, team FROM favs")
    return cur.fetchall()

def send(chat_id, text, keyboard=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(url, data=data, timeout=15)
    except Exception as e:
        print("Send error:", e)

def answer_cb(cb_id, text=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": cb_id, "text": text}
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(url, data=data, timeout=5)
    except:
        pass

def wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z","+00:00"))
        h = (dt.hour + 1) % 24
        return f"{h:02d}:{dt.minute:02d} WAT"
    except:
        return ""

LEAGUE_NAMES = {
    "eng.1": "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "esp.1": "La Liga 🇪🇸",
    "ita.1": "Serie A 🇮🇹",
    "ger.1": "Bundesliga 🇩🇪",
    "tur.1": "Süper Lig 🇹🇷",
    "fra.1": "Ligue 1 🇫🇷",
    "uefa.champions": "Champions League 🏆"
}

TEAM_LOGOS = {
    "Galatasaray": "🦁", "Amed": "💚", "Fenerbahce": "💛", "Fenerbahçe": "💛",
    "Besiktas": "🦅", "Beşiktaş": "🦅", "Trabzonspor": "🔴", "Corum": "❤️",
    "Çorum": "❤️", "Kocaelispor": "💚", "Alanyaspor": "🧡", "Kasimpasa": "🔵",
    "Kasımpaşa": "🔵", "Erzurumspor": "💙", "Samsunspor": "🔴", "Goztepe": "💛",
    "Arsenal": "🔴", "Manchester City": "💙", "Man City": "💙", "Liverpool": "🔴",
    "Chelsea": "💙", "Real Madrid": "👑", "Barcelona": "🔵", "Bayern Munich": "🔴"
}

def get_live_scores_map(lid):
    try:
        today_str = date.today().strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
        with urllib.request.urlopen(url, timeout=8) as r:
            d = json.loads(r.read().decode())
        m = {}
        for ev in d.get("events", []):
            if ev.get("status", {}).get("type", {}).get("state") == "in":
                comp = ev["competitions"][0]
                teams = comp["competitors"]
                h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                pill = f"{h.get('score','0')}-{a.get('score','0')}"
                m[h["team"]["displayName"].lower()] = pill
                m[a["team"]["displayName"].lower()] = pill
                m[h["team"]["shortDisplayName"].lower()] = pill
                m[a["team"]["shortDisplayName"].lower()] = pill
        return m
    except:
        return {}

def get_beautiful_table(lid):
    try:
        urls = [
            f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings",
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/standings"
        ]
        entries = []
        for url in urls:
            try:
                with urllib.request.urlopen(url, timeout=12) as r:
                    d = json.loads(r.read().decode())
                if "standings" in d and "entries" in d["standings"]:
                    entries = d["standings"]["entries"]
                elif "children" in d:
                    entries = d.get("children", [{}])[0].get("standings", {}).get("entries", [])
                if entries:
                    break
            except:
                continue
        
        if not entries:
            return None

        live_map = get_live_scores_map(lid)
        title = LEAGUE_NAMES.get(lid, lid)
        
        txt = f"*{title}*\n"
        if "tur" in lid:
            txt += "Türkiye • 2026/2027\n"
        txt += "━━━━━━━━━━━━━━━━━━━━\n"
        txt += "🔴 Live      P    GD    Pts\n"
        txt += "```\n"
        
        for i, e in enumerate(entries[:10], 1):
            full_name = e.get("team", {}).get("displayName", "")
            short = e.get("team", {}).get("shortDisplayName", full_name)[:14]
            logo = TEAM_LOGOS.get(full_name, TEAM_LOGOS.get(short, "⚽"))
            
            stats = {s['name']: s.get('value', 0) for s in e.get("stats", [])}
            p = stats.get("gamesPlayed", 0)
            if p == 0:
                p = stats.get("wins", 0) + stats.get("losses", 0) + stats.get("ties", 0)
            gd = stats.get("pointDifferential", 0)
            pts = stats.get("points", 0)
            
            lower = full_name.lower()
            pill = live_map.get(lower, "")
            if pill:
                pill_display = f"[{pill}]"
            else:
                pill_display = ""
            
            # Like screenshot: 9 Kasımpaşa 0-0 8 0 11
            if pill_display:
                line = f"{i:2} {logo} {short:<11} {pill_display:<5} {p:2} {gd:3} {pts:3}\n"
            else:
                line = f"{i:2} {logo} {short:<11}       {p:2} {gd:3} {pts:3}\n"
            txt += line
        
        txt += "```\n"
        txt += f"Updated {datetime.now().strftime('%H:%M')} WAT • Tap Tabs Below"
        return txt
    except Exception as ex:
        print("Table error:", ex)
        return None

def get_matches(lid):
    try:
        today_str = date.today().strftime("%Y%m%d")
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode())
        evs = d.get("events", [])
        if not evs:
            return f"No matches today for {LEAGUE_NAMES.get(lid)}"
        
        txt = f"*{LEAGUE_NAMES.get(lid)} - Matches*\n\n"
        for ev in evs[:8]:
            comp = ev["competitions"][0]
            teams = comp["competitors"]
            h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
            a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
            state = ev["status"]["type"]["state"]
            detail = ev["status"]["type"]["detail"]
            icon = "🔴" if state == "in" else "✅" if state == "post" else "🕒"
            txt += f"{icon} {h['team']['shortDisplayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['shortDisplayName']} • {detail} • {wat(ev['date'])}\n"
        return txt
    except:
        return "Matches not available."

def get_lineup_detail(event_id, lid):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode())
        comp = d["header"]["competitions"][0]
        teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        
        txt = f"*{h['team']['displayName']} {h.get('score','0')} - {a.get('score','0')} {a['team']['displayName']}*\n"
        txt += f"{comp.get('status',{}).get('type',{}).get('detail','FT')} • {wat(comp.get('date',''))}\n\n"
        txt += f"📋 *Line-up | Formation: 4-1-4-1*\n"
        txt += "```\n"
        for ros in d.get("rosters", [])[:2]:
            tname = ros.get("team", {}).get("displayName", "")
            txt += f"\n{tname}:\n"
            for pl in ros.get("roster", [])[:11]:
                name = pl.get("athlete", {}).get("displayName", "")
                jersey = pl.get("jersey", "")
                txt += f"{jersey} {name}\n"
        txt += "```\n"
        
        # Stats
        if "boxscore" in d:
            txt += "\n📊 *Stats:*\n"
            for tm in d["boxscore"].get("teams", [])[:2]:
                txt += f"*{tm.get('team',{}).get('displayName','')}*\n"
                for s in tm.get("statistics", [])[:5]:
                    txt += f"{s['name']}: {s['displayValue']}\n"
        return txt[:3500]
    except Exception as e:
        print(e)
        return "Line-up available only for live/finished games. Try Live Now."

def main_menu():
    return {"inline_keyboard": [
        [{"text": "🇹🇷 Süper Lig", "callback_data": "league_tur.1"}, {"text": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL", "callback_data": "league_eng.1"}],
        [{"text": "🇪🇸 La Liga", "callback_data": "league_esp.1"}, {"text": "🇩🇪 Bundesliga", "callback_data": "league_ger.1"}],
        [{"text": "🏆 Champions League", "callback_data": "league_uefa.champions"}, {"text": "🇮🇹 Serie A", "callback_data": "league_ita.1"}],
        [{"text": "🔴 Live Now", "callback_data": "live"}, {"text": "📅 Today WAT", "callback_data": "today"}],
        [{"text": "⭐ My Team Alerts", "callback_data": "myteam"}]
    ]}

def league_tabs(lid):
    return {"inline_keyboard": [
        [{"text": "📊 Table", "callback_data": f"table_{lid}"}, {"text": "⚽ Matches", "callback_data": f"matches_{lid}"}, {"text": "📈 Form", "callback_data": f"form_{lid}"}],
        [{"text": "👤 Player stats", "callback_data": f"pstats_{lid}"}, {"text": "👥 Team stats", "callback_data": f"tstats_{lid}"}],
        [{"text": "🏆 UCL", "callback_data": "league_uefa.champions"}, {"text": "🇹🇷 Süper Lig", "callback_data": "league_tur.1"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}

def back_menu():
    return {"inline_keyboard": [[{"text": "⬅️ Back to Menu", "callback_data": "menu"}]]}

# AUTO NOTIFIER FOR TEAM GOALS
def notifier():
    print("🔔 Notifier ON - Team alerts + live")
    while True:
        try:
            today_str = date.today().strftime("%Y%m%d")
            for lid in ["tur.1", "eng.1", "esp.1", "ger.1", "uefa.champions"]:
                try:
                    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
                    with urllib.request.urlopen(url, timeout=8) as r:
                        data = json.loads(r.read().decode())
                    for ev in data.get("events", []):
                        if ev.get("status", {}).get("type", {}).get("state") != "in":
                            continue
                        eid = ev["id"]
                        comp = ev["competitions"][0]
                        teams = comp["competitors"]
                        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                        hs = int(h.get("score", 0) or 0)
                        asn = int(a.get("score", 0) or 0)
                        
                        cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?", (eid,))
                        row = cur.fetchone()
                        if not row:
                            cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)", (eid, h["team"]["displayName"], a["team"]["displayName"], hs, asn))
                            conn.commit()
                        else:
                            oh, oa = row
                            if hs != oh or asn != oa:
                                cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?", (hs, asn, eid))
                                conn.commit()
                                for chat_id, fav in db_get_favs():
                                    if fav in h["team"]["displayName"].lower() or fav in a["team"]["displayName"].lower():
                                        kb = {"inline_keyboard": [[{"text": "📊 View Table", "callback_data": f"table_{lid}"}], [{"text": "📋 Line-up", "callback_data": f"detail_{lid}_{eid}"}]]}
                                        send(chat_id, f"⚽ *GOAL ALERT!* 🔴\n*{h['team']['displayName']} {hs}-{asn} {a['team']['displayName']}*\n⏱️ {ev['status']['type']['detail']} • {wat(ev['date'])}", kb)
                except Exception as e:
                    print("score fetch err", e)
                    continue
            time.sleep(25)
        except Exception as e:
            print("Notifier err", e)
            time.sleep(25)

threading.Thread(target=notifier, daemon=True).start()

offset = 0
print("✅ BOT V5 BEAUTIFUL RUNNING - NO DOUBLE BUG")

while True:
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url, timeout=35) as r:
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
                    send(chat_id, f"⚽ *Football Hub Beautiful* ✨\nOrganized like FotMob\nAll times WAT 🇳🇬\nChoose league:", main_menu())
                elif d.startswith("league_"):
                    lid = d.replace("league_", "")
                    answer_cb(cid, f"Loading {LEAGUE_NAMES.get(lid)}...")
                    tbl = get_beautiful_table(lid)
                    if tbl:
                        send(chat_id, tbl, league_tabs(lid))
                    else:
                        send(chat_id, f"*{LEAGUE_NAMES.get(lid)}*\nTable loading, try again...", league_tabs(lid))
                elif d.startswith("table_"):
                    lid = d.replace("table_", "")
                    answer_cb(cid, "Loading table...")
                    tbl = get_beautiful_table(lid)
                    send(chat_id, tbl if tbl else "Table loading...", league_tabs(lid))
                elif d.startswith("matches_"):
                    lid = d.replace("matches_", "")
                    answer_cb(cid)
                    send(chat_id, get_matches(lid), league_tabs(lid))
                elif d.startswith("form_") or d.startswith("pstats_") or d.startswith("tstats_"):
                    lid = d.split("_")[1]
                    answer_cb(cid, "Coming soon")
                    send(chat_id, f"📊 *Player / Team Stats*\nTop scorers & form coming next update.\nUse Table & Matches for now.", league_tabs(lid))
                elif d == "today":
                    answer_cb(cid, "Fetching today...")
                    txt = "📅 *Today WAT:*\n\n"
                    for lid in ["tur.1", "eng.1", "uefa.champions"]:
                        txt += get_matches(lid) + "\n\n"
                    send(chat_id, txt[:3800], back_menu())
                elif d == "live":
                    answer_cb(cid, "Checking live...")
                    txt = "🔴 *Live Now:*\n\n"
                    for lid in ["tur.1", "eng.1", "esp.1", "uefa.champions"]:
                        matches = get_matches(lid)
                        if "🔴" in matches:
                            txt += matches + "\n"
                    if "🔴" not in txt:
                        txt = "😴 No live games now. Try again later."
                    send(chat_id, txt[:3800], back_menu())
                elif d.startswith("detail_"):
                    _, lid, eid = d.split("_", 2)
                    answer_cb(cid, "Loading lineup...")
                    send(chat_id, get_lineup_detail(eid, lid), back_menu())
                elif d == "myteam":
                    answer_cb(cid)
                    cur.execute("SELECT team FROM favs WHERE chat_id=?", (str(chat_id),))
                    r = cur.fetchone()
                    if r:
                        send(chat_id, f"⭐ Your team: *{r[0].title()}*\n🔔 Goal alerts ON!", back_menu())
                    else:
                        send(chat_id, "⭐ No team yet.\nType team name like `Galatasaray` or `Arsenal`", back_menu())
                continue
            
            if "message" not in up:
                continue
            
            chat_id = str(up["message"]["chat"]["id"])
            txt = up["message"].get("text", "").strip()
            low = txt.lower().replace("@myfootballaiagent_bot", "")
            
            if low.startswith("/start") or low == "menu":
                send(chat_id, "⚽ *Welcome to Beautiful Football Bot!* ✨\n\n"
                     "🇹🇷 *Süper Lig like your screenshot:*\n"
                     "`  Team           P  GD Pts`\n"
                     "🔴 Live pills [0-0] when playing\n"
                     "🦁 Club logos\n"
                     "🏆 *Champions League added*\n"
                     "📊 Table | ⚽ Matches | 👤 Player stats\n"
                     "🔔 Type team name for alerts\n\n"
                     "Tap a league:", main_menu())
            elif len(low) >= 3 and not low.startswith("/"):
                db_add_fav(chat_id, low)
                send(chat_id, f"⭐ Saved *{txt.title()}* - Alerts ON! 🔔\n\nNow choose league:", main_menu())
        
        time.sleep(1)
    except Exception as e:
        print("Loop error:", e)
        time.sleep(3)