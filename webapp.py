# webapp.py — เว็บแอปคุมการสร้างคอนเทนต์ + ตั้งเวลาโพสต์
# ใช้ AI pipeline เดิมจาก caption_bot (import — บอท Telegram ไม่สตาร์ท เพราะอยู่ใต้ __main__)
#
# รัน:  python3 -m uvicorn webapp:app --host 0.0.0.0 --port 8000
# เปิด: http://localhost:8000

import os
import json
import time
import hashlib
import secrets
import sqlite3
import threading
import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.concurrency import run_in_threadpool

import caption_bot as cb

HERE = os.path.dirname(__file__)
DB = os.path.join(HERE, "webapp.db")

app = FastAPI(title="AutoContentPoster")


def db_conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100000).hex()
    return f"{salt}${h}"


def verify_pw(pw, stored):
    try:
        salt, h = stored.split("$", 1)
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 100000).hex() == h
    except Exception:
        return False


SESSIONS = {}   # sid -> {"user":..,"role":..}  (อยู่ในหน่วยความจำ — รีสตาร์ทแล้วต้องล็อกอินใหม่)


def db_init():
    with db_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caption TEXT, image_path TEXT, destination TEXT,
            scheduled_ts REAL, status TEXT DEFAULT 'pending',
            created_ts REAL)""")
        try:
            c.execute("ALTER TABLE posts ADD COLUMN result_id TEXT")   # เก็บ id/ลิงก์โพสต์ปลายทาง
        except Exception:
            pass
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE, pw TEXT, role TEXT DEFAULT 'user', created_ts REAL)""")
        if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():   # สร้างแอดมินเริ่มต้น
            u = os.getenv("ADMIN_USER", "admin")
            p = os.getenv("ADMIN_PASS", "admin1234")
            c.execute("INSERT INTO users(username,pw,role,created_ts) VALUES(?,?,?,?)",
                      (u, hash_pw(p), "admin", time.time()))


db_init()


def current_user(request):
    return SESSIONS.get(request.cookies.get("sid"))


def deliver(post):
    # คืนค่า result_id (id โพสต์ปลายทาง ถ้ามี)
    if post["destination"] == "telegram":
        if post["image_path"] and os.path.exists(post["image_path"]):
            cb.send_photo(post["image_path"])
        cb.send_message(post["caption"])
        return None
    elif post["destination"].startswith("fb:"):
        pid = post["destination"][3:]
        page = next((p for p in cb.fb_pages() if str(p["id"]) == pid), None)
        if not page:
            raise RuntimeError("ไม่พบเพจ Facebook ที่เลือก")
        res = cb.fb_post(post["caption"], post["image_path"] or None, page)
        # เก็บลิงก์โพสต์จริง (permalink) ถ้ามี ไม่งั้นเก็บ id โพสต์
        return res.get("permalink_url") or res.get("post_id") or res.get("id")
    return None


def scheduler_loop():
    while True:
        try:
            now = time.time()
            with db_conn() as c:
                due = c.execute("SELECT * FROM posts WHERE status='pending' AND scheduled_ts<=?", (now,)).fetchall()
                for p in due:
                    try:
                        rid = deliver(p)
                        c.execute("UPDATE posts SET status='sent', result_id=? WHERE id=?", (rid, p["id"]))
                    except Exception as e:
                        print("⚠️ ส่งโพสต์ไม่สำเร็จ:", repr(e))
                        c.execute("UPDATE posts SET status='error' WHERE id=?", (p["id"],))
        except Exception as e:
            print("⚠️ scheduler error:", repr(e))
        time.sleep(15)


threading.Thread(target=scheduler_loop, daemon=True).start()


@app.post("/api/browse")
async def api_browse(req: Request):
    body = await req.json()
    query = body.get("query", "")
    with_image = bool(body.get("image", False))
    topics = await run_in_threadpool(cb.browse_news, query, with_image=with_image)
    return {"topics": topics}


@app.get("/api/fbstatus")
def api_fbstatus():
    return cb.fb_status()


@app.post("/api/hooks")
async def api_hooks(req: Request):
    d = await req.json()
    hooks = await run_in_threadpool(cb.gen_hooks, d.get("topic", ""), d.get("news"))
    return {"hooks": hooks}


@app.post("/api/captions")
async def api_captions(req: Request):
    d = await req.json()
    caps = await run_in_threadpool(cb.gen_captions, d.get("topic", ""), d.get("hook", ""), d.get("news"))
    return {"captions": caps}


@app.post("/api/images")
async def api_images(req: Request):
    d = await req.json()
    arts = await run_in_threadpool(cb.make_ai_images, d.get("hook", ""), d.get("caption", ""))
    return {"images": [{"url": "/img/" + os.path.basename(a["path"])} for a in arts]}


@app.get("/img/{name}")
def img(name):
    if not (name.startswith("art_") and name.endswith(".png")):
        return JSONResponse({"error": "not allowed"}, status_code=403)
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@app.post("/api/schedule")
async def api_schedule(req: Request):
    d = await req.json()
    img_name = os.path.basename(d.get("image_url", "").replace("/img/", "")) if d.get("image_url") else ""
    image_path = os.path.join(HERE, img_name) if img_name else ""
    when_ms = d.get("when_ms")
    ts = (when_ms / 1000.0) if when_ms else time.time()
    with db_conn() as c:
        c.execute("INSERT INTO posts(caption,image_path,destination,scheduled_ts,created_ts) VALUES(?,?,?,?,?)",
                  (d.get("caption", ""), image_path, d.get("destination", "telegram"), ts, time.time()))
    return {"ok": True}


@app.get("/api/scheduled")
def api_scheduled():
    with db_conn() as c:
        rows = c.execute("SELECT * FROM posts ORDER BY scheduled_ts DESC LIMIT 80").fetchall()
    return {"posts": [{"id": r["id"], "caption": (r["caption"] or "")[:120],
                       "when": datetime.datetime.fromtimestamp(r["scheduled_ts"]).strftime("%d/%m/%Y %H:%M"),
                       "ts_ms": int(r["scheduled_ts"] * 1000),
                       "destination": r["destination"], "status": r["status"],
                       "result_id": (r["result_id"] if "result_id" in r.keys() else None)} for r in rows]}


@app.delete("/api/scheduled/{pid}")
def api_cancel(pid: int):
    with db_conn() as c:
        c.execute("UPDATE posts SET status='cancelled' WHERE id=? AND status='pending'", (pid,))
    return {"ok": True}


TH_MONTH_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                 "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _parse_date(s, default):
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return default


def _range_window(sd, ed):
    # ช่วงวันที่ [sd, ed] (รวมปลาย) → (start_ts, end_ts, label, span_days)
    s = datetime.datetime.combine(sd, datetime.time.min)
    e = datetime.datetime.combine(ed, datetime.time.min) + datetime.timedelta(days=1)
    span = (ed - sd).days + 1
    if span <= 1:
        label = sd.strftime("%d/%m/") + str(sd.year + 543)
    else:
        label = (sd.strftime("%d/%m/") + str(sd.year + 543) + " – " +
                 ed.strftime("%d/%m/") + str(ed.year + 543))
    return s.timestamp(), e.timestamp(), label, span


def _bucket_chart(rows, sd, ed, span):
    # เลือกความละเอียดกราฟตามความยาวช่วง: <=1วัน=รายชั่วโมง, <=62วัน=รายวัน, มากกว่านั้น=รายเดือน
    if span <= 1:
        cnt = [0] * 24
        for r in rows:
            d = datetime.datetime.fromtimestamp(r["scheduled_ts"])
            if d.date() == sd:
                cnt[d.hour] += 1
        return [{"label": f"{h:02d}", "count": cnt[h]} for h in range(24)]
    if span <= 62:
        days = [sd + datetime.timedelta(days=i) for i in range(span)]
        counts = {}
        for r in rows:
            dd = datetime.datetime.fromtimestamp(r["scheduled_ts"]).date()
            counts[dd] = counts.get(dd, 0) + 1
        return [{"label": d.strftime("%d/%m"), "count": counts.get(d, 0)} for d in days]
    # รายเดือน
    months, y, m = [], sd.year, sd.month
    while (y < ed.year) or (y == ed.year and m <= ed.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    cnt = {k: 0 for k in months}
    for r in rows:
        d = datetime.datetime.fromtimestamp(r["scheduled_ts"])
        if (d.year, d.month) in cnt:
            cnt[(d.year, d.month)] += 1
    return [{"label": TH_MONTH_ABBR[mm - 1] + " " + str((yy + 543) % 100),
             "count": cnt[(yy, mm)]} for yy, mm in months]


def _match_page(dest, page):
    if page == "all":
        return True
    return (dest or "") == "fb:" + page


def _fetch_fb_posts(page, since, until, max_pages=8):
    # ดึงโพสต์จริง 'ทั้งหมด' บนเพจจาก Facebook ในช่วง [since,until] (ไล่ทุกหน้า/paging)
    # ต้องการสิทธิ์ pages_read_user_content — ไม่มีจะคืน available:False พร้อมเหตุผล
    import requests
    pgs = cb.fb_pages()
    if page != "all":
        pgs = [p for p in pgs if str(p["id"]) == page]
    posts, reason = [], None
    for pg in pgs:
        url = f"{cb.FB_API}/{pg['id']}/published_posts"
        params = {"fields": "created_time,permalink_url,message,"
                  "reactions.summary(true).limit(0),comments.summary(true).limit(0),shares",
                  "since": int(since), "until": int(until), "limit": 100, "access_token": pg["token"]}
        fetched = 0
        while url and fetched < max_pages:
            try:
                r = requests.get(url, params=params, timeout=25).json()
            except Exception as e:
                reason = str(e)
                break
            if "error" in r:
                reason = r["error"].get("message")
                break
            for d in r.get("data", []):
                ct = d.get("created_time", "")
                try:
                    ts = datetime.datetime.strptime(ct, "%Y-%m-%dT%H:%M:%S%z").timestamp()
                except Exception:
                    ts = 0
                react = d.get("reactions", {}).get("summary", {}).get("total_count", 0)
                comm = d.get("comments", {}).get("summary", {}).get("total_count", 0)
                shr = d.get("shares", {}).get("count", 0)
                posts.append({"page": pg["name"], "scheduled_ts": ts,
                              "message": (d.get("message") or "(ไม่มีข้อความ)")[:90],
                              "permalink": d.get("permalink_url", ""), "when": ct[:10],
                              "reactions": react, "comments": comm, "shares": shr,
                              "engagement": react + comm + shr})
            url = r.get("paging", {}).get("next")
            params = None     # next มี query ครบแล้ว
            fetched += 1
    if not posts and reason:
        return {"available": False, "reason": reason, "posts": []}
    return {"available": True, "posts": posts}


@app.get("/api/dashboard")
def api_dashboard(start: str = "", end: str = "", page: str = "all"):
    # สถิติภายใน — กรองตามช่วงวันที่ [start, end] และตามเพจ (page)
    today = datetime.date.today()
    sd = _parse_date(start, today.replace(day=1))
    ed = _parse_date(end, today)
    if ed < sd:
        sd, ed = ed, sd
    s_ts, e_ts, label, span = _range_window(sd, ed)
    with db_conn() as c:
        rows = c.execute("SELECT scheduled_ts,status,destination,caption FROM posts").fetchall()
        up = c.execute("SELECT id,caption,destination,scheduled_ts FROM posts "
                       "WHERE status='pending' ORDER BY scheduled_ts ASC LIMIT 8").fetchall()
    rows = [r for r in rows if _match_page(r["destination"], page)]      # กรองเพจก่อน
    inrange = [r for r in rows if s_ts <= r["scheduled_ts"] < e_ts]      # แล้วกรองช่วงเวลา
    by_status = {"sent": 0, "pending": 0, "cancelled": 0, "error": 0}
    by_dest = {"telegram": 0, "facebook": 0}
    for r in inrange:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_dest["facebook" if (r["destination"] or "").startswith("fb:") else "telegram"] += 1
    pending_all = sum(1 for r in rows if r["status"] == "pending")
    done = by_status["sent"] + by_status["error"]
    success_rate = round(by_status["sent"] * 100 / done) if done else 100
    daily_internal = _bucket_chart(inrange, sd, ed, span)
    upcoming = [{"id": r["id"], "caption": (r["caption"] or "")[:80],
                 "destination": r["destination"],
                 "when": datetime.datetime.fromtimestamp(r["scheduled_ts"]).strftime("%d/%m %H:%M"),
                 "ts_ms": int(r["scheduled_ts"] * 1000)}
                for r in up if _match_page(r["destination"], page)]
    # ----- ดึงโพสต์จริงทั้งหมดจากเพจบน Facebook (ไม่ใช่แค่ที่โพสต์ผ่านระบบนี้) -----
    fb = _fetch_fb_posts(page, s_ts, e_ts)
    if fb["available"]:
        fp = fb["posts"]
        daily = _bucket_chart(fp, sd, ed, span)
        eng = {"reactions": sum(p["reactions"] for p in fp),
               "comments": sum(p["comments"] for p in fp),
               "shares": sum(p["shares"] for p in fp)}
        top = sorted(fp, key=lambda x: x["engagement"], reverse=True)[:10]
        top = [{"page": p["page"], "message": p["message"], "permalink": p["permalink"],
                "when": p["when"], "reactions": p["reactions"],
                "comments": p["comments"], "shares": p["shares"]} for p in top]
        fb_block = {"available": True, "page_total": len(fp), "totals": eng, "top": top}
    else:
        daily = daily_internal
        fb_block = {"available": False, "reason": fb.get("reason")}
    return {"range_label": label, "span": span,
            "system_total": len(inrange), "by_status": by_status, "by_dest": by_dest,
            "pending_all": pending_all, "success_rate": success_rate,
            "daily": daily, "upcoming": upcoming, "fb": fb_block}


ENV_PATH = os.path.join(HERE, ".env")
_DISCOVERED = []   # เพจที่ค้นเจอล่าสุดจากการวาง token (ใช้ตอนกดบันทึก)


def _write_fb_pages(pages):
    # อัปเดต FB_PAGES ทั้งใน os.environ (ให้มีผลทันที) และในไฟล์ .env
    val = json.dumps(pages, ensure_ascii=False)
    os.environ["FB_PAGES"] = val
    line = "FB_PAGES='" + val + "'\n"
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    out, done = [], False
    for ln in lines:
        if ln.strip().startswith("FB_PAGES="):
            out.append(line)
            done = True
        else:
            out.append(ln)
    if not done:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(line)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(out)


@app.get("/api/connections")
def api_connections():
    st = cb.fb_status()           # {pages:[{id,name,connected,error}]}
    return {"pages": st["pages"], "app_id": os.getenv("FB_APP_ID", "")}


@app.post("/api/conn/remove")
async def api_conn_remove(req: Request):
    pid = str((await req.json()).get("id", ""))
    pages = [p for p in cb.fb_pages() if str(p["id"]) != pid]
    _write_fb_pages(pages)
    return {"ok": True, "remaining": len(pages)}


@app.post("/api/conn/discover")
async def api_conn_discover(req: Request):
    # วาง user token → (ถ้ามี app id+secret แลกเป็น token ถาวร) → ดึงเพจทั้งหมดในบัญชี
    import requests
    d = await req.json()
    token = (d.get("token") or "").strip()
    app_id = (d.get("app_id") or "").strip()
    app_secret = (d.get("app_secret") or "").strip()
    if not token:
        return {"ok": False, "error": "กรุณาวาง User Token ก่อน"}
    user_tok = token
    if app_id and app_secret:                 # แลกเป็น long-lived → token เพจถาวร
        try:
            r = await run_in_threadpool(lambda: requests.get(
                f"{cb.FB_API}/oauth/access_token",
                params={"grant_type": "fb_exchange_token", "client_id": app_id,
                        "client_secret": app_secret, "fb_exchange_token": token}, timeout=30).json())
            if "access_token" in r:
                user_tok = r["access_token"]
                if app_id:
                    os.environ["FB_APP_ID"] = app_id
        except Exception:
            pass

    def _fetch():
        pages, url = [], f"{cb.FB_API}/me/accounts"
        params = {"access_token": user_tok, "fields": "id,name,access_token", "limit": 100}
        while url:
            rr = requests.get(url, params=params, timeout=30).json()
            if "data" not in rr:
                return None, rr.get("error", {}).get("message", "ดึงเพจไม่สำเร็จ")
            for p in rr["data"]:
                pages.append({"id": p["id"], "name": p.get("name", "Facebook"), "token": p["access_token"]})
            url = rr.get("paging", {}).get("next")
            params = None
        return pages, None

    pages, err = await run_in_threadpool(_fetch)
    if err:
        return {"ok": False, "error": err}
    global _DISCOVERED
    _DISCOVERED = pages
    connected = {str(p["id"]) for p in cb.fb_pages()}
    return {"ok": True, "pages": [{"id": p["id"], "name": p["name"],
                                   "connected": str(p["id"]) in connected} for p in pages]}


@app.post("/api/conn/save")
async def api_conn_save(req: Request):
    ids = {str(i) for i in (await req.json()).get("ids", [])}
    chosen = [p for p in _DISCOVERED if str(p["id"]) in ids]      # เพจที่ติ๊กเลือก (token ใหม่)
    disc_ids = {str(p["id"]) for p in _DISCOVERED}
    kept = [p for p in cb.fb_pages() if str(p["id"]) not in disc_ids]   # เพจเดิมที่ไม่ได้อยู่ในการค้นครั้งนี้
    _write_fb_pages(kept + chosen)
    return {"ok": True, "count": len(kept + chosen)}


OPEN_API = {"/api/login", "/api/me"}   # เรียกได้โดยไม่ต้องล็อกอิน


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p not in OPEN_API and not current_user(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.post("/api/login")
async def api_login(req: Request):
    d = await req.json()
    u = (d.get("username") or "").strip()
    p = d.get("password") or ""
    with db_conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone()
    if not row or not verify_pw(p, row["pw"]):
        return JSONResponse({"ok": False, "error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"})
    sid = secrets.token_hex(16)
    SESSIONS[sid] = {"user": u, "role": row["role"]}
    resp = JSONResponse({"ok": True, "user": u, "role": row["role"]})
    resp.set_cookie("sid", sid, httponly=True, max_age=7 * 86400, samesite="lax")
    return resp


@app.post("/api/logout")
def api_logout(request: Request):
    SESSIONS.pop(request.cookies.get("sid"), None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("sid")
    return resp


@app.get("/api/me")
def api_me(request: Request):
    u = current_user(request)
    return {"authed": bool(u), "user": u["user"] if u else None, "role": u["role"] if u else None}


@app.get("/api/users")
def api_users(request: Request):
    if not (current_user(request) or {}).get("role") == "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    with db_conn() as c:
        rows = c.execute("SELECT username,role,created_ts FROM users ORDER BY created_ts").fetchall()
    return {"users": [{"username": r["username"], "role": r["role"],
                       "created": datetime.datetime.fromtimestamp(r["created_ts"]).strftime("%d/%m/%Y")} for r in rows]}


@app.post("/api/users/add")
async def api_users_add(request: Request):
    if not (current_user(request) or {}).get("role") == "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    u = (d.get("username") or "").strip()
    p = d.get("password") or ""
    role = d.get("role") if d.get("role") in ("admin", "user") else "user"
    if not u or not p:
        return {"ok": False, "error": "กรอกชื่อผู้ใช้และรหัสผ่านให้ครบ"}
    try:
        with db_conn() as c:
            c.execute("INSERT INTO users(username,pw,role,created_ts) VALUES(?,?,?,?)",
                      (u, hash_pw(p), role, time.time()))
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "มีชื่อผู้ใช้นี้อยู่แล้ว"}
    return {"ok": True}


@app.post("/api/users/delete")
async def api_users_delete(request: Request):
    if not (current_user(request) or {}).get("role") == "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    u = (await request.json()).get("username", "")
    with db_conn() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] <= 1:
            return {"ok": False, "error": "ต้องเหลือผู้ใช้อย่างน้อย 1 คน"}
        c.execute("DELETE FROM users WHERE username=?", (u,))
    # เตะ session ของคนที่ถูกลบออก
    for sid, s in list(SESSIONS.items()):
        if s["user"] == u:
            SESSIONS.pop(sid, None)
    return {"ok": True}


@app.post("/api/users/passwd")
async def api_users_passwd(request: Request):
    # เปลี่ยนรหัสผ่านตัวเอง
    cu = current_user(request)
    d = await request.json()
    new = d.get("password") or ""
    if len(new) < 4:
        return {"ok": False, "error": "รหัสผ่านอย่างน้อย 4 ตัวอักษร"}
    with db_conn() as c:
        c.execute("UPDATE users SET pw=? WHERE username=?", (hash_pw(new), cu["user"]))
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML_PAGE


HTML_PAGE = r"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoContentPoster</title>
<style>
:root{
  --navy:#0c2230; --navy2:#0f3a40; --blue:#0ea5e9; --blue-d:#0284c7;
  --red:#14b8a6; --bg:#eefcfb; --surface:#fff; --ink:#10282e; --muted:#5b8089; --line:#d9eef0;
  --ok:#10b981; --warn:#f59e0b; --err:#ef4444;
  --grad:linear-gradient(135deg,#06b6d4,#0ea5e9 45%,#10b981);
  --shadow:0 1px 2px rgba(10,60,70,.04),0 12px 30px rgba(15,110,120,.10); --radius:18px;
}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,sans-serif;background:
  radial-gradient(1100px 520px at 100% -5%,rgba(16,185,129,.12),transparent 60%),
  radial-gradient(900px 460px at -5% 8%,rgba(14,165,233,.12),transparent 55%),
  var(--bg);background-attachment:fixed;color:var(--ink)}
.app{display:flex;min-height:100vh}

/* sidebar */
.nav{width:248px;background:linear-gradient(180deg,var(--navy),var(--navy2));color:#dce6f5;
  padding:20px 14px;display:flex;flex-direction:column;gap:6px;position:sticky;top:0;height:100vh;
  border-right:1px solid rgba(255,255,255,.06)}
.brand{display:flex;align-items:center;gap:10px;padding:6px 8px 20px}
.brand .logo{width:38px;height:38px;border-radius:12px;background:var(--grad);
  display:flex;align-items:center;justify-content:center;font-size:18px;box-shadow:0 6px 18px rgba(16,185,129,.45)}
.brand b{font-size:15px;color:#fff;letter-spacing:.2px}
.navitem{display:flex;align-items:center;gap:11px;padding:12px 14px;border-radius:13px;cursor:pointer;
  font-size:14px;color:#aba7c8;transition:.18s}
.navitem:hover{background:rgba(255,255,255,.06);color:#fff}
.navitem.active{background:var(--grad);color:#fff;font-weight:600;box-shadow:0 8px 22px rgba(14,165,233,.45)}
.navitem .ic{font-size:17px}
.nav .foot{margin-top:auto;font-size:11px;color:#6f6b90;padding:8px}
.nav:not(.is-admin) #navUsers{display:none!important}   /* เมนูผู้ใช้เฉพาะแอดมิน */
.hamb{display:none;margin-left:auto;background:rgba(255,255,255,.1);border:0;color:#fff;font-size:20px;
  width:40px;height:40px;border-radius:11px;cursor:pointer;line-height:1}
.hamb:active{background:rgba(255,255,255,.2)}

/* content */
.content{flex:1;padding:26px 30px}   /* เต็มความกว้างจอทุกหน้า */
.content h1{font-size:21px;margin:0 0 4px}
.content .lead{color:var(--muted);font-size:13px;margin:0 0 20px}

/* ===== มือถือ: เมนูแฮมเบอร์เกอร์ ===== */
@media(max-width:820px){
  .app{flex-direction:column}
  .nav{width:auto;height:auto;position:sticky;top:0;z-index:60;flex-direction:column;
    gap:5px;padding:10px 14px;border-right:0;border-bottom:1px solid rgba(255,255,255,.08)}
  .brand{padding:2px 2px;margin:0}
  .brand b{font-size:16px}
  .hamb{display:flex;align-items:center;justify-content:center}
  .nav .navitem{display:none}            /* ซ่อนเมนูจนกว่าจะกด ☰ */
  .nav.open .navitem{display:flex}
  .nav .foot{display:none}
  .nav.open .foot{display:block;margin-top:6px}
  .navitem{padding:13px 14px;font-size:15px}
  .content{padding:18px 16px}
  .content h1{font-size:19px}
  .dash-controls,.cal-head{gap:10px}
}

/* steps */
.steps{display:flex;gap:6px;margin-bottom:18px;flex-wrap:wrap}
.steps .s{flex:1;min-width:84px;font-size:12px;color:var(--muted);padding:9px;border-radius:11px;
  background:var(--surface);border:1px solid var(--line);text-align:center}
.steps .s b{display:block;font-size:11px;opacity:.75}
.steps .s.on{background:var(--grad);color:#fff;border-color:transparent;box-shadow:0 6px 16px rgba(14,165,233,.30)}
.steps .s.done{border-color:var(--blue);color:var(--blue)}

.sec{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;
  margin-bottom:16px;box-shadow:var(--shadow);display:none}
.sec.on{display:block;animation:rise .25s ease}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1}}
.sec h2{font-size:15px;margin:0 0 14px;display:flex;align-items:center;gap:8px}
.sec h2 .n{width:24px;height:24px;border-radius:50%;background:var(--blue);color:#fff;font-size:13px;
  display:flex;align-items:center;justify-content:center}

label{font-size:12px;color:var(--muted);display:block;margin:10px 0 4px}
input,textarea,select{width:100%;padding:12px 14px;border:1px solid var(--line);border-radius:12px;
  font-size:14px;font-family:inherit;background:#f8fafd;color:var(--ink)}
input:focus,select:focus{outline:0;border-color:var(--blue);background:#fff}

.btn{background:var(--grad);background-size:160% 160%;color:#fff;border:0;border-radius:13px;padding:12px 20px;
  font-size:14px;font-weight:600;cursor:pointer;transition:.18s;box-shadow:0 8px 20px rgba(14,165,233,.30)}
.btn:hover{filter:brightness(1.06);transform:translateY(-1px);box-shadow:0 12px 26px rgba(14,165,233,.40)}
.btn.ghost{background:#fff;color:var(--blue-d);border:1.5px solid var(--line);box-shadow:var(--shadow)}
.btn.ghost:hover{border-color:var(--blue);filter:none}
.btn.red{background:linear-gradient(135deg,#fb7185,#f43f5e);box-shadow:0 8px 20px rgba(244,63,94,.30)} .btn.block{width:100%}
.btnrow{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}

.cat{font-size:11px;font-weight:700;color:var(--red);text-transform:uppercase;letter-spacing:.5px;margin:16px 0 6px}
.opt{border:1.5px solid var(--line);border-radius:12px;padding:13px 15px;margin:8px 0;cursor:pointer;
  transition:.15s;position:relative;background:#fff}
.opt:hover{border-color:var(--blue);transform:translateX(2px)}
.opt.sel{border-color:var(--blue);background:#e3f8fb;box-shadow:0 0 0 1px var(--blue)}
.opt.sel::after{content:"✓";position:absolute;top:10px;right:14px;color:var(--blue);font-weight:700}
.opt pre{white-space:pre-wrap;font-family:inherit;margin:0;font-size:14px}
.opt .sub{color:var(--muted);font-size:12px;margin-top:4px}

.imgs{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.imgs img{width:100%;border-radius:14px;cursor:pointer;border:3px solid transparent;transition:.15s}
.imgs img.sel{border-color:var(--blue);box-shadow:var(--shadow)}

/* loading bar */
.loadwrap{display:flex;align-items:center;gap:10px;color:var(--blue);font-size:13px;margin:6px 0}
.loadbar{height:7px;background:var(--line);border-radius:7px;overflow:hidden;position:relative;flex:1;min-width:120px}
.loadbar>i{position:absolute;left:-40%;width:40%;height:100%;border-radius:7px;
  background:linear-gradient(90deg,var(--blue),var(--red));animation:slide 1.05s infinite}
@keyframes slide{0%{left:-40%}100%{left:100%}}

/* schedule table */
.tbl{width:100%;border-collapse:collapse;background:var(--surface);border-radius:var(--radius);
  overflow:hidden;box-shadow:var(--shadow)}
.tbl th{background:var(--navy);color:#dce6f5;font-size:12px;text-align:left;padding:12px 14px;font-weight:600}
.tbl td{border-top:1px solid var(--line);padding:12px 14px;font-size:13px;vertical-align:top}
.tbl tr:hover td{background:#f6f9fe}
.badge{font-size:11px;padding:3px 11px;border-radius:20px;font-weight:600;white-space:nowrap}
.b-sent{background:#e6f6ee;color:var(--ok)} .b-error{background:#fdeaea;color:var(--err)} .b-cancelled{background:#eef0f4;color:#8893a5}
.miniload{display:flex;align-items:center;gap:7px;color:var(--warn);font-size:12px;font-weight:600}
.miniload .bar{height:6px;width:64px;background:#f3e6cf;border-radius:6px;overflow:hidden;position:relative}
.miniload .bar>i{position:absolute;left:-40%;width:40%;height:100%;background:var(--warn);border-radius:6px;animation:slide 1.05s infinite}
.empty{color:var(--muted);font-size:13px;text-align:center;padding:30px}
.linkbtn{color:var(--red);cursor:pointer;font-size:12px;font-weight:600}

/* calendar (ตารางการโพสต์) */
.cal{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.cal-head{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:16px 18px;border-bottom:1px solid var(--line)}
.cal-nav{display:flex;gap:6px}
.cal-btn{background:#fff;border:1.5px solid var(--line);border-radius:10px;padding:7px 13px;font-size:14px;font-weight:600;
  color:var(--ink);cursor:pointer;transition:.15s}
.cal-btn:hover{border-color:var(--blue);color:var(--blue-d)}
.cal-title{font-size:18px;font-weight:700;color:var(--navy);flex:1}
.cal-legend{display:flex;gap:14px;font-size:12px;color:var(--muted);flex-wrap:wrap}
.cal-legend span{display:flex;align-items:center;gap:5px}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.d-pending{background:var(--warn)} .d-sent{background:var(--blue)} .d-cancelled{background:#9aa5b5} .d-error{background:var(--err)}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr)}
.cal-dow{background:#f6f9fb;border-bottom:1px solid var(--line)}
.cal-dow div{padding:9px 6px;text-align:center;font-size:12px;font-weight:700;color:var(--muted)}
.cal-cell{min-height:104px;border-right:1px solid var(--line);border-bottom:1px solid var(--line);padding:6px 6px 8px;
  display:flex;flex-direction:column;gap:3px;cursor:pointer;transition:.12s;overflow:hidden}
.cal-cell:nth-child(7n){border-right:0}
.cal-cell:hover{background:#f3fbfc}
.cal-cell.other{background:#fafbfc;color:#c2cad6}
.cal-cell.other .cal-day{color:#c2cad6}
.cal-day{font-size:13px;font-weight:600;color:var(--ink);align-self:flex-start;line-height:1;
  width:24px;height:24px;display:flex;align-items:center;justify-content:center;border-radius:50%}
.cal-cell.today .cal-day{background:var(--grad);color:#fff}
.ev{display:flex;align-items:center;gap:5px;font-size:11px;line-height:1.3;border-radius:6px;padding:3px 6px;
  background:#eef4f7;border-left:3px solid var(--blue);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ev b{font-weight:700;flex:0 0 auto}
.ev.s-pending{border-left-color:var(--warn);background:#fff7ec}
.ev.s-sent{border-left-color:var(--blue);background:#eaf5fb}
.ev.s-cancelled{border-left-color:#9aa5b5;background:#f0f2f5;color:#8893a5;text-decoration:line-through}
.ev.s-error{border-left-color:var(--err);background:#fdecec}
.ev .et{font-weight:700}
.ev-more{font-size:11px;color:var(--blue-d);font-weight:600;padding:1px 6px}
/* day detail panel */
.day-detail{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);
  margin-top:16px;padding:18px}
.day-detail h3{margin:0 0 12px;font-size:16px;color:var(--navy)}
.dd-row{display:flex;align-items:flex-start;gap:12px;padding:11px 0;border-top:1px solid var(--line)}
.dd-row:first-of-type{border-top:0}
.dd-time{font-weight:700;font-size:14px;color:var(--blue-d);flex:0 0 52px}
.dd-body{flex:1;min-width:0}
.dd-cap{font-size:13px;color:var(--ink);white-space:pre-wrap;max-height:60px;overflow:hidden}
.dd-meta{display:flex;align-items:center;gap:10px;margin-top:4px;font-size:12px;color:var(--muted);flex-wrap:wrap}

/* dashboard */
.dash-controls{display:flex;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.dash-controls .chips{margin:0}
.dash-filter{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow)}
.filter-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.filter-row input[type=date]{width:auto;min-width:150px}
.dash-pagewrap{min-width:220px}
.dash-pagewrap select{min-width:220px}
.dash-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:18px}
.scard{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:16px 18px;box-shadow:var(--shadow);
  position:relative;overflow:hidden}
.scard::before{content:"";position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--grad)}
.scard .sv{font-size:30px;font-weight:800;color:var(--navy);line-height:1.1}
.scard .sl{font-size:12.5px;color:var(--muted);margin-top:3px}
.scard .si{position:absolute;right:14px;top:14px;font-size:20px;opacity:.5}
.dash-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:900px){.dash-grid{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow);margin-bottom:16px}
.panel h3{margin:0 0 14px;font-size:15px;color:var(--navy)}
/* bar chart */
.bars{display:flex;align-items:flex-end;gap:3px;height:150px;border-bottom:2px solid var(--line);padding-bottom:0}
.bar{flex:1;background:var(--grad);border-radius:4px 4px 0 0;min-height:2px;position:relative;transition:.15s}
.bar:hover{filter:brightness(1.1)}
.bar .bt{position:absolute;top:-18px;left:50%;transform:translateX(-50%);font-size:10px;font-weight:700;color:var(--blue-d);opacity:0}
.bar:hover .bt{opacity:1}
.bars-x{display:flex;gap:3px;margin-top:5px}
.bars-x span{flex:1;text-align:center;font-size:9px;color:var(--muted);overflow:hidden;white-space:nowrap}
/* engagement leaderboard */
.lead-row{display:flex;gap:12px;align-items:center;padding:10px 0;border-top:1px solid var(--line)}
.lead-row:first-child{border-top:0}
.lead-rank{font-size:16px;font-weight:800;color:var(--blue);flex:0 0 26px;text-align:center}
.lead-body{flex:1;min-width:0}
.lead-msg{font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lead-stats{font-size:12px;color:var(--muted);margin-top:2px;display:flex;gap:12px}
.dash-note{background:#fff7ec;border:1px solid #f3d9a6;border-radius:12px;padding:14px 16px;font-size:13px;color:#8a5a00;line-height:1.6}
.dash-note code{background:#fff;border:1px solid var(--line);border-radius:6px;padding:1px 6px;font-size:12px}
.donut{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.donut .leg{display:flex;flex-direction:column;gap:6px;font-size:13px}
.donut .leg span{display:flex;align-items:center;gap:7px}
.donut .leg i{width:11px;height:11px;border-radius:3px;display:inline-block}

/* connection page */
.conn-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.conn-card{display:flex;align-items:center;gap:14px;background:var(--surface);border:1px solid var(--line);
  border-radius:16px;padding:15px 16px;box-shadow:var(--shadow);transition:.15s}
.conn-card:hover{box-shadow:0 12px 28px rgba(15,110,120,.14)}
.conn-ic{width:46px;height:46px;border-radius:13px;display:flex;align-items:center;justify-content:center;
  font-size:22px;background:#e3f8fb;flex:0 0 auto}
.conn-ic.err{background:#fdecec}
.conn-info{flex:1;min-width:0}
.conn-name{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conn-id{font-size:11px;color:var(--muted)}
.conn-st{font-size:12px;font-weight:600;margin-top:3px}
.conn-st.on{color:var(--ok)} .conn-st.off{color:var(--err)}
.conn-x{background:#fff;border:1.5px solid var(--line);color:var(--err);border-radius:10px;padding:8px 13px;
  font-size:12px;font-weight:700;cursor:pointer;flex:0 0 auto;transition:.15s}
.conn-x:hover{border-color:var(--err);background:#fdecec}
.conn-form{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:600px){.conn-form{grid-template-columns:1fr}}
.conn-pick{display:flex;align-items:center;gap:9px;padding:11px 13px;border:1.5px solid var(--line);
  border-radius:11px;margin:7px 0;cursor:pointer;font-size:13px;transition:.12s}
.conn-pick:hover{border-color:var(--blue)}
.conn-pick input{width:auto;margin:0}
.conn-pick .conn-id{margin-left:auto}

/* login overlay */
.login-ov{position:fixed;inset:0;z-index:999;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#0c2230,#0f3a40)}
.login-box{background:#fff;border-radius:20px;padding:34px 30px;width:340px;max-width:90vw;box-shadow:0 24px 60px rgba(0,0,0,.35)}
.login-box .logo{width:54px;height:54px;border-radius:15px;background:var(--grad);display:flex;align-items:center;
  justify-content:center;font-size:26px;margin:0 auto 14px;box-shadow:0 8px 22px rgba(16,185,129,.45)}
.login-box h2{text-align:center;margin:0 0 4px;font-size:19px}
.login-box p{text-align:center;color:var(--muted);font-size:13px;margin:0 0 18px}
.login-box label{margin-top:12px}
.login-box .btn{width:100%;margin-top:18px;justify-content:center}
.login-err{color:var(--err);font-size:13px;text-align:center;margin-top:10px;min-height:18px}
/* user management */
.usr-row{display:flex;align-items:center;gap:12px;padding:12px 0;border-top:1px solid var(--line)}
.usr-row:first-of-type{border-top:0}
.usr-av{width:40px;height:40px;border-radius:50%;background:var(--grad);color:#fff;display:flex;align-items:center;
  justify-content:center;font-weight:700;flex:0 0 auto}
.usr-info{flex:1;min-width:0}
.usr-name{font-weight:700;font-size:14px}
.usr-role{font-size:12px;font-weight:600;padding:2px 9px;border-radius:20px;display:inline-block;margin-top:2px}
.usr-role.admin{background:#e3f8fb;color:var(--blue-d)} .usr-role.user{background:#eef0f4;color:#6b7280}
.nav .who{font-size:12px;color:#cfe9ec;padding:6px 8px;border-top:1px solid rgba(255,255,255,.08);margin-top:6px}
.nav .who b{color:#fff}
.logout-btn{display:block;width:100%;margin-top:8px;background:rgba(255,255,255,.08);color:#fff;border:0;
  border-radius:10px;padding:9px;font-size:13px;cursor:pointer;font-weight:600}
.logout-btn:hover{background:rgba(255,255,255,.16)}

/* preview card */
.preview{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff;margin-bottom:16px}
.preview img{width:100%;display:block;max-height:340px;object-fit:cover}
.preview .pcap{padding:14px 16px}
.preview .pcap pre{white-space:pre-wrap;font-family:inherit;margin:0;font-size:14px;line-height:1.55}
.preview .pdest{padding:8px 16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);background:#f8fafd}
.preview .ph{padding:24px;text-align:center;color:var(--muted);font-size:13px}

/* time chips */
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}
.chip{background:#f1f5fb;border:1.5px solid var(--line);color:var(--ink);border-radius:22px;padding:9px 15px;
  font-size:13px;cursor:pointer;transition:.15s}
.chip:hover{border-color:var(--blue)}
.chip.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.whenlabel{margin:10px 0;font-size:14px;font-weight:600;color:var(--blue-d);background:#e3f8fb;
  border-radius:10px;padding:10px 14px}

/* two option boxes in step 1 */
.obox{border:1.5px solid var(--line);border-radius:14px;padding:16px;background:#fbfcff}
.obox h4{margin:0 0 2px;font-size:14px}
.obox .obsub{margin:0 0 10px;font-size:12px;color:var(--muted)}
.divider{display:flex;align-items:center;gap:12px;margin:14px 0;color:var(--muted);font-size:12px}
.divider::before,.divider::after{content:"";flex:1;height:1px;background:var(--line)}
.fbnote{font-size:12px;color:var(--muted);margin:6px 0 2px}

/* recap chips (สิ่งที่เลือกไว้) */
.recap{display:flex;flex-direction:column;gap:6px;margin-bottom:14px}
.recap:empty{display:none}
.recap .rc{background:#fff;border:1px solid var(--line);border-left:4px solid var(--blue);border-radius:10px;
  padding:8px 12px;font-size:13px;box-shadow:var(--shadow)}
.recap .rc b{color:var(--blue);font-size:11px;display:block;margin-bottom:2px}

/* phone mockups (พรีวิวเหมือนในมือถือ) */
.phones{display:flex;gap:20px;flex-wrap:wrap;justify-content:center}
.pwrap{text-align:center}
.plabel{font-size:12px;color:var(--muted);margin-bottom:8px;font-weight:600}
.phone{width:300px;background:#0e1116;border-radius:36px;padding:11px;box-shadow:0 12px 34px rgba(20,30,60,.22)}
.notch{width:120px;height:20px;background:#0e1116;border-radius:0 0 14px 14px;margin:-11px auto 0;position:relative;z-index:2}
.pscreen{background:#eef1f5;border-radius:26px;overflow:hidden;max-height:560px;overflow-y:auto}
.fbtop{background:#1877f2;color:#fff;font-weight:700;padding:9px 14px;font-size:15px}
.fbpost{background:#fff;margin:8px;border-radius:10px;overflow:hidden;font-size:13px;color:#1c1e21}
.fbhead{display:flex;gap:8px;align-items:center;padding:10px 12px}
.fbavatar{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,var(--blue),var(--red));
  color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;flex:0 0 auto}
.fbname{font-weight:700}
.fbtime{font-size:11px;color:#8a8d91}
.fbcap{padding:0 12px 10px;line-height:1.55;white-space:normal}
.fbcap.short{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.fbimg img{width:100%;display:block}
.fbbar{display:flex;justify-content:space-around;padding:9px 0;border-top:1px solid #eef0f2;color:#65676b;font-size:12px}

/* news cards (social listening) */
.news-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:16px;margin-top:14px}
.ncard{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;
  display:flex;flex-direction:column;transition:.18s;box-shadow:var(--shadow)}
.ncard:hover{transform:translateY(-3px);border-color:var(--blue);box-shadow:0 14px 30px rgba(15,110,120,.16)}
.nthumb{position:relative;aspect-ratio:16/10;background:#e8eef0;display:flex;align-items:center;justify-content:center;overflow:hidden}
.nthumb img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.nthumb.ph{background:var(--g)}
.nemo{font-size:38px;filter:drop-shadow(0 2px 5px rgba(0,0,0,.25))}
.ncat{position:absolute;top:8px;left:8px;background:rgba(255,255,255,.92);color:var(--blue-d);
  font-size:10px;font-weight:700;padding:3px 9px;border-radius:20px;backdrop-filter:blur(4px);z-index:2}
.nbody{padding:11px 12px 12px;display:flex;flex-direction:column;gap:6px;flex:1}
.ntitle{font-size:13px;font-weight:600;line-height:1.4;color:var(--ink);
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.nsum{font-size:11.5px;color:var(--muted);line-height:1.45;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.nmeta{display:flex;align-items:center;gap:6px;padding-top:2px}
.nsrc{font-size:10.5px;color:var(--blue-d);font-weight:600;background:#e3f8fb;padding:2px 8px;border-radius:8px;
  max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ncard-btns{margin-top:auto;display:flex;gap:7px;padding-top:8px}
.nbtn{flex:1;text-align:center;font-size:11.5px;font-weight:700;padding:8px 6px;border-radius:9px;
  cursor:pointer;border:0;transition:.15s;text-decoration:none;display:block}
.nbtn.read{background:#eef3f5;color:var(--blue-d);border:1px solid var(--line)}
.nbtn.read:hover{background:#e3f8fb;border-color:var(--blue)}
.nbtn.make{background:var(--grad);color:#fff;box-shadow:0 5px 14px rgba(14,165,233,.28)}
.nbtn.make:hover{filter:brightness(1.06)}
</style></head><body>
<div id="loginOv" class="login-ov" style="display:none">
  <div class="login-box">
    <div class="logo">📋</div>
    <h2>AutoContentPoster</h2>
    <p>เข้าสู่ระบบเพื่อใช้งาน</p>
    <label>ชื่อผู้ใช้</label>
    <input id="loginU" placeholder="username" onkeydown="if(event.key==='Enter')$('loginP').focus()">
    <label>รหัสผ่าน</label>
    <input id="loginP" type="password" placeholder="password" onkeydown="if(event.key==='Enter')doLogin()">
    <button class="btn" onclick="doLogin()">เข้าสู่ระบบ</button>
    <div class="login-err" id="loginErr"></div>
  </div>
</div>
<div class="app">
  <nav class="nav">
    <div class="brand"><div class="logo">📋</div><b>AutoContentPoster</b><button class="hamb" onclick="toggleNav()">☰</button></div>
    <div class="navitem active" data-v="ai" onclick="nav('ai')"><span class="ic">🤖</span> สร้างโพสต์ด้วย AI</div>
    <div class="navitem" data-v="news" onclick="nav('news')"><span class="ic">📰</span> สร้างโพสต์จากข่าว</div>
    <div class="navitem" data-v="schedule" onclick="nav('schedule')"><span class="ic">🗓️</span> ตารางการโพสต์</div>
    <div class="navitem" data-v="dash" onclick="nav('dash')"><span class="ic">📊</span> Dashboard</div>
    <div class="navitem" data-v="conn" onclick="nav('conn')"><span class="ic">🔌</span> การเชื่อมต่อเพจ</div>
    <div class="navitem" data-v="users" onclick="nav('users')" id="navUsers"><span class="ic">👥</span> ผู้ใช้งาน &amp; สิทธิ์</div>
    <div class="foot">
      <div class="who" id="whoBox"></div>
      <button class="logout-btn" onclick="doLogout()">🚪 ออกจากระบบ</button>
    </div>
  </nav>

  <div class="content">
    <!-- ===== 1. สร้างโพสต์ด้วย AI ===== -->
    <div id="view-ai">
      <h1>สร้างโพสต์ด้วย AI</h1>
      <p class="lead">พิมพ์หัวข้อที่อยากโพสต์ แล้วให้ AI สร้าง Hook · แคปชัน · รูป ให้</p>
      <section class="sec on">
        <h2>✏️ หัวข้อที่อยากโพสต์</h2>
        <input id="topic" placeholder="เช่น โปรโมทเมนูใหม่ ลาเต้น้ำผึ้ง">
        <div class="btnrow"><button class="btn" onclick="start()">สร้างจากหัวข้อนี้ →</button></div>
      </section>
    </div>

    <!-- ===== 2. สร้างโพสต์จากข่าว (เรดาร์เซฟตี้ + ค้นหาเอง รวมกัน) ===== -->
    <div id="view-news" style="display:none">
      <h1>📰 สร้างโพสต์จากข่าว</h1>
      <p class="lead">เปิดมาก็ดึงข่าวกลุ่มไฟไหม้ · จราจร · ความปลอดภัย ให้อัตโนมัติ — หรือพิมพ์ค้นหัวข้อเองก็ได้ คลิกข่าวเพื่อสร้างคอนเทนต์ต่อ</p>
      <section class="sec on">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">
          <div style="flex:1;min-width:220px">
            <label>🔎 ค้นข่าวเกี่ยวกับ (เว้นว่าง = เรดาร์เซฟตี้อัตโนมัติ)</label>
            <input id="newsq" placeholder="เช่น ไฟไหม้อาคาร, กฎหมายอาคารใหม่, อุบัติเหตุบนถนน" onkeydown="if(event.key==='Enter')loadNews(this.value.trim())">
          </div>
          <button class="btn" onclick="loadNews($('newsq').value.trim())">🔎 ค้นหา</button>
          <button class="btn ghost" onclick="$('newsq').value='';loadNews('')">🚨 เรดาร์เซฟตี้</button>
        </div>
        <div id="newsCards"></div>
      </section>
    </div>

    <!-- ===== ขั้นตอนสร้างคอนเทนต์ (ใช้ร่วมกันทั้ง 2 เมนู) ===== -->
    <div id="wizard" style="display:none">
      <div class="steps">
        <div class="s on" id="st2">1<b>Hook</b></div><div class="s" id="st3">2<b>แคปชัน</b></div>
        <div class="s" id="st4">3<b>รูปภาพ</b></div><div class="s" id="st5">4<b>พรีวิว</b></div>
      </div>
      <div id="recap" class="recap"></div>
      <section class="sec" id="s2"><h2><span class="n">1</span> เลือกพาดหัว (Hook)</h2><div id="hooks"></div></section>
      <section class="sec" id="s3"><h2><span class="n">2</span> เลือกแคปชัน</h2><div id="caps"></div></section>
      <section class="sec" id="s4"><h2><span class="n">3</span> เลือกรูปภาพ</h2><div id="imgs" class="imgs"></div></section>
      <section class="sec" id="s5">
        <h2><span class="n">4</span> พรีวิวก่อนโพสต์</h2>
        <div class="preview" id="preview"><div class="ph">เลือกรูปในขั้นที่ 3 เพื่อดูพรีวิว</div></div>
        <label>ปลายทาง</label>
        <select id="dest" onchange="buildPreview()"><option value="telegram">📨 Telegram</option><option value="facebook" disabled>📘 Facebook (กำลังเช็ค...)</option></select>
        <div id="fbnote" class="fbnote"></div>
        <label>โพสต์เมื่อไหร่?</label>
        <div class="chips" id="chips">
          <button class="chip on" onclick="setWhen('now',this)">⚡ โพสต์เลย</button>
          <button class="chip" onclick="setWhen('1h',this)">🕐 อีก 1 ชม.</button>
          <button class="chip" onclick="setWhen('tonight',this)">🌙 คืนนี้ 2 ทุ่ม</button>
          <button class="chip" onclick="setWhen('tomorrow',this)">☀️ พรุ่งนี้ 9 โมง</button>
          <button class="chip" onclick="setWhen('custom',this)">📅 เลือกวัน-เวลาเอง</button>
        </div>
        <input type="datetime-local" id="when" style="display:none" onchange="setCustom()">
        <div class="whenlabel" id="whenlabel">⚡ จะโพสต์ทันที</div>
        <div class="btnrow"><button class="btn block" onclick="schedule()">✅ ยืนยันโพสต์</button></div>
      </section>
    </div>

    <!-- ===== 3. ตารางการโพสต์ ===== -->
    <div id="view-schedule" style="display:none">
      <h1>ตารางการโพสต์</h1>
      <p class="lead">ปฏิทินรวมโพสต์ทั้งหมด — วันไหนมีคอนเทนต์/ตั้งเวลาไว้จะโชว์เวลาด้านหน้า คลิกวันเพื่อดูรายละเอียด</p>
      <div class="cal">
        <div class="cal-head">
          <div class="cal-nav">
            <button class="cal-btn" onclick="calMove(-1)">‹</button>
            <button class="cal-btn" onclick="calToday()">วันนี้</button>
            <button class="cal-btn" onclick="calMove(1)">›</button>
          </div>
          <div class="cal-title" id="calTitle"></div>
          <div class="cal-legend">
            <span><i class="dot d-pending"></i>รอโพสต์</span>
            <span><i class="dot d-sent"></i>โพสต์แล้ว</span>
            <span><i class="dot d-cancelled"></i>ยกเลิก</span>
          </div>
        </div>
        <div class="cal-grid cal-dow">
          <div>อา</div><div>จ</div><div>อ</div><div>พ</div><div>พฤ</div><div>ศ</div><div>ส</div>
        </div>
        <div class="cal-grid" id="calBody"></div>
      </div>
      <div id="dayDetail" class="day-detail" style="display:none"></div>
    </div>

    <!-- ===== 4. Dashboard ===== -->
    <div id="view-dash" style="display:none">
      <h1>📊 Dashboard</h1>
      <p class="lead">เลือกช่วงวันที่ (จาก–ถึง) และเพจ แล้วกด <b>🔍 ค้นหาข้อมูล</b> ตัวเลขถึงจะขึ้น</p>
      <div class="dash-controls">
        <div class="dash-filter">
          <label style="margin:0 0 3px">📅 เลือกช่วงวันที่</label>
          <div class="filter-row">
            <input type="date" id="dashFrom"><span style="color:var(--muted)">ถึง</span><input type="date" id="dashTo">
            <div class="chips" id="dashPreset">
              <button class="chip" onclick="setRange('week',this)">สัปดาห์นี้</button>
              <button class="chip" onclick="setRange('month',this)">เดือนนี้</button>
              <button class="chip" onclick="setRange('year',this)">ปีนี้</button>
            </div>
          </div>
        </div>
        <div class="dash-pagewrap">
          <label style="margin:0 0 3px">เพจ</label>
          <select id="dashPageSel"><option value="all">📘 ทุกเพจ</option></select>
        </div>
        <button class="btn" onclick="loadDash()">🔍 ค้นหาข้อมูล</button>
      </div>
      <div id="dashBody"><div class="empty">กำลังโหลด...</div></div>
    </div>

    <!-- ===== 5. การเชื่อมต่อเพจ ===== -->
    <div id="view-conn" style="display:none">
      <h1>🔌 การเชื่อมต่อเพจ</h1>
      <p class="lead">จัดการเพจ Facebook ที่เชื่อมกับระบบ — เพิ่มเพจใหม่ หรือตัดเพจออกได้ที่นี่</p>
      <div id="connList"></div>
      <div class="panel" style="margin-top:18px">
        <h3>➕ เพิ่ม / รีเฟรชเพจ</h3>
        <p class="fbnote">วาง User Token (จาก Graph API Explorer) เพื่อค้นหาเพจในบัญชี → ติ๊กเลือกเพจที่จะเชื่อม → บันทึก<br>ใส่ App ID + App Secret ด้วยจะได้ <b>token ถาวร</b> (ถ้าเว้นว่าง token จะหมดอายุใน 1-2 ชม.)</p>
        <div class="conn-form">
          <div><label>App ID</label><input id="connAppId" placeholder="เช่น 1346921670682371"></div>
          <div><label>App Secret</label><input id="connAppSecret" placeholder="App Secret (เว้นว่างได้)"></div>
        </div>
        <label>User Token</label>
        <input id="connToken" placeholder="EAA...">
        <div class="btnrow"><button class="btn" onclick="discoverPages()">🔎 ค้นหาเพจในบัญชี</button></div>
        <div id="connDiscover"></div>
      </div>
    </div>

    <!-- ===== 6. ผู้ใช้งาน & สิทธิ์ (เฉพาะแอดมิน) ===== -->
    <div id="view-users" style="display:none">
      <h1>👥 ผู้ใช้งาน &amp; สิทธิ์การเข้าใช้</h1>
      <p class="lead">เฉพาะคนที่อยู่ในรายชื่อนี้เท่านั้นที่ล็อกอินเข้าใช้เว็บได้ — เพิ่ม/ลบผู้ใช้ได้ที่นี่</p>
      <div class="panel"><h3>รายชื่อผู้ใช้</h3><div id="usrList"></div></div>
      <div class="panel">
        <h3>➕ เพิ่มผู้ใช้ใหม่</h3>
        <div class="conn-form">
          <div><label>ชื่อผู้ใช้</label><input id="newU" placeholder="เช่น staff1 หรืออีเมล"></div>
          <div><label>รหัสผ่าน</label><input id="newP" type="text" placeholder="ตั้งรหัสผ่าน"></div>
        </div>
        <label>สิทธิ์</label>
        <select id="newRole"><option value="user">ผู้ใช้ทั่วไป (ใช้งานได้)</option><option value="admin">แอดมิน (จัดการผู้ใช้ได้)</option></select>
        <div class="btnrow"><button class="btn" onclick="addUser()">➕ เพิ่มผู้ใช้</button></div>
        <div id="usrAddMsg"></div>
      </div>
      <div class="panel">
        <h3>🔑 เปลี่ยนรหัสผ่านของฉัน</h3>
        <label>รหัสผ่านใหม่</label><input id="myNewP" type="text" placeholder="รหัสผ่านใหม่">
        <div class="btnrow"><button class="btn ghost" onclick="changeMyPw()">บันทึกรหัสใหม่</button></div>
        <div id="myPwMsg"></div>
      </div>
    </div>
  </div>
</div>

<script>
let st={topic:"",hook:"",caption:"",image_url:"",news:null};
const $=id=>document.getElementById(id);
async function post(u,b){const r=await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b||{})});return r.json()}
const LOAD=t=>`<div class="loadwrap"><div class="loadbar"><i></i></div><span>${t}</span></div>`;

function toggleNav(){document.querySelector('.nav').classList.toggle('open');}
function nav(v){
  document.querySelector('.nav').classList.remove('open');   // เลือกเมนูแล้วปิดเมนูมือถือ
  document.querySelectorAll('.navitem').forEach(n=>n.classList.toggle('active',n.dataset.v===v));
  $('view-ai').style.display=v==='ai'?'block':'none';
  $('view-news').style.display=v==='news'?'block':'none';
  $('view-schedule').style.display=v==='schedule'?'block':'none';
  $('view-dash').style.display=v==='dash'?'block':'none';
  $('view-conn').style.display=v==='conn'?'block':'none';
  $('view-users').style.display=v==='users'?'block':'none';
  $('wizard').style.display='none';   // ซ่อนขั้นตอนสร้างเมื่อสลับเมนู
  if(v==='schedule')loadQueue();
  if(v==='dash')dashInit();
  if(v==='conn')loadConnections();
  if(v==='users')loadUsers();
  if(v==='news'&&!window._newsLoaded){window._newsLoaded=true;loadNews('')}  // เปิดมาดึงเรดาร์เซฟตี้อัตโนมัติ
}
function step(n){for(let i=2;i<=5;i++){const e=$("st"+i);e.classList.toggle("on",i===n);e.classList.toggle("done",i<n)}}
// แสดงทีละขั้น — โชว์เฉพาะ section เดียว ที่เหลือซ่อน
function open(id){['s2','s3','s4','s5'].forEach(s=>$(s).classList.toggle('on',s===id));window.scrollTo({top:0,behavior:'smooth'})}

function newsEmoji(cat){const c=(cat||"").toLowerCase();
  if(/ไฟ|อัคคี|เพลิง|fire/.test(c))return"🔥";
  if(/จราจร|ถนน|อุบัติเหตุ|รถ|traffic|road/.test(c))return"🚧";
  if(/ก่อสร้าง|โรงงาน|อาคาร|construct|factory/.test(c))return"🏗️";
  if(/เซฟตี้|ปลอดภัย|safety|ป้องกัน/.test(c))return"🦺";
  return"📰";}
const NGRAD=["linear-gradient(135deg,#06b6d4,#0ea5e9)","linear-gradient(135deg,#10b981,#06b6d4)",
  "linear-gradient(135deg,#0ea5e9,#6366f1)","linear-gradient(135deg,#14b8a6,#0ea5e9)",
  "linear-gradient(135deg,#0891b2,#10b981)"];
function renderNews(box,topics){
  let h='<div class="news-grid">';
  topics.forEach((t,i)=>{const emo=newsEmoji(t.cat),g=NGRAD[i%NGRAD.length];
    const thumb=t.image
      ?`<div class="nthumb" style="--g:${g}"><img src="${t.image}" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.nthumb').classList.add('ph');this.remove()"><span class="nemo">${emo}</span><span class="ncat">${t.cat}</span></div>`
      :`<div class="nthumb ph" style="--g:${g}"><span class="nemo">${emo}</span><span class="ncat">${t.cat}</span></div>`;
    const readBtn=t.url
      ?`<a class="nbtn read" href="${t.url}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🔗 อ่านข่าว</a>`
      :"";
    h+=`<div class="ncard">${thumb}<div class="nbody">`
      +`<div class="ntitle">${t.title}</div>`
      +(t.summary?`<div class="nsum">${t.summary}</div>`:"")
      +(t.source?`<div class="nmeta"><span class="nsrc">${t.source}</span></div>`:"")
      +`<div class="ncard-btns">${readBtn}<button class="nbtn make" onclick="pickTopic(${i})">✨ สร้างคอนเทนต์</button></div>`
      +`</div></div>`;});
  h+='</div>';$(box).innerHTML=h;
}
const RADAR_Q="ไฟไหม้/อัคคีภัย, อุบัติเหตุและความปลอดภัยทางถนน, งานจราจรและการจัดการจราจร, "
  +"ความปลอดภัยในงานก่อสร้าง/โรงงาน, อุปกรณ์จราจรและอุปกรณ์เซฟตี้ "
  +"และข่าวที่กระทบกลุ่มผู้ใช้อุปกรณ์จราจร/เซฟตี้ (ผู้รับเหมา หน่วยงานท้องถิ่น กรมทางหลวง โรงงาน)";
async function loadNews(q){
  if(window._newsBusy)return;             // กันโหลดซ้อนกัน
  window._newsBusy=true;
  const isRadar=!q;                        // เว้นว่าง = เรดาร์เซฟตี้, มีคำ = ค้นเอง
  $("newsCards").innerHTML=LOAD(isRadar?"กำลังสแกนข่าวไฟไหม้ · จราจร · เซฟตี้ ล่าสุด... (~20-30 วิ)":`กำลังค้นข่าวเกี่ยวกับ "${q}"... (~20-30 วิ)`);
  try{const d=await post("/api/browse",{query:isRadar?RADAR_Q:q,image:true});window._topics=d.topics;renderNews("newsCards",d.topics);}
  catch(e){$("newsCards").innerHTML='<div class="empty">โหลดข่าวไม่สำเร็จ ลองใหม่อีกครั้งครับ</div>';}
  finally{window._newsBusy=false;}
}
function pickTopic(i){const t=window._topics[i];
  st.news={title:t.title,summary:t.summary||"",source:t.source||"",url:t.url||""};  // เก็บข้อเท็จจริงข่าว → โหมดอิงข่าว
  start(t.title);}

async function start(ov){
  const fromNews=(typeof ov==="string"&&ov);   // pickTopic ส่งพาดหัวข่าวมา = โหมดอิงข่าว
  st.topic=fromNews?ov:$("topic").value.trim();
  if(!fromNews)st.news=null;                    // พิมพ์หัวข้อเอง = โหมดปกติ (ล้างข่าว)
  if(!st.topic){alert("ใส่หัวข้อก่อนครับ");return}
  st.hook="";st.caption="";st.image_url="";updateRecap();   // เริ่มใหม่ ล้างของเลือกเดิม
  // ซ่อนหน้าเริ่มต้น (หัวข้อ/ค้นข่าว) แล้วเข้าขั้นตอนสร้าง — โชว์ทีละขั้น
  $('view-ai').style.display='none';$('view-news').style.display='none';
  $('wizard').style.display='block';
  step(2);open("s2");$("hooks").innerHTML=LOAD("กำลังคิดพาดหัว 9 แบบ...");
  const d=await post("/api/hooks",{topic:st.topic,news:st.news});
  renderPick("hooks",d.hooks,(it)=>{const b=it.text.split("\n");return `<pre>${b[0]}</pre>${b[1]?`<div class="sub">${b[1]}</div>`:""}`},(it)=>{st.hook=it.text.split("\n")[0];loadCaps()});
}
function renderPick(box,items,fmt,onPick){
  let h="",cur="";
  items.forEach((it,i)=>{if(it.cat!=cur){cur=it.cat;h+=`<div class="cat">${it.cat}</div>`}
    h+=`<div class="opt" id="${box}_${i}" onclick="window._p_${box}(${i})">${fmt(it)}</div>`});
  window["_p_"+box]=i=>{document.querySelectorAll('#'+box+' .opt').forEach(c=>c.classList.remove('sel'));$(box+"_"+i).classList.add("sel");onPick(items[i])};
  $(box).innerHTML=h;
}
async function loadCaps(){
  updateRecap();   // โชว์พาดหัวที่เพิ่งเลือก
  step(3);open("s3");$("caps").innerHTML=LOAD("กำลังเขียนแคปชัน...");
  const d=await post("/api/captions",{topic:st.topic,hook:st.hook,news:st.news});
  renderPick("caps",d.captions,(it)=>`<pre>${it.text}</pre>`,(it)=>{st.caption=it.text;loadImgs()});
}
async function loadImgs(){
  updateRecap();   // โชว์พาดหัว + แคปชันที่เลือก
  step(4);open("s4");$("imgs").innerHTML=LOAD("AI กำลังสร้างรูป 2 แบบ (รอสักครู่)...");
  const d=await post("/api/images",{hook:st.hook,caption:st.caption});
  $("imgs").innerHTML=d.images.map(im=>`<img src="${im.url}" onclick="pickImg('${im.url}',this)">`).join("")
    +'<div class="sub" style="grid-column:1/3;color:var(--muted);font-size:12px">👆 เลือกรูป 1 อันเพื่อไปหน้าพรีวิว</div>';
}
function pickImg(u,el){
  st.image_url=u;document.querySelectorAll('#imgs img').forEach(x=>x.classList.remove('sel'));el.classList.add('sel');
  buildPreview();step(5);open("s5");
}

// ---- แถบสรุปสิ่งที่เลือก (ค้างอยู่ทุกขั้น) ----
function updateRecap(){
  let h="";
  if(st.hook)h+=`<div class="rc"><b>พาดหัวที่เลือก</b>${st.hook}</div>`;
  if(st.caption)h+=`<div class="rc"><b>แคปชันที่เลือก</b>${st.caption.split("\n")[0].slice(0,90)}…</div>`;
  $("recap").innerHTML=h;
}

// ---- พรีวิว: mockup โทรศัพท์ เหมือนโพสต์จริงบน Facebook (2 แบบ) ----
function buildPreview(){
  const sel=$("dest");
  const dval=sel.value;
  const pageName=dval.startsWith("fb:")?sel.options[sel.selectedIndex].text.replace("📘 ",""):"เพจของคุณ";
  const cap=(st.caption||"").replace(/</g,"&lt;").replace(/\n/g,"<br>");
  const img=st.image_url?`<div class="fbimg"><img src="${st.image_url}"></div>`:"";
  const av=(pageName.trim()[0]||"P");
  function card(variant){
    const head=`<div class="fbhead"><div class="fbavatar">${av}</div><div><div class="fbname">${pageName}</div><div class="fbtime">เมื่อสักครู่ · 🌐</div></div></div>`;
    const bar=`<div class="fbbar"><span>👍 ถูกใจ</span><span>💬 คอมเมนต์</span><span>↗ แชร์</span></div>`;
    const body=variant==="big"
      ? head+img+`<div class="fbcap short">${cap}</div>`+bar
      : head+`<div class="fbcap">${cap}</div>`+img+bar;
    return `<div class="pwrap"><div class="plabel">${variant==="big"?"แบบเน้นรูป":"แบบฟีดปกติ"}</div>`
      +`<div class="phone"><div class="notch"></div><div class="pscreen"><div class="fbtop">facebook</div><div class="fbpost">${body}</div></div></div></div>`;
  }
  $("preview").innerHTML=`<div class="phones">${card("feed")}${card("big")}</div>`;
}

// ---- เลือกเวลาแบบกดง่าย ----
function fmtTH(d){return d.toLocaleString('th-TH',{weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}
function setWhen(kind,el){
  document.querySelectorAll('#chips .chip').forEach(c=>c.classList.remove('on'));if(el)el.classList.add('on');
  $("when").style.display='none';
  const now=new Date();
  if(kind==='now'){st.when_ms=null;$("whenlabel").textContent='⚡ จะโพสต์ทันที';return}
  if(kind==='custom'){$("when").style.display='block';$("when").focus();$("whenlabel").textContent='📅 เลือกวัน-เวลาด้านล่าง';return}
  let d=new Date(now);
  if(kind==='1h')d=new Date(now.getTime()+3600e3);
  if(kind==='tonight'){d.setHours(20,0,0,0);if(d<now)d.setDate(d.getDate()+1)}
  if(kind==='tomorrow'){d.setDate(d.getDate()+1);d.setHours(9,0,0,0)}
  st.when_ms=d.getTime();$("whenlabel").textContent='🗓️ จะโพสต์: '+fmtTH(d);
}
function setCustom(){const v=$("when").value;if(!v)return;const d=new Date(v);st.when_ms=d.getTime();$("whenlabel").textContent='🗓️ จะโพสต์: '+fmtTH(d)}

async function schedule(){
  if(!st.caption){alert("ยังไม่ได้เลือกแคปชัน");return}
  await post("/api/schedule",{caption:st.caption,image_url:st.image_url,destination:$("dest").value,when_ms:st.when_ms||null});
  alert(st.when_ms?"ตั้งเวลาโพสต์แล้ว ✅":"ส่งเข้าคิวโพสต์ทันที ✅");nav('schedule');
}
// ---- ปฏิทินการโพสต์ ----
const TH_MONTHS=["มกราคม","กุมภาพันธ์","มีนาคม","เมษายน","พฤษภาคม","มิถุนายน","กรกฎาคม","สิงหาคม","กันยายน","ตุลาคม","พฤศจิกายน","ธันวาคม"];
const pad2=n=>String(n).padStart(2,"0");
const evTime=ts=>{const d=new Date(ts);return pad2(d.getHours())+":"+pad2(d.getMinutes())};
const dKey=d=>d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate());
const destIcon=p=>p.destination.startsWith('fb:')?'📘':'📨';
let _cal=null;            // {y,m} เดือนที่กำลังดู
window._openDay=null;     // วันที่เปิดรายละเอียดอยู่

async function loadQueue(){
  const d=await(await fetch("/api/scheduled")).json();
  window._posts=d.posts||[];
  if(!_cal){const now=new Date();_cal={y:now.getFullYear(),m:now.getMonth()};}
  renderCal();
  if(window._openDay)openDay(window._openDay,true);  // อัปเดตรายละเอียดที่เปิดอยู่
}
function calMove(delta){let m=_cal.m+delta,y=_cal.y;if(m<0){m=11;y--}if(m>11){m=0;y++}_cal={y,m};renderCal();}
function calToday(){const n=new Date();_cal={y:n.getFullYear(),m:n.getMonth()};renderCal();}

function renderCal(){
  const {y,m}=_cal;
  $("calTitle").textContent=TH_MONTHS[m]+" "+(y+543);
  // จัดกลุ่มโพสต์ตามวัน
  const byDay={};
  (window._posts||[]).forEach(p=>{const k=dKey(new Date(p.ts_ms));(byDay[k]=byDay[k]||[]).push(p);});
  const todayKey=dKey(new Date());
  const first=new Date(y,m,1);
  const start=new Date(y,m,1-first.getDay());   // ย้อนไปวันอาทิตย์ของสัปดาห์แรก
  let html="";
  for(let i=0;i<42;i++){
    const cur=new Date(start);cur.setDate(start.getDate()+i);
    const k=dKey(cur),inMonth=cur.getMonth()===m;
    const evs=(byDay[k]||[]).slice().sort((a,b)=>a.ts_ms-b.ts_ms);
    let evHtml=evs.slice(0,3).map(p=>{
      const cap=(p.caption||"(รูป)").replace(/\n/g," ");
      return `<div class="ev s-${p.status}" onclick="event.stopPropagation();openDay('${k}')"><span class="et">${evTime(p.ts_ms)}</span> ${destIcon(p)} ${cap}</div>`;
    }).join("");
    if(evs.length>3)evHtml+=`<div class="ev-more" onclick="event.stopPropagation();openDay('${k}')">+${evs.length-3} เพิ่มเติม</div>`;
    html+=`<div class="cal-cell ${inMonth?'':'other'} ${k===todayKey?'today':''}" onclick="openDay('${k}')">`
      +`<span class="cal-day">${cur.getDate()}</span>${evHtml}</div>`;
  }
  $("calBody").innerHTML=html;
}

function openDay(k,keepScroll){
  window._openDay=k;
  const evs=(window._posts||[]).filter(p=>dKey(new Date(p.ts_ms))===k).sort((a,b)=>a.ts_ms-b.ts_ms);
  const [yy,mm,dd]=k.split("-").map(Number);
  const head=`${dd} ${TH_MONTHS[mm-1]} ${yy+543}`;
  let rows;
  if(!evs.length){rows='<div class="empty">วันนี้ยังไม่มีโพสต์</div>';}
  else rows=evs.map(p=>{
    const dest=p.destination.startsWith('fb:')?'📘 Facebook':'📨 Telegram';
    let badge,act='';
    if(p.status==='pending'){badge='<span class="badge b-error" style="background:#fff7ec;color:var(--warn)">⏳ รอโพสต์</span>';
      act=`<span class="linkbtn" onclick="cancelPost(${p.id})">ยกเลิก</span>`;}
    else if(p.status==='cancelled')badge='<span class="badge b-cancelled">ยกเลิกแล้ว</span>';
    else if(p.status==='error')badge='<span class="badge b-error">ผิดพลาด</span>';
    else badge='<span class="badge b-sent">✓ โพสต์แล้ว</span>';
    if(p.status==='sent'&&p.destination.startsWith('fb:')&&p.result_id){
      const url=p.result_id.startsWith('http')?p.result_id:`https://www.facebook.com/${p.result_id}`;
      act=`<a href="${url}" target="_blank" style="color:var(--blue);font-weight:600">🔗 ดูโพสต์</a>`;}
    return `<div class="dd-row"><div class="dd-time">${evTime(p.ts_ms)}</div><div class="dd-body">`
      +`<div class="dd-cap">${p.caption||"(รูปอย่างเดียว)"}</div>`
      +`<div class="dd-meta">${dest} ${badge} ${act}</div></div></div>`;
  }).join("");
  const dt=$("dayDetail");
  dt.innerHTML=`<h3>📅 ${head}</h3>${rows}`;
  dt.style.display="block";
  if(!keepScroll)dt.scrollIntoView({behavior:"smooth",block:"nearest"});
}
async function cancelPost(id){await fetch("/api/scheduled/"+id,{method:"DELETE"});loadQueue();}

// ---- การเชื่อมต่อเพจ ----
async function loadConnections(){
  $('connList').innerHTML=LOAD('กำลังตรวจสอบสถานะเพจ...');
  const d=await(await fetch('/api/connections')).json();
  if($('connAppId')&&!$('connAppId').value&&d.app_id)$('connAppId').value=d.app_id;
  if(!d.pages.length){$('connList').innerHTML='<div class="panel"><div class="empty">ยังไม่มีเพจที่เชื่อมต่อ — เพิ่มได้ด้านล่าง 👇</div></div>';return;}
  $('connList').innerHTML='<div class="conn-grid">'+d.pages.map(p=>{
    const ok=p.connected;const nm=(p.name||'').replace(/"/g,'&quot;');
    return `<div class="conn-card"><div class="conn-ic ${ok?'':'err'}">📘</div>`
      +`<div class="conn-info"><div class="conn-name">${nm}</div><div class="conn-id">ID: ${p.id}</div>`
      +`<div class="conn-st ${ok?'on':'off'}">${ok?'● เชื่อมต่อปกติ':'● ปัญหา: '+((p.error||'token หมดอายุ').slice(0,40))}</div></div>`
      +`<button class="conn-x" onclick="removePage('${p.id}',this.dataset.n)" data-n="${nm}">ตัดออก</button></div>`;
  }).join('')+'</div>';
}
async function removePage(id,name){
  if(!confirm('ตัดการเชื่อมต่อเพจ "'+name+'" ออกจากระบบ?'))return;
  await post('/api/conn/remove',{id});window._dashPagesLoaded=false;loadConnections();
}
async function discoverPages(){
  const token=$('connToken').value.trim();
  if(!token){alert('วาง User Token ก่อนครับ');return;}
  $('connDiscover').innerHTML=LOAD('กำลังค้นหาเพจในบัญชี...');
  const d=await post('/api/conn/discover',{token,app_id:$('connAppId').value.trim(),app_secret:$('connAppSecret').value.trim()});
  if(!d.ok){$('connDiscover').innerHTML='<div class="dash-note">❌ '+(d.error||'ค้นหาไม่สำเร็จ')+'</div>';return;}
  if(!d.pages.length){$('connDiscover').innerHTML='<div class="empty">ไม่พบเพจในบัญชีนี้</div>';return;}
  $('connDiscover').innerHTML='<div style="margin:14px 0 4px;font-weight:600">เลือกเพจที่จะเชื่อม ('+d.pages.length+' เพจ):</div>'
    +d.pages.map(p=>`<label class="conn-pick"><input type="checkbox" ${p.connected?'checked':''} value="${p.id}"> 📘 ${p.name}`
      +`${p.connected?' <span class="conn-st on">● เชื่อมอยู่</span>':''}<span class="conn-id">${p.id}</span></label>`).join('')
    +'<div class="btnrow"><button class="btn" onclick="savePages()">💾 บันทึกการเชื่อมต่อ</button></div>';
}
async function savePages(){
  const ids=[...document.querySelectorAll('#connDiscover input:checked')].map(c=>c.value);
  const d=await post('/api/conn/save',{ids});
  alert('บันทึกแล้ว ✅ เชื่อมต่อ '+d.count+' เพจ');
  $('connToken').value='';$('connAppSecret').value='';$('connDiscover').innerHTML='';
  window._dashPagesLoaded=false;   // ให้ Dashboard โหลดรายชื่อเพจใหม่
  loadConnections();
}

// ---- Dashboard ----
const isoDate=d=>d.getFullYear()+"-"+pad2(d.getMonth()+1)+"-"+pad2(d.getDate());
function setRange(kind,el){   // ปุ่มลัด: เติมช่วงวันที่ให้อัตโนมัติ (ยังไม่โหลด รอกดค้นหา)
  const n=new Date();let from,to;
  if(kind==='week'){const dow=(n.getDay());from=new Date(n);from.setDate(n.getDate()-dow);to=new Date(from);to.setDate(from.getDate()+6);}
  else if(kind==='year'){from=new Date(n.getFullYear(),0,1);to=new Date(n.getFullYear(),11,31);}
  else{from=new Date(n.getFullYear(),n.getMonth(),1);to=new Date(n.getFullYear(),n.getMonth()+1,0);} // เดือนนี้
  $('dashFrom').value=isoDate(from);$('dashTo').value=isoDate(to);
  document.querySelectorAll('#dashPreset .chip').forEach(c=>c.classList.toggle('on',c===el));
}
function dashInit(){
  $("dashBody").innerHTML='<div class="empty">👆 เลือกช่วงวันที่ และเพจ แล้วกด 🔍 ค้นหาข้อมูล</div>';
  ensurePageOptions();   // เติม dropdown เพจ + ตั้งช่วงวันที่เริ่มต้น (background ไม่ทับ prompt)
}
async function ensurePageOptions(){
  if($('dashFrom')&&!$('dashFrom').value){           // ตั้งช่วงเริ่มต้น = เดือนนี้
    const monthChip=[...document.querySelectorAll('#dashPreset .chip')][1];
    setRange('month',monthChip);
  }
  if(window._dashPagesLoaded)return;window._dashPagesLoaded=true;
  try{const s=await(await fetch('/api/fbstatus')).json();
    const sel=$('dashPageSel');
    (s.pages||[]).filter(p=>p.connected).forEach(p=>{
      const o=document.createElement('option');o.value=p.id;o.textContent='📘 '+p.name;sel.appendChild(o);});
  }catch(e){}
}
function dashQS(){
  const from=$('dashFrom')?$('dashFrom').value:'';
  const to=$('dashTo')?$('dashTo').value:'';
  const page=$('dashPageSel')?$('dashPageSel').value:'all';
  return `start=${from}&end=${to}&page=${page}`;
}
async function loadDash(){
  await ensurePageOptions();
  $("dashBody").innerHTML=LOAD("กำลังดึงโพสต์ทั้งหมดจากเพจ Facebook... (อาจใช้เวลาสักครู่)");
  const d=await(await fetch("/api/dashboard?"+dashQS())).json();
  const fb=d.fb||{available:false};
  const card=(v,l,ic)=>`<div class="scard"><div class="si">${ic}</div><div class="sv">${v}</div><div class="sl">${l}</div></div>`;
  let h=`<div style="font-size:13px;color:var(--muted);margin-bottom:10px">📆 ช่วงที่ดู: <b style="color:var(--blue-d)">${d.range_label}</b></div>`;
  // การ์ดบนสุด — ใช้ข้อมูลจริงจากเพจ Facebook (ถ้าดึงได้)
  if(fb.available){
    const t=fb.totals;
    h+='<div class="dash-cards">'
      +card(fb.page_total,"โพสต์บนเพจ (ทั้งหมดในช่วง)","📦")
      +card(t.reactions,"รีแอกชันรวม","👍")
      +card(t.comments,"คอมเมนต์รวม","💬")
      +card(t.shares,"แชร์รวม","↗️")
      +'</div>';
  }else{
    h+='<div class="dash-cards">'
      +card(d.system_total,"โพสต์ผ่านระบบ (ในช่วง)","📦")
      +card(d.by_status.sent||0,"โพสต์แล้ว","✅")
      +card(d.pending_all||0,"รอโพสต์","⏳")
      +card(d.success_rate+"%","อัตราสำเร็จ","🎯")
      +'</div>';
  }
  // กราฟตามความยาวช่วง
  const chartTitle=(d.span<=1?"📈 โพสต์รายชั่วโมง":(d.span<=62?"📈 โพสต์รายวัน":"📈 โพสต์รายเดือน"))+(fb.available?" (จากเพจ)":"");
  const mx=Math.max(1,...d.daily.map(x=>x.count));
  const step=d.daily.length>14?Math.ceil(d.daily.length/12):1;
  const bars=d.daily.map(x=>`<div class="bar" style="height:${Math.round(x.count/mx*100)}%"><span class="bt">${x.count}</span></div>`).join("");
  const xlab=d.daily.map((x,i)=>`<span>${i%step===0?x.label:""}</span>`).join("");
  // กล่องข้างขวา = สรุปการโพสต์ผ่านระบบนี้
  const tg=d.by_dest.telegram||0,fbd=d.by_dest.facebook||0;
  h+='<div class="dash-grid">'
    +`<div class="panel"><h3>${chartTitle}</h3><div class="bars">${bars}</div><div class="bars-x">${xlab}</div></div>`
    +`<div class="panel"><h3>🛠️ โพสต์ผ่านระบบนี้ (ในช่วง)</h3><div style="font-size:13px;line-height:2">`
      +`<div>รวม <b>${d.system_total}</b> โพสต์ · สำเร็จ ${d.success_rate}%</div>`
      +`<div>📨 Telegram: <b>${tg}</b> · 📘 Facebook: <b>${fbd}</b></div>`
      +`<div style="color:var(--muted)">รอโพสต์ ${d.pending_all||0} · ยกเลิก ${d.by_status.cancelled||0} · ผิดพลาด ${d.by_status.error||0}</div>`
    +`</div></div></div>`;
  // คิวที่กำลังจะออก
  let up='<div class="empty">ไม่มีโพสต์รออยู่</div>';
  if(d.upcoming.length)up=d.upcoming.map(p=>{
    const dest=p.destination.startsWith('fb:')?'📘':'📨';
    return `<div class="dd-row"><div class="dd-time">${p.when}</div><div class="dd-body"><div class="dd-cap">${dest} ${p.caption||"(รูป)"}</div></div></div>`;
  }).join("");
  h+=`<div class="panel"><h3>⏳ คิวที่กำลังจะออก</h3>${up}</div>`;
  // อันดับโพสต์ยอดนิยมจากเพจจริง
  if(fb.available){
    if(fb.top&&fb.top.length){
      h+='<div class="panel"><h3>🏆 โพสต์ยอดนิยมบนเพจ (ตาม engagement)</h3>'
        +fb.top.map((p,i)=>`<div class="lead-row"><div class="lead-rank">${i+1}</div><div class="lead-body">`
          +`<div class="lead-msg">${p.permalink?`<a href="${p.permalink}" target="_blank" style="color:inherit">${p.message}</a>`:p.message}</div>`
          +`<div class="lead-stats"><span>👍 ${p.reactions}</span><span>💬 ${p.comments}</span><span>↗️ ${p.shares}</span><span>${p.when}</span><span>${p.page}</span></div>`
          +`</div></div>`).join("")
        +'</div>';
    }else h+='<div class="panel"><h3>🏆 โพสต์ยอดนิยมบนเพจ</h3><div class="empty">ไม่มีโพสต์บนเพจในช่วงนี้</div></div>';
  }else{
    h+='<div class="panel"><h3>❤️ Engagement จาก Facebook</h3>'
      +'<div class="dash-note">ยังดึงข้อมูลเพจไม่ได้ เพราะ token ขาดสิทธิ์ <code>pages_read_user_content</code><br>'
      +'วิธีเปิด: เอา user token ใหม่จาก Graph API Explorer (ติ๊กเพิ่ม pages_read_user_content) แล้วรัน '
      +'<code>python3 fb_setup.py &lt;App_ID&gt; &lt;App_Secret&gt; &lt;token&gt;</code> รีสตาร์ทเว็บ</div></div>';
  }
  $("dashBody").innerHTML=h;
}
async function checkFB(){
  try{
    const s=await(await fetch('/api/fbstatus')).json();
    const sel=$('dest');
    sel.querySelectorAll('option[data-fb]').forEach(o=>o.remove());      // ล้างของเก่า
    const ph=sel.querySelector('option[value=facebook]'); if(ph)ph.remove();
    const conn=(s.pages||[]).filter(p=>p.connected);
    conn.forEach(p=>{const o=document.createElement('option');o.value='fb:'+p.id;o.textContent='📘 '+p.name;o.dataset.fb='1';sel.appendChild(o)});
    $('fbnote').innerHTML = conn.length
      ? '✅ เชื่อมเพจ Facebook แล้ว '+conn.length+' เพจ — เลือกปลายทางได้เลย'
      : '⚠️ ยังไม่ได้เชื่อมเพจ (ใส่ FB_PAGES ใน .env แล้วรีสตาร์ท)';
  }catch(e){}
}
// ---- ระบบล็อกอิน / สิทธิ์ผู้ใช้ ----
async function boot(){
  let me={authed:false};
  try{me=await(await fetch('/api/me')).json();}catch(e){}
  if(!me.authed){$('loginOv').style.display='flex';return;}
  $('loginOv').style.display='none';
  $('whoBox').innerHTML='เข้าใช้โดย <b>'+me.user+'</b><br>'+(me.role==='admin'?'แอดมิน':'ผู้ใช้ทั่วไป');
  document.querySelector('.nav').classList.toggle('is-admin',me.role==='admin');   // เมนูจัดการผู้ใช้เฉพาะแอดมิน
  checkFB();
}
async function doLogin(){
  const u=$('loginU').value.trim(),p=$('loginP').value;
  $('loginErr').textContent='';
  const r=await(await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})})).json();
  if(!r.ok){$('loginErr').textContent=r.error||'เข้าสู่ระบบไม่สำเร็จ';return;}
  $('loginP').value='';boot();
}
async function doLogout(){await fetch('/api/logout',{method:'POST'});location.reload();}

async function loadUsers(){
  $('usrList').innerHTML=LOAD('กำลังโหลด...');
  const d=await(await fetch('/api/users')).json();
  if(d.error){$('usrList').innerHTML='<div class="empty">เฉพาะแอดมินเท่านั้น</div>';return;}
  $('usrList').innerHTML=d.users.map(u=>`<div class="usr-row"><div class="usr-av">${(u.username[0]||'?').toUpperCase()}</div>`
    +`<div class="usr-info"><div class="usr-name">${u.username}</div>`
    +`<span class="usr-role ${u.role}">${u.role==='admin'?'แอดมิน':'ผู้ใช้ทั่วไป'}</span> <span class="conn-id">เพิ่มเมื่อ ${u.created}</span></div>`
    +`<button class="conn-x" onclick="delUser('${u.username.replace(/'/g,'')}')">ลบ</button></div>`).join("");
}
async function addUser(){
  const u=$('newU').value.trim(),p=$('newP').value,role=$('newRole').value;
  const r=await post('/api/users/add',{username:u,password:p,role});
  if(!r.ok){$('usrAddMsg').innerHTML='<div class="dash-note">❌ '+(r.error||'เพิ่มไม่สำเร็จ')+'</div>';return;}
  $('usrAddMsg').innerHTML='<div style="color:var(--ok);font-weight:700;margin-top:8px">✅ เพิ่มผู้ใช้ "'+u+'" แล้ว</div>';
  $('newU').value='';$('newP').value='';loadUsers();
}
async function delUser(u){
  if(!confirm('ลบผู้ใช้ "'+u+'" ออกจากระบบ?'))return;
  const r=await post('/api/users/delete',{username:u});
  if(!r.ok){alert(r.error||'ลบไม่สำเร็จ');return;}
  loadUsers();
}
async function changeMyPw(){
  const p=$('myNewP').value;
  const r=await post('/api/users/passwd',{password:p});
  if(!r.ok){$('myPwMsg').innerHTML='<div class="dash-note">❌ '+(r.error||'เปลี่ยนไม่สำเร็จ')+'</div>';return;}
  $('myPwMsg').innerHTML='<div style="color:var(--ok);font-weight:700;margin-top:8px">✅ เปลี่ยนรหัสผ่านแล้ว</div>';$('myNewP').value='';
}

boot();
setInterval(()=>{if($('view-schedule').style.display!=='none')loadQueue()},6000);
</script>
</body></html>"""
