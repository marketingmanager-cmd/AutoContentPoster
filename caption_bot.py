# caption_bot.py
# Flow 4 สเต็ป คุยกันใน Telegram ทีละสเต็ป (กดเลือกก่อนถึงไปต่อ):
#   /create → ใส่หัวข้อ (หรือพิมพ์หัวข้อตรงๆ ก็ได้)
#   สเต็ป 1 HOOK    : AI คิดพาดหัว 9 อัน (3 มุม×3) → กดเลือก 1
#   สเต็ป 2 CAPTION : เขียนแคปชัน 9 อัน (3 หมวด) จาก hook ที่เลือก → กดเลือก 1
#   สเต็ป 3 ARTWORK : ทำรูป 9 แบบ (3 หมวด) ใช้ข้อความจาก hook → กดเลือก 1
#   สเต็ป 4 ยืนยัน  : สรุปรูป+แคปชัน → กดยืนยัน → ส่งสรุป
#
# วิธีรัน: Mac `python3 -u caption_bot.py`  |  Windows `py -X utf8 -u caption_bot.py`

import os
import sys
import json
import time
import base64
import atexit
import colorsys
import unicodedata
import re
import requests
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from openai import OpenAI
from dotenv import load_dotenv

# ---------- โหลดค่าลับจาก .env ----------
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
missing = [n for n, v in [("TELEGRAM_TOKEN", TELEGRAM_TOKEN), ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
                          ("OPENAI_API_KEY", OPENAI_API_KEY)] if not v]
if missing:
    print("❌ ไม่พบค่าใน .env:", ", ".join(missing))
    raise SystemExit

API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- ตั้งค่าแบรนด์: ร้าน + ลูกค้า + โทน (แก้ตรงนี้ให้ตรงกับร้านคุณ) ----------
BRAND = "ร้านไทยจราจร — ร้านขายอุปกรณ์จราจรและอุปกรณ์เซฟตี้/ความปลอดภัย (เช่น กรวยจราจร แผงกั้น ป้ายเตือน ไฟกระพริบ เสื้อสะท้อนแสง อุปกรณ์ดับเพลิง อุปกรณ์ความปลอดภัยในงานก่อสร้าง/โรงงาน/บนถนน)"
AUDIENCE = "ผู้รับเหมาก่อสร้าง โรงงาน หน่วยงานราชการ/ท้องถิ่น เจ้าหน้าที่ความปลอดภัย (จป.) และคนที่ดูแลความปลอดภัยบนถนน/ในไซต์งาน"
TONE = "เป็นกันเอง เหมือนเพื่อนที่รู้เรื่องความปลอดภัยมาเล่าให้ฟัง จริงใจ ไม่ขายแข็ง ให้ความรู้/เตือนภัยเป็นหลัก"
PERSONA = (
    f"แบรนด์: {BRAND}\n"
    f"กลุ่มลูกค้า: {AUDIENCE}\n"
    f"โทนการเขียน: {TONE}\n"
    "สำคัญ: ทุกคอนเทนต์ต้องเชื่อมโยงกับ 'ความปลอดภัย/การป้องกัน/อุปกรณ์จราจร-เซฟตี้' ให้เป็นธรรมชาติ "
    "ห้ามโยงไปเรื่องที่ไม่เกี่ยวกับแบรนด์ (เช่น อาหาร สุขภาพส่วนตัว ชีวิตออฟฟิศทั่วไป) เด็ดขาด"
)
# บล็อกข้อมูลติดต่อ — แทรกท้ายแคปชันทุกอัน (ก่อนแฮชแท็ก)
CONTACT = (
    "📌 สนใจ อุปกรณ์จราจร อุปกรณ์เซฟตี้ ติดต่อได้ที่ 📌\n"
    "Line ID: @trafficthai\n"
    "Tel: 02-114-7006\n"
    "Email: sale@smartbestbuys.com\n"
    "Website : https://trafficthai.com/shop"
)


def with_contact(text):
    # แทรกบล็อกติดต่อก่อน 'กลุ่มแฮชแท็กท้ายโพสต์' — ถ้าไม่เจอแฮชแท็ก ต่อท้ายสุด
    lines = text.rstrip().split("\n")
    idx = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#"):
            rest = [x.strip() for x in lines[i:] if x.strip()]
            if rest and all(r.startswith("#") for r in rest):   # ตั้งแต่บรรทัดนี้ลงไปเป็นแฮชแท็กล้วน
                idx = i
                break
    if idx is None:
        return text.rstrip() + "\n\n" + CONTACT
    before = "\n".join(lines[:idx]).rstrip()
    tags = "\n".join(lines[idx:]).strip()
    return before + "\n\n" + CONTACT + "\n\n" + tags


# กฎการเขียนที่ใช้ร่วมกันทุกครั้ง
WRITING_RULES = (
    "เขียนเหมือนคนคุยกับเพื่อน ไม่ใช่หุ่นยนต์เขียนโฆษณา "
    "ห้ามพูดลอยๆ แบบ 'ดีที่สุด' 'สูตรใหม่ล่าสุด' "
    "ถ้ายังไม่มีข้อมูลจริงของสินค้า ห้ามแต่งตัวเลขหรือข้อเท็จจริงขึ้นเอง "
    "ให้เล่นที่ 'มุม' กับ 'อารมณ์' แทน "
    "แฮชแท็กต้องเกี่ยวกับความปลอดภัย/จราจร/งานก่อสร้าง/แบรนด์ ห้ามใช้แฮชแท็กนอกเรื่อง (เช่น #ออฟฟิศ #สุขภาพ)"
)


# ---------- ฟังก์ชันคุยกับ Telegram ----------
def nfc(t):
    return unicodedata.normalize("NFC", t)


def send_message(text, buttons=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": nfc(text)}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    requests.post(f"{API}/sendMessage", data=payload)


def send_photo(path, caption=None, button=None):
    with open(path, "rb") as f:
        data = {"chat_id": TELEGRAM_CHAT_ID}
        if caption:
            data["caption"] = nfc(caption)
        if button:
            data["reply_markup"] = json.dumps({"inline_keyboard": [[button]]})
        requests.post(f"{API}/sendPhoto", files={"photo": f}, data=data)


def get_updates(offset=None, timeout=30):
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    return requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 10).json()


def answer_callback(cid):
    requests.post(f"{API}/answerCallbackQuery", data={"callback_query_id": cid})


# ---------- Facebook Page (โพสต์ขึ้นเพจด้วย Page Access Token) ----------
FB_API = "https://graph.facebook.com/v21.0"


def fb_pages():
    # อ่านรายชื่อเพจจาก .env (FB_PAGES = JSON list ของ {id,name,token})
    raw = os.getenv("FB_PAGES")
    if raw:
        try:
            return json.loads(raw)
        except Exception as e:
            print("⚠️ อ่าน FB_PAGES ไม่ได้:", repr(e))
    pid, tok = os.getenv("FB_PAGE_ID"), os.getenv("FB_PAGE_TOKEN")   # รองรับแบบเพจเดียวเดิม
    if pid and tok:
        return [{"id": pid, "name": "Facebook", "token": tok}]
    return []


def fb_status():
    # คืนสถานะของทุกเพจ (เช็ค token ใช้ได้ไหม)
    out = []
    for p in fb_pages():
        try:
            r = requests.get(f"{FB_API}/{p['id']}", params={"fields": "name", "access_token": p["token"]}, timeout=15).json()
            out.append({"id": p["id"], "name": r.get("name", p.get("name", "")),
                        "connected": "name" in r, "error": r.get("error", {}).get("message")})
        except Exception as e:
            out.append({"id": p["id"], "name": p.get("name", ""), "connected": False, "error": str(e)})
    return {"pages": out}


def fb_post(caption, image_path, page):
    # โพสต์ขึ้นเพจที่ระบุ (page = {id, token}) — มีรูปใช้ /photos, ไม่มีรูปใช้ /feed
    tok, pid = page["token"], page["id"]
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            r = requests.post(f"{FB_API}/{pid}/photos",
                              data={"caption": caption, "access_token": tok},
                              files={"source": f}, timeout=60).json()
    else:
        r = requests.post(f"{FB_API}/{pid}/feed",
                          data={"message": caption, "access_token": tok}, timeout=60).json()
    if not ("id" in r or "post_id" in r):
        raise RuntimeError(f"FB error: {r}")
    post_id = r.get("post_id") or r.get("id")
    # ดึงลิงก์โพสต์จริง (permalink) เพื่อให้กดเปิดดูได้ถูกต้อง
    try:
        pr = requests.get(f"{FB_API}/{post_id}",
                          params={"fields": "permalink_url", "access_token": tok}, timeout=20).json()
        if pr.get("permalink_url"):
            r["permalink_url"] = pr["permalink_url"]
    except Exception:
        pass
    return r


# ---------- ตัวช่วยทั่วไป + สี ----------
def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    final = []
    for ln in lines:
        if draw.textlength(ln, font=font) <= max_w:
            final.append(ln)
        else:
            s = ""
            for ch in ln:
                if draw.textlength(s + ch, font=font) <= max_w:
                    s += ch
                else:
                    final.append(s)
                    s = ch
            if s:
                final.append(s)
    return final


def _hex_to_rgb(h):
    h = h.strip().lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _rot_hue(c, dh):
    h, l, s = colorsys.rgb_to_hls(c[0] / 255, c[1] / 255, c[2] / 255)
    r, g, b = colorsys.hls_to_rgb((h + dh) % 1.0, l, s)
    return (int(r * 255), int(g * 255), int(b * 255))


DEFAULT_COLORS = ((242, 226, 205), (74, 51, 38))


def pick_colors(topic):
    try:
        resp = client.chat.completions.create(
            model="gpt-5.4-mini",
            messages=[
                {"role": "system", "content":
                    "เลือกคู่สีพื้นหลังที่เข้ากับเนื้อหาของหัวข้อ ตอบเป็นโค้ดสีฮกซ์ 2 สีคั่นช่องว่าง "
                    "สีอ่อนก่อนแล้วสีเข้ม เช่น '#F2E2CD #4A3326' ตอบแค่ 2 สีนี้เท่านั้น"},
                {"role": "user", "content": f"หัวข้อ: {topic}"},
            ],
        )
        parts = resp.choices[0].message.content.strip().split()
        return _hex_to_rgb(parts[0]), _hex_to_rgb(parts[1])
    except Exception as e:
        print("⚠️ เลือกสีไม่สำเร็จ:", repr(e))
        return DEFAULT_COLORS


def art_palettes(topic):
    # 3 โทนสีจากเนื้อหา (หมุนเฉดสีเล็กน้อยให้ต่างกัน)
    t, b = pick_colors(topic)
    return [(t, b), (_rot_hue(t, 0.08), _rot_hue(b, 0.08)), (_rot_hue(t, -0.08), _rot_hue(b, -0.08))]


# ---------- การ์ดภาพ ----------
CARD_SIZE = 1080
FONT_BOLD = os.path.join(os.path.dirname(__file__), "fonts", "Sarabun-Bold.ttf")
WHITE = (250, 248, 244)
BRAND = "SMARTBESTBUYS"
SOCIALS = ["f", "tt", "yt", "x", "ig", "web"]
LAYOUTS = ["วางข้อความกลาง", "วางข้อความบน", "ชิดซ้าย + แถบสี"]


def _paper_texture(img):
    noise = Image.effect_noise((CARD_SIZE, CARD_SIZE), 24).convert("L").filter(ImageFilter.GaussianBlur(1.5))
    img = Image.blend(img, Image.merge("RGB", (noise, noise, noise)), 0.05)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.line([(0, 330), (CARD_SIZE, 140)], fill=(255, 255, 255, 14), width=46)
    od.line([(0, 760), (CARD_SIZE, 900)], fill=(0, 0, 0, 16), width=70)
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _social(d, kind, cx, cy, r, gc):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255))
    lw = max(2, int(r * 0.12))
    if kind in ("f", "x"):
        d.text((cx, cy), "f" if kind == "f" else "X",
               font=ImageFont.truetype(FONT_BOLD, int(r * 1.5)), fill=gc, anchor="mm")
    elif kind == "yt":
        d.polygon([(cx - r * 0.32, cy - r * 0.42), (cx - r * 0.32, cy + r * 0.42), (cx + r * 0.5, cy)], fill=gc)
    elif kind == "ig":
        rr = r * 0.52
        d.rounded_rectangle((cx - rr, cy - rr, cx + rr, cy + rr), radius=int(r * 0.26), outline=gc, width=lw)
        d.ellipse((cx - r * 0.22, cy - r * 0.22, cx + r * 0.22, cy + r * 0.22), outline=gc, width=lw)
        d.ellipse((cx + r * 0.27 - 4, cy - r * 0.34 - 4, cx + r * 0.27 + 4, cy - r * 0.34 + 4), fill=gc)
    elif kind == "tt":
        d.ellipse((cx - r * 0.42, cy + r * 0.04, cx - r * 0.08, cy + r * 0.38), fill=gc)
        d.line([(cx - r * 0.1, cy + r * 0.22), (cx - r * 0.1, cy - r * 0.42)], fill=gc, width=lw)
        d.line([(cx - r * 0.1, cy - r * 0.42), (cx + r * 0.32, cy - r * 0.26)], fill=gc, width=lw)
    elif kind == "web":
        d.ellipse((cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55), outline=gc, width=lw)
        d.ellipse((cx - r * 0.24, cy - r * 0.55, cx + r * 0.24, cy + r * 0.55), outline=gc, width=max(2, lw - 1))
        d.line([(cx - r * 0.55, cy), (cx + r * 0.55, cy)], fill=gc, width=max(2, lw - 1))


def make_card(text, colors=None, layout=1, out_path="card.png"):
    top, bottom = colors or DEFAULT_COLORS
    bg = _mix(bottom, (12, 16, 24), 0.32)
    img = _paper_texture(Image.new("RGB", (CARD_SIZE, CARD_SIZE), bg))
    d = ImageDraw.Draw(img)

    # โลโก้แบรนด์มุมขวาบน
    d.rounded_rectangle((CARD_SIZE - 300, 52, CARD_SIZE - 60, 132), radius=18, fill=(255, 255, 255))
    d.text((CARD_SIZE - 180, 92), BRAND, font=ImageFont.truetype(FONT_BOLD, 30), fill=bg, anchor="mm")

    # หัวข้อ (ย่อขนาดอัตโนมัติ + เว้นบรรทัด 1.6 กันสระซ้อน)
    x = 90
    max_w = CARD_SIZE - 2 * x
    for size in (132, 116, 100, 86, 72, 60):
        fnt = ImageFont.truetype(FONT_BOLD, size)
        lines = _wrap(d, text, fnt, max_w)
        lh = int(size * 1.6)
        if len(lines) * lh <= 560:
            break

    block_h = len(lines) * lh
    start_y = 250 if layout == 2 else 300 + (560 - block_h) // 2

    if layout == 3:  # ชิดซ้าย + แถบสี
        d.rounded_rectangle((x - 4, start_y + 8, x + 10, start_y + block_h - 12), radius=5, fill=_mix(top, WHITE, 0.2))
        y = start_y
        for ln in lines:
            d.text((x + 30, y), ln, font=fnt, fill=(255, 255, 255), anchor="la")
            y += lh
    else:            # จัดกลาง (layout 1, 2)
        y = start_y
        for ln in lines:
            d.text((CARD_SIZE // 2, y), ln, font=fnt, fill=(255, 255, 255), anchor="ma")
            y += lh

    # แถบล่าง: แบรนด์ + ไอคอนโซเชียล
    d.text((CARD_SIZE // 2, 930), BRAND, font=ImageFont.truetype(FONT_BOLD, 34), fill=(235, 240, 248), anchor="mm")
    r, gap = 26, 86
    sx = CARD_SIZE // 2 - (len(SOCIALS) - 1) * gap // 2
    for i, k in enumerate(SOCIALS):
        _social(d, k, sx + i * gap, 1010, r, bg)

    img.save(out_path)
    return out_path


IMAGE_MODEL = "gpt-image-2"   # = GPT Images 2.0 (ห้ามเปลี่ยนเป็นตัวอื่น)
IMAGE_QUALITY = "low"


BRAND_LOGO = os.path.join(os.path.dirname(__file__), "brand_logo.png")   # โลโก้ร้าน (ถ้ามี → ซ้อนมุมขวาบนทุกรูป)


def _overlay_logo(path):
    # ซ้อนโลโก้ร้านไว้มุมขวาบนของรูป (มีพื้นขาวโปร่งให้เห็นชัดบนทุกพื้นหลัง)
    if not os.path.exists(BRAND_LOGO):
        return
    try:
        base = Image.open(path).convert("RGBA")
        logo = Image.open(BRAND_LOGO).convert("RGBA")
        W = base.width
        tw = int(W * 0.26)                                  # โลโก้กว้าง ~26% ของรูป
        logo = logo.resize((tw, max(1, int(logo.height * tw / logo.width))))
        m = int(W * 0.035)
        pad = int(tw * 0.07)
        bw, bh = logo.width + pad * 2, logo.height + pad * 2
        bx, by = base.width - bw - m, m
        backdrop = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(backdrop).rounded_rectangle([0, 0, bw - 1, bh - 1],
                                                   radius=int(bh * 0.28), fill=(255, 255, 255, 215))
        base.alpha_composite(backdrop, (bx, by))
        base.alpha_composite(logo, (bx + pad, by + pad))
        base.convert("RGB").save(path, "PNG")
    except Exception as e:
        print("overlay logo fail:", str(e)[:120])


def make_ai_images(hook, caption, n=2):
    # สร้างรูปด้วย GPT Images 2.0 (low) — prompt ตามฟอร์แมตที่กำหนด, ขนาด 1:1, จำนวน n รูป
    prompt = f"สร้างรูปขนาด 1:1 ตามนี้\nHook : {hook}\nCaption : {caption}"
    resp = client.images.generate(model=IMAGE_MODEL, prompt=prompt,
                                   size="1024x1024", quality=IMAGE_QUALITY, n=n)
    arts = []
    for i, d in enumerate(resp.data):
        path = os.path.join(os.path.dirname(__file__), f"art_{i}.png")
        with open(path, "wb") as f:
            f.write(base64.b64decode(d.b64_json))
        _overlay_logo(path)                                 # ซ้อนโลโก้ร้านมุมขวาบน
        arts.append({"cat": "GPT Image", "path": path})
    return arts


# ---------- AI: คิด hook / caption ----------
def parse_groups(text):
    # อ่านผลลัพธ์รูปแบบ "หมวด: ชื่อ" ตามด้วยรายการ → [(ชื่อหมวด, [item,...]), ...]
    groups, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("หมวด") and (":" in line or "：" in line):
            name = line.split(":", 1)[-1].strip() if ":" in line else line.split("：", 1)[-1].strip()
            cur = [name, []]
            groups.append(cur)
        elif cur is not None:
            item = line.lstrip("-•*0123456789.()） ").strip()
            if item:
                cur[1].append(item)
    return groups


def _flatten(groups):
    # แปลงเป็นลิสต์ {cat, text} (เอาหมวดละไม่เกิน 3)
    out = []
    for name, items in groups:
        for it in items[:3]:
            out.append({"cat": name, "text": it})
    return out


def parse_caption_groups(text):
    # อ่านแคปชัน "หลายย่อหน้า" ได้ — หมวดขึ้นต้นด้วย 'หมวด:' / แต่ละแคปชันคั่นด้วยบรรทัด ===
    groups, cur, buf = [], None, []

    def flush():
        if cur is not None and buf:
            item = "\n".join(buf).strip()
            if item:
                cur[1].append(item)
        buf.clear()

    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("หมวด") and (":" in s or "：" in s):
            flush()
            name = s.split(":", 1)[-1].strip() if ":" in s else s.split("：", 1)[-1].strip()
            cur = [name, []]
            groups.append(cur)
        elif s in ("===", "---", "###", "* * *"):
            flush()
        else:
            buf.append(raw.rstrip())
    flush()
    return groups


def _news_block(news):
    # สร้างบล็อกข้อเท็จจริงของข่าว + กฎ "ห้ามแต่งเกินข่าว" สำหรับ user message (โหมดข่าว)
    if not news:
        return None
    title = (news.get("title") or "").strip()
    summary = (news.get("summary") or "").strip()
    source = (news.get("source") or "").strip()
    lines = [f"พาดหัวข่าว: {title}"]
    if summary:
        lines.append(f"สรุปข้อเท็จจริง: {summary}")
    if source:
        lines.append(f"แหล่งข่าว: {source}")
    return "\n".join(lines)


_NEWS_RULE = (
    "นี่คือ 'โหมดอิงข่าวจริง' — เนื้อหาทุกชิ้นต้องเกาะข่าวนี้โดยตรง สื่อถึงเหตุการณ์/ประเด็นในข่าวจริง ห้ามออกนอกเรื่อง\n"
    "ใช้ได้เฉพาะข้อเท็จจริงที่ให้ไว้ด้านล่างเท่านั้น — ห้ามแต่งตัวเลข ชื่อคน/บริษัท สถานที่ วันเวลา หรือรายละเอียดที่ไม่มีในข่าว\n"
    "ถ้าจะเชื่อมโยงกับร้าน/อุปกรณ์จราจร-เซฟตี้ ให้ทำเป็นมุมบทเรียน/ข้อคิด/การเตือนภัยจากข่าว ไม่ใช่กุข้อมูลหรือยัดขายแข็งๆ\n"
)


def gen_hooks(topic, news=None):
    nb = _news_block(news)
    if nb:
        sys = (
            "คุณเป็นครีเอเตอร์คอนเทนต์ไทยที่เขียนเหมือนเพื่อนชวนคุย\n"
            f"{PERSONA}\n" + _NEWS_RULE +
            "ช่วยคิดพาดหัว (hook) สำหรับโพสต์โซเชียลที่ 'อิงข่าวนี้' 9 อัน แบ่งเป็น 3 มุม หมวดละ 3 อัน\n"
            "**แต่ละ hook ต้องมี 2 บรรทัด:**\n"
            "  บรรทัด 1 = พาดหัวสะดุด อ่านแล้วอยากรู้ต่อ (ต้องสะท้อนเหตุการณ์ในข่าว)\n"
            "  บรรทัด 2 = บรรทัดขยายที่บอกว่า 'เกี่ยวอะไรกับคนอ่าน' (พูดกับเขาตรงๆ)\n"
            f"กฎ: {WRITING_RULES}\n"
            "รูปแบบการตอบ (ตอบแค่นี้) — แต่ละหมวดขึ้นต้น 'หมวด:' และคั่นแต่ละ hook ด้วยบรรทัด === :\n"
            "หมวด: <ชื่อมุม>\n<บรรทัดพาดหัว>\n<บรรทัดขยาย>\n===\n<พาดหัว>\n<ขยาย>\n===\n<พาดหัว>\n<ขยาย>\n"
            "(ทำครบ 3 หมวด)")
        user = f"ข่าวที่ต้องอิง:\n{nb}"
    else:
        sys = (
            "คุณเป็นครีเอเตอร์คอนเทนต์ไทยที่เขียนเหมือนเพื่อนชวนคุย\n"
            f"{PERSONA}\n"
            "ช่วยคิดพาดหัว (hook) สำหรับโพสต์โซเชียล 9 อัน แบ่งเป็น 3 มุมที่เหมาะกับหัวข้อ "
            "(เลือกมุมเอง อย่าใช้หมวดตายตัว) หมวดละ 3 อัน\n"
            "**แต่ละ hook ต้องมี 2 บรรทัด:**\n"
            "  บรรทัด 1 = พาดหัวสะดุด อ่านแล้วอยากรู้ต่อ\n"
            "  บรรทัด 2 = บรรทัดขยายที่บอกว่า 'เกี่ยวอะไรกับคนอ่าน' (พูดกับเขาตรงๆ)\n"
            f"กฎ: {WRITING_RULES}\n"
            "รูปแบบการตอบ (ตอบแค่นี้) — แต่ละหมวดขึ้นต้น 'หมวด:' และคั่นแต่ละ hook ด้วยบรรทัด === :\n"
            "หมวด: <ชื่อมุม>\n<บรรทัดพาดหัว>\n<บรรทัดขยาย>\n===\n<พาดหัว>\n<ขยาย>\n===\n<พาดหัว>\n<ขยาย>\n"
            "(ทำครบ 3 หมวด)")
        user = f"หัวข้อ: {topic}"
    resp = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
    )
    return _flatten(parse_caption_groups(resp.choices[0].message.content.strip()))


def gen_captions(topic, hook, news=None):
    nb = _news_block(news)
    base = (
        "เขียนแคปชันโพสต์โซเชียลภาษาไทย 9 อัน แบ่งเป็น 3 หมวดที่เหมาะกับงานนี้ "
        "(คุณเลือกแนวเอง เช่น สั้นกระชับ / เล่าเรื่อง / ขายตรง) หมวดละ 3 อัน\n"
        f"{PERSONA}\n"
        + (_NEWS_RULE if nb else "") +
        f"กฎ: {WRITING_RULES}\n"
        "แต่ละแคปชันต้อง: (1) มีเนื้อหาครบ เกริ่นนำ–ขยายความ–ปิดท้ายชวนแอ็กชัน "
        "(2) จัดย่อหน้าสวยงาม เว้นบรรทัดระหว่างย่อหน้า (3) แทรกอีโมจิพอเหมาะ "
        "(4) ปิดท้ายด้วยแฮชแท็ก 3-5 อัน\n"
        "ตอบตามรูปแบบนี้เป๊ะ ห้ามมีอย่างอื่น — แต่ละหมวดขึ้นต้นด้วย 'หมวด:' "
        "และคั่นแต่ละแคปชันด้วยบรรทัดที่มีแค่ === :\n"
        "หมวด: <ชื่อหมวด>\n<แคปชันเต็ม หลายย่อหน้า>\n===\n<แคปชันเต็ม>\n===\n<แคปชันเต็ม>\n"
        "(ทำครบ 3 หมวด)")
    if nb:
        user = f"ข่าวที่ต้องอิง:\n{nb}\n\nพาดหัวที่เลือก: {hook}"
    else:
        user = f"หัวข้อ: {topic}\nพาดหัวที่เลือก: {hook}"
    resp = client.chat.completions.create(
        model="gpt-5.4-mini",
        messages=[{"role": "system", "content": base}, {"role": "user", "content": user}],
    )
    items = _flatten(parse_caption_groups(resp.choices[0].message.content.strip()))
    for it in items:                       # แทรกข้อมูลติดต่อก่อนแฮชแท็กทุกแคปชัน
        it["text"] = with_contact(it["text"])
    return items


def browse_news(query="", with_image=False):
    # ค้นข่าวจริงด้วย gpt-5.4-mini + web search → คืนหัวข้อ ~20 อัน (อิง fact + แหล่ง)
    # ถ้ามี query → ค้นเฉพาะเรื่องนั้น; ถ้าไม่มี → ค้นกว้างๆ ตามกลุ่มเป้าหมาย
    # with_image=True → ขอ URL รูปภาพประกอบข่าวด้วย (สำหรับหน้าการ์ด social listening)
    query = (query or "").strip()
    if query:
        focus = (f"ค้นข่าว/ประเด็นที่เกี่ยวกับ \"{query}\" โดยเฉพาะ ประมาณ 20 หัวข้อ "
                 "จากหลายแหล่งและหลายแง่มุม ทั้งในและต่างประเทศ\n")
    else:
        focus = ("ช่วยค้นเว็บหาข่าว/ประเด็นที่น่าเอาไปทำคอนเทนต์ ประมาณ 20 หัวข้อ "
                 "แบ่งเป็นหลายแนว (เช่น ฝ่ายอาคาร, กฎหมายอาคาร, ความปลอดภัย/ไฟไหม้, จราจร — ในและต่างประเทศ; "
                 "เลือกแนวที่น่าสนใจกับกลุ่มเป้าหมายเอง)\n")
    if with_image:
        fmt = ("รูปแบบ '- พาดหัว | สรุปสั้น 1 ประโยค | ชื่อแหล่งข่าว | URL ลิงก์หน้าข่าวต้นฉบับ':\n"
               "หมวด: <ชื่อแนว>\n- <พาดหัว> | <สรุป> | <แหล่ง> | <URL ลิงก์ข่าวจริงที่เปิดอ่านได้>\n- ...\n(รวม ~20 หัวข้อ)")
    else:
        fmt = ("รูปแบบ '- พาดหัว | สรุปสั้น 1 ประโยค | ชื่อแหล่งข่าว':\n"
               "หมวด: <ชื่อแนว>\n- <พาดหัว> | <สรุป> | <แหล่ง>\n- ...\n(รวม ~20 หัวข้อ)")
    prompt = (
        focus +
        f"{PERSONA}\n"
        "ทุกหัวข้อต้องอิงข่าวจริงที่ค้นเจอเท่านั้น ห้ามแต่งขึ้นเอง\n"
        "ตอบตามรูปแบบนี้เป๊ะ ห้ามมีอย่างอื่น — แต่ละแนว/มุมขึ้นต้น 'หมวด:' และแต่ละหัวข้อ 1 บรรทัด "
        + fmt
    )
    r = client.responses.create(model="gpt-5.4-mini", tools=[{"type": "web_search"}], input=prompt)
    topics, seen = [], set()
    for cat, items in parse_groups(r.output_text.strip()):
        for it in items:
            p = [x.strip() for x in it.split("|")]
            title = p[0] if p else it
            key = title.lower()
            if key in seen:          # ตัดหัวข้อซ้ำ
                continue
            seen.add(key)
            url = p[3] if len(p) > 3 else ""
            if not url.startswith("http"):
                url = ""
            topics.append({"cat": cat, "title": title,
                           "summary": p[1] if len(p) > 1 else "",
                           "source": p[2] if len(p) > 2 else "",
                           "url": url, "image": ""})
    topics = topics[:20]
    if with_image:                    # ดึงรูปหน้าปกข่าว (og:image) แบบขนาน
        urls = [t["url"] for t in topics]
        with ThreadPoolExecutor(max_workers=10) as ex:
            imgs = list(ex.map(fetch_og_image, urls))
        for t, im in zip(topics, imgs):
            t["image"] = im
    return topics


_OG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\'][^>]*>',
    re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']+)["\']', re.I)

def fetch_og_image(url):
    # เปิดหน้าข่าว แล้วดึง URL รูปหน้าปก (og:image / twitter:image) — best-effort, ล้มเหลวคืน ""
    if not url:
        return ""
    try:
        h = {"User-Agent": "Mozilla/5.0 (compatible; ContentRadar/1.0)"}
        r = requests.get(url, headers=h, timeout=5)
        html = r.text[:300000]
        for tag in _OG_RE.findall(html):
            m = _CONTENT_RE.search(tag)
            if m:
                img = m.group(1).strip()
                if img.startswith("//"):
                    img = "https:" + img
                if img.startswith("http"):
                    return img
    except Exception:
        pass
    return ""


# ---------- แสดงผลแต่ละสเต็ปใน Telegram ----------
def show_topics():
    topics = state["topics"]
    send_message(f"📰 /browse — เจอ {len(topics)} หัวข้อข่าวน่าทำคอนเทนต์ เลือก 1 หัวข้อ 👇")
    lines, cur = [], None
    for i, t in enumerate(topics):
        if t["cat"] != cur:
            cur = t["cat"]
            lines.append(f"\n📌 {cur}")
        extra = f" — {t['summary']}" if t["summary"] else ""
        lines.append(f"{i + 1}. {t['title']}{extra}")
    # ปุ่มเลข เรียงแถวละ 5
    nums = [{"text": str(i + 1), "callback_data": f"topic:{i}"} for i in range(len(topics))]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    send_message("\n".join(lines), buttons=rows)


def show_hooks():
    hooks = state["hooks"]
    lines, rows, cur, rowbuf = ["✨ สเต็ป 1/4 — เลือกพาดหัว (Hook) ที่ชอบ 1 อัน 👇"], [], None, []
    for i, h in enumerate(hooks):
        if h["cat"] != cur:
            if rowbuf:
                rows.append(rowbuf)
                rowbuf = []
            cur = h["cat"]
            lines.append(f"\n📌 {cur}")
        # แสดง 2 ชั้น: พาดหัว + บรรทัดขยาย
        bits = h["text"].split("\n", 1)
        lines.append(f"\n{i + 1}. {bits[0].strip()}")
        if len(bits) > 1 and bits[1].strip():
            lines.append(f"     ↳ {bits[1].strip()}")
        rowbuf.append({"text": str(i + 1), "callback_data": f"hook:{i}"})
    if rowbuf:
        rows.append(rowbuf)
    send_message("\n".join(lines), buttons=rows)


def show_captions():
    caps = state["captions"]
    send_message("📝 สเต็ป 2/4 — เลือกแคปชันที่ชอบ 1 อัน (ดู 3 หมวดด้านล่าง) 👇")
    cur, buf, btns = None, [], []
    sep = "\n\n━━━━━━━━━\n\n"

    def flush(name, buf, btns):
        send_message(f"📌 หมวด: {name}" + sep + sep.join(buf), buttons=[btns])

    for i, c in enumerate(caps):
        if c["cat"] != cur:
            if buf:
                flush(cur, buf, btns)
            cur, buf, btns = c["cat"], [], []
        buf.append(f"〔 ตัวเลือก {i + 1} 〕\n{c['text']}")   # เลข + แคปชันหลายย่อหน้า
        btns.append({"text": str(i + 1), "callback_data": f"cap:{i}"})
    if buf:
        flush(cur, buf, btns)


def show_artworks():
    arts = state["arts"]
    send_message("🎨 สเต็ป 3/4 — เลือกรูปที่ชอบ 1 อัน (AI สร้างมา 2 แบบ) 👇")
    for i, a in enumerate(arts):
        send_photo(a["path"], caption=f"แบบ {i + 1}",
                   button={"text": f"✅ เลือกแบบ {i + 1}", "callback_data": f"art:{i}"})


def show_confirm():
    state["step"] = "confirm"
    save_state()
    send_photo(state["sel_art"]["path"], caption="🔎 สเต็ป 4/4 — ตรวจสอบก่อนยืนยัน")
    send_message("แคปชันที่เลือก:\n\n" + state["sel_caption"]["text"],
                 buttons=[[{"text": "✅ ยืนยันโพสต์นี้", "callback_data": "confirm"}]])


# ---------- สถานะ + ล็อก ----------
state = {"step": "idle", "topic": None, "topics": [], "hooks": [], "sel_hook": None,
         "captions": [], "sel_caption": None, "arts": [], "sel_art": None}
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
LOCK_FILE = os.path.join(os.path.dirname(__file__), "caption_bot.lock")


def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        print("⚠️ บันทึกสถานะไม่สำเร็จ:", repr(e))


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state.update(json.load(f))
        except Exception as e:
            print("⚠️ โหลดสถานะไม่สำเร็จ:", repr(e))


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            old = int(open(LOCK_FILE).read().strip())
            os.kill(old, 0)
            print(f"⚠️ บอทกำลังรันอยู่แล้ว (PID {old}) — ปิดตัวเก่าก่อนนะครับ")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(LOCK_FILE) and os.remove(LOCK_FILE))


def start_topic(topic):
    state["topic"] = topic
    state["sel_hook"] = state["sel_caption"] = state["sel_art"] = None
    send_message(f"🤖 รับหัวข้อ “{topic}” แล้ว — กำลังคิดพาดหัว 9 แบบ...")
    state["hooks"] = gen_hooks(topic)
    state["step"] = "pick_hook"
    save_state()
    if state["hooks"]:
        show_hooks()
    else:
        send_message("ขออภัย คิดพาดหัวไม่สำเร็จ ลองพิมพ์หัวข้อใหม่อีกครั้งครับ")


# ---------- main loop ----------
def main():
    acquire_lock()
    load_state()
    bot_start = time.time()
    offset = None
    while True:
        drained = get_updates(offset=offset, timeout=0).get("result", [])
        if not drained:
            break
        offset = drained[-1]["update_id"] + 1

    send_message("👋 บอทพร้อมแล้ว! พิมพ์หัวข้อที่อยากโพสต์มาได้เลย (หรือ /create)")
    print("⏳ รอรับข้อความ/การกดปุ่ม... (Ctrl+C เพื่อหยุด)")

    try:
        while True:
            try:
                data = get_updates(offset=offset, timeout=30)
            except Exception as e:
                print("⚠️ เชื่อมต่อ Telegram สะดุด ลองใหม่:", repr(e))
                time.sleep(3)
                continue
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    cb = update.get("callback_query")
                    if cb:
                        answer_callback(cb["id"])
                        act = cb["data"]

                        if act.startswith("topic:"):
                            n = int(act.split(":")[1])
                            if n < len(state["topics"]):
                                t = state["topics"][n]
                                topic_text = t["title"]
                                if t["summary"]:
                                    topic_text += f"\n(ข้อเท็จจริงอ้างอิง: {t['summary']})"
                                if t["source"]:
                                    topic_text += f"\n(แหล่ง: {t['source']})"
                                start_topic(topic_text)   # เข้าสู่ flow เดิม: hook → caption → รูป
                            else:
                                send_message("พิมพ์ /browse เพื่อค้นข่าวใหม่ก่อนนะครับ 🙂")

                        elif act.startswith("hook:"):
                            n = int(act.split(":")[1])
                            if n < len(state["hooks"]):
                                state["sel_hook"] = state["hooks"][n]
                                send_message(f"✅ เลือกพาดหัว:\n“{state['sel_hook']['text']}”\n\n📝 กำลังเขียนแคปชัน 9 แบบ...")
                                state["captions"] = gen_captions(state["topic"], state["sel_hook"]["text"])
                                state["step"] = "pick_caption"
                                save_state()
                                show_captions() if state["captions"] else send_message("เขียนแคปชันไม่สำเร็จ ลองใหม่ครับ")
                            else:
                                send_message("พิมพ์หัวข้อใหม่เพื่อเริ่มก่อนนะครับ 🙂")

                        elif act.startswith("cap:"):
                            n = int(act.split(":")[1])
                            if n < len(state["captions"]):
                                state["sel_caption"] = state["captions"][n]
                                send_message("✅ เลือกแคปชันแล้ว\n\n🎨 กำลังสร้างรูป 2 แบบด้วย AI (รอสักครู่)...")
                                hook_line = state["sel_hook"]["text"].split("\n", 1)[0].strip()
                                try:
                                    state["arts"] = make_ai_images(hook_line, state["sel_caption"]["text"])
                                    state["step"] = "pick_art"
                                    save_state()
                                    show_artworks()
                                except Exception as e:
                                    print("⚠️ สร้างรูปไม่สำเร็จ:", repr(e))
                                    send_message("ขออภัย สร้างรูปไม่สำเร็จ ลองกดเลือกแคปชันใหม่อีกครั้งครับ")
                            else:
                                send_message("พิมพ์หัวข้อใหม่เพื่อเริ่มก่อนนะครับ 🙂")

                        elif act.startswith("art:"):
                            n = int(act.split(":")[1])
                            if n < len(state["arts"]):
                                state["sel_art"] = state["arts"][n]
                                save_state()
                                show_confirm()
                            else:
                                send_message("พิมพ์หัวข้อใหม่เพื่อเริ่มก่อนนะครับ 🙂")

                        elif act == "confirm":
                            if state.get("sel_art") and state.get("sel_caption"):
                                send_message("🎉 สรุปโพสต์ของคุณ — พร้อมเอาไปใช้ได้เลย!")
                                send_photo(state["sel_art"]["path"])
                                send_message(state["sel_caption"]["text"])
                                state["step"] = "done"
                                save_state()
                            else:
                                send_message("พิมพ์หัวข้อใหม่เพื่อเริ่มก่อนนะครับ 🙂")
                        continue

                    # ---- ข้อความที่ผู้ใช้พิมพ์ ----
                    msg = update.get("message")
                    if not (msg and "text" in msg):
                        continue
                    if msg.get("date", 0) < bot_start - 3:
                        print(f"⏭️ ข้ามข้อความเก่า (date {msg.get('date')})")
                        continue
                    text = msg["text"].strip()

                    if text.startswith("/browse"):
                        send_message("📰 กำลังค้นข่าวจากเว็บ... (อาจใช้เวลาสักครู่)")
                        try:
                            state["topics"] = browse_news()
                            state["step"] = "pick_topic"
                            save_state()
                            if state["topics"]:
                                show_topics()
                            else:
                                send_message("ค้นข่าวไม่เจอ ลองใหม่อีกครั้งครับ")
                        except Exception as e:
                            print("⚠️ ค้นข่าวไม่สำเร็จ:", repr(e))
                            send_message("ขออภัย ค้นข่าวไม่สำเร็จ ลอง /browse ใหม่อีกครั้งครับ")
                        continue

                    if text.startswith("/create"):
                        state["step"] = "await_topic"
                        save_state()
                        send_message("อยากโพสต์เรื่องอะไรดี? พิมพ์หัวข้อมาได้เลยครับ")
                        continue

                    # ข้อความอื่น = หัวข้อ → เริ่ม flow (พิมพ์ตรงๆ ได้ ไม่ต้อง /create)
                    start_topic(text)

                except Exception as e:
                    print("⚠️ ข้ามอัปเดตที่มีปัญหา:", repr(e))
                    continue
    except KeyboardInterrupt:
        print("\n👋 หยุดบอทแล้วครับ")


if __name__ == "__main__":
    main()
