from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route('/')
def home(): return "Football Bot V9 - Phoenix Style + Refresh"
def run_web(): app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)), use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading, re
from datetime import datetime, date
TOKEN=os.environ.get("BOT_TOKEN")
DB="football.db"
conn=sqlite3.connect(DB, check_same_thread=False)
cur=conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS subs (chat_id TEXT PRIMARY KEY)")
conn.commit()

def db_add_fav(c,t):
    cur.execute("DELETE FROM favs WHERE chat_id=?",(str(c),)); cur.execute("INSERT INTO favs VALUES (?,?)",(str(c),t.lower()))
    cur.execute("INSERT OR IGNORE INTO subs VALUES (?)",(str(c),)); conn.commit()
def db_get_all(): cur.execute("SELECT chat_id FROM subs"); return [x[0] for x in cur.fetchall()]
def send(chat_id, text, kb=None):
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        p={"chat_id":chat_id,"text":text,"parse_mode":"Markdown"}
        if kb: p["reply_markup"]=json.dumps(kb)
        req=urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(), headers={"User-Agent":"Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=12)
    except Exception as e: print("send err",e)
def answer_cb(cid, txt=""):
    try:
        url=f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        p={"callback_query_id":cid,"text":txt}
        req=urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(), headers={"User-Agent":"Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=5)
    except: pass
def fetch_json(url):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
        with urllib.request.urlopen(req, timeout=15) as r: return json.loads(r.read().decode())
    except: return None
def wat(iso):
    try: dt=datetime.fromisoformat(iso.replace("Z","+00:00")); return f"{(dt.hour+1)%24:02d}:{dt.minute:02d} WAT"
    except: return ""

LEAGUE_NAMES={"eng.1":"Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿","tur.1":"Süper Lig 🇹🇷","esp.1":"La Liga 🇪🇸","ger.1":"Bundesliga 🇩🇪","ita.1":"Serie A 🇮🇹","uefa.champions":"Champions League 🏆"}
TEAM_LOGOS={"Galatasaray":"🦁","Fenerbahce":"💛","Fenerbahçe":"💛","Besiktas":"🦅","Beşiktaş":"🦅","Trabzonspor":"🔴","Arsenal":"🔴","Man City":"💙","Real Betis":"💚","Barcelona":"🔵🔴","Kasimpasa":"🔵","Kasımpaşa":"🔵","Samsunspor":"🔴"}

# NEW: Get scorer name + goal type like Phoenix
def get_last_goal_detail(event_id, lid, home_score, away_score):
    try:
        # Try play-by-play
        data=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/playbyplay?event={event_id}")
        if not data: 
            data=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}")
        scorer=""; gtype="⚽"
        if data:
            # Look in commentary
            plays=data.get("commentary",[]) or data.get("plays",[]) or data.get("page",{}).get("commentary",[]) or []
            # Find last scoring play
            for play in reversed(plays[-20:]):
                txt=(play.get("text","")+" "+play.get("shortText","")).lower()
                if "goal" in txt or "score" in txt or "penalty" in txt:
                    # extract player
                    athlete=play.get("athletesInvolved",[])
                    if athlete:
                        scorer=athlete[0].get("displayName","")
                    else:
                        # try regex from text
                        m=re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)?) \(.*goal", play.get("text",""))
                        if m: scorer=m.group(1)
                    if "penalty" in txt: gtype="🎯 Penalty"
                    elif "own goal" in txt or "own-goal" in txt: gtype="😱 Own Goal"
                    elif "header" in txt: gtype="⚽ Header"
                    else: gtype="⚽ Normal Goal"
                    break
        return scorer, gtype
    except Exception as e:
        print("scorer err",e)
        return "", "⚽ Goal"

def get_beautiful_table(lid):
    endpoints=[f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings",f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/standings"]
    entries=None
    for url in endpoints:
        d=fetch_json(url)
        if not d: continue
        if "standings" in d and "entries" in d.get("standings",{}): entries=d["standings"]["entries"]
        elif "children" in d:
            try: entries=d["children"][0]["standings"]["entries"]
            except: pass
        if entries: break
    if not entries: return f"*{LEAGUE_NAMES.get(lid,lid)}*\nTable updating... tap Refresh 🔄"

    # Live scores map with W/L
    live_scores={}
    try:
        today=date.today().strftime("%Y%m%d")
        sd=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
        if sd:
            for ev in sd.get("events",[]):
                if ev["status"]["type"]["state"]=="in":
                    comp=ev["competitions"][0]; h=comp["competitors"][0]; a=comp["competitors"][1]
                    hs=int(h.get("score",0) or 0); as_=int(a.get("score",0) or 0)
                    # For each team, store if winning/losing
                    if hs>as_: live_scores[h["team"]["displayName"].lower()]=(f"{hs}-{as_}","win"); live_scores[a["team"]["displayName"].lower()]=(f"{hs}-{as_}","lose")
                    elif as_>hs: live_scores[a["team"]["displayName"].lower()]=(f"{hs}-{as_}","win"); live_scores[h["team"]["displayName"].lower()]=(f"{hs}-{as_}","lose")
                    else: live_scores[h["team"]["displayName"].lower()]=(f"{hs}-{as_}","draw"); live_scores[a["team"]["displayName"].lower()]=(f"{hs}-{as_}","draw")
    except: pass

    title=LEAGUE_NAMES.get(lid,lid)
    txt=f"*{title}*\n"
    if "tur" in lid: txt+="Türkiye • 2026/2027\n"
    txt+="━━━━━━━━━━━━━━━━\n```\n"
    txt+=f"  # Team          Live   P  GD Pts\n"
    for i,e in enumerate(entries[:12],1):
        full=e.get("team",{}).get("displayName",""); short=e.get("team",{}).get("shortDisplayName",full)[:11]
        logo=TEAM_LOGOS.get(full, TEAM_LOGOS.get(short,"⚽"))
        stats={s['name']:s.get('value',0) for s in e.get("stats",[])}
        p=stats.get("gamesPlayed",0) or stats.get("wins",0)+stats.get("ties",0)+stats.get("losses",0)
        gd=stats.get("pointDifferential",0); pts=stats.get("points",0)
        score_info=live_scores.get(full.lower())
        if score_info:
            sc, res=score_info
            if res=="win": pill=f"🟩 {sc}"
            elif res=="lose": pill=f"🟥 {sc}"
            else: pill=f"🟨 {sc}"
        else: pill=""
        txt+=f"{i:2} {logo} {short:<11} {pill:<6} {p:2} {gd:3} {pts:3}\n"
    txt+="```\n"
    txt+=f"Updated {datetime.now().strftime('%H:%M')} WAT"
    return txt

def get_matches(lid, only_live=False):
    today=date.today().strftime("%Y%m%d")
    d=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
    if not d or not d.get("events"): return ""
    out=""
    for ev in d["events"]:
        state=ev["status"]["type"]["state"]
        if only_live and state!="in": continue
        comp=ev["competitions"][0]; teams=comp["competitors"]
        h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
        hs=h.get('score','0'); as_=a.get('score','0')
        det=ev["status"]["type"]["detail"]
        if state=="in": icon="🔴"
        elif state=="post": icon="✅"
        else: icon="🕒"
        # Phoenix style for live
        if state=="in":
            if int(hs or 0)>int(as_ or 0): out+=f"{icon} {h['team']['shortDisplayName']} 🟩 {hs}-{as_} {a['team']['shortDisplayName']} • {det} • {wat(ev['date'])}\n"
            elif int(as_ or 0)>int(hs or 0): out+=f"{icon} {a['team']['shortDisplayName']} 🟩 {as_}-{hs} {h['team']['shortDisplayName']} • {det} • {wat(ev['date'])}\n"
            else: out+=f"{icon} {h['team']['shortDisplayName']} 🟨 {hs}-{as_} {a['team']['shortDisplayName']} • {det} • {wat(ev['date'])}\n"
        else:
            out+=f"{icon} {h['team']['shortDisplayName']} {hs}-{as_} {a['team']['shortDisplayName']} • {det} • {wat(ev['date'])}\n"
    return out

def get_stats(event_id,lid):
    d=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}")
    if not d: return "Stats not yet available"
    try:
        comp=d["header"]["competitions"][0]; teams=comp["competitors"]
        h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
        txt=f"*Match Stats*\n{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}\n{comp['status']['type']['detail']}\n\n"
        for tm in d.get("boxscore",{}).get("teams",[])[:2]:
            txt+=f"*{tm.get('team',{}).get('displayName','')}*\n"
            for s in tm.get("statistics",[])[:6]: txt+=f"{s['name']}: {s['displayValue']}\n"
            txt+="\n"
        return txt[:3500]
    except: return "Stats loading..."

def main_menu():
    return {"inline_keyboard":[
        [{"text":"🇹🇷 Süper Lig","callback_data":"league_tur.1"},{"text":"🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL","callback_data":"league_eng.1"}],
        [{"text":"🏆 Champions League","callback_data":"league_uefa.champions"},{"text":"🇪🇸 La Liga","callback_data":"league_esp.1"}],
        [{"text":"🔴 Live Now","callback_data":"live"},{"text":"📅 Today","callback_data":"today"}],
        [{"text":"⭐ My Team","callback_data":"myteam"}]
    ]}
def league_tabs(lid):
    return {"inline_keyboard":[
        [{"text":"📊 Table","callback_data":f"table_{lid}"},{"text":"⚽ Matches","callback_data":f"matches_{lid}"}],
        [{"text":"🔄 Refresh Table","callback_data":f"refresh_table_{lid}"}],
        [{"text":"🏆 UCL","callback_data":"league_uefa.champions"},{"text":"🇹🇷 Süper Lig","callback_data":"league_tur.1"}],
        [{"text":"⬅️ Main Menu","callback_data":"menu"}]
    ]}

# NOTIFIER WITH SCORER + GOAL TYPE
def notifier():
    print("🔴 Live Goals Notifier V9 ON - with scorer")
    while True:
        try:
            today=date.today().strftime("%Y%m%d")
            for lid in ["esp.1","eng.1","tur.1","ita.1","ger.1","uefa.champions"]:
                d=fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
                if not d: continue
                for ev in d.get("events",[]):
                    if ev["status"]["type"]["state"]!="in": continue
                    eid=ev["id"]; comp=ev["competitions"][0]; teams=comp["competitors"]
                    h=next((t for t in teams if t.get("homeAway")=="home"),teams[0]); a=next((t for t in teams if t.get("homeAway")=="away"),teams[1])
                    hs=int(h.get("score",0) or 0); as_=int(a.get("score",0) or 0)
                    minute=ev["status"]["type"]["detail"]
                    cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?",(eid,)); row=cur.fetchone()
                    if not row:
                        cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)",(eid,h["team"]["displayName"],a["team"]["displayName"],hs,as_)); conn.commit()
                    else:
                        oh,oa=row
                        if hs!=oh or as_!=oa:
                            cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?",(hs,as_,eid)); conn.commit()
                            scorer, gtype=get_last_goal_detail(eid,lid,hs,as_)
                            # Build Phoenix-like notification
                            if scorer: scorer_line=f"{scorer} — {gtype}"
                            else: scorer_line=gtype
                            msg=f"⚽ *{h['team']['displayName']} - {a['team']['displayName']}*\n{minute} {scorer_line}\nNew score is *{hs} - {as_}*"
                            if "Penalty" in gtype: msg=f"🎯 *{h['team']['displayName']} - {a['team']['displayName']}*\n{minute} {scorer} — Penalty!\nNew score is *{hs} - {as_}*"
                            elif "Own Goal" in gtype: msg=f"😱 *{h['team']['displayName']} - {a['team']['displayName']}*\n{minute} {scorer} — Own Goal!\nNew score is *{hs} - {as_}*"
                            kb={"inline_keyboard":[[{"text":"Match Stats","callback_data":f"stats_{lid}_{eid}"}],[{"text":"🔄 Refresh Live","callback_data":"live"}]]}
                            for chat_id in db_get_all():
                                send(chat_id, msg, kb)
            time.sleep(12)
        except Exception as e:
            print("notifier err",e); time.sleep(12)

threading.Thread(target=notifier, daemon=True).start()
offset=0
print("✅ V9 PHOENIX STYLE RUNNING")
while True:
    try:
        req=urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30", headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=35) as r: data=json.loads(r.read().decode())
        for up in data.get("result",[]):
            offset=up["update_id"]+1
            if "callback_query" in up:
                cb=up["callback_query"]; chat_id=cb["message"]["chat"]["id"]; d=cb["data"]; cid=cb["id"]
                if d=="menu": answer_cb(cid); send(chat_id,"⚽ *Football Hub V9* - Phoenix Style ✨",main_menu())
                elif d.startswith("league_") or d.startswith("table_") or d.startswith("refresh_table_"):
                    lid=d.replace("league_","").replace("table_","").replace("refresh_table_",""); answer_cb(cid,"Refreshing..."); tbl=get_beautiful_table(lid); send(chat_id, tbl, league_tabs(lid))
                elif d.startswith("matches_") or d.startswith("refresh_matches_"):
                    lid=d.replace("matches_","").replace("refresh_matches_",""); answer_cb(cid,"Refreshing..."); txt=get_matches(lid); txt=f"*{LEAGUE_NAMES.get(lid)} - Today WAT*\n\n"+(txt or "No matches"); send(chat_id, txt, {"inline_keyboard":[[{"text":"🔄 Refresh Matches","callback_data":f"refresh_matches_{lid}"}],[{"text":"📊 Table","callback_data":f"table_{lid}"}],[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="live" or d=="refresh_live":
                    answer_cb(cid,"Checking live..."); live_txt="🔴 *Live Now + Auto Feed ON*\nYou will get goals auto\n\n"; found=False
                    for lid in ["tur.1","eng.1","esp.1","ita.1","ger.1","uefa.champions"]:
                        m=get_matches(lid, only_live=True)
                        if m: live_txt+=f"*{LEAGUE_NAMES.get(lid)}:*\n{m}\n"; found=True
                    if not found: live_txt+="😴 No games live RIGHT NOW.\nFeed ON - next goal auto!\n"
                    send(chat_id, live_txt[:3800], {"inline_keyboard":[[{"text":"🔄 Refresh Live","callback_data":"refresh_live"}],[{"text":"📅 Today","callback_data":"today"}],[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="today" or d=="refresh_today":
                    answer_cb(cid,"Loading today..."); full=""
                    for lid in ["tur.1","eng.1","esp.1","ger.1","ita.1","uefa.champions"]:
                        m=get_matches(lid)
                        if m: full+=f"*{LEAGUE_NAMES.get(lid)}:*\n{m}\n"
                    if not full: full="No matches today."
                    send(chat_id, full[:3800], {"inline_keyboard":[[{"text":"🔄 Refresh Today","callback_data":"refresh_today"}],[{"text":"🔴 Live Now","callback_data":"live"}],[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d.startswith("stats_"):
                    _,lid,eid=d.split("_",2); answer_cb(cid,"Loading Stats..."); send(chat_id, get_stats(eid,lid), {"inline_keyboard":[[{"text":"🔄 Refresh Stats","callback_data":d}],[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                elif d=="myteam": answer_cb(cid); cur.execute("SELECT team FROM favs WHERE chat_id=?",(str(chat_id),)); r=cur.fetchone(); send(chat_id, f"⭐ {r[0].title()}" if r else "Type team name", {"inline_keyboard":[[{"text":"⬅️ Menu","callback_data":"menu"}]]})
                continue
            if "message" not in up: continue
            chat_id=str(up["message"]["chat"]["id"]); txt=up["message"].get("text","").strip(); low=txt.lower()
            if low.startswith("/start") or low=="menu":
                cur.execute("INSERT OR IGNORE INTO subs VALUES (?)",(chat_id,)); conn.commit()
                send(chat_id,"⚽ *Football Live V9 PHOENIX* ✅\n\n✅ Table: 🟩 4-1 green win, 🟥 4-1 red lose like screenshot\n✅ 🔄 Refresh button under Table, Live, Today\n✅ Auto goal: scorer name + 🎯 Penalty / 😱 Own Goal / ⚽ Normal\n\nFor ALL TEAMS!",main_menu())
            elif len(low)>=3 and not low.startswith("/"):
                db_add_fav(chat_id,low); send(chat_id,f"⭐ {txt.title()} saved!",main_menu())
        time.sleep(1)
    except Exception as e:
        print("loop err",e); time.sleep(3)