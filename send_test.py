# send_test.py
# สคริปต์นี้ทำหน้าที่: ส่งข้อความทดสอบเข้า Telegram ของเรา

import requests
from config import BOT_TOKEN, CHAT_ID

# เตือนถ้ายังไม่ได้ใส่ chat id
if not CHAT_ID:
    print("⚠️  ยังไม่ได้ใส่ CHAT_ID ในไฟล์ config.py")
    print("    👉 รัน find_chat_id.py ก่อนเพื่อหา chat id ครับ")
    raise SystemExit  # หยุดโปรแกรม

# ที่อยู่ของคำสั่ง sendMessage = "ส่งข้อความ"
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# ข้อมูลที่จะส่งไป: ส่งหาใคร (chat_id) และส่งข้อความว่าอะไร (text)
payload = {
    "chat_id": CHAT_ID,
    "text": "สวัสดี ทดสอบ",
}

# ยิงคำขอออกไป
response = requests.post(url, data=payload)
result = response.json()

# ตรวจผลลัพธ์
if result.get("ok"):
    print("✅ ส่งสำเร็จ! ลองเช็คใน Telegram ได้เลยครับ")
else:
    print("❌ ส่งไม่สำเร็จ:", result)
