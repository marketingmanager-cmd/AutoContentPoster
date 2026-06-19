# make_post.py
# ขั้นตอนการทำงาน:
#   1. ถามหัวข้อโพสต์จากผู้ใช้
#   2. ให้ AI เขียนแคปชัน 3 สไตล์ + คิด "ชื่อปุ่มเก๋ๆ" ให้แต่ละอันด้วย
#   3. ส่งเข้า Telegram พร้อมปุ่มกดเลือกที่มีชื่อโดนๆ (ไม่ใช่ Caption 1/2/3 จืดๆ)
#   4. รันค้างไว้ คอยฟังการกดปุ่ม แล้วตอบกลับแบบสนุกๆ

from openai import OpenAI
from config import OPENAI_API_KEY
from telegram_helper import send_message, get_updates, answer_callback

# ---------- กำหนดสไตล์ทั้ง 3 แบบ ----------
# emoji = อีโมจิประจำสไตล์   /   fallback = ชื่อปุ่มสำรอง เผื่อ AI ไม่ได้คิดชื่อมาให้
STYLES = [
    {
        "label": "อารมณ์/บรรยากาศ",
        "color": "🟣",   # อีโมจิวงกลมสี ใช้เพิ่ม "สีสัน" บนปุ่ม (Telegram กำหนดสีพื้นปุ่มเองไม่ได้)
        "emoji": "🌙",
        "fallback": "สายฟีลกู๊ด",
        "instruction": "เขียนแนวอารมณ์และบรรยากาศ กระตุ้นให้ผู้อ่านรู้สึกอยากดื่มทันที เน้นภาพสัมผัส กลิ่น รสชาติ และความรู้สึก",
    },
    {
        "label": "เล่าเรื่อง/ที่มา (พรีเมียม)",
        "color": "🟡",
        "emoji": "✨",
        "fallback": "สายพรีเมียม",
        "instruction": "เขียนแนวเล่าเรื่องหรือที่มาของเมนู สร้างคุณค่าและความรู้สึกพรีเมียม เล่าถึงวัตถุดิบหรือแรงบันดาลใจเบื้องหลัง",
    },
    {
        "label": "สนุก/ชวนคอมเมนต์",
        "color": "🟠",
        "emoji": "🎉",
        "fallback": "สายฮาเฮ",
        "instruction": "เขียนแนวสนุก เป็นกันเอง ใส่คำถามหรือคำชวนให้คนคอมเมนต์และมีส่วนร่วม เพื่อเพิ่ม engagement",
    },
]


def split_button_and_caption(raw, fallback_label):
    """แยก 'ชื่อปุ่ม' กับ 'แคปชัน' ออกจากกัน
    เราขอให้ AI ตอบโดยขึ้นบรรทัดแรกว่า 'BUTTON: ชื่อปุ่ม' แล้วตามด้วยแคปชัน
    ถ้า AI ไม่ทำตามรูปแบบ ก็ใช้ชื่อปุ่มสำรอง (fallback) แทน
    """
    lines = raw.splitlines()
    if lines and lines[0].strip().upper().startswith("BUTTON:"):
        label = lines[0].split(":", 1)[1].strip() or fallback_label
        caption = "\n".join(lines[1:]).strip()
    else:
        label = fallback_label
        caption = raw
    return label[:28], caption   # ตัดชื่อปุ่มไม่ให้ยาวเกินไป


# ---------- ขั้นที่ 1: ถามหัวข้อ ----------
topic = input("หัวข้อโพสต์ คืออะไร? ").strip()

if not topic:
    print("⚠️  ยังไม่ได้พิมพ์หัวข้อ ลองรันใหม่อีกครั้งครับ")
    raise SystemExit

client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- ขั้นที่ 2: ให้ AI เขียนทั้ง 3 สไตล์ + ชื่อปุ่ม ----------
captions = []
total_tokens = 0

for i, style in enumerate(STYLES, start=1):
    print(f"🤖 ({i}/3) กำลังเขียนสไตล์: {style['label']} ...")
    response = client.chat.completions.create(
        model="gpt-5.4-mini",   # โมเดลตามที่กำหนด (ห้ามเปลี่ยน)
        messages=[
            {"role": "system", "content": (
                "คุณเป็นผู้ช่วยเขียนแคปชันโพสต์โซเชียลมีเดียภาษาไทย เขียนให้กระชับ น่าอ่าน "
                "และทุกครั้งต้องลงท้ายด้วยแฮชแท็ก (#) ที่เกี่ยวข้องอย่างน้อย 3-5 อันเสมอ\n"
                "รูปแบบการตอบ: บรรทัดแรกขึ้นต้นด้วย 'BUTTON:' ตามด้วยชื่อปุ่มสั้นๆ โดนๆ ไม่เกิน 3 คำ\n"
                "ชื่อปุ่มต้องครีเอทีฟ มีลูกเล่น/เล่นคำ สื่อถึงอารมณ์ของแคปชันสไตล์นั้นโดยเฉพาะ "
                "และต้องมีเอกลักษณ์ไม่ซ้ำแนวคำกว้างๆ ห้ามใช้คำธรรมดาอย่าง 'ลองเลย' หรือ 'จิบเลย' เดี่ยวๆ "
                "(ห้ามใส่คำว่า Caption) จากนั้นเว้นบรรทัด แล้วตามด้วยแคปชันเต็ม"
            )},
            {"role": "user", "content": f"ช่วยเขียนแคปชันโพสต์โซเชียลจากหัวข้อนี้: {topic}\nสไตล์ที่ต้องการ: {style['instruction']}"},
        ],
    )
    raw = response.choices[0].message.content.strip()
    total_tokens += response.usage.total_tokens

    button_label, caption = split_button_and_caption(raw, style["fallback"])
    captions.append({
        "label": style["label"],
        "emoji": style["emoji"],
        "button": f"{style['color']}{style['emoji']} {button_label}",   # วงกลมสี + อีโมจิ + ชื่อ
        "text": caption,
    })

# ---------- ขั้นที่ 3: รวมแคปชัน + ปุ่มชื่อเก๋ ----------
parts = []
for i, c in enumerate(captions, start=1):
    parts.append(f"{c['emoji']} แบบที่ {i} · {c['label']}\n{c['text']}")
full_text = (
    "✨ เลือกแคปชันที่ใช่ที่สุดของคุณ! ✨\n"
    "อ่านครบทั้ง 3 แบบแล้ว กดปุ่มข้างล่างเพื่อเลือกได้เลย 👇\n\n"
    + "\n\n━━━━━━━━━━━\n\n".join(parts)
)

# ปุ่มแบบ 1 ปุ่มต่อ 1 แถว (เรียงลงมา อ่านง่าย ชื่อยาวๆ ก็ไม่ล้น)
buttons = [[{"text": c["button"], "callback_data": str(i)}]
           for i, c in enumerate(captions, start=1)]

send_message(full_text, buttons=buttons)
print(f"\n📤 ส่งแคปชัน 3 สไตล์พร้อมปุ่มชื่อเก๋เข้า Telegram แล้ว ({total_tokens} tokens)")
for c in captions:
    print(f"   ปุ่ม: {c['button']}")

# ---------- ขั้นที่ 4: รันค้างไว้ คอยฟังการกดปุ่ม ----------
init = get_updates(timeout=0)
offset = init["result"][-1]["update_id"] + 1 if init.get("result") else None

print("⏳ บอทกำลังรอรับการกดปุ่ม... (กด Ctrl+C เพื่อหยุด)")

try:
    while True:
        data = get_updates(offset=offset, timeout=30)
        for update in data.get("result", []):
            offset = update["update_id"] + 1

            cb = update.get("callback_query")
            if not cb:
                continue

            answer_callback(cb["id"])
            choice = cb["data"]
            chosen = captions[int(choice) - 1]

            reply = (
                f"🎯 จัดไป! คุณเลือก “{chosen['button']}”\n"
                f"(สไตล์: {chosen['label']})\n\n"
                f"📋 ก๊อปไปโพสต์ได้เลย:\n\n{chosen['text']}"
            )
            send_message(reply)
            print(f"👉 ผู้ใช้กดเลือกแบบที่ {choice}")
except KeyboardInterrupt:
    print("\n👋 หยุดบอทแล้วครับ")
