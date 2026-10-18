from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route('/')
def home(): return "Football Bot V11.6 REAL LOGOS"
def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading, io
from datetime import datetime, date
from PIL import Image, ImageDraw, ImageFont

TOKEN = os.environ.get("BOT_TOKEN")
DB = "football.db"
conn = sqlite3.connect(DB, check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS favs (chat_id TEXT, team TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS subs (chat_id TEXT PRIMARY KEY)")
conn.commit()

def db_add_fav(c, t):
    cur.execute("DELETE FROM favs WHERE chat_id=?", (str(c),))
    cur.execute("INSERT INTO favs VALUES (?,?)", (str(c), t.lower()))
    cur.execute("INSERT OR IGNORE INTO subs VALUES (?)", (str(c),))
    conn.commit()
def db_get_all():
    cur.execute("SELECT chat_id FROM subs")
    return [x[0] for x in cur.fetchall()]

def send(chat_id, text, kb=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        if kb: p["reply_markup"] = json.dumps(kb)
        req = urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(), headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=12)
    except Exception as e: print("send err", e)

def send_photo(chat_id, photo_path, caption="", kb=None):
    try:
        import requests
        url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
        with open(photo_path, 'rb') as f:
            files = {'photo': f}
            data = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
            if kb: data['reply_markup'] = json.dumps(kb)
            requests.post(url, data=data, files=files, timeout=15)
    except:
        # fallback urllib
        try:
            import http.client, mimetypes, uuid
            boundary = uuid.uuid4().hex
            with open(photo_path, 'rb') as f: file_data = f.read()
            body = b''
            body += f'--{boundary}\r\n'.encode()
            body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
            body += f'--{boundary}\r\n'.encode()
            body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
            if kb:
                body += f'--{boundary}\r\n'.encode()
                body += f'Content-Disposition: form-data; name="reply_markup"\r\n\r\n{json.dumps(kb)}\r\n'.encode()
            body += f'--{boundary}\r\n'.encode()
            body += f'Content-Disposition: form-data; name="photo"; filename="table.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
            body += file_data + b'\r\n'
            body += f'--{boundary}--\r\n'.encode()
            req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "Mozilla/5.0"})
            urllib.request.urlopen(req, timeout=15)
        except Exception as e: print("send_photo err", e)

def edit_msg(chat_id, msg_id, text, kb=None):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/editMessageText"
        p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
        if kb: p["reply_markup"] = json.dumps(kb)
        req = urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(), headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=12)
    except Exception as e: print("edit ignored:", e)

def answer_cb(cid, txt=""):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/answerCallbackQuery"
        p = {"callback_query_id": cid, "text": txt}
        req = urllib.request.Request(url, data=urllib.parse.urlencode(p).encode(), headers={"User-Agent": "Mozilla/5.0"})
        urllib.request.urlopen(req, timeout=5)
    except: pass

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except: return None

def wat(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{(dt.hour + 1) % 24:02d}:{dt.minute:02d} WAT"
    except: return ""

LEAGUE_NAMES = {"eng.1": "Premier League 🏴󠁧󠁢󠁥󠁮󠁧󠁿", "tur.1": "Süper Lig 🇹🇷", "esp.1": "La Liga 🇪🇸", "ger.1": "Bundesliga 🇩🇪", "ita.1": "Serie A 🇮🇹", "uefa.champions": "Champions League 🏆"}

def get_goal_detail(event_id, lid):
    try:
        d = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}")
        if not d: return "", "Goal", ""
        for play in reversed((d.get("commentary", []) or [])[-60:]):
            txt_low = (play.get("text","") + " " + play.get("type",{}).get("text","")).lower()
            scorer = play.get("athletesInvolved", [{}])[0].get("displayName","") if play.get("athletesInvolved") else ""
            club = play.get("team",{}).get("displayName","") if play.get("team") else ""
            if "penalty" in txt_low and ("miss" in txt_low or "saved" in txt_low):
                return scorer, "Penalty Missed", club
            if "penalty" in txt_low and "goal" in txt_low:
                return scorer, "Penalty", club
            if "own goal" in txt_low:
                return scorer, "Own Goal", club
            if "goal" in txt_low and "disallowed" not in txt_low:
                return scorer, "Goal", club
        return "", "Goal", ""
    except: return "", "Goal", ""

def download_logo(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGBA")
            return img.resize((42,42), Image.LANCZOS)
    except: return None

def generate_table_image(lid):
    endpoints = [f"https://site.web.api.espn.com/apis/v2/sports/soccer/{lid}/standings", f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/standings"]
    entries = None
    for url in endpoints:
        d = fetch_json(url)
        if not d: continue
        if "standings" in d and "entries" in d.get("standings", {}): entries = d["standings"]["entries"]
        elif "children" in d:
            try: entries = d["children"][0]["standings"]["entries"]
            except: pass
        if entries: break
    if not entries: return None

    live_map = {}
    today = date.today().strftime("%Y%m%d")
    sd = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
    if sd:
        for ev in sd.get("events", []):
            if ev["status"]["type"]["state"] == "in":
                comp = ev["competitions"][0]; comps = comp["competitors"]
                h = next((t for t in comps if t.get("homeAway") == "home"), comps[0])
                a = next((t for t in comps if t.get("homeAway") == "away"), comps[1])
                hs = int(h.get("score", 0) or 0); as_ = int(a.get("score", 0) or 0)
                live_map[h["team"]["displayName"].lower()] = {"score": f"{hs}-{as_}", "pts": 3 if hs>as_ else 1 if hs==as_ else 0, "gd": hs-as_, "live": True}
                live_map[a["team"]["displayName"].lower()] = {"score": f"{as_}-{hs}", "pts": 3 if as_>hs else 1 if hs==as_ else 0, "gd": as_-hs, "live": True}

    W, row_h, header_h = 720, 56, 90
    H = header_h + len(entries)*row_h + 20
    img = Image.new("RGB", (W, H), (18,18,18))
    draw = ImageDraw.Draw(img)
    try: font_bold = ImageFont.truetype("arial.ttf", 22)
    except: font_bold = ImageFont.load_default()
    try: font = ImageFont.truetype("arial.ttf", 20)
    except: font = ImageFont.load_default()
    try: font_small = ImageFont.truetype("arial.ttf", 16)
    except: font_small = ImageFont.load_default()

    # header
    draw.rectangle([0,0,W,header_h], fill=(30,30,30))
    draw.text((20,15), LEAGUE_NAMES.get(lid,lid), font=font_bold, fill=(255,255,255))
    draw.text((20,48), "Live", font=font, fill=(255,70,70))
    draw.text((420,48), "P", font=font, fill=(180,180,180))
    draw.text((490,48), "GD", font=font, fill=(180,180,180))
    draw.text((580,48), "Pts", font=font_bold, fill=(255,255,255))

    y = header_h
    for i, e in enumerate(entries, 1):
        team = e.get("team", {})
        full = team.get("displayName","")
        short = team.get("shortDisplayName", full)[:18]
        logo_url = team.get("logos", [{}])[0].get("href") if team.get("logos") else None
        if not logo_url: logo_url = e.get("team",{}).get("logos",[{}])[0].get("href") if e.get("team",{}).get("logos") else None

        stats = {s['name']: s.get('value', 0) for s in e.get("stats", [])}
        p = int(stats.get("gamesPlayed", 0) or stats.get("wins", 0) + stats.get("ties", 0) + stats.get("losses", 0))
        gd = int(stats.get("pointDifferential", 0)); pts = int(stats.get("points", 0))
        lm = live_map.get(full.lower())
        if lm:
            pts_live = pts + lm['pts']
            gd_live = gd + lm['gd']
            live_txt = lm['score']
        else:
            pts_live = pts; gd_live = gd; live_txt = ""

        # row bg
        bg = (28,28,35) if i%2==0 else (22,22,28)
        if lm: bg = (45,35,35)  # live highlight
        draw.rectangle([0,y,W,y+row_h], fill=bg)

        # position
        draw.text((20, y+16), str(i), font=font, fill=(200,200,200))

        # logo
        if logo_url:
            logo_img = download_logo(logo_url)
            if logo_img:
                img.paste(logo_img, (60, y+7), logo_img)

        # name
        draw.text((115, y+16), short, font=font, fill=(255,255,255))

        # live score box like screenshot
        if lm:
            draw.rounded_rectangle([300, y+10, 380, y+42], radius=6, fill=(220,50,50))
            draw.text((312, y+14), live_txt, font=font_small, fill=(255,255,255))

        # P GD Pts
        draw.text((425, y+16), str(p), font=font, fill=(220,220,220))
        draw.text((490, y+16), str(gd_live), font=font, fill=(220,220,220))
        draw.text((590, y+16), str(pts_live), font=font_bold, fill=(255,255,255))

        y += row_h

    path = f"/tmp/table_{lid}.png"
    img.save(path)
    return path

def get_matches(lid, only_live=False):
    today = date.today().strftime("%Y%m%d")
    d = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
    if not d or not d.get("events"): return ""
    out = ""
    for ev in d["events"]:
        state = ev["status"]["type"]["state"]
        if only_live and state != "in": continue
        comp = ev["competitions"][0]; teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        hs = h.get('score', '0'); as_ = a.get('score', '0'); det = ev["status"]["type"]["detail"]
        if state == "in": out += f"🔴 {h['team']['shortDisplayName']} {hs}-{as_} {a['team']['shortDisplayName']} • {det}\n"
        else:
            icon = "✅" if state == "post" else "🕒"
            out += f"{icon} {h['team']['shortDisplayName']} {hs}-{as_} {a['team']['shortDisplayName']} • {det}\n"
    return out

def get_stats(event_id, lid):
    d = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/summary?event={event_id}")
    if not d: return "Stats not yet available"
    try:
        comp = d["header"]["competitions"][0]; teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        txt = f"*{h['team']['displayName']} {h.get('score','0')}-{a.get('score','0')} {a['team']['displayName']}*\n{comp['status']['type']['detail']} • {wat(comp['date'])}\n\n"
        if comp.get("details"):
            txt += "*Goals:*\n"
            for det in comp.get("details",[])[:12]:
                if "goal" in det.get("type",{}).get("text","").lower():
                    ath = det.get('athletesInvolved',[{}])[0].get('displayName','')
                    txt += f"⚽ {det.get('clock',{}).get('displayValue','')} {ath}\n"
            txt += "\n"
        txt += "*Stats:*\n"
        for tm in d.get("boxscore", {}).get("teams", [])[:2]:
            tname = tm.get('team',{}).get('displayName','')
            txt += f"\n*{tname}*\n"
            for s in tm.get("statistics", []):
                if s['name'] in ["possessionPct","totalShots","shotsOnTarget","cornerKicks","foulsCommitted","yellowCards","redCards","offsides","saves"]:
                    txt += f"{s['name']}: {s['displayValue']}\n"
        if d.get("rosters"):
            txt += "\n*Lineups:*\n"
            for roster in d.get("rosters",[])[:2]:
                txt += f"\n*{roster.get('team',{}).get('displayName','')}*\n"
                lineup = ", ".join([p.get('athlete',{}).get('displayName','') for p in roster.get("roster",[])[:11]])
                txt += lineup[:800] + "\n"
        return txt[:3800]
    except: return "Stats loading..."

def main_menu():
    return {"inline_keyboard": [
        [{"text": "🇹🇷 Süper Lig", "callback_data": "league_tur.1"}, {"text": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL", "callback_data": "league_eng.1"}],
        [{"text": "🏆 Champions League", "callback_data": "league_uefa.champions"}, {"text": "🇪🇸 La Liga", "callback_data": "league_esp.1"}],
        [{"text": "🔴 Live Now", "callback_data": "live"}, {"text": "📅 Today", "callback_data": "today"}]
    ]}
def league_tabs(lid):
    return {"inline_keyboard": [
        [{"text": "📊 Table", "callback_data": f"table_{lid}"}, {"text": "⚽ Matches", "callback_data": f"matches_{lid}"}],
        [{"text": "🔄 Refresh Table", "callback_data": f"refresh_table_{lid}"}],
        [{"text": "⬅️ Main Menu", "callback_data": "menu"}]
    ]}

def notifier():
    print("🔴 Notifier V11.6 REAL LOGO ON")
    while True:
        try:
            today = date.today().strftime("%Y%m%d")
            for lid in ["esp.1", "eng.1", "tur.1", "ita.1", "ger.1", "uefa.champions"]:
                d = fetch_json(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={today}")
                if not d: continue
                for ev in d.get("events", []):
                    if ev["status"]["type"]["state"] != "in": continue
                    eid = ev["id"]; comp = ev["competitions"][0]; teams = comp["competitors"]
                    h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                    a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                    hs = int(h.get("score", 0) or 0); as_ = int(a.get("score", 0) or 0)
                    minute = ev["status"]["type"]["detail"]
                    cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?", (eid,)); row = cur.fetchone()
                    if not row:
                        cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)", (eid, h["team"]["displayName"], a["team"]["displayName"], hs, as_)); conn.commit()
                    else:
                        oh, oa = row
                        if hs != oh or as_ != oa:
                            cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?", (hs, as_, eid)); conn.commit()
                            scorer, gtype, club = get_goal_detail(eid, lid)
                            if gtype == "Penalty Missed":
                                msg = f"❌ *Penalty Missed!* {h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}\n{minute} {club} - {scorer} missed penalty!\n*Score stays {hs}-{as_}*"
                            elif gtype == "Penalty":
                                msg = f"🎯 *Penalty Goal!* {h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}\n{minute} {club} - {scorer} scored penalty!\n*New score {hs}-{as_}*"
                            elif gtype == "Own Goal":
                                msg = f"😱 *Own Goal!* {h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}\n{minute} {club} - {scorer} (OWN GOAL)\n*New score {hs}-{as_}*"
                            else:
                                msg = f"⚽ *Goal!* {h['team']['displayName']} {hs}-{as_} {a['team']['displayName']}\n{minute} {club} - {scorer} scored!\n*New score {hs}-{as_}*"
                            for chat_id in db_get_all(): send(chat_id, msg)
            time.sleep(10)
        except Exception as e:
            print("notifier err", e); time.sleep(10)

threading.Thread(target=notifier, daemon=True).start()

offset = 0
print("✅ V11.6 RUNNING - REAL LOGO IMAGE TABLE")
while True:
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=30", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=35) as r: data = json.loads(r.read().decode())
        for up in data.get("result", []):
            offset = up["update_id"] + 1
            if "callback_query" in up:
                cb = up["callback_query"]; chat_id = cb["message"]["chat"]["id"]; msg_id = cb["message"]["message_id"]; d = cb["data"]; cid = cb["id"]
                if d == "menu":
                    answer_cb(cid); send(chat_id, "⚽ *Football Hub*\nChoose league:", main_menu())
                elif d.startswith("league_") or d.startswith("table_"):
                    lid = d.replace("league_", "").replace("table_", "")
                    answer_cb(cid, "Generating real logo table...")
                    path = generate_table_image(lid)
                    if path: send_photo(chat_id, path, f"*{LEAGUE_NAMES.get(lid)} - FULL TABLE*", league_tabs(lid))
                    else: send(chat_id, "Table updating... try Refresh", league_tabs(lid))
                elif d.startswith("refresh_table_"):
                    lid = d.replace("refresh_table_", ""); answer_cb(cid, "Refreshing table image...")
                    path = generate_table_image(lid)
                    if path: send_photo(chat_id, path, f"*{LEAGUE_NAMES.get(lid)} - FULL TABLE* (Refreshed)", league_tabs(lid))
                    else: send(chat_id, "Table updating...", league_tabs(lid))
                elif d.startswith("matches_") and not d.startswith("refresh_matches_"):
                    lid = d.replace("matches_", ""); answer_cb(cid, "Loading Matches..."); txt = get_matches(lid); full = f"*{LEAGUE_NAMES.get(lid)} - Today*\n\n" + (txt or "No matches"); kb = {"inline_keyboard": [[{"text": "🔄 Refresh Matches", "callback_data": f"refresh_matches_{lid}"}], [{"text": "📊 Table", "callback_data": f"table_{lid}"}], [{"text": "⬅️ Menu", "callback_data": "menu"}]]}; send(chat_id, full, kb)
                elif d.startswith("refresh_matches_"):
                    lid = d.replace("refresh_matches_", ""); answer_cb(cid, "Refreshing..."); txt = get_matches(lid); full = f"*{LEAGUE_NAMES.get(lid)} - Today*\n\n" + (txt or "No matches"); kb = {"inline_keyboard": [[{"text": "🔄 Refresh Matches", "callback_data": f"refresh_matches_{lid}"}], [{"text": "📊 Table", "callback_data": f"table_{lid}"}], [{"text": "⬅️ Menu", "callback_data": "menu"}]]}; edit_msg(chat_id, msg_id, full, kb)
                elif d == "live":
                    answer_cb(cid, "Checking live..."); live_txt = "🔴 *Live Now*\n\n"; found=False
                    for lid in ["tur.1", "eng.1", "esp.1", "ita.1", "ger.1", "uefa.champions"]:
                        m = get_matches(lid, only_live=True)
                        if m: live_txt += f"*{LEAGUE_NAM