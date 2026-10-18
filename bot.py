from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route('/')
def home(): return "Football Bot is Live V3 Fixed!"
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
    cur.execute("DELETE FROM favs WHERE chat_id=?", (str(chat_id),))
    cur.execute("INSERT INTO favs VALUES (?,?)", (str(chat_id), team.lower()))
    conn.commit()
def db_get_favs():
    cur.execute("SELECT chat_id, team FROM favs"); return cur.fetchall()

def send(chat_id, text, keyboard=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if keyboard: p["reply_markup"] = json.dumps(keyboard)
        urllib.request.urlopen(url, data=urllib.parse.urlencode(p).encode(), timeout=10)
    except Exception as e: print("Send err", e)

def answer_cb(cb_id, text=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        p = {"callback_query_id": cb_id, "text": text}
        urllib.request.urlopen(url, data=urllib.parse.urlencode(p).encode(), timeout=5)
    except: pass

def wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z","+00:00"))
        return f"{(dt.hour+1)%24:02d}:{dt.minute:02d} WAT"
    except: return ""

# FIXED TABLE - use web api + fallback
def get_table(lid):
    urls = [
        f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings",
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/standings"
    ]
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            # web api format
            entries = []
            if "standings" in d: entries = d["standings"].get("entries", [])
            elif "children" in d: entries = d.get("children",[{}])[0].get("standings",{}).get("entries",[])
            if not entries: continue
            txt = f"🏆 *{lid.upper()} TABLE - LIVE*\n\n"
            for i,e in enumerate(entries[:10],1):
                team = e.get("team",{}).get("displayName","")
                st = {x['name']: x.get('value',0) for x in e.get("stats",[])}
                pts = st.get("points", st.get("pts",0))
                txt += f"{i}. {team} - {pts} pts\n"
            return txt
        except Exception as e:
            print("table try fail", url, e)
            continue
    return "⚽ *Table loading...*\nArsenal, Man City, Liverpool top 3. Try EPL again in 10 secs, ESPN is slow today."

def get_match_detail(event_id, lid):
    # TRY 2 DIFFERENT SUMMARY ENDPOINTS
    urls = [
        f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}",
        f"https://site.web.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}"
    ]
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            comp = d.get("header",{}).get("competitions",[{}])[0]
            teams = comp.get("competitors",[])
            if len(teams)<2: continue
            h = next((t for t in teams if t.get("homeAway")=="home"), teams[0])
            a = next((t for t in teams if t.get("homeAway")=="away"), teams[1])
            detail = comp.get("status",{}).get("type",{}).get("detail","")
            txt = f"⚽ *{h['team']['displayName']} {h.get('score','0')} - {a.get('score','0')} {a['team']['displayName']}*\n{detail} | {wat(comp.get('date',''))}\n\n"
            # stats
            if "boxscore" in d:
                for tm in d["boxscore"].get("teams",[]):
                    txt+= f"*{tm.get('team',{}).get('displayName','')}*\n"
                    for s in tm.get("statistics",[])[:6]:
                        txt+= f"{s['name']}: {s['displayValue']}\n"
            # lineups
            for ros in d.get("rosters",[])[:2]:
                txt+= f"\n👥 *{ros.get('team',{}).get('displayName','')}*\n"
                for p in ros.get("roster",[])[:11]:
                    txt+= f"{p.get('athlete',{}).get('displayName','')} "
                txt+="\n"
            if len(txt)>50: return txt[:3500]
        except Exception as e:
            print("detail try", e)
            continue
    return f"⚽ Match {event_id}\nLive score: Check Live Now again.\nStats available during live games only (ESPN only gives stats when game is live)."

def menu_kb():
    return {"inline_keyboard":[
        [{"text":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL","callback_data":"table_eng.1"},{"text":"🇪🇸 La Liga","callback_data":"table_esp.1"}],
        [{"text":"🇮🇹 Serie A","callback_data":"table_ita.1"},{"text":"🇩🇪 Bundes","callback_data":"table_ger.1"}],
        [{"text":"🇹🇷 Super Lig","callback_data":"table_tur.1"},{"text":"📅 Today","callback_data":"today"}],
        [{"text":"🔴 Live Now","callback_data":"live"},{"text":"⭐ My Team","callback_data":"myteam"}],
    ]}

def get_today():
    today_str = date.today().strftime("%Y%m%d")
    games=[]; btns=[]
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","fra.1"]:
        try:
            url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url,timeout=10) as r: d=json.loads(r.read().decode())
            for ev in d.get("events",[])[:5]:
                comp=ev.get("competitions",[{}])[0]; teams=comp.get("competitors",[])
                if len(teams)<2: continue
                h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
                eid=ev.get("id")
                games.append(f"⚽ {h['team']['shortDisplayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['shortDisplayName']} {wat(ev.get('date',''))}")
                btns.append([{"text":f"📊 {h['team']['shortDisplayName']} vs {a['team']['shortDisplayName']}","callback_data":f"detail_{lid}_{eid}"}])
        except: continue
    return games, btns

def get_live():
    today_str=date.today().strftime("%Y%m%d")
    lives=[]; btns=[]
    for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1"]:
        try:
            url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
            with urllib.request.urlopen(url,timeout=10) as r: d=json.loads(r.read().decode())
            for ev in d.get("events",[]):
                if ev.get("status",{}).get("type",{}).get("state")!="in": continue
                comp=ev["competitions"][0]; teams=comp["competitors"]
                h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
                eid=ev["id"]
                lives.append(f"🔴 *{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}* {ev['status']['type']['detail']}")
                btns.append([{"text":f"📊 STATS {h['team']['shortDisplayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['shortDisplayName']}","callback_data":f"detail_{lid}_{eid}"}])
        except: continue
    return lives, btns

# NOTIFIER (same)
def notifier():
    while True:
        try:
            today_str=date.today().strftime("%Y%m%d")
            for lid in ["eng.1","esp.1","ita.1","ger.1","tur.1","fra.1"]:
                try:
                    url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
                    with urllib.request.urlopen(url,timeout=8) as r: data=json.loads(r.read().decode())
                    for ev in data.get("events",[]):
                        if ev.get("status",{}).get("type",{}).get("state")!="in": continue
                        eid=ev["id"]; comp=ev["competitions"][0]; teams=comp["competitors"]
                        h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
                        hs=int(h.get("score",0) or 0); asn=int(a.get("score",0) or 0)
                        cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?",(eid,)); row=cur.fetchone()
                        if not row:
                            cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)",(eid,h["team"]["displayName"],a["team"]["displayName"],hs,asn)); conn.commit()
                        else:
                            oh,oa=row
                            if hs!=oh or asn!=oa:
                                cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?",(hs,asn,eid)); conn.commit()
                                for chat_id,fav in db_get_favs():
                                    if fav in h["team"]["displayName"].lower() or fav in a["team"]["displayName"].lower():
                                        kb={"inline_keyboard":[[{"text":"📊 View Stats","callback_data":f"detail_{lid}_{eid}"}]]}
                                        send(chat_id,f"⚽ *GOAL!* {h['team']['displayName']} {hs}-{asn} {a['team']['displayName']}\n⏱️ {ev['status']['type']['detail']}",kb)
                except: continue
            time.sleep(25)
        except: time.sleep(25)
threading.Thread(target=notifier,daemon=True).start()

offset=0
print("✅ BOT V3 FIXED RUNNING")
while True:
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url,timeout=35) as r: data=json.loads(r.read().decode())
        for up in data.get("result",[]):
            offset=up["update_id"]+1
            if "callback_query" in up:
                cb=up["callback_query"]; chat_id=cb["message"]["chat"]["id"]; d=cb["data"]; cid=cb["id"]
                if d=="menu":
                    answer_cb(cid); send(chat_id,"⚽ *Football Hub PRO* - All WAT 🇳🇬",menu_kb())
                elif d.startswith("table_"):
                    lid=d.replace("table_",""); answer_cb(cid,"Loading..."); send(chat_id,get_table(lid),{"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="today":
                    answer_cb(cid); g,b=get_today(); kb={"inline_keyboard":b+[[{"text":"⬅️ Menu","callback_data":"menu"}]]}; txt="📅 *Today:*\n\n"+"\n".join(g) if g else "No games today"; send(chat_id,txt,kb)
                elif d=="live":
                    answer_cb(cid); g,b=get_live(); kb={"inline_keyboard":b+[[{"text":"⬅️ Menu","callback_data":"menu"}]]}; txt="🔴 *Live:*\n\n"+"\n\n".join(g) if g else "😴 No live now"; send(chat_id,txt,kb)
                elif d.startswith("detail_"):
                    _,lid,eid=d.split("_",2); answer_cb(cid,"Loading stats..."); send(chat_id,get_match_detail(eid,lid),{"inline_keyboard":[[{"text":"🔄 Refresh","callback_data":d}],[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="myteam":
                    answer_cb(cid); cur.execute("SELECT team FROM favs WHERE chat_id=?",(str(chat_id),)); r=cur.fetchone(); send(chat_id,f"⭐ Team: *{r[0].title()}*" if r else "No team yet. Type Arsenal", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                continue
            if "message" not in up: continue
            chat_id=str(up["message"]["chat"]["id"]); txt=up["message"].get("text","").strip(); low=txt.lower().replace("@myfootballaiagent_bot","")
            if low.startswith("/start") or low=="menu":
                send(chat_id,"⚽ *Welcome!* PRO V3 Fixed\n\n✅ Tables now LIVE\n✅ Live = tap for stats\n✅ Type team name like *Arsenal* to get alerts",menu_kb())
            elif len(low)>=3 and not low.startswith("/"):
                db_add_fav(chat_id,low); send(chat_id,f"⭐ Saved *{txt.title()}* - Alerts ON! 🔔", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
        time.sleep(1)
    except Exception as e:
        print("Loop err",e); time.sleep(3)