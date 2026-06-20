# fb_setup.py — ทำ Facebook Page token แบบถาวร แล้วเขียนลง .env ให้อัตโนมัติ
#
# ใช้ครั้งเดียว เมื่อ token เพจหมดอายุ:
#   python3 fb_setup.py <APP_ID> <APP_SECRET> <USER_TOKEN ชั่วคราว>
#
# - APP_ID / APP_SECRET : เอาจาก developers.facebook.com → แอปของคุณ → Settings → Basic
# - USER_TOKEN ชั่วคราว : เอาจาก Graph API Explorer (เลือกแอป + เพิ่มสิทธิ์
#     pages_show_list, pages_manage_posts, pages_read_engagement, pages_read_user_content)
#     แล้วกด Generate Access Token
#     (pages_read_user_content จำเป็นสำหรับ Dashboard → ดึงยอด react/comment/share)
#
# สคริปต์จะ: แลก user token สั้น → ยาว (60 วัน) → ดึง page token (ถาวร) → เขียน FB_PAGES ลง .env

import sys
import json
import requests

FB_API = "https://graph.facebook.com/v21.0"
ENV_PATH = ".env"


def die(msg):
    print("❌", msg)
    sys.exit(1)


def main():
    if len(sys.argv) < 4:
        die("ใช้: python3 fb_setup.py <APP_ID> <APP_SECRET> <USER_TOKEN ชั่วคราว>")
    app_id, app_secret, short_token = sys.argv[1].strip(), sys.argv[2].strip(), sys.argv[3].strip()

    # 1) แลก user token สั้น → ยาว (~60 วัน)
    print("① กำลังแลก user token เป็นแบบยาว...")
    r = requests.get(f"{FB_API}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }, timeout=30).json()
    if "access_token" not in r:
        die(f"แลก token ยาวไม่สำเร็จ: {r}")
    long_user = r["access_token"]
    print("   ✓ ได้ user token ยาวแล้ว")

    # 2) ดึงเพจทั้งหมด + page token (จาก long-lived user token จะได้ page token ที่ไม่หมดอายุ)
    print("② กำลังดึง page token ของทุกเพจ...")
    pages, url = [], f"{FB_API}/me/accounts"
    params = {"access_token": long_user, "fields": "id,name,access_token", "limit": 100}
    while url:
        d = requests.get(url, params=params, timeout=30).json()
        if "data" not in d:
            die(f"ดึงเพจไม่สำเร็จ: {d}")
        for p in d["data"]:
            pages.append({"id": p["id"], "name": p.get("name", "Facebook"), "token": p["access_token"]})
        url = d.get("paging", {}).get("next")
        params = None  # next มี query ครบแล้ว
    if not pages:
        die("ไม่พบเพจในบัญชีนี้ (เช็คสิทธิ์ pages_show_list)")
    print(f"   ✓ พบ {len(pages)} เพจ:")
    for p in pages:
        print(f"      - {p['name']} ({p['id']})")

    # 3) เขียน FB_PAGES ลง .env (แทนบรรทัดเดิม ถ้ามี / ไม่มีก็เพิ่มท้ายไฟล์)
    print("③ กำลังเขียนลง .env...")
    fb_line = "FB_PAGES='" + json.dumps(pages, ensure_ascii=False) + "'\n"
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []
    out, replaced = [], False
    for ln in lines:
        if ln.strip().startswith("FB_PAGES="):
            out.append(fb_line)
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(fb_line)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(out)
    print("   ✓ อัปเดต .env เรียบร้อย")

    # 4) ตรวจสอบ token ใหม่ใช้ได้จริง
    print("④ ตรวจสอบ token ใหม่...")
    ok = 0
    for p in pages:
        chk = requests.get(f"{FB_API}/{p['id']}", params={"fields": "name", "access_token": p["token"]}, timeout=20).json()
        good = "name" in chk
        ok += good
        print(f"   {'✓' if good else '✗'} {p['name']}: {'ใช้ได้' if good else chk.get('error', {}).get('message', 'error')}")
    print(f"\n🎉 เสร็จ! {ok}/{len(pages)} เพจพร้อมโพสต์ — รีสตาร์ทเว็บแอปแล้วลองโพสต์ได้เลย")


if __name__ == "__main__":
    main()
