from flask import Flask
from threading import Thread
import os
app = Flask(__name__)
@app.route("/")
def home():
    return "Football Bot V12.3 OK"
def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), use_reloader=False)
Thread(target=run_web, daemon=True).start()

import urllib.request, urllib.parse, json, time, sqlite3, threading, io, uuid
from datetime import date
from PIL import Image, ImageDraw, ImageFont

TOKEN = os.environ.get("BOT_TOKEN")
conn = sqlite3.connect("football.db", check_same_thread=False)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS scores (event_id TEXT PRIMARY KEY, home TEXT, away TEXT, h_score INTEGER, a_score INTEGER)")
cur.execute("CREATE TABLE IF NOT EXISTS subs (chat_id TEXT PRIMARY KEY)")
conn.commit()

def db_get_all():
    cur.execute("SELECT chat_id FROM subs")
    return [x[0] for x in cur.fetchall()]

def send(chat_id, text, kb=None):
    url = "https://api.telegram.org/bot" + TOKEN + "/sendMessage"
    p = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if kb:
        p["reply_markup"] = json.dumps(kb)
    data = urllib.parse.urlencode(p).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    urllib.request.urlopen(req, timeout=12)

def send_photo(chat_id, photo_path, caption="", kb=None):
    boundary = uuid.uuid4().hex
    with open(photo_path, 'rb') as f:
        file_data = f.read()
    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="chat_id"\r\n\r\n' + str(chat_id) + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="caption"\r\n\r\n' + caption + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += 'Content-Disposition: form-data; name="parse_mode"\r\n\r\nMarkdown\r\n'.encode()
    if kb:
        body += ("--" + boundary + "\r\n").encode()
        body += ('Content-Disposition: form-data; name="reply_markup"\r\n\r\n' + json.dumps(kb) + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += 'Content-Disposition: form-data; name="photo"; filename="table.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
    body += file_data + b'\r\n'
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + TOKEN + "/sendPhoto", data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    urllib.request.urlopen(req, timeout=20)

def edit_photo(chat_id, msg_id, photo_path, caption="", kb=None):
    boundary = uuid.uuid4().hex
    with open(photo_path, 'rb') as f:
        file_data = f.read()
    media = {"type": "photo", "media": "attach://photo", "caption": caption, "parse_mode": "Markdown"}
    body = b""
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="chat_id"\r\n\r\n' + str(chat_id) + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="message_id"\r\n\r\n' + str(msg_id) + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += ('Content-Disposition: form-data; name="media"\r\n\r\n' + json.dumps(media) + "\r\n").encode()
    if kb:
        body += ("--" + boundary + "\r\n").encode()
        body += ('Content-Disposition: form-data; name="reply_markup"\r\n\r\n' + json.dumps(kb) + "\r\n").encode()
    body += ("--" + boundary + "\r\n").encode()
    body += 'Content-Disposition: form-data; name="photo"; filename="table.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
    body += file_data + b'\r\n'
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request("https://api.telegram.org/bot" + TOKEN + "/editMessageMedia", data=body, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    urllib.request.urlopen(req, timeout=20)

def edit_msg(chat_id, msg_id, text, kb=None):
    url = "https://api.telegram.org/bot" + TOKEN + "/editMessageText"
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "Markdown"}
    if kb:
        p["reply_markup"] = json.dumps(kb)
    data = urllib.parse.urlencode(p).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    urllib.request.urlopen(req, timeout=12)

def answer_cb(cid, txt=""):
    url = "https://api.telegram.org/bot" + TOKEN + "/answerCallbackQuery"
    p = {"callback_query_id": cid, "text": txt}
    data = urllib.parse.urlencode(p).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0"})
    urllib.request.urlopen(req, timeout=5)

def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except:
        return None

LEAGUE_NAMES = {"eng.1": "Premier League", "tur.1": "Super Lig Turkiye", "esp.1": "La Liga", "ger.1": "Bundesliga", "ita.1": "Serie A", "uefa.champions": "Champions League"}

def get_goal_detail(event_id, lid):
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/soccer/" + lid + "/summary?event=" + event_id
        d = fetch_json(url)
        if not d:
            return "", "Goal", ""
        commentary = d.get("commentary", []) or []
        for play in reversed(commentary[-70:]):
            txt = play.get("text","") + " " + play.get("type",{}).get("text","")
            txt_low = txt.lower()
            scorer = ""
            if play.get("athletesInvolved"):
                scorer = play["athletesInvolved"][0].get("displayName","")
            club = ""
            if play.get("team"):
                club = play["team"].get("displayName","")
            if "penalty" in txt_low and ("miss" in txt_low or "saved" in txt_low):
                return scorer, "Penalty Missed", club
            if "penalty" in txt_low and "goal" in txt_low:
                return scorer, "Penalty", club
            if "own goal" in txt_low:
                return scorer, "Own Goal", club
            if "goal" in txt_low and "disallowed" not in txt_low:
                return scorer, "Goal", club
        return "", "Goal", ""
    except:
        return "", "Goal", ""

def download_logo(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            img = Image.open(io.BytesIO(r.read())).convert("RGBA")
            return img.resize((44,44), Image.LANCZOS)
    except:
        return None

def generate_table_image(lid):
    endpoints = ["https://site.web.api.espn.com/apis/v2/sports/soccer/" + lid + "/standings", "https://site.api.espn.com/apis/site/v2/sports/soccer/" + lid + "/standings"]
    entries = None
    for url in endpoints:
        d = fetch_json(url)
        if not d:
            continue
        if "standings" in d and "entries" in d.get("standings", {}):
            entries = d["standings"]["entries"]
        elif "children" in d:
            try:
                entries = d["children"][0]["standings"]["entries"]
            except:
                pass
        if entries:
            break
    if not entries:
        return None
    live_map = {}
    today = date.today().strftime("%Y%m%d")
    sd = fetch_json("https://site.api.espn.com/apis/site/v2/sports/soccer/" + lid + "/scoreboard?dates=" + today)
    if sd:
        for ev in sd.get("events", []):
            if ev["status"]["type"]["state"] == "in":
                comp = ev["competitions"][0]
                comps = comp["competitors"]
                h = next((t for t in comps if t.get("homeAway") == "home"), comps[0])
                a = next((t for t in comps if t.get("homeAway") == "away"), comps[1])
                hs = int(h.get("score", 0) or 0)
                as_ = int(a.get("score", 0) or 0)
                live_map[h["team"]["displayName"].lower()] = {"score": str(hs) + "-" + str(as_), "pts": 3 if hs>as_ else 1 if hs==as_ else 0, "gd": hs-as_}
                live_map[a["team"]["displayName"].lower()] = {"score": str(as_) + "-" + str(hs), "pts": 3 if as_>hs else 1 if hs==as_ else 0, "gd": as_-hs}
    W, row_h, header_h = 760, 58, 95
    H = header_h + len(entries)*row_h + 15
    img = Image.new("RGB", (W, H), (18,18,18))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.rectangle([0,0,W,header_h], fill=(28,28,28))
    draw.text((20,12), LEAGUE_NAMES.get(lid,lid), font=font, fill=(255,255,255))
    draw.text((20,52), "Live", fill=(255,60,60))
    draw.text((445,52), "P", fill=(170,170,170))
    draw.text((515,52), "GD", fill=(170,170,170))
    draw.text((605,52), "Pts", fill=(255,255,255))
    y = header_h
    for i, e in enumerate(entries, 1):
        team = e.get("team", {})
        full = team.get("displayName","")
        short = team.get("shortDisplayName", full)[:20]
        logo_url = None
        if team.get("logos"):
            logo_url = team["logos"][0].get("href")
        stats = {s['name']: s.get('value', 0) for s in e.get("stats", [])}
        games = int(stats.get("gamesPlayed", 0))
        if games == 0:
            games = int(stats.get("wins", 0) + stats.get("ties", 0) + stats.get("losses", 0))
        gd = int(stats.get("pointDifferential", 0))
        pts = int(stats.get("points", 0))
        lm = live_map.get(full.lower())
        pts_live = pts + lm['pts'] if lm else pts
        gd_live = gd + lm['gd'] if lm else gd
        live_txt = lm['score'] if lm else ""
        bg = (28,28,38) if i%2==0 else (22,22,28)
        if lm:
            bg = (50,35,35)
        draw.rectangle([0,y,W,y+row_h], fill=bg)
        if i <= 2:
            draw.rectangle([0,y,6,y+row_h], fill=(60,120,255))
        elif i <= 4:
            draw.rectangle([0,y,6,y+row_h], fill=(100,220,130))
        draw.text((18, y+18), str(i), fill=(220,220,220))
        if logo_url:
            logo_img = download_logo(logo_url)
            if logo_img:
                img.paste(logo_img, (52, y+7), logo_img)
        draw.text((108, y+18), short, fill=(255,255,255))
        if lm:
            draw.rounded_rectangle([330, y+12, 405, y+44], radius=8, fill=(220,50,50))
            draw.text((342, y+16), live_txt, fill=(255,255,255))
        draw.text((452, y+18), str(games), fill=(210,210,210))
        draw.text((520, y+18), str(gd_live), fill=(210,210,210))
        draw.text((615, y+18), str(pts_live), fill=(255,255,255))
        y += row_h
    path = "/tmp/table_" + lid + ".png"
    img.save(path)
    return path

def get_matches(lid, only_live=False):
    today = date.today().strftime("%Y%m%d")
    d = fetch_json("https://site.api.espn.com/apis/site/v2/sports/soccer/" + lid + "/scoreboard?dates=" + today)
    if not d or not d.get("events"):
        return ""
    out = ""
    for ev in d["events"]:
        state = ev["status"]["type"]["state"]
        if only_live and state != "in":
            continue
        comp = ev["competitions"][0]
        teams = comp["competitors"]
        h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
        a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
        hs = h.get('score', '0')
        as_ = a.get('score', '0')
        det = ev["status"]["type"]["detail"]
        if state == "in":
            out = out + "LIVE " + h['team']['shortDisplayName'] + " " + str(hs) + "-" + str(as_) + " " + a['team']['shortDisplayName'] + " " + det + "\n"
        else:
            icon = "FT" if state == "post" else "TODAY"
            out = out + icon + " " + h['team']['shortDisplayName'] + " " + str(hs) + "-" + str(as_) + " " + a['team']['shortDisplayName'] + " " + det + "\n"
    return out

def main_menu():
    return {"inline_keyboard": [[{"text": "Super Lig", "callback_data": "league_tur.1"}, {"text": "EPL", "callback_data": "league_eng.1"}], [{"text": "Champions League", "callback_data": "league_uefa.champions"}, {"text": "La Liga", "callback_data": "league_esp.1"}], [{"text": "Live Now", "callback_data": "live"}, {"text": "Today", "callback_data": "today"}]]}

def league_tabs(lid):
    return {"inline_keyboard": [[{"text": "Table", "callback_data": "table_" + lid}, {"text": "Matches", "callback_data": "matches_" + lid}], [{"text": "Refresh Table", "callback_data": "refresh_table_" + lid}], [{"text": "Main Menu", "callback_data": "menu"}]]}

def notifier():
    print("Notifier ON")
    while True:
        try:
            today = date.today().strftime("%Y%m%d")
            for lid in ["esp.1", "eng.1", "tur.1", "ita.1", "ger.1", "uefa.champions"]:
                url = "https://site.api.espn.com/apis/site/v2/sports/soccer/" + lid + "/scoreboard?dates=" + today
                d = fetch_json(url)
                if not d:
                    continue
                for ev in d.get("events", []):
                    if ev["status"]["type"]["state"] != "in":
                        continue
                    eid = ev["id"]
                    comp = ev["competitions"][0]
                    teams = comp["competitors"]
                    h = next((t for t in teams if t.get("homeAway") == "home"), teams[0])
                    a = next((t for t in teams if t.get("homeAway") == "away"), teams[1])
                    hs = int(h.get("score", 0) or 0)
                    as_ = int(a.get("score", 0) or 0)
                    minute = ev["status"]["type"]["detail"]
                    cur.execute("SELECT h_score,a_score FROM scores WHERE event_id=?", (eid,))
                    row = cur.fetchone()
                    if not row:
                        cur.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?)", (eid, h["team"]["displayName"], a["team"]["displayName"], hs, as_))
                        conn.commit()
                    else:
                        oh, oa = row
                        if hs != oh or as_ != oa:
                            cur.execute("UPDATE scores SET h_score=?,a_score=? WHERE event_id=?", (hs, as_, eid))
                            conn.commit()
                            scorer, gtype, club = get_goal_detail(eid, lid)
                            if gtype == "Penalty Missed":
                                msg = "Penalty Missed! " + h["team"]["displayName"] + " " + str(hs) + "-" + str(as_) + " " + a["team"]["displayName"] + "\n" + minute + " " + club + " - " + scorer + " missed!"
                            elif gtype == "Penalty":
                                msg = "Penalty Goal! " + h["team"]["displayName"] + " " + str(hs) + "-" + str(as_) + " " + a["team"]["displayName"] + "\n" + minute + " " + club + " - " + scorer + " penalty"
                            elif gtype == "Own Goal":
                                msg = "Own Goal! " + h["team"]["displayName"] + " " + str(hs) + "-" + str(as_) + " " + a["team"]["displayName"] + "\n" + minute + " " + club + " - " + scorer + " OWN GOAL"
                            else:
                                msg = "Goal! " + h["team"]["displayName"] + " " + str(hs) + "-" + str(as_) + " " + a["team"]["displayName"] + "\n" + minute + " " + club + " - " + scorer + " scored"
                            for chat_id in db_get_all():
                                send(chat_id, msg)
            time.sleep(10)
        except Exception as e:
            print("notifier err", e)
            time.sleep(10)

threading.Thread(target=notifier, daemon=True).start()
offset = 0
print("V12.3 RUNNING")
while True:
    try:
        url = "https://api.telegram.org/bot" + TOKEN + "/getUpdates?offset=" + str(offset) + "&timeout=30"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=35) as r:
            data = json.loads(r.read().decode())
        for up in data.get("result", []):
            offset = up["update_id"] + 1
            if "callback_query" in up:
                cb = up["callback_query"]
                chat_id = cb["message"]["chat"]["id"]
                msg_id = cb["message"]["message_id"]
                d = cb["data"]
                cid = cb["id"]
                if d == "menu":
                    answer_cb(cid)
                    send(chat_id, "Football Hub - Choose league:", main_menu())
                elif d.startswith("league_") or d.startswith("table_"):
                    lid = d.replace("league_", "").replace("table_", "")
                    answer_cb(cid, "Generating table...")
                    path = generate_table_image(lid)
                    if path:
                        cap = LEAGUE_NAMES.get(lid) + " - FULL TABLE"
                        send_photo(chat_id, path, cap, league_tabs(lid))
                elif d.startswith("refresh_table_"):
                    lid = d.replace("refresh_table_", "")
                    answer_cb(cid, "Refreshing...")
                    path = generate_table_image(lid)
                    if path:
                        cap = LEAGUE_NAMES.get(lid) + " - FULL TABLE Refreshed"
                        edit_photo(chat_id, msg_id, path, cap, league_tabs(lid))
                elif d.startswith("refresh_matches_"):
                    lid = d.replace("refresh_matches_", "")
                    answer_cb(cid, "Refreshing...")
                    txt = get_matches(lid)
                    if txt == "":
                        txt = "No matches"
                    full = LEAGUE_NAMES.get(lid) + "\n\n" + txt
                    kb = {"inline_keyboard": [[{"text": "Refresh Matches", "callback_data": "refresh_matches_" + lid}], [{"text": "Table", "callback_data": "table_" + lid}], [{"text": "Menu", "callback_data": "menu"}]]}
                    edit_msg(chat_id, msg_id, full, kb)
                elif d.startswith("matches_"):
                    lid = d.replace("matches_", "")
                    answer_cb(cid, "Loading...")
                    txt = get_matches(lid)
                    if txt == "":
                        txt = "No matches"
                    full = LEAGUE_NAMES.get(lid) + "\n\n" + txt
                    kb = {"inline_keyboard": [[{"text": "Refresh Matches", "callback_data": "refresh_matches_" + lid}], [{"text": "Table", "callback_data": "table_" + lid}], [{"text": "Menu", "callback_data": "menu"}]]}
                    send(chat_id, full, kb)
                elif d == "live":
                    answer_cb(cid, "Checking live...")
                    live_txt = "Live Now\n\n"
                    found = False
                    for lid in ["tur.1", "eng.1", "esp.1", "ita.1", "ger.1", "uefa.champions"]:
                        m = get_matches(lid, only_live=True)
                        if m:
                            live_txt = live_txt + LEAGUE_NAMES.get(lid) + ":\n" + m + "\n"
                            found = True
                    if not found:
                        live_txt = live_txt + "No live games.\n"
                    kb = {"inline_keyboard": [[{"text": "Refresh Live", "callback_data": "refresh_live"}], [{"text": "Menu", "callback_data": "menu"}]]}
                    send(chat_id, live_txt[:3800], kb)
                elif d == "refresh_live":
                    answer_cb(cid, "Refreshing...")
                    live_txt = "Live Now\n\n"
                    found = False
                    for lid in ["tur.1", "eng.1", "esp.1", "ita.1", "ger.1", "uefa.champions"]:
                        m = get_matches(lid, only_live=True)
                        if m:
                            live_txt = live_txt + LEAGUE_NAMES.get(lid) + ":\n" + m + "\n"
                            found = True
                    if not found:
                        live_txt = live_txt + "No live games.\n"
                    kb = {"inline_keyboard": [[{"text": "Refresh Live", "callback_data": "refresh_live"}], [{"text": "Menu", "callback_data": "menu"}]]}
                    e