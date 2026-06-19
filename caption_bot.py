# caption_bot.py
# วิธีรัน:
#   Mac:     python3 -u caption_bot.py "หัวข้อ"
#   Windows: py -X utf8 -u caption_bot.py "หัวข้อ"
# (ถ้าไม่ใส่หัวข้อตอนรัน บอทจะถามด้วย input())

import os
import sys
import json
import unicodedata
import requests
from openai import OpenAI
from dotenv import load_dotenv

# ---------- อ่านค่าลับจาก .env เท่านั้น ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

missing = [name for name, val in [
    ("TELEGRAM_TOKEN", TELEGRAM_TOKEN),
    ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
    ("OPENAI_API_KEY", OPENAI_API_KEY),
] if not val]
if missing:
    print("❌ ไม่พบค่าใน .env:", ", ".join(missing))
    raise SystemExit

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# ---------- ฟังก์ชันคุยกับ Telegram ----------
def nfc(text):
    # normalize เป็น NFC กันสระอู/ไม้เอกเพี้ยน
    return unicodedata.normalize("NFC", text)


def send_message(text, buttons=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": nfc(text)}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    requests.post(f"{API}/sendMessage", data=payload)


def get_updates(offset=None, timeout=30):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    return requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 10).json()


def answer_callback(callback_id):
    requests.post(f"{API}/answerCallbackQuery", data={"callback_query_id": callback_id})


# ---------- สไตล์ทั้ง 3 ----------
STYLES = [
    {"emoji": "🌅", "name": "อารมณ์",
     "instruction": "เขียนแนวอารมณ์/บรรยากาศ เน้นภาพ กลิ่น รส สัมผัส ให้คนอ่านรู้สึกอยากดื่ม"},
    {"emoji": "📖", "name": "เล่าเรื่อง",
     "instruction": "เขียนแนวเล่าเรื่อง/ที่มา สร้างคุณค่า ให้รู้สึกพรีเมียมและน่าเชื่อถือ"},
    {"emoji": "🎉", "name": "สนุก",
     "instruction": "เขียนแนวสนุก เป็นกันเอง และจบด้วยคำถามชวนคอมเมนต์"},
]


# ---------- รับหัวข้อ ----------
topic = sys.argv[1].strip() if len(sys.argv) > 1 else input("หัวข้อโพสต์ คืออะไร? ").strip()
if not topic:
    print("⚠️  ไม่มีหัวข้อ ลองรันใหม่อีกครั้ง")
    raise SystemExit

client = OpenAI(api_key=OPENAI_API_KEY)


def generate_caption(style):
    resp = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[
            {"role": "system", "content": "คุณเป็นผู้ช่วยเขียนแคปชันโพสต์โซเชียลภาษาไทย ตอบเฉพาะตัวแคปชันล้วนๆ ห้ามมีคำทักทาย คำอธิบาย หรือคำถามทวนกลับถึงผู้ใช้"},
            {"role": "user", "content": f"หัวข้อ: {topic}\nสไตล์: {style['instruction']}"},
        ],
    )
    return resp.choices[0].message.content.strip()


print("🤖 กำลังเขียนแคปชัน 3 สไตล์...")
captions = [generate_caption(s) for s in STYLES]


# ---------- หน้าจอ 1: เลือกสไตล์ ----------
def show_screen1():
    parts = [f"{s['emoji']} {s['name']}\n{cap}" for s, cap in zip(STYLES, captions)]
    text = "เลือกแคปชันที่ชอบได้เลย 👇\n\n" + "\n\n———\n\n".join(parts)
    buttons = [[
        {"text": "🌅 อารมณ์", "callback_data": "style:0"},
        {"text": "📖 เล่าเรื่อง", "callback_data": "style:1"},
        {"text": "🎉 สนุก", "callback_data": "style:2"},
    ]]
    send_message(text, buttons)


# ---------- หน้าจอ 2: หลังเลือกสไตล์ ----------
def show_screen2(idx):
    buttons = [[
        {"text": "✅ ใช้เลย", "callback_data": "use"},
        {"text": "✏️ แก้เอง", "callback_data": "edit"},
        {"text": "❌ เปลี่ยนใจ", "callback_data": "back"},
    ]]
    send_message(captions[idx], buttons)


# ---------- เริ่ม flow ----------
# ล้าง update เก่าที่ค้างทิ้งก่อน กันการกดเก่าเด้งซ้ำ
init = get_updates(timeout=0)
offset = init["result"][-1]["update_id"] + 1 if init.get("result") else None

state = {"mode": "choosing", "idx": None}
show_screen1()

print("⏳ บอทกำลังรอรับการกดปุ่ม/ข้อความ... (กด Ctrl+C เพื่อหยุด)")

try:
    while True:
        data = get_updates(offset=offset, timeout=30)
        for update in data.get("result", []):
            offset = update["update_id"] + 1

            # --- การกดปุ่ม ---
            cb = update.get("callback_query")
            if cb:
                answer_callback(cb["id"])
                action = cb["data"]

                if action.startswith("style:"):
                    state["idx"] = int(action.split(":")[1])
                    state["mode"] = "reviewing"
                    show_screen2(state["idx"])

                elif action == "use" and state["idx"] is not None:
                    send_message("✅ ใช้แคปชันนี้เลย\n\n" + captions[state["idx"]])
                    state["mode"] = "done"

                elif action == "edit" and state["idx"] is not None:
                    send_message("ก๊อปแคปชันด้านล่างไปแก้ แล้วพิมพ์ส่งกลับมา")
                    send_message(captions[state["idx"]])
                    state["mode"] = "awaiting_edit"

                elif action == "back":
                    state["idx"] = None
                    state["mode"] = "choosing"
                    show_screen1()
                continue

            # --- ข้อความที่ผู้ใช้พิมพ์ (เฉพาะตอนรอแคปชันที่แก้) ---
            msg = update.get("message")
            if msg and "text" in msg and state["mode"] == "awaiting_edit":
                send_message("✅ รับแคปชันที่แก้แล้ว\n\n" + msg["text"])
                state["mode"] = "done"
except KeyboardInterrupt:
    print("\n👋 หยุดบอทแล้วครับ")
