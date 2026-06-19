# telegram_helper.py
# รวมฟังก์ชันคุยกับ Telegram ไว้ที่เดียว เผื่อสคริปต์อื่นเรียกใช้ซ้ำได้

import json
import requests
from config import BOT_TOKEN, CHAT_ID

# ที่อยู่หลักของ API ของบอทเรา (เอาไว้ต่อท้ายด้วยชื่อคำสั่งต่างๆ)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text, buttons=None):
    """ส่งข้อความเข้า Telegram
    ถ้าใส่ buttons มาด้วย จะมีปุ่มกดโผล่ใต้ข้อความ
    """
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
    }
    # ถ้ามีปุ่ม ให้แนบ "คีย์บอร์ดปุ่มกด" ไปด้วย (ต้องแปลงเป็นข้อความ JSON ก่อน)
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    result = requests.post(f"{API}/sendMessage", data=payload).json()
    if result.get("ok"):
        return True
    else:
        print("❌ ส่ง Telegram ไม่สำเร็จ:", result)
        return False


def get_updates(offset=None, timeout=30):
    """ถาม Telegram ว่ามีอะไรใหม่เข้ามาบ้าง (ข้อความ/การกดปุ่ม)
    timeout = รอแบบค้างสายได้นานกี่วินาที (ประหยัดการยิงซ้ำ)
    offset  = บอกว่าอ่านมาถึงอันไหนแล้ว จะได้ไม่อ่านซ้ำ
    """
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    return requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 10).json()


def answer_callback(callback_query_id):
    """ตอบรับการกดปุ่ม เพื่อให้ Telegram หยุดหมุนวงกลมโหลดที่ปุ่ม"""
    requests.post(f"{API}/answerCallbackQuery",
                  data={"callback_query_id": callback_query_id})
