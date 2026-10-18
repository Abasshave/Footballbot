from flask import Flask
import os
app = Flask(__name__)
@app.route('/')
def home(): return "OK"
import threading
threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000))), daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3
TOKEN=os.environ.get("BOT_TOKEN")
conn=sqlite3.connect("db.db", check_same_thread=False)
cur=conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS scores (id TEXT PRIMARY KEY, hs INT, ast INT)")
cur.execute("CREATE TABLE IF NOT EXISTS subs (id TEXT PRIMARY KEY)")
conn.commit()

def send(cid, txt):
    try:
        url="https://api.telegram.org/bot"+TOKEN+"/sendMessage"
        p={"chat_id":cid,"text":txt}
        d=urllib.parse.urlencode(p).encode()
        urllib.request.urlopen(urllib.request.Request(url,data=d),timeout=10)
    except: pass

def fetch(u):
    try:
        req=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.loads(r.read().decode())
    except: return None

def get_scorer(eid, lid):
    try:
        d=fetch("https://site.api.espn.com/apis/site/v2/sports/soccer/"+lid+"/summary?event="+eid)
        if not d: return "Goal"
        comm=d.get("commentary",[])[-20:]
        for c in reversed(comm):
            if "goal" in c.get("text","").lower():
                sc=""
                if c.get("athletesInvolved"): sc=c["athletesInvolved"][0].get("displayName","")
                return sc
        return "Goal"
    except: return "Goal"

off=0
while True:
    try:
        url="https://api.telegram.org/bot"+TOKEN+"/getUpdates?offset="+str(off)+"&timeout=20"
        req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req,timeout=25) as r:
            data=json.loads(r.read().decode())
        for up in data.get("result",[]):
            off=up["update_id"]+1
            if "message" not in up: continue
            cid=str(up["message"]["chat"]["id"])
            cur.execute("INSERT OR IGNORE INTO subs VALUES (?)",(cid,))
            conn.commit()
            send(cid,"Bot is LIVE now. V12.3 fixed - will show scorer.")
        time.sleep(2)
    except Exception as e:
        print(e)
        time.sleep(3)