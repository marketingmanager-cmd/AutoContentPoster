# config.py
# โหลดค่าลับจากไฟล์ .env แล้วเอามาใช้ในโปรเจกต์
# ข้อดี: รหัสลับไม่อยู่ในโค้ด → ปลอดภัย และไม่หลุดขึ้น GitHub

import os
from dotenv import load_dotenv

# อ่านไฟล์ .env เข้ามาเก็บไว้ในระบบ
load_dotenv()

# ดึงค่าออกมาจาก .env ทีละตัว
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")   # คีย์ของ OpenAI
