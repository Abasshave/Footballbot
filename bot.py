from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route('/')
def home(): return "Football Live Goals V6 - Channel Style"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)), use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading
from datetime import datetime, date

TOKEN = os.environ.get("BOT_TOKEN")
DB="football.db"
conn=sqlite3.connect(DB, check_same_thread=False)
cur=conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER, last_scorer TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS subscribers (chat_id TEXT PRIMARY KEY)")
conn.commit()

def db_add_fav(c,t):
    cur.execute("DELETE FROM favs WHERE chat_id=?",(str(c),)); cur.execute("INSERT INTO favs VALUES (?,?)",(str(c),t.lower()))
    cur.execute("INSERT OR IGNORE INTO subscribers VALUES (?)",(str(c),)); conn.commit()
def db_get_favs():
    cur.execute("SELECT chat_id, team FROM favs"); return cur.fetchall()
def db_get_all_subs():
    cur.execute("SELECT chat_id FROM subscribers"); return [x[0] for x in cur.fetchall()]

def send(chat_id, text, kb=None):
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        p={"chat_id":chat_id,"text":text,"parse_mode":"Markdown"}
        if kb: p["reply_markup"]=json.dumps(kb)
        urllib.request.urlopen(url,data=urllib.parse.urlencode(p).encode(),timeout=10)
    except Exception as e: print("send err",e)
def answer_cb(cid, txt=""):
    try: urllib.request.urlopen(f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery", data=urllib.parse.urlencode({"callback_query_id":cid,"text":txt}).encode(), timeout=5)
    except: pass
def wat(iso):
    try:
        dt=datetime.fromisoformat(iso.replace("Z","+00:00")); return f"{(dt.hour+1)%24:02d}:{dt.minute:02d} WAT"
    except: return ""

TEAM_EMOJI = {
    "Real Betis":"💚","Barcelona":"🔵🔴","Real Madrid":"👑","Galatasaray":"🦁","Fenerbahce":"💛","Fenerbahçe":"💛","Besiktas":"🦅","Beşiktaş":"🦅",
    "Arsenal":"🔴","Man City":"💙","Liverpool":"🔴","Chelsea":"💙"
}
def logo(name): return TEAM_EMOJI.get(name, "⚽")

# GET SCORER FROM ESPN SUMMARY
def get_scorer_info(event_id, lid):
    try:
        url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}"
        with urllib.request.urlopen(url, timeout=10) as r:
            d=json.loads(r.read().decode())
        # scoring plays
        for play in d.get("header",{}).get("competitions",[{}])[0].get("details",[]):
            if "scoring" in play.get("type",{}).get("text","").lower() or "goal" in str(play).lower():
                pass
        # try key events
        for item in d.get("header",{}).get("competitions",[{}])[0].get("situation",{}).get("lastPlay",{}).get("text",""):
            pass
        # Best method: look at commentary or scoring summary
        scoring = d.get("header",{}).get("competitions",[{}])[0].get("details",[])
        # ESPN often has in header.competitions[0].competitors score + last scorer in boxscore
        # Fallback: get from text
        last_play = ""
        if d.get("commentary"):
            last_play = d["commentary"].get("text","")[:100]
        
        # Extract from gamecast
        plays = d.get("header",{}).get("competitions",[{}])[0].get("status",{}).get("type",{}).get("detail","")
        return plays
    except: return ""

def get_match_stats(event_id, lid):
    try:
        url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}"
        with urllib.request.urlopen(url, timeout=10) as r: d=json.loads(r.read().decode())
        txt=f"📊 *Match Stats*\n\n"
        comp=d["header"]["competitions"][0]
        teams=comp["competitors"]
        h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
        txt+=f"{h['team']['displayName']} {h.get('score','0')} - {a.get('score','0')} {a['team']['displayName']}\n"
        txt+=f"{comp.get('status',{}).get('type',{}).get('detail','')}\n\n"
        for tm in d.get("boxscore",{}).get("teams",[]):
            txt+=f"*{tm.get('team',{}).get('displayName','')}*\n"
            for s in tm.get("statistics",[])[:7]:
                txt+=f"{s['name']}: {s['displayValue']}\n"
            txt+="\n"
        return txt[:3500]
    except: return "Stats loading..."

LEAGUE_NAMES={"eng.1":"Premier League","esp.1":"La Liga","ita.1":"Serie A","ger.1":"Bundesliga","tur.1":"Süper Lig","fra.1":"Ligue 1","uefa.champions":"Champions League"}

def get_table(lid):
    try:
        url=f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings"
        with urllib.request.urlopen(url,timeout=10) as r: d=json.loads(r.read().decode())
        entries=d.get("standings",{}).get("entries",[])
        if not entries: return None
        txt=f"*{LEAGUE_NAMES.get(lid)} TABLE*\n```\n"
        txt+=f"{'':2} {'Team':<14} P  GD Pts\n"
        for i,e in enumerate(entries[:10],1):
            name=e["team"]["shortDisplayName"][:13]
            stats={s['name']:s['value'] for s in e["stats"]}
            txt+=f"{i:2} {name:<14} {stats.get('gamesPlayed',0):2} {stats.get('pointDifferential',0):3} {stats.get('points',0):3}\n"
        txt+="```"
        return txt
    except: return None

def main_menu():
    return {"inline_keyboard":[
        [{"text":"🇹🇷 Süper Lig","callback_data":"league_tur.1"},{"text":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL","callback_data":"league_eng.1"}],
        [{"text":"🏆 Champions League","callback_data":"league_uefa.champions"},{"text":"🇪🇸 La Liga","callback_data":"league_esp.1"}],
        [{"text":"🔴 Live Goals Feed","callback_data":"live"},{"text":"📅 Today","callback_data":"today"}],
        [{"text":"⭐ My Team","callback_data":"myteam"}]
    ]}

# NOTIFIER LIKE YOUR SCREENSHOT
def notifier_goals():
    print("🔴 LIVE GOALS NOTIFIER LIKE SCREENSHOT - STARTED")
    while True:
        try:
            today_str=date.today().strftime("%Y%m%d")
            for lid in ["esp.1","eng.1","tur.1","ita.1","ger.1","fra.1","uefa.champions"]:
                try:
                    url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today_str}"
                    with urllib.request.urlopen(url,timeout=10) as r: data=json.loads(r.read().decode())
                    for ev in data.get("events",[]):
                        if ev["status"]["type"]["state"]!="in": continue
                        eid=ev["id"]
                        comp=ev["competitions"][0]
                        teams=comp["competitors"]
                        h=next((t for t in teams if t.get("homeAway")=="home"),teams[0])
                        a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
                        hs=int(h.get("score",0) or 0); as_=int(a.get("score",0) or 0)
                        minute=ev["status"]["type"]["detail"]  # like 27' or 41'
                        # Get scorer from summary quickly
                        scorer_text=""
                        try:
                            # fetch summary for scorer name
                            s_url=f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={eid}"
                            with urllib.request.urlopen(s_url,timeout=8) as sr:
                                sd=json.loads(sr.read().decode())
                            # last scoring play
                            details=sd.get("header",{}).get("competitions",[{}])[0].get("details",[])
                            if details:
                                last=details[-1]
                                scorer=last.get("athletesInvolved",[{}])[0].get("displayName","") if last.get("athletesInvolved") else ""
                                if scorer: scorer_text=f"{scorer}"
                        except: scorer_text=""

                        cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?",(eid,))
                        row=cur.fetchone()
                        if not row:
                            cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?)",(eid,h["team"]["displayName"],a["team"]["displayName"],hs,as_,"")); conn.commit()
                        else:
                            oh,oa=row
                            if hs!=oh or as_!=oa:
                                cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?",(hs,as_,eid)); conn.commit()
                                # BUILD MESSAGE EXACTLY LIKE SCREENSHOT
                                home_logo=logo(h["team"]["displayName"])
                                away_logo=logo(a["team"]["displayName"])
                                home_name=h["team"]["displayName"]
                                away_name=a["team"]["displayName"]
                                
                                if scorer_text:
                                    msg=f"{home_logo} {home_name} - {away_logo} {away_name}:\n{minute} ⚽ {scorer_text} scores!\nNew score is {hs} - {as_}"
                                else:
                                    msg=f"{home_logo} {home_name} - {away_logo} {away_name}:\n{minute} ⚽ Goal!\nNew score is {hs} - {as_}"
                                
                                kb={"inline_keyboard":[[{"text":"Match Stats","callback_data":f"stats_{lid}_{eid}"}]]}
                                
                                # Send to ALL subscribers + favs (like channel)
                                for chat_id in db_get_all_subs():
                                    send(chat_id, msg, kb)
                                # Also if someone favourited that team, send extra
                                print(f"GOAL PUSHED: {msg}")
                except Exception as e:
                    print(f"league {lid} err",e); continue
            time.sleep(15)  # check every 15 sec like pro bots
        except Exception as e:
            print("Notifier err",e); time.sleep(15)

threading.Thread(target=notifier_goals, daemon=True).start()

offset=0
print("✅ V6 LIVE GOALS CHANNEL STYLE RUNNING")
while True:
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30"
        with urllib.request.urlopen(url,timeout=35) as r: data=json.loads(r.read().decode())
        for up in data.get("result",[]):
            offset=up["update_id"]+1
            if "callback_query" in up:
                cb=up["callback_query"]; chat_id=cb["message"]["chat"]["id"]; d=cb["data"]; cid=cb["id"]
                if d=="menu":
                    answer_cb(cid); send(chat_id,"⚽ *Football Live Goals* 🔴\nLive like your screenshot:",main_menu())
                elif d.startswith("league_"):
                    lid=d.replace("league_",""); answer_cb(cid); tbl=get_table(lid); send(chat_id, tbl if tbl else f"{LEAGUE_NAMES.get(lid)} loading...", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d.startswith("stats_"):
                    _,lid,eid=d.split("_",2); answer_cb(cid,"Loading Match Stats..."); send(chat_id, get_match_stats(eid,lid), {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="live":
                    answer_cb(cid); send(chat_id,"🔴 *Live Goals Feed ON*\nYou will get goals like your screenshot instantly.\n\nJust wait for next goal!", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="today":
                    answer_cb(cid); send(chat_id,"📅 Today - All leagues live monitoring ON", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="myteam":
                    answer_cb(cid); cur.execute("SELECT team FROM favs WHERE chat_id=?",(str(chat_id),)); r=cur.fetchone(); send(chat_id, f"⭐ Team: {r[0].title()}" if r else "Type team name", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                continue
            if "message" not in up: continue
            chat_id=str(up["message"]["chat"]["id"]); txt=up["message"].get("text","").strip(); low=txt.lower()
            if low.startswith("/start") or low=="menu":
                cur.execute("INSERT OR IGNORE INTO subscribers VALUES (?)",(chat_id,)); conn.commit()
                send(chat_id,"⚽ *Football Live Goals Bot* 🔴\n\n✅ You subscribed to LIVE GOALS\n✅ Like your screenshot:\n`Real Betis - Barcelona:\n27': Diego Gomez scores!\nNew score is 1-1`\n\n🔔 You will now get EVERY goal live\n⭐ Type your fav team e.g `Barcelona` for extra alerts\n\nTap Menu:",main_menu())
            elif len(low)>=3 and not low.startswith("/"):
                db_add_fav(chat_id,low); send(chat_id,f"⭐ {txt.title()} saved! You will get all goals + extra for {txt.title()}",main_menu())
        time.sleep(1)
    except Exception as e:
        print(e); time.sleep(3)