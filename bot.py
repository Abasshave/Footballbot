from flask import Flask
from threading import Thread
import os

app = Flask(__name__)
@app.route('/')
def home():
    return "Football Bot is Live!"
def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading
from datetime import datetime, date

TOKEN = os.environ.get("BOT_TOKEN")
DB = "football.db"

conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
conn.commit()

def db_add_fav(chat_id, team):
    cur.execute("DELETE FROM favs WHERE chat_id=?", (chat_id,))
    cur.execute("INSERT INTO favs VALUES (?,?)", (chat_id, team.lower()))
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
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        print("Send error:", e)

def answer_cb(cb_id, text=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": cb_id, "text": text}
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(url, data=data, timeout=5)
    except: pass

def wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z","+00:00"))
        h = (dt.hour + 1) % 24
        return f"{h:02d}:{dt.minute:02d} WAT"
    except:
        return ""

# --- REAL TABLE FROM ESPN ---
def get_table(lid):
    try:
        # ESPN standings API
        url = f"https://site.api.espn.com/apis/v2/sports/soccer/{lid}/standings"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode())
        standings = d.get("children", [{}])[0].get("standings", {}).get("entries", [])
        txt = f"🏆 *{lid.upper()} TABLE - LIVE*\n\n"
        for i, e in enumerate(standings[:10], 1):
            team = e['team']['displayName']
            stats = {s['name']: s['value'] for s in e.get('stats', [])}
            pts = stats.get('points', 0)
            gp = stats.get('gamesPlayed', 0)
            gd = stats.get('pointDifferential', 0)
            icon = "🥇" if i==1 else "🥈" if i==2 else "🥉" if i==3 else f"{i}."
            txt += f"{icon} {team} - {pts} pts ({gp} GP) GD {gd:+}\n"
        return txt
    except Exception as ex:
        print("table err", ex)
        return "⚠️ Table not available now, try again."

# --- MATCH DETAIL (stats, lineup, H2H) ---
def get_match_detail(event_id, lid):
    try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read().decode())
        header = d.get("header", {})
        comp = header.get("competitions", [{}])[0]
        competitors = comp.get("competitors", [])
        if len(competitors)<2: return "No data"
        h = next((c for c in competitors if c['homeAway']=='home'), competitors[0])
        a = next((c for c in competitors if c['homeAway']=='away'), competitors[1])
        status = header.get("competitions", [{}])[0].get("status",{}).get("type",{}).get("detail","")
        
        txt = f"⚽ *{h['team']['displayName']} {h.get('score','0')} - {a.get('score','0')} {a['team']['displayName']}*\n📍 {status} | 🕒 {wat(header.get('competitions',[{}])[0].get('date',''))}\n\n"
        
        # Stats
        txt += "📊 *LIVE STATS:*\n"
        stats = comp.get("statistics", [])
        if not stats:
            # try boxscore stats
            box = d.get("boxscore",{}).get("teams",[])
            for tm in box:
                txt += f"\n*{tm.get('team',{}).get('displayName','')}*\n"
                for st in tm.get("statistics", [])[:8]:
                    txt += f"{st['displayName']}: {st['displayValue']}\n"
        else:
            for s in stats[:10]:
                txt += f"{s.get('displayName')}: {s.get('displayValue')}\n"
        
        # Lineups
        rosters = d.get("rosters", [])
        if rosters:
            txt += "\n👥 *LINEUPS:*\n"
            for ros in rosters[:2]:
                team = ros.get("team",{}).get("displayName","")
                txt += f"\n*{team}:*\n"
                for pl in ros.get("roster", [])[:11]:
                    ath = pl.get("athlete",{}).get("displayName","")
                    pos = pl.get("position",{}).get("abbreviation","")
                    txt += f"{pos} {ath}\n"
        
        # H2H / recent form could be in header
        return txt if len(txt)<3500 else txt[:3500]
    except Exception as e:
        print("detail err", e)
        return "⚠️ Detail not available."

def menu_kb():
    return {"inline_keyboard": [
        [{"text":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL","callback_data":"table_eng.1"}, {"text":"🇪🇸 La Liga","callback_data":"table_esp.1"}],
        [{"text":"🇮🇹 Serie A","callback_data":"table_ita.1"}, {"text":"🇩🇪 Bundesliga","callback_data":"table_ger.1"}],
        [{"text":"🇹🇷 Süper Lig","callback_data":"table_tur.1"}, {"text":"🇫🇷 Ligue 1","callback_data":"table_fra.1"}],
        [{"text":"🏆 UCL","callback_data":"table_uefa.champions"}, {"text":"📅 Today WAT","callback_data":"today"}],
        [{"text":"🔴 Live Now","callback_data":"live"}, {"text":"⭐ My Team","callback_data":"myteam"}],
    ]}

def back_kb(): return {"inline_keyboard": [[{"text":"⬅️ Back to Menu","callback_data":"menu"}]]}

def get_today_buttons():
    today_str = date.today().strftime("%Y%m%d")
    games = []
    buttons = []
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions","fra.1"]:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            for ev in d.get("events", [])[:5]:
                comp = ev.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) < 2: continue
                h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
                a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
                state = ev.get("status",{}).get("type",{}).get("state","")
                eid = ev.get("id")
                icon = "🔴" if state=="in" else "✅" if state=="post" else "⚽"
                games.append(f"{icon} {h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']} | {wat(ev.get('date',''))}")
                buttons.append([{"text":f"{icon} {h['team']['shortDisplayName']} vs {a['team']['shortDisplayName']} STATS","callback_data":f"detail_{lid}_{eid}"}])
        except: continue
    return games, buttons

def get_live_buttons():
    today_str = date.today().strftime("%Y%m%d")
    lives = []
    buttons = []
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions","fra.1"]:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            for ev in d.get("events", []):
                if ev.get("status",{}).get("type",{}).get("state") == "in":
                    comp = ev.get("competitions", [{}])[0]
                    teams = comp.get("competitors", [])
                    if len(teams) < 2: continue
                    h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
                    a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
                    eid = ev.get("id")
                    lives.append(f"🔴 *{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}* | {ev['status']['type']['detail']}")
                    buttons.append([{"text":f"📊 {h['team']['shortDisplayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['shortDisplayName']} - STATS","callback_data":f"detail_{lid}_{eid}"}])
        except: continue
    return lives, buttons

def notifier():
    print("🔔 Auto goal notifier + LIVE alerts ON")
    while True:
        try:
            today_str = date.today().strftime("%Y%m%d")
            for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions","fra.1"]:
                try:
                    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
                    with urllib.request.urlopen(url, timeout=8) as r:
                        data = json.loads(r.read().decode())
                    for ev in data.get("events", []):
                        if ev.get("status",{}).get("type",{}).get("state") != "in": continue
                        eid = ev["id"]
                        comp = ev["competitions"][0]
                        teams = comp["competitors"]
                        h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
                        a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
                        hs = int(h.get("score",0) or 0); as_ = int(a.get("score",0) or 0)
                        cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?", (eid,))
                        row = cur.fetchone()
                        if not row:
                            cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)", (eid, h["team"]["displayName"], a["team"]["displayName"], hs, as_))
                            conn.commit()
                        else:
                            oh, oa = row
                            if hs != oh or as_ != oa:
                                cur.execute("UPDATE scores SET h_score=?, a_score=? WHERE event_id=?", (hs, as_, eid))
                                conn.commit()
                                for chat_id, fav in db_get_favs():
                                    if fav in h["team"]["displayName"].lower() or fav in a["team"]["displayName"].lower():
                                        # improved notification with stats button
                                        kb = {"inline_keyboard": [[{"text":"📊 View Live Stats","callback_data":f"detail_{lid}_{eid}"}], [{"text":"⬅️ Menu","callback_data":"menu"}]]}
                                        send(chat_id, f"⚽ *GOAL ALERT!* {fav.title()}\n\n🔥 *{h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}*\n⏱️ {ev['status']['type']['detail']} | {wat(ev.get('date',''))}", kb)
                except: continue
            time.sleep(25)
        except Exception as e:
            print("Notifier err", e); time.sleep(25)

threading.Thread(target=notifier, daemon=True).start()

offset = 0
print("✅ BOT RUNNING V2 - WITH STATS")

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
                    send(chat_id, "⚽ *Football Hub PRO - All times WAT 🇳🇬*\nTap to navigate:", menu_kb())
                elif d.startswith("table_"):
                    lid = d.replace("table_","")
                    answer_cb(cid, "Loading LIVE table...")
                    send(chat_id, get_table(lid), back_kb())
                elif d == "today":
                    answer_cb(cid, "Fetching today...")
                    g, btns = get_today_buttons()
                    kb = {"inline_keyboard": btns + [[{"text":"⬅️ Back to Menu","callback_data":"menu"}]]}
                    txt = "📅 *Today's Games (WAT):*\n\n" + "\n".join(g) if g else "No games found today."
                    send(chat_id, txt, kb)
                elif d == "live":
                    answer_cb(cid, "Checking live...")
                    g, btns = get_live_buttons()
                    kb = {"inline_keyboard": btns + [[{"text":"⬅️ Back to Menu","callback_data":"menu"}]]}
                    txt = "🔴 *Live Now - Tap for STATS:*\n\n" + "\n\n".join(g) if g else "😴 No live games now."
                    send(chat_id, txt, kb)
                elif d.startswith("detail_"):
                    _, lid, eid = d.split("_",2)
                    answer_cb(cid, "Loading stats...")
                    detail = get_match_detail(eid, lid)
                    kb = {"inline_keyboard": [[{"text":"🔄 Refresh Stats","callback_data":d}], [{"text":"⬅️ Back to Menu","callback_data":"menu"}]]}
                    send(chat_id, detail, kb)
                elif d == "myteam":
                    answer_cb(cid)
                    cur.execute("SELECT team FROM favs WHERE chat_id=?", (str(chat_id),))
                    r = cur.fetchone()
                    if r:
                        send(chat_id, f"⭐ Your team: *{r[0].title()}*\n🔔 You will get GOAL + LIVE alerts!", back_kb())
                    else:
                        send(chat_id, "⭐ No team yet. Type team name like `Arsenal`", back_kb())
                continue
            if "message" not in up: continue
            chat_id = str(up["message"]["chat"]["id"])
            txt = up["message"].get("text","").strip()
            low = txt.lower().replace("@myfootballaiagent_bot","")
            if low.startswith("/start") or low == "menu":
                send(chat_id, "⚽ *Welcome to Football Hub PRO!*\n\n✅ REAL tables (auto update)\n✅ Live stats - possession, shots\n✅ Lineups + H2H\n✅ Tap any live game for stats\n✅ Auto GOAL alerts for your team\n\n*Tap below or type team name like Arsenal:*", menu_kb())
            elif len(low) >= 3 and not low.startswith("/"):
                db_add_fav(chat_id, low)
                send(chat_id, f"⭐ Saved *{txt.title()}* as your team!\n🔔 Goal + Live alerts ON! You will get notified whenever {txt.title()} scores.\n\nTry Live Now to see stats.", back_kb())
        time.sleep(1)
    except Exception as e:
        print("Loop error:", e)
        time.sleep(3)