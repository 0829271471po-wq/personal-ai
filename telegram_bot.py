"""AI ส่วนตัวผ่าน Telegram — รัน: python telegram_bot.py

ตั้งค่าใน .env:
    TELEGRAM_BOT_TOKEN=<token จาก @BotFather>
    TELEGRAM_OWNER_ID=<ว่างไว้ได้ คนแรกที่ส่ง /start จะเป็นเจ้าของ>
"""
import base64
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

import ai

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
OWNER_ID = os.environ.get("TELEGRAM_OWNER_ID", "").strip()
EDIT_INTERVAL = 1.5  # วินาที (Telegram จำกัดความถี่การแก้ข้อความ)
MAX_LEN = 4000       # Telegram รับได้ 4096 ตัวอักษรต่อข้อความ

TOOL_LABEL = {
    "remember": "จำ", "forget": "ลืม", "search_brain": "ค้นสมอง", "list_notes": "ดูโน้ต",
    "read_note": "อ่านโน้ต", "write_note": "จดโน้ต", "get_datetime": "ดูเวลา",
}
HELP = """🤖 **น้องสมอง** — AI ส่วนตัวที่จำเรื่องของคุณได้

พิมพ์คุยได้เลย หรือส่งรูปพร้อมคำถามก็ได้
บอกว่า "จำไว้ว่า..." แล้วจะจำไว้ตลอด

ยิ่งคุยเยอะ ยิ่งรู้จักคุณมากขึ้น — จะสังเกตนิสัย สไตล์การพูด และกิจวัตรของคุณเอง

/profile — ดูว่าน้องสมองเข้าใจคุณว่ายังไง
/learn — ให้วิเคราะห์ตัวตนคุณใหม่เดี๋ยวนี้
/interview — ให้ถามคำถามเพื่อรู้จักคุณ
/mem — ดูความจำทั้งหมด
/forget N — ลบความจำหมายเลข N
/notes — ดูรายการโน้ต
/think — เปิด/ปิดโหมดคิดก่อนตอบ
/new — เริ่มบทสนทนาใหม่ (ความจำยังอยู่)"""

INTERVIEW_PROMPT = ("ช่วยสัมภาษณ์ฉันเพื่อทำความรู้จักฉันให้ลึกขึ้นหน่อย ถามทีละ 1 คำถามสั้นๆ แบบเป็นกันเอง "
                    "เลือกเรื่องที่ยังไม่รู้เกี่ยวกับฉัน (นิสัย กิจวัตร ความชอบ เป้าหมาย คนสำคัญ สิ่งที่อยากให้ช่วย) "
                    "แล้วจำคำตอบของฉันไว้ทุกครั้ง ถามต่อไปเรื่อยๆ จนกว่าฉันจะบอกพอ")


# ---------------------------------------------------------------- Telegram API

class TelegramError(Exception):
    pass


def tg(method, timeout=30, **params):
    req = urllib.request.Request(f"{API}/{method}", json.dumps(params).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["result"]
    except urllib.error.HTTPError as e:
        raise TelegramError(e.read().decode("utf-8", "ignore")) from None


def download_photo(file_id):
    path = tg("getFile", file_id=file_id)["file_path"]
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=60) as r:
        return base64.b64encode(r.read()).decode()


def to_html(md):
    """แปลง Markdown จาก AI เป็น HTML แบบที่ Telegram รองรับ"""
    out = []
    for i, part in enumerate(re.split(r"```(?:[\w+-]*\n)?", md)):
        if i % 2:  # ส่วนที่อยู่ใน ``` ```
            out.append(f"<pre>{html.escape(part.strip())}</pre>")
            continue
        s = html.escape(part)
        s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?m)^#{1,6}\s+(.+)$", r"<b>\1</b>", s)
        s = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", s)
        out.append(s)
    return "".join(out)


def split_text(text, limit=MAX_LEN):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text]


def send(chat_id, text, reply_to=None):
    """ส่งข้อความแบบจัดรูปแบบ ถ้า HTML พังก็ส่งเป็นข้อความธรรมดา"""
    for chunk in split_text(text):
        try:
            tg("sendMessage", chat_id=chat_id, text=to_html(chunk), parse_mode="HTML",
               reply_to_message_id=reply_to)
        except TelegramError:
            tg("sendMessage", chat_id=chat_id, text=chunk, reply_to_message_id=reply_to)
        reply_to = None


# ---------------------------------------------------------------- ข้อความสด (stream)

class LiveReply:
    """ข้อความที่ค่อยๆ แก้ไขตามที่ AI พิมพ์ออกมา"""

    def __init__(self, chat_id, reply_to):
        self.chat_id = chat_id
        self.tools = []
        self.text = ""
        self.thinking = False
        self.last_edit = 0.0
        self.shown = ""
        self.msg_id = tg("sendMessage", chat_id=chat_id, text="⏳",
                         reply_to_message_id=reply_to)["message_id"]

    def render(self):
        head = "\n".join(self.tools)
        body = self.text or ("💭 กำลังคิด..." if self.thinking else "⏳")
        return (head + "\n\n" + body if head else body)[:MAX_LEN]

    def on_event(self, kind, data):
        if kind == "think":
            self.thinking = True
        elif kind == "text":
            self.text += data
        elif kind == "tool":
            label = TOOL_LABEL.get(data["name"], data["name"])
            try:
                args = json.loads(data["args"] or "{}")
            except json.JSONDecodeError:
                args = {}
            detail = args.get("fact") or args.get("query") or args.get("name") or args.get("memory_id") or ""
            self.tools.append(f"🧠 {label} {str(detail)[:60]}".rstrip())
            self.text = ""  # ข้อความก่อนเรียก tool เป็นแค่ช่วงกลาง
        if time.monotonic() - self.last_edit >= EDIT_INTERVAL:
            self.edit(self.render())

    def edit(self, text, parse_mode=None):
        if text == self.shown:
            return
        try:
            params = {"parse_mode": parse_mode} if parse_mode else {}
            tg("editMessageText", chat_id=self.chat_id, message_id=self.msg_id, text=text, **params)
            self.shown = text
        except TelegramError as e:
            if parse_mode:
                raise
            if "message is not modified" not in str(e):
                print("edit error:", e)
        self.last_edit = time.monotonic()

    def finish(self, answer):
        head = "\n".join(f"<i>{html.escape(t)}</i>" for t in self.tools)
        chunks = split_text(answer or "(ไม่มีคำตอบ)")
        first = (head + "\n\n" if head else "") + to_html(chunks[0])
        try:
            self.edit(first, parse_mode="HTML")
        except TelegramError:
            self.edit(self.render() if len(chunks) == 1 else chunks[0])
        for chunk in chunks[1:]:
            send(self.chat_id, chunk)


# ---------------------------------------------------------------- จัดการข้อความ

bots = {}  # chat_id -> Assistant
bots_lock = threading.Lock()


def get_bot(chat_id):
    with bots_lock:
        if chat_id not in bots:
            bots[chat_id] = ai.Assistant()
            bots[chat_id].lock = threading.Lock()
        return bots[chat_id]


def save_owner(user_id):
    global OWNER_ID
    OWNER_ID = str(user_id)
    env = ai.ROOT / ".env"
    lines = [l for l in env.read_text(encoding="utf-8").splitlines() if not l.startswith("TELEGRAM_OWNER_ID=")]
    lines.append(f"TELEGRAM_OWNER_ID={OWNER_ID}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")


def handle_command(chat_id, text):
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0]
    bot = get_bot(chat_id)
    if cmd in ("/start", "/help"):
        send(chat_id, HELP)
    elif cmd == "/profile":
        text = ai.USER_FILE.read_text(encoding="utf-8") if ai.USER_FILE.exists() else ""
        state = ai.load_state()
        send(chat_id, text or "ยังไม่มีภาพรวมตัวตน คุยกันอีกสักพัก หรือพิมพ์ /learn ได้เลย")
        if text:
            left = max(ai.REFLECT_EVERY - state.get("turns_since_reflect", 0), 0)
            send(chat_id, f"อัปเดตล่าสุด: {state.get('reflected_at', '-')[:16].replace('T', ' ')}"
                          f" · จะวิเคราะห์ใหม่อีกใน {left} ข้อความ")
    elif cmd == "/learn":
        tg("sendMessage", chat_id=chat_id, text="🔍 กำลังทบทวนบทสนทนาทั้งหมดเพื่อทำความเข้าใจคุณ... (อาจใช้เวลา 1-2 นาที)")
        tg("sendChatAction", chat_id=chat_id, action="typing")
        result = ai.reflect(force=True)
        send(chat_id, f"✅ {result}\nพิมพ์ /profile เพื่อดู" if "แล้ว" in result else f"ℹ️ {result}")
    elif cmd == "/mem":
        mems = ai.load_memories()
        send(chat_id, "\n".join(f"#{m['id']} [{m['category']}] {m['fact']}" for m in mems)
             or "ยังไม่มีความจำ ลองบอกอะไรเกี่ยวกับตัวคุณดูสิ")
    elif cmd == "/forget":
        send(chat_id, ai.forget(arg.strip()) if arg.strip().isdigit() else "ใช้แบบนี้: /forget 3")
    elif cmd == "/notes":
        send(chat_id, ai.list_notes())
    elif cmd == "/think":
        bot.thinking = not bot.thinking
        send(chat_id, "💭 โหมดคิดก่อนตอบ: " + ("เปิด (ช้าลงแต่ละเอียดขึ้น)" if bot.thinking else "ปิด"))
    elif cmd == "/new":
        bot.history.clear()
        send(chat_id, "🆕 เริ่มบทสนทนาใหม่แล้ว (ความจำถาวรยังอยู่)")
    else:
        send(chat_id, "ไม่รู้จักคำสั่งนี้ พิมพ์ /help ดูคำสั่งทั้งหมด")


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    user_id = str(msg.get("from", {}).get("id", ""))
    text = (msg.get("text") or msg.get("caption") or "").strip()

    if not OWNER_ID and text.startswith("/start"):
        save_owner(user_id)
        print(f"👤 ตั้งเจ้าของบอทเป็น user id {user_id}")
    if user_id != OWNER_ID:
        tg("sendMessage", chat_id=chat_id, text="🔒 บอทนี้เป็นผู้ช่วยส่วนตัว ใช้ได้เฉพาะเจ้าของ")
        print(f"🚫 ปฏิเสธ user id {user_id}")
        return

    if text.split("@")[0] == "/interview":
        text = INTERVIEW_PROMPT
    elif text.startswith("/"):
        handle_command(chat_id, text)
        return

    image = None
    if msg.get("photo"):
        image = download_photo(msg["photo"][-1]["file_id"])  # ขนาดใหญ่สุด
    if not text and not image:
        send(chat_id, "ตอนนี้รับได้แค่ข้อความกับรูปภาพนะครับ")
        return

    bot = get_bot(chat_id)
    with bot.lock:  # ตอบทีละข้อความต่อแชท
        tg("sendChatAction", chat_id=chat_id, action="typing")
        live = LiveReply(chat_id, msg["message_id"])
        n = len(bot.history)
        try:
            answer = bot.ask(text, on_event=live.on_event, image_b64=image)
            live.finish(answer)
        except Exception as e:
            del bot.history[n:]
            live.edit(f"❌ เกิดข้อผิดพลาด: {e}")


def safe_handle(msg):
    try:
        handle_message(msg)
    except Exception as e:
        print("handler error:", e)


def main():
    if not TOKEN:
        raise SystemExit("❌ ยังไม่ได้ใส่ TELEGRAM_BOT_TOKEN ในไฟล์ .env")
    # กันเปิดซ้ำ: Telegram ไม่ยอมให้ 2 โปรแกรมรับข้อความจากบอทเดียวกันพร้อมกัน
    global _instance_lock
    _instance_lock = socket.socket()
    try:
        _instance_lock.bind(("127.0.0.1", 47821))
    except OSError:
        print("ℹ️ บอทเปิดอยู่แล้ว (อาจทำงานเบื้องหลัง) ไม่ต้องเปิดซ้ำ")
        sys.exit(3)
    me = tg("getMe")
    ai.maybe_reflect()  # ถ้าไม่ได้วิเคราะห์มาเกิน 1 วัน ให้ทำตอนเปิด
    tg("setMyCommands", commands=[
        {"command": "profile", "description": "ดูว่าน้องสมองเข้าใจคุณว่ายังไง"},
        {"command": "interview", "description": "ให้ถามคำถามเพื่อรู้จักคุณ"},
        {"command": "learn", "description": "วิเคราะห์ตัวตนคุณใหม่เดี๋ยวนี้"},
        {"command": "mem", "description": "ดูความจำทั้งหมด"},
        {"command": "forget", "description": "ลบความจำ เช่น /forget 3"},
        {"command": "notes", "description": "ดูรายการโน้ต"},
        {"command": "think", "description": "เปิด/ปิดโหมดคิดก่อนตอบ"},
        {"command": "new", "description": "เริ่มบทสนทนาใหม่"},
        {"command": "help", "description": "วิธีใช้"},
    ])
    print(f"🤖 บอท @{me['username']} พร้อมแล้ว — เปิด https://t.me/{me['username']}")
    print("   เจ้าของ:", OWNER_ID or "(ยังไม่มี — ส่ง /start ในแชทเพื่อเป็นเจ้าของ)")
    print("   หยุดบอท: ดับเบิลคลิก หยุดบอท.bat")

    offset = None
    while True:
        try:
            updates = tg_poll(offset)
        except Exception as e:
            print("poll error:", e)
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            if "message" in u:
                threading.Thread(target=safe_handle, args=(u["message"],), daemon=True).start()


def tg_poll(offset):
    params = {"timeout": 50, "allowed_updates": ["message"]}
    if offset is not None:
        params["offset"] = offset
    req = urllib.request.Request(f"{API}/getUpdates", json.dumps(params).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["result"]


if __name__ == "__main__":
    main()
