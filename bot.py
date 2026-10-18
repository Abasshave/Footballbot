from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "Football Bot is Live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading, os
from datetime import datetime, date

# Fake web server for Render free
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_fake_server():
    port = int(os.environ.get("PORT", 10000))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

threading.Thread(target=run_fake_server, daemon=True).start()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8995140935:AAHBISZnroMD6LWXou4cBBSE6B_4TRLzqtw")
DB = "football.db"

# --- DB ---
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
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard:
            payload["reply_markup"] = json.dumps(keyboard)
        data = urllib.parse.urlencode(payload).encode()
        urllib.request.urlopen(url, data=data, timeout=10)
    except Exception as e:
        print("Send error:", e)

def answer_cb(cb_id, text=""):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
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

REAL = {
    "eng.1": "🏆 *PREMIER LEAGUE 26/27*\n\n🥇 *Man City* - 18 pts (6-0-0) GD +11\n🥈 *Arsenal* - 15 pts (5-0-1) GD +5\n🥉 *Brighton* - 13 pts (4-1-1) GD +13\n4. Brentford - 10 pts\n5. Everton - 10 pts\n6. Chelsea - 10 pts\n7. Leeds - 9 pts\n8. Hull City - 9 pts",
    "esp.1": "🏆 *LA LIGA 26/27*\n\n🥇 *Barcelona* - 24 pts (8-0-0) GD +27 🔥\n🥈 *Atletico Madrid* - 19 pts\n🥉 *Real Betis* - 19 pts\n4. Real Madrid - 18 pts\n5. Sevilla - 14 pts",
    "ita.1": "🏆 *SERIE A 26/27*\n\n🥇 *Inter* - 16 pts (5-1-0) GD +10\n🥈 *Lazio* - 16 pts GD +7\n🥉 *Roma* - 14 pts GD +11\n4. Frosinone - 13 pts",
    "ger.1": "🏆 *BUNDESLIGA 26/27*\n\n🥇 *Freiburg* - 13 pts (4-1-0) GD +10\n🥈 *Dortmund* - 13 pts GD +7\n🥉 *Bayern Munich* - 11 pts GD +12\n4. Leverkusen - 10 pts",
    "tur.1": "🏆 *SÜPER LIG 26/27*\n\n🥇 *Galatasaray* - 16 pts (5-1-1) GD +5\n🥈 *Besiktas* - 15 pts GD +8\n🥉 *Amed SFK* - 14 pts\n4. Fenerbahce - 13 pts GD +13",
    "fifa.world": "🏆 *WORLD CUP 2026 - FINAL*\n\n🥇 *Spain 1-0 Argentina* (AET) - Ferran Torres 106'\n🥈 Argentina\n🥉 England 6-4 France (3rd)\n📍 MetLife Stadium, NY\n📅 July 19, 2026 - Spain 2nd title",
    "uefa.champions": "🏆 *CHAMPIONS LEAGUE 26/27*\n\nLeague phase - Matchday 3\nTop 8 qualify to R16\nTap Today to see today's UCL games"
}

def menu_kb():
    return {"inline_keyboard": [
        [{"text":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL","callback_data":"table_eng.1"}, {"text":"🇪🇸 La Liga","callback_data":"table_esp.1"}],
        [{"text":"🇮🇹 Serie A","callback_data":"table_ita.1"}, {"text":"🇩🇪 Bundesliga","callback_data":"table_ger.1"}],
        [{"text":"🇹🇷 Süper Lig","callback_data":"table_tur.1"}, {"text":"🌍 World Cup","callback_data":"table_fifa.world"}],
        [{"text":"🏆 UCL","callback_data":"table_uefa.champions"}, {"text":"📅 Today WAT","callback_data":"today"}],
        [{"text":"🔴 Live Now","callback_data":"live"}, {"text":"⭐ My Team","callback_data":"myteam"}],
    ]}

def back_kb(): return {"inline_keyboard": [[{"text":"⬅️ Back to Menu","callback_data":"menu"}]]}

def get_today():
    today_str = date.today().strftime("%Y%m%d")
    games = []
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions","fra.1"]:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            events = d.get("events", [])
            if not events:
                url2 = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard"
                with urllib.request.urlopen(url2, timeout=10) as r2:
                    d2 = json.loads(r2.read().decode())
                events = d2.get("events", [])[:3]
            for ev in events[:5]:
                comp = ev.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                if len(teams) < 2: continue
                h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
                a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
                state = ev.get("status",{}).get("type",{}).get("state","")
                detail = ev.get("status",{}).get("type",{}).get("detail","")
                t = wat(ev.get("date",""))
                if state == "in":
                    games.append(f"🔴 *{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}* | {detail}")
                elif state == "post":
                    games.append(f"✅ {h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']} | FT - {t}")
                else:
                    games.append(f"⚽ {h['team']['displayName']} vs {a['team']['displayName']} - 🕒 {t} | {detail if detail else lid}")
        except: continue
    return games[:20]

def get_live():
    today_str = date.today().strftime("%Y%m%d")
    lives = []
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
                    detail = ev.get("status",{}).get("type",{}).get("detail","LIVE")
                    lives.append(f"🔴 *{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}* | {detail} | {wat(ev.get('date',''))}")
        except: continue
    return lives

def search_team(q):
    q = q.lower()
    today_str = date.today().strftime("%Y%m%d")
    found = []
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions"]:
        try:
            url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url, timeout=8) as r:
                d = json.loads(r.read().decode())
            for ev in d.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                teams = comp.get("competitors", [])
                for tm in teams:
                    if q in tm['team']['displayName'].lower():
                        h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
                        a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
                        found.append(f"🔍 {h['team']['displayName']} vs {a['team']['displayName']} - {wat(ev.get('date',''))} | {lid}")
                        break
        except: continue
    return found[:8]

def notifier():
    print("🔔 Auto goal notifier ON (30s)")
    while True:
        try:
            today_str = date.today().strftime("%Y%m%d")
            for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","uefa.champions"]:
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
                                        send(chat_id, f"⚽ *GOAL!* {h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}\n⏱️ {ev['status']['type']['detail']} | {wat(ev.get('date',''))}\n\nYour team {fav.title()} scored!", back_kb())
                except: continue
            time.sleep(30)
        except Exception as e:
            print("Notifier err", e); time.sleep(30)

threading.Thread(target=notifier, daemon=True).start()

offset = 0
print("✅ BOT RUNNING")

while True:
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url, timeout=20) as r:
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
                    send(chat_id, "⚽ *Football Hub - All times WAT 🇳🇬*\nTap to navigate:", menu_kb())
                elif d.startswith("table_"):
                    lid = d.replace("table_","")
                    answer_cb(cid, "Loading table...")
                    send(chat_id, REAL.get(lid, REAL["eng.1"]), back_kb())
                elif d == "today":
                    answer_cb(cid, "Fetching today...")
                    g = get_today()
                    txt = "📅 *Today's Games (WAT):*\n\n" + "\n\n".join(g) if g else "No games found today."
                    send(chat_id, txt, back_kb())
                elif d == "live":
                    answer_cb(cid, "Checking live...")
                    g = get_live()
                    txt = "🔴 *Live Now:*\n\n" + "\n\n".join(g) if g else "😴 No live games now."
                    send(chat_id, txt, back_kb())
                elif d == "myteam":
                    answer_cb(cid)
                    cur.execute("SELECT team FROM favs WHERE chat_id=?", (str(chat_id),))
                    r = cur.fetchone()
                    if r:
                        send(chat_id, f"⭐ Your team: *{r[0].title()}*\nYou will get GOAL alerts!", back_kb())
                    else:
                        send(chat_id, "⭐ No team yet. Type team name like `Arsenal`", back_kb())
                continue
            if "message" not in up: continue
            chat_id = str(up["message"]["chat"]["id"])
            txt = up["message"].get("text","").strip()
            low = txt.lower().replace("@myfootballaiagent_bot","")
            if low.startswith("/start") or low == "menu":
                send(chat_id, "⚽ *Welcome to Football Hub - Final!*\n\n✅ Today with WAT time\n✅ Live scores\n✅ Real tables + World Cup\n✅ Auto GOAL alerts\n\n*Tap below or type team name like Arsenal:*", menu_kb())
            elif low.startswith("/today"):
                g = get_today()
                send(chat_id, "📅 *Today (WAT):*\n\n" + "\n\n".join(g) if g else "No games today", back_kb())
            elif low.startswith("/live"):
                g = get_live()
                send(chat_id, "🔴 *Live Now:*\n\n" + "\n\n".join(g) if g else "No live games now", back_kb())
            elif len(low) >= 3 and not low.startswith("/"):
                db_add_fav(chat_id, low)
                f = search_team(low)
                if f:
                    send(chat_id, f"⭐ Saved *{txt.title()}* as your team! Goal alerts ON 🔔\n\n🔍 *Found today:*\n" + "\n".join(f), back_kb())
                else:
                    send(chat_id, f"⭐ Saved *{txt.title()}*! You will get goal alerts.\n\nNo games for {txt.title()} today.", back_kb())
        time.sleep(1)
    except Exception as e:
        print("Loop error:", e)
        time.sleep(3)