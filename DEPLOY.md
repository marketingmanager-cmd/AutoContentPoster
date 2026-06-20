# วิธีเอาเว็บแอปขึ้นออนไลน์ด้วย Render.com (เข้าได้จากทุกที่)

เว็บแอปนี้เป็นเซิร์ฟเวอร์ Python (FastAPI) ต้องใช้ host ที่รันเซิร์ฟเวอร์ค้างได้
(Netlify/Vercel ใช้ไม่ได้ เพราะรันได้แค่สั้นๆ ไม่มีตัวตั้งเวลา) — แนะนำ **Render.com** ฟรี

ไฟล์ที่เตรียมไว้ให้แล้ว: `render.yaml` · `Procfile` · `runtime.txt` · `requirements.txt`

---

## ขั้นตอน (ครั้งเดียวจบ)

### 1) เอาโค้ดขึ้น GitHub
```bash
git add .
git commit -m "ready for deploy"
git push
```
⚠️ `.env` และ `webapp.db` ถูก `.gitignore` ไว้แล้ว — ความลับไม่หลุด

### 2) สมัคร Render.com → New → **Blueprint** → เลือก repo นี้
Render จะอ่าน `render.yaml` แล้วตั้ง Build/Start command ให้อัตโนมัติ
(หรือเลือก **Web Service** เองก็ได้ — Build: `pip install -r requirements.txt`,
Start: `uvicorn webapp:app --host 0.0.0.0 --port $PORT`)

### 3) ใส่ Environment Variables (สำคัญมาก! ไม่ใส่เว็บไม่บูต)
เอาค่าจากไฟล์ `.env` ในเครื่องมาวางในหน้า Render → Environment:

| Key | ค่า |
|---|---|
| `OPENAI_API_KEY` | คีย์ OpenAI |
| `TELEGRAM_TOKEN` | token บอท |
| `TELEGRAM_CHAT_ID` | chat id |
| `FB_PAGES` | **วาง JSON ดิบ** `[{"id":"...","name":"...","token":"..."}]` (ไม่ต้องมีเครื่องหมาย ' ครอบแบบในไฟล์ .env) |
| `ADMIN_USER` | ชื่อแอดมินเริ่มต้น เช่น `admin` |
| `ADMIN_PASS` | รหัสแอดมินเริ่มต้น (ตั้งให้เดายาก!) |

### 4) กด Deploy → รอสักครู่ → ได้ URL เช่น `https://autocontentposter.onrender.com`
เปิดจากที่ไหนก็ได้ → เจอหน้าล็อกอิน → ใช้ ADMIN_USER/ADMIN_PASS ที่ตั้งไว้เข้าสู่ระบบ

---

## ⚠️ ข้อควรรู้สำคัญ

1. **ตั้งเวลาโพสต์ + แพลนฟรี:** Render ฟรีจะ "หลับ" เมื่อไม่มีคนเข้า ~15 นาที → ตัวจับเวลาหยุด → โพสต์ตามเวลาอาจไม่ยิงตรงเวลา
   - 👉 ใช้จริงจัง: อัปเกรดเป็นแพลน always-on **หรือ** ใช้ cron-job.org ยิง ping เข้าเว็บทุก ~10 นาทีให้ไม่หลับ

2. **ฐานข้อมูล `webapp.db` (เก็บผู้ใช้ + โพสต์ที่ตั้งเวลา):** แพลนฟรีดิสก์เป็นแบบชั่วคราว → **หายเมื่อ redeploy/หลับ-ตื่น**
   - 👉 ใช้จริงจัง: เพิ่ม Persistent Disk (แพลนเสียเงิน) หรือย้ายไป Postgres ของ Render
   - ทุกครั้งที่ DB ถูกสร้างใหม่ ระบบจะสร้างแอดมินจาก ADMIN_USER/ADMIN_PASS ให้อัตโนมัติ

3. **การล็อกอิน (session):** เก็บในหน่วยความจำ → รีสตาร์ท/หลับ-ตื่น แล้วทุกคนต้องล็อกอินใหม่ (ปกติ ปลอดภัยดี)

4. **Facebook token:** เป็นแบบถาวร (~60 วัน) ถ้าหมดอายุให้รัน `fb_setup.py` ในเครื่อง แล้วเอา `FB_PAGES` ใหม่ไปอัปเดตใน Render → Environment แล้ว redeploy
   - การแก้ผ่านเมนู "🔌 การเชื่อมต่อเพจ" บนเว็บได้ แต่ถ้า DB/ENV เป็น ephemeral จะหายตอน redeploy → แก้ที่ Render Environment ชัวร์กว่า

---

## สรุปไฟล์ deploy
- `render.yaml` — Blueprint (Render อ่านเอง)
- `Procfile` — คำสั่งสตาร์ท (เผื่อสร้าง service เอง)
- `runtime.txt` — เวอร์ชัน Python (3.12.7)
- `requirements.txt` — ไลบรารีที่ต้องลง
- `fonts/` — ฟอนต์ไทย (จำเป็นสำหรับสร้างรูป) ถูก commit ขึ้นไปด้วย
