# find_chat_id.py
# สคริปต์นี้ทำหน้าที่เดียว: ไปถาม Telegram ว่า "มีใครทักบอทเรามาบ้าง"
# แล้วดึง chat id ออกมาแสดงให้เรา

import requests          # ไลบรารีไว้คุยกับเว็บ/API
from config import BOT_TOKEN

# ที่อยู่ (URL) ของคำสั่ง getUpdates = "ขอดูข้อความล่าสุดที่ส่งมาหาบอท"
url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

# ส่งคำขอไปหา Telegram แล้วแปลงคำตอบเป็นข้อมูล Python (dict)
response = requests.get(url)
data = response.json()

# ถ้าไม่มีใครทักบอทเลย ส่วน "result" จะว่าง
if not data.get("ok"):
    print("❌ มีปัญหา:", data)
elif not data["result"]:
    print("⚠️  ยังไม่เจอข้อความเลยครับ")
    print("    👉 กรุณาเปิด Telegram แล้วทัก @PeterPosterBot ก่อน (พิมพ์อะไรก็ได้ เช่น hi)")
    print("    แล้วค่อยรันไฟล์นี้ใหม่อีกครั้ง")
else:
    # วนดูทุกข้อความที่บอทได้รับ แล้วดึงชื่อ + chat id ออกมา
    print("✅ เจอแล้วครับ! คนที่ทักบอทมา:\n")
    seen = set()
    for item in data["result"]:
        # ข้อความอาจมาในรูปแบบ message หรือ edited_message
        msg = item.get("message") or item.get("edited_message")
        if not msg:
            continue
        chat = msg["chat"]
        chat_id = chat["id"]
        if chat_id in seen:
            continue
        seen.add(chat_id)
        name = chat.get("first_name", "") + " " + chat.get("last_name", "")
        print(f"   ชื่อ: {name.strip() or '(ไม่มีชื่อ)'}")
        print(f"   chat id: {chat_id}")
        print()
    print("👉 ก๊อปปี้เลข chat id ด้านบน ไปใส่ในไฟล์ config.py ที่ช่อง CHAT_ID")
