"""แกนสมองของ AI ส่วนตัว: ความจำถาวร คลังโน้ต และการเชื่อมต่อ LLM
ใช้โดย telegram_bot.py  (หรือเรียกจากโค้ดอื่น: from ai import ask)
"""
import datetime
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRAIN = ROOT / "brain"
NOTES = BRAIN / "notes"
HISTORY = BRAIN / "history"
MEMORY_FILE = BRAIN / "memory.json"
PROFILE_FILE = BRAIN / "profile.md"   # ตัวตนของ AI (แก้เองได้)
USER_FILE = BRAIN / "user.md"         # ภาพรวมตัวตนผู้ใช้ (AI เขียนเองจากการสังเกต)
STATE_FILE = BRAIN / "state.json"

for d in (BRAIN, NOTES, HISTORY):
    d.mkdir(parents=True, exist_ok=True)


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
BASE_URL = os.environ.get("LLM_BASE_URL", "https://llm.mcp-digitalstudio.com").rstrip("/")
API_KEY = os.environ["LLM_API_KEY"]
MODEL_ID = os.environ.get("LLM_MODEL_ID", "qwen3_8_27b")
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "personal-ai/1.0",  # Cloudflare บล็อก User-Agent เริ่มต้นของ Python
}

# ---------------------------------------------------------------- สมอง: ความจำ

def load_memories():
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    return []


def save_memories(mems):
    MEMORY_FILE.write_text(json.dumps(mems, ensure_ascii=False, indent=2), encoding="utf-8")


def remember(fact, category="ทั่วไป"):
    mems = load_memories()
    if any(m["fact"] == fact for m in mems):
        return "มีอยู่ในความจำแล้ว"
    new_id = max((m["id"] for m in mems), default=0) + 1
    mems.append({"id": new_id, "fact": fact, "category": category,
                 "created": datetime.datetime.now().isoformat(timespec="minutes")})
    save_memories(mems)
    return f"บันทึกความจำ #{new_id} แล้ว"


def forget(memory_id):
    mems = load_memories()
    kept = [m for m in mems if m["id"] != int(memory_id)]
    if len(kept) == len(mems):
        return f"ไม่พบความจำ #{memory_id}"
    save_memories(kept)
    return f"ลบความจำ #{memory_id} แล้ว"


# ---------------------------------------------------------------- สมอง: ค้นหา
# ภาษาไทยไม่มีการเว้นวรรคระหว่างคำ จึงใช้ n-gram ตัวอักษรแทนการตัดคำ

def _grams(text, n=3):
    t = re.sub(r"\s+", " ", text.lower())
    return {t[i:i + n] for i in range(max(len(t) - n + 1, 1))}


def _score(query, text):
    q = _grams(query)
    return len(q & _grams(text)) / (len(q) or 1)


def _note_chunks(size=800):
    for f in sorted(NOTES.rglob("*")):
        if f.suffix.lower() not in (".md", ".txt") or f.name == "README.md":
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for i in range(0, len(text), size):
            yield f.relative_to(NOTES).as_posix(), text[i:i + size]


def search_brain(query, k=5, min_score=0.12):
    hits = [(_score(query, m["fact"]), f"[ความจำ #{m['id']}] {m['fact']}") for m in load_memories()]
    hits += [(_score(query, c), f"[โน้ต {name}]\n{c}") for name, c in _note_chunks()]
    hits = sorted((h for h in hits if h[0] >= min_score), reverse=True)[:k]
    return "\n\n".join(h[1] for h in hits) or "ไม่พบข้อมูลที่เกี่ยวข้องในสมอง"


def list_notes():
    files = [f.relative_to(NOTES).as_posix() for f in NOTES.rglob("*") if f.is_file()]
    return "\n".join(files) or "ยังไม่มีโน้ต"


def _safe_note_path(name):
    p = (NOTES / name).resolve()
    if NOTES.resolve() not in p.parents:
        raise ValueError("ชื่อไฟล์ไม่ถูกต้อง")
    if p.suffix.lower() not in (".md", ".txt"):
        p = p.with_suffix(".md")
    return p


def read_note(name):
    p = _safe_note_path(name)
    return p.read_text(encoding="utf-8") if p.exists() else f"ไม่พบโน้ต {name}"


def write_note(name, content, append=False):
    p = _safe_note_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a" if append else "w", encoding="utf-8") as f:
        f.write(("\n" if append else "") + content)
    return f"บันทึกโน้ต {p.relative_to(NOTES).as_posix()} แล้ว"


def get_datetime():
    now = datetime.datetime.now()
    return now.strftime("%A %d %B %Y") + " เวลา " + now.strftime("%H:%M")


TOOLS = {
    "remember": (remember, "บันทึกข้อเท็จจริงสำคัญเกี่ยวกับผู้ใช้ลงความจำถาวร (ชื่อ ความชอบ เป้าหมาย คนสำคัญ นัดหมาย ฯลฯ) ใช้ทุกครั้งที่ผู้ใช้บอกข้อมูลที่ควรจำ หรือสั่งให้จำ",
                 {"fact": {"type": "string", "description": "ข้อเท็จจริงสั้นๆ ชัดเจน 1 ประโยค"},
                  "category": {"type": "string", "description": "หมวด เช่น ส่วนตัว งาน ความชอบ นัดหมาย"}}, ["fact"]),
    "forget": (forget, "ลบความจำตามหมายเลข เมื่อผู้ใช้สั่งให้ลืม หรือข้อมูลเก่าไม่ถูกต้องแล้ว",
               {"memory_id": {"type": "integer"}}, ["memory_id"]),
    "search_brain": (search_brain, "ค้นหาในความจำและคลังโน้ตทั้งหมด ใช้เมื่อต้องการข้อมูลที่อาจเคยบันทึกไว้",
                     {"query": {"type": "string"}}, ["query"]),
    "list_notes": (list_notes, "แสดงรายชื่อไฟล์โน้ตทั้งหมดในคลังความรู้", {}, []),
    "read_note": (read_note, "อ่านเนื้อหาโน้ตทั้งไฟล์", {"name": {"type": "string"}}, ["name"]),
    "write_note": (write_note, "เขียนหรือต่อท้ายโน้ตในคลังความรู้ ใช้กับข้อมูลยาว เช่น สรุป แผนงาน รายการ",
                   {"name": {"type": "string", "description": "ชื่อไฟล์ เช่น งาน/แผนปี.md"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "description": "true = ต่อท้ายไฟล์เดิม"}}, ["name", "content"]),
    "get_datetime": (get_datetime, "ดูวันและเวลาปัจจุบัน", {}, []),
}
TOOL_SPECS = [{"type": "function", "function": {
    "name": name, "description": desc,
    "parameters": {"type": "object", "properties": props, "required": req}}}
    for name, (_, desc, props, req) in TOOLS.items()]


def run_tool(name, args_json):
    try:
        fn = TOOLS[name][0]
        return str(fn(**json.loads(args_json or "{}")))
    except Exception as e:
        return f"ERROR: {e}"


# ---------------------------------------------------------------- เชื่อมต่อ LLM

def load_model():
    print("⏳ กำลังโหลดโมเดล (อาจใช้เวลาสักครู่)...", file=sys.stderr)
    req = urllib.request.Request(f"{BASE_URL}/api/load/{MODEL_ID}", data=b"", headers=HEADERS, method="POST")
    urllib.request.urlopen(req, timeout=600).read()


def chat_stream(messages, thinking=False, on_event=None, tools=True):
    """ส่งข้อความแบบ stream (กัน Cloudflare ตัดที่ 2 นาที) คืนค่า (ข้อความ, tool_calls)
    on_event(kind, data) ถูกเรียกระหว่างทาง: kind = "think" | "text" """
    body = {"model": "qwen", "messages": messages, "stream": True,
            "max_tokens": 8192 if thinking else 4096,
            "chat_template_kwargs": {"enable_thinking": thinking}}
    if tools:
        body["tools"] = TOOL_SPECS
    req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions",
                                 json.dumps(body).encode(), HEADERS)
    for attempt in range(2):
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            if attempt == 0 and "No model is currently loaded" in detail:
                load_model()
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}") from None

    emit = on_event or (lambda kind, data: None)
    text, calls = "", {}
    for raw in resp:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        choices = json.loads(data).get("choices") or [{}]
        delta = choices[0].get("delta", {})
        if delta.get("reasoning_content"):
            emit("think", delta["reasoning_content"])
        if delta.get("content"):
            text += delta["content"]
            emit("text", delta["content"])
        for tc in delta.get("tool_calls") or []:
            c = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
            c["id"] = tc.get("id") or c["id"]
            fn = tc.get("function") or {}
            c["name"] += fn.get("name") or ""
            c["args"] += fn.get("arguments") or ""
    return text, [calls[i] for i in sorted(calls)]


# ---------------------------------------------------------------- เรียนรู้ตัวตนผู้ใช้
# ทุกๆ REFLECT_EVERY ข้อความ (หรือวันละครั้ง) AI จะอ่านบทสนทนาย้อนหลัง
# แล้วเขียน brain/user.md ใหม่ = ภาพรวมนิสัย สไตล์ กิจวัตร ของผู้ใช้

REFLECT_EVERY = 8
_reflect_lock = threading.Lock()

REFLECT_PROMPT = """คุณคือส่วน "ความเข้าใจตัวตน" ของผู้ช่วย AI ส่วนตัว
หน้าที่: อัปเดตเอกสารภาพรวมตัวตนของผู้ใช้ จากข้อมูลด้านล่าง เพื่อให้ผู้ช่วยเข้าใจและปรับตัวเข้ากับผู้ใช้ได้ดีที่สุด

กฎ:
- ใช้เฉพาะสิ่งที่มีหลักฐานจากบทสนทนาหรือความจำ ห้ามแต่งเติม
- สิ่งที่อนุมานจากพฤติกรรม (ไม่ได้บอกตรงๆ) ให้ต่อท้ายว่า (คาดว่า)
- เก็บข้อมูลเดิมไว้ เว้นแต่มีหลักฐานใหม่ที่ขัดแย้ง
- สังเกตจากวิธีพิมพ์ด้วย: ความยาวข้อความ ระดับภาษา คำลงท้าย อีโมจิ ความเร่งรีบ อารมณ์
- หัวข้อที่ยังไม่มีข้อมูลให้เขียนว่า "ยังไม่ทราบ"
- กระชับ ไม่เกิน 400 คำ ตอบเป็นเอกสาร Markdown อย่างเดียว ไม่ต้องมีคำนำ

รูปแบบ:
# ตัวตนของผู้ใช้
## ข้อมูลพื้นฐาน
## บุคลิกและนิสัย
## สไตล์การสื่อสาร
## ความสนใจ / ชอบ / ไม่ชอบ
## เป้าหมายและสิ่งที่กำลังทำ
## กิจวัตรและช่วงเวลาที่มักคุย
## คนสำคัญในชีวิต
## วิธีช่วยเหลือที่เหมาะกับผู้ใช้คนนี้"""


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_reflect": "", "turns_since_reflect": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _logs_since(since, limit=300):
    rows = []
    for f in sorted(HISTORY.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["t"] > since:
                rows.append(r)
    return rows[-limit:]


def _activity_summary():
    """สถิติช่วงเวลาที่ผู้ใช้มักคุย (จาก log ทั้งหมด)"""
    hours, days = {}, {}
    names = ["จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์"]
    for r in _logs_since("", limit=10**6):
        if r["role"] != "user":
            continue
        t = datetime.datetime.fromisoformat(r["t"])
        if t.hour < 5:
            slot = "ดึก(0-4)"
        elif t.hour < 12:
            slot = "เช้า(5-11)"
        elif t.hour < 18:
            slot = "บ่าย(12-17)"
        else:
            slot = "เย็น/ค่ำ(18-23)"
        hours[slot] = hours.get(slot, 0) + 1
        days[names[t.weekday()]] = days.get(names[t.weekday()], 0) + 1
    return f"ช่วงเวลา: {hours}\nวัน: {days}"


def reflect(force=False):
    """วิเคราะห์บทสนทนาใหม่ แล้วอัปเดต brain/user.md คืนค่าข้อความสถานะ"""
    if not _reflect_lock.acquire(blocking=False):
        return "กำลังวิเคราะห์อยู่แล้ว"
    try:
        state = load_state()
        logs = _logs_since(state["last_reflect"])
        if not logs and not force:
            return "ยังไม่มีบทสนทนาใหม่ให้เรียนรู้"
        convo = "\n".join(f"[{r['t'][5:16]}] {'ผู้ใช้' if r['role'] == 'user' else 'AI'}: {r['content'][:500]}"
                          for r in logs) or "(ไม่มี)"
        mems = "\n".join(f"- [{m['category']}] {m['fact']}" for m in load_memories()) or "(ไม่มี)"
        current = USER_FILE.read_text(encoding="utf-8") if USER_FILE.exists() else "(ยังไม่มี)"
        msgs = [{"role": "system", "content": REFLECT_PROMPT},
                {"role": "user", "content": f"## เอกสารเดิม\n{current}\n\n## ความจำถาวร\n{mems}\n\n"
                                            f"## สถิติการใช้งาน\n{_activity_summary()}\n\n"
                                            f"## บทสนทนาใหม่\n{convo}"}]
        text, _ = chat_stream(msgs, thinking=False, tools=False)
        text = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", text.strip())
        if "#" not in text:
            return "วิเคราะห์ไม่สำเร็จ ลองใหม่ภายหลัง"
        USER_FILE.write_text(text + "\n", encoding="utf-8")
        save_state({"last_reflect": logs[-1]["t"] if logs else state["last_reflect"],
                    "turns_since_reflect": 0,
                    "reflected_at": datetime.datetime.now().isoformat(timespec="seconds")})
        return "อัปเดตความเข้าใจตัวตนแล้ว"
    finally:
        _reflect_lock.release()


def maybe_reflect(count_turn=False):
    """เรียกหลังจบแต่ละข้อความ: ครบรอบหรือเกิน 1 วัน -> วิเคราะห์เบื้องหลัง"""
    state = load_state()
    if count_turn:
        state["turns_since_reflect"] = state.get("turns_since_reflect", 0) + 1
        save_state(state)
    last = state.get("reflected_at", "")
    stale = last < (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()
    has_new = state.get("turns_since_reflect", 0) > 0
    if state.get("turns_since_reflect", 0) >= REFLECT_EVERY or (stale and has_new):
        threading.Thread(target=reflect, daemon=True).start()


# ---------------------------------------------------------------- ตัวผู้ช่วย

class Assistant:
    MAX_HISTORY = 40

    def __init__(self, thinking=False):
        self.thinking = thinking
        self.history = []

    def system_prompt(self, user_msg):
        profile = PROFILE_FILE.read_text(encoding="utf-8") if PROFILE_FILE.exists() else ""
        mems = load_memories()
        if len(mems) <= 60:
            mem_text = "\n".join(f"#{m['id']} [{m['category']}] {m['fact']}" for m in mems)
        else:  # ความจำเยอะ: ใส่เฉพาะที่เกี่ยวข้อง
            ranked = sorted(mems, key=lambda m: _score(user_msg, m["fact"]), reverse=True)[:30]
            mem_text = "\n".join(f"#{m['id']} [{m['category']}] {m['fact']}" for m in ranked)
        notes = search_brain(user_msg, k=3, min_score=0.2)
        user = USER_FILE.read_text(encoding="utf-8") if USER_FILE.exists() else "(ยังไม่มี — กำลังเรียนรู้)"
        return f"""{profile}

## ภาพรวมตัวตนของผู้ใช้ (สรุปจากการสังเกตบทสนทนาที่ผ่านมา)
{user}

## ความจำถาวรเกี่ยวกับผู้ใช้
{mem_text or "(ยังไม่มี)"}

## ข้อมูลจากคลังโน้ตที่อาจเกี่ยวข้อง
{notes}

## การเข้าใจผู้ใช้ (สำคัญที่สุด)
- คุณคือเพื่อนคู่ใจที่รู้จักผู้ใช้ดีที่สุด ปรับวิธีตอบให้เข้ากับภาพรวมตัวตนด้านบนเสมอ:
  ความยาว ระดับภาษา คำลงท้าย อีโมจิ อารมณ์ขัน ให้กลมกลืนกับสไตล์ของผู้ใช้
- ใช้สิ่งที่รู้อย่างเป็นธรรมชาติ เช่น เรียกชื่อ อ้างถึงเป้าหมาย/ความชอบที่เกี่ยวข้อง แต่ไม่ต้องท่องความจำให้ฟัง
- สังเกตตลอดเวลา: ถ้าผู้ใช้เผยนิสัย ความชอบ สิ่งที่ไม่ชอบ กิจวัตร คนสำคัญ เป้าหมาย ความกังวล
  หรือวิธีที่อยากให้ตอบ ให้ remember ทันทีโดยไม่ต้องรอให้สั่ง (หมวด: ส่วนตัว นิสัย สไตล์ ความชอบ กิจวัตร คนสำคัญ เป้าหมาย)
- อย่าจำเรื่องชั่วคราวหรือเรื่องทั่วไปที่ไม่ได้บอกอะไรเกี่ยวกับตัวผู้ใช้
- สังเกตอารมณ์จากข้อความ ถ้าดูเหนื่อย เครียด หรือรีบ ให้ปรับน้ำเสียงและความยาวตาม
- บางครั้ง (ไม่บ่อย และเมื่อจังหวะเหมาะ) ถามคำถามสั้นๆ 1 ข้อเพื่อรู้จักผู้ใช้มากขึ้น

## กฎการใช้สมอง
- เมื่อผู้ใช้สั่งให้จำ ให้เรียก remember ทันที
- ถ้าข้อมูลใหม่ขัดกับความจำเดิม ให้ forget อันเก่าแล้ว remember อันใหม่
- ถ้าต้องการข้อมูลที่ไม่อยู่ด้านบน ให้ใช้ search_brain / read_note ก่อนตอบ
- ข้อมูลยาว (สรุป แผน รายการ) ให้เก็บด้วย write_note
- คุยผ่าน Telegram: ตอบกระชับ ใช้ **ตัวหนา** และ `โค้ด` ได้ ไม่ต้องใช้ตาราง
- วันเวลาปัจจุบัน: {get_datetime()}"""

    @staticmethod
    def _log(role, content):
        log_file = HISTORY / f"{datetime.date.today()}.jsonl"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.datetime.now().isoformat(timespec="seconds"),
                                "role": role, "content": content}, ensure_ascii=False) + "\n")

    def ask(self, user_msg, on_event=None, image_b64=None):
        """ถาม AI; on_event(kind, data) ได้รับ "think" / "text" / "tool" ระหว่างทาง
        image_b64 = รูป JPEG แบบ base64 (ถ้ามี)"""
        content = user_msg
        if image_b64:
            content = [{"type": "text", "text": user_msg or "ช่วยดูรูปนี้หน่อย"},
                       {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
        self.history.append({"role": "user", "content": content})
        self._log("user", ("[รูปภาพ] " if image_b64 else "") + user_msg)
        emit = on_event or (lambda kind, data: None)
        answer = ""
        for _ in range(8):  # จำกัดรอบการเรียก tool
            msgs = [{"role": "system", "content": self.system_prompt(user_msg)}] + self._recent()
            text, calls = chat_stream(msgs, self.thinking, emit)
            if not calls:
                answer = text
                self.history.append({"role": "assistant", "content": text})
                break
            self.history.append({"role": "assistant", "content": text, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                for c in calls]})
            for c in calls:
                result = run_tool(c["name"], c["args"])
                emit("tool", {"name": c["name"], "args": c["args"], "result": result})
                self.history.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        self._log("assistant", answer)
        maybe_reflect(count_turn=True)
        return answer

    def _recent(self):
        msgs = self.history[-self.MAX_HISTORY:]
        while msgs and msgs[0]["role"] == "tool":  # อย่าเริ่มด้วยผลลัพธ์ tool ที่ไม่มีคำสั่งนำ
            msgs = msgs[1:]
        # เก็บรูปเต็มไว้แค่ข้อความล่าสุด รูปเก่าแทนด้วยข้อความ เพื่อไม่ให้ context บวม
        last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=-1)
        out = []
        for i, m in enumerate(msgs):
            if isinstance(m["content"], list) and i != last_user:
                m = {**m, "content": m["content"][0]["text"] + " [มีรูปแนบ]"}
            out.append(m)
        return out


def ask(question, thinking=False):
    """เรียกใช้จากโค้ดอื่น: from ai import ask; print(ask("สวัสดี"))"""
    return Assistant(thinking=thinking).ask(question)
