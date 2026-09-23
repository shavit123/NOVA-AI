# ============================================================
# NOVA v16.0 - Public Multi-User Edition
# Each user provides their own API keys on signup
# ============================================================
import os, sys, json, time, base64, hashlib, secrets, threading, traceback, urllib.parse, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

try: import psycopg2
except ImportError: psycopg2 = None
try: import requests
except ImportError: requests = None
try: from groq import Groq
except ImportError: Groq = None

APP_NAME = "NOVA"
VERSION = "16.0"
BRAND = "POWERED BY SK"
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))

USE_SQLITE = not os.environ.get("DATABASE_URL")
SQLITE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova.db")
DATABASE_URL = os.environ.get("DATABASE_URL") or ""

MAX_BODY = 25 * 1024 * 1024
USAGE = {"messages": 0, "images": 0}

NVIDIA_FLUX_SCHNELL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-schnell"

MOUSE_POS = {}
MOUSE_LOCK = threading.Lock()


def log(m, t="INFO"):
    print("[" + datetime.now().strftime('%H:%M:%S') + "][" + t + "] " + str(m), flush=True)


def now():
    return int(time.time())


BASE_SYSTEM = """You are NOVA, an advanced AI assistant.

=== LANGUAGE RULES ===
Support ONLY Hebrew, English, Russian. Detect user's language and respond in the same language.
Default to Hebrew if unclear. Be precise and useful.
"""


def detect_lang(text):
    if not text:
        return "he"
    he = len(re.findall(r'[\u0590-\u05FF]', text))
    ru = len(re.findall(r'[\u0400-\u04FF]', text))
    if he >= ru and he > 0:
        return "he"
    if ru > 0:
        return "ru"
    return "en"


def detect_image_intent(msg):
    m = (msg or "").lower()
    if not m:
        return False
    code_words = ["קוד","פונקציה","class","function","script","javascript","python","html","css","код","функция"]
    for w in code_words:
        if w in m:
            return False
    img_words = ["צור לי תמונה","צור תמונה","תמונה של","צייר לי","צייר",
                 "generate image","create image","draw me","paint me",
                 "нарисуй","создай картинку"]
    for w in img_words:
        if w in m:
            return True
    return False


class _SQLiteCursor:
    def __init__(self, real):
        self._real = real
        self._returning = False
        self._last_insert_id = None
    def execute(self, sql, params=None):
        s = sql
        s = s.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
        s = s.replace("BIGINT", "INTEGER")
        s = s.replace("BOOLEAN DEFAULT FALSE", "INTEGER DEFAULT 0")
        s = s.replace("BOOLEAN DEFAULT TRUE", "INTEGER DEFAULT 1")
        s = s.replace("BOOLEAN", "INTEGER")
        s = s.replace("ILIKE", "LIKE")
        s = s.replace("%s", "?")
        upper = s.upper()
        if " RETURNING " in upper:
            idx = upper.find(" RETURNING ")
            self._returning = True
            s = s[:idx]
        else:
            self._returning = False
        if params is not None:
            self._real.execute(s, params)
        else:
            self._real.execute(s)
        self._last_insert_id = self._real.lastrowid
        return self
    def fetchone(self):
        if self._returning:
            self._returning = False
            return (self._last_insert_id,)
        row = self._real.fetchone()
        return tuple(row) if row else None
    def fetchall(self):
        return [tuple(r) for r in self._real.fetchall()]
    def close(self):
        self._real.close()
    @property
    def lastrowid(self):
        return self._real.lastrowid


class _SQLiteConn:
    def __init__(self, real):
        self._real = real
    def cursor(self):
        return _SQLiteCursor(self._real.cursor())
    def commit(self):
        self._real.commit()
    def rollback(self):
        self._real.rollback()
    def close(self):
        self._real.close()


def db():
    if USE_SQLITE:
        import sqlite3
        conn = sqlite3.connect(SQLITE_PATH, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return _SQLiteConn(conn)
    if not psycopg2:
        raise RuntimeError("psycopg2 not installed")
    return psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=20)


def init_db():
    c = db()
    cur = c.cursor()
    if USE_SQLITE:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            first_name TEXT, last_name TEXT,
            password TEXT, password_plain TEXT,
            groq_key TEXT, nvidia_key TEXT,
            role TEXT DEFAULT 'user', vip INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0, is_guest INTEGER DEFAULT 0,
            created_at INTEGER, last_login INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER, expires INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY, user_id INTEGER, title TEXT,
            mode TEXT DEFAULT 'General', tags TEXT DEFAULT '',
            created_at INTEGER, updated_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT,
            model TEXT, edited INTEGER DEFAULT 0, favorite INTEGER DEFAULT 0, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, text TEXT,
            category TEXT, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT,
            prompt TEXT, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS gallery (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prompt TEXT,
            url TEXT, created_at INTEGER)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS agent_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            command TEXT NOT NULL, status TEXT DEFAULT 'pending', result TEXT,
            created_at INTEGER NOT NULL, executed_at INTEGER)""")
        c.commit(); cur.close(); c.close()
        log("SQLite DB ready")
        return
    cur.execute("""CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        first_name TEXT, last_name TEXT,
        password TEXT, password_plain TEXT,
        groq_key TEXT, nvidia_key TEXT,
        role TEXT DEFAULT 'user', vip BOOLEAN DEFAULT FALSE,
        banned BOOLEAN DEFAULT FALSE, is_guest BOOLEAN DEFAULT FALSE,
        created_at BIGINT, last_login BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY, user_id INTEGER, expires BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY, user_id INTEGER, title TEXT,
        mode TEXT DEFAULT 'General', tags TEXT DEFAULT '',
        created_at BIGINT, updated_at BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY, chat_id TEXT, role TEXT, content TEXT,
        model TEXT, edited BOOLEAN DEFAULT FALSE, favorite BOOLEAN DEFAULT FALSE, created_at BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS memories (
        id SERIAL PRIMARY KEY, user_id INTEGER, text TEXT, category TEXT, created_at BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS agents (
        id SERIAL PRIMARY KEY, user_id INTEGER, name TEXT, prompt TEXT, created_at BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS gallery (
        id SERIAL PRIMARY KEY, user_id INTEGER, prompt TEXT, url TEXT, created_at BIGINT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS agent_tasks (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
        command TEXT NOT NULL, status TEXT DEFAULT 'pending', result TEXT,
        created_at BIGINT NOT NULL, executed_at BIGINT)""")
    c.commit(); cur.close(); c.close()
    log("Postgres DB ready")


def hash_pw(pw):
    salt = secrets.token_hex(16)
    return salt + "$" + hashlib.sha256((salt + pw).encode()).hexdigest()


def check_pw(pw, stored):
    try:
        salt, h = stored.split("$", 1)
        return hashlib.sha256((salt + pw).encode()).hexdigest() == h
    except:
        return False


def create_session(uid, days=365):
    tok = secrets.token_urlsafe(32)
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO sessions (token,user_id,expires) VALUES (%s,%s,%s)",
                (tok, uid, now() + days * 86400))
    c.commit(); cur.close(); c.close()
    return {"token": tok, "expires": now() + days * 86400}


def verify_session(tok):
    if not tok:
        return None
    try:
        c = db(); cur = c.cursor()
        cur.execute("""SELECT u.id, u.username, u.role, u.vip, u.banned,
                       u.first_name, u.last_name, u.groq_key, u.nvidia_key
                       FROM sessions s JOIN users u ON u.id=s.user_id
                       WHERE s.token=%s AND s.expires>%s""", (tok, now()))
        r = cur.fetchone(); cur.close(); c.close()
        if not r or r[4]:
            return None
        return {
            "user_id": r[0], "username": r[1], "role": r[2],
            "vip": bool(r[3]), "first_name": r[5], "last_name": r[6],
            "groq_key": r[7], "nvidia_key": r[8]
        }
    except Exception as e:
        log("verify_session: " + str(e), "ERROR")
        return None


def register_user(username, password, first_name, last_name, groq_key, nvidia_key):
    if len(username) < 3:
        return {"ok": False, "error": "שם משתמש חייב להיות 3+ תווים"}
    if len(password) < 4:
        return {"ok": False, "error": "סיסמה חייבת להיות 4+ תווים"}
    if not first_name or not last_name:
        return {"ok": False, "error": "שם פרטי ושם משפחה הם שדות חובה"}
    if not groq_key or not groq_key.startswith("gsk_"):
        return {"ok": False, "error": "Groq API Key חסר או לא תקין (מתחיל ב-gsk_)"}
    if nvidia_key and not nvidia_key.startswith("nvapi-"):
        return {"ok": False, "error": "NVIDIA API Key לא תקין (מתחיל ב-nvapi-)"}
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id FROM users WHERE username=%s", (username,))
        if cur.fetchone():
            cur.close(); c.close()
            return {"ok": False, "error": "שם משתמש תפוס"}
        cur.execute("SELECT COUNT(*) FROM users WHERE is_guest=0")
        count = cur.fetchone()[0]
        role = "admin" if count == 0 else "user"
        cur.execute("""INSERT INTO users (username, first_name, last_name, password, password_plain,
            groq_key, nvidia_key, role, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (username, first_name, last_name, hash_pw(password), password,
             groq_key, nvidia_key, role, now()))
        row = cur.fetchone()
        uid = row[0] if row else None
        c.commit(); cur.close(); c.close()
        if not uid:
            return {"ok": False, "error": "יצירת משתמש נכשלה"}
        sess = create_session(uid)
        return {"ok": True, "session": sess, "user_id": uid, "role": role}
    except Exception as e:
        log("register: " + str(e), "ERROR")
        return {"ok": False, "error": str(e)[:200]}


def login_user(username, password):
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id, password, banned FROM users WHERE username=%s", (username,))
        r = cur.fetchone()
        if not r:
            cur.close(); c.close()
            return {"ok": False, "error": "שם משתמש או סיסמה שגויים"}
        uid, stored, banned = r
        if banned:
            cur.close(); c.close()
            return {"ok": False, "error": "חשבון חסום"}
        if not stored or not check_pw(password, stored):
            cur.close(); c.close()
            return {"ok": False, "error": "שם משתמש או סיסמה שגויים"}
        cur.execute("UPDATE users SET last_login=%s WHERE id=%s", (now(), uid))
        c.commit(); cur.close(); c.close()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    sess = create_session(uid)
    return {"ok": True, "session": sess, "user_id": uid}


def update_user_keys(uid, groq_key, nvidia_key):
    try:
        c = db(); cur = c.cursor()
        cur.execute("UPDATE users SET groq_key=%s, nvidia_key=%s WHERE id=%s",
                    (groq_key, nvidia_key, uid))
        c.commit(); cur.close(); c.close()
        return True
    except:
        return False


def create_chat(uid, title="שיחה חדשה"):
    cid = secrets.token_urlsafe(10)
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO chats (id,user_id,title,created_at,updated_at) VALUES (%s,%s,%s,%s,%s)",
                (cid, uid, title, now(), now()))
    c.commit(); cur.close(); c.close()
    return cid


def get_chats(uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id,title,mode,updated_at,tags FROM chats WHERE user_id=%s ORDER BY updated_at DESC LIMIT 200", (uid,))
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "title": r[1], "mode": r[2], "updated_at": r[3], "tags": r[4] or ""} for r in rows]
    except:
        return []


def get_chat(cid, uid):
    c = db(); cur = c.cursor()
    cur.execute("SELECT id,title,mode,tags FROM chats WHERE id=%s AND user_id=%s", (cid, uid))
    r = cur.fetchone()
    if not r:
        cur.close(); c.close()
        return None
    cur.execute("SELECT id,role,content,edited,favorite,created_at FROM messages WHERE chat_id=%s ORDER BY id ASC", (cid,))
    msgs = cur.fetchall(); cur.close(); c.close()
    return {"chat": {"id": r[0], "title": r[1], "mode": r[2], "tags": r[3] or ""},
            "messages": [{"id": m[0], "role": m[1], "content": m[2],
                          "edited": bool(m[3]), "favorite": bool(m[4]), "created_at": m[5]} for m in msgs]}


def save_message(cid, role, content):
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO messages (chat_id,role,content,created_at) VALUES (%s,%s,%s,%s)",
                (cid, role, content, now()))
    try: mid = cur.lastrowid
    except: mid = None
    cur.execute("UPDATE chats SET updated_at=%s WHERE id=%s", (now(), cid))
    c.commit(); cur.close(); c.close()
    return mid


def get_history(cid, limit=14):
    c = db(); cur = c.cursor()
    cur.execute("SELECT role,content FROM messages WHERE chat_id=%s ORDER BY id DESC LIMIT %s", (cid, limit))
    rows = cur.fetchall(); cur.close(); c.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def delete_chat(cid, uid):
    c = db(); cur = c.cursor()
    cur.execute("DELETE FROM messages WHERE chat_id=%s", (cid,))
    cur.execute("DELETE FROM chats WHERE id=%s AND user_id=%s", (cid, uid))
    c.commit(); cur.close(); c.close()


def delete_message(mid, uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("DELETE FROM messages WHERE id=%s AND chat_id IN (SELECT id FROM chats WHERE user_id=%s)", (mid, uid))
        c.commit(); cur.close(); c.close()
        return True
    except:
        return False


def edit_message(mid, uid, new_content):
    try:
        c = db(); cur = c.cursor()
        cur.execute("UPDATE messages SET content=%s, edited=1 WHERE id=%s AND chat_id IN (SELECT id FROM chats WHERE user_id=%s)",
                    (new_content, mid, uid))
        c.commit(); cur.close(); c.close()
        return True
    except:
        return False


def toggle_fav(mid, uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("UPDATE messages SET favorite = CASE WHEN COALESCE(favorite,0)=0 THEN 1 ELSE 0 END WHERE id=%s AND chat_id IN (SELECT id FROM chats WHERE user_id=%s)", (mid, uid))
        c.commit()
        cur.execute("SELECT favorite FROM messages WHERE id=%s", (mid,))
        r = cur.fetchone(); cur.close(); c.close()
        return bool(r[0]) if r else None
    except:
        return None


def add_memory(uid, text, category="Preference"):
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO memories (user_id,text,category,created_at) VALUES (%s,%s,%s,%s)",
                (uid, text, category, now()))
    c.commit(); cur.close(); c.close()


def get_memories(uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id,text,category FROM memories WHERE user_id=%s ORDER BY id DESC LIMIT 200", (uid,))
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "text": r[1], "category": r[2]} for r in rows]
    except:
        return []


def delete_memory(uid, mid):
    c = db(); cur = c.cursor()
    cur.execute("DELETE FROM memories WHERE id=%s AND user_id=%s", (mid, uid))
    c.commit(); cur.close(); c.close()


def memory_ctx(uid):
    ms = get_memories(uid)[:10]
    return "\n".join(["- " + m['text'] for m in ms]) if ms else ""


def add_agent(uid, name, prompt):
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO agents (user_id,name,prompt,created_at) VALUES (%s,%s,%s,%s)",
                (uid, name, prompt, now()))
    c.commit(); cur.close(); c.close()


def get_agents(uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id,name,prompt FROM agents WHERE user_id=%s ORDER BY id DESC", (uid,))
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "name": r[1], "prompt": r[2]} for r in rows]
    except:
        return []


def delete_agent(uid, aid):
    c = db(); cur = c.cursor()
    cur.execute("DELETE FROM agents WHERE id=%s AND user_id=%s", (aid, uid))
    c.commit(); cur.close(); c.close()


def log_image(uid, prompt, url):
    try:
        c = db(); cur = c.cursor()
        cur.execute("INSERT INTO gallery (user_id,prompt,url,created_at) VALUES (%s,%s,%s,%s)",
                    (uid, prompt, url, now()))
        c.commit(); cur.close(); c.close()
    except:
        pass


def get_user_gallery(uid):
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT id,prompt,url FROM gallery WHERE user_id=%s ORDER BY id DESC LIMIT 200", (uid,))
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "prompt": r[1], "url": r[2]} for r in rows]
    except:
        return []


def get_all_users():
    try:
        c = db(); cur = c.cursor()
        cur.execute("""SELECT id,username,first_name,last_name,role,vip,banned,created_at,last_login
                       FROM users WHERE is_guest=0 ORDER BY id DESC LIMIT 1000""")
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "username": r[1], "first_name": r[2], "last_name": r[3],
                 "role": r[4], "vip": bool(r[5]), "banned": bool(r[6]),
                 "created_at": r[7], "last_login": r[8]} for r in rows]
    except:
        return []


def set_user_ban(uid, ban):
    c = db(); cur = c.cursor()
    cur.execute("UPDATE users SET banned=%s WHERE id=%s", (1 if ban else 0, uid))
    c.commit(); cur.close(); c.close()


def set_user_vip(uid, vip):
    c = db(); cur = c.cursor()
    cur.execute("UPDATE users SET vip=%s WHERE id=%s", (1 if vip else 0, uid))
    c.commit(); cur.close(); c.close()


def delete_user(uid):
    c = db(); cur = c.cursor()
    cur.execute("DELETE FROM messages WHERE chat_id IN (SELECT id FROM chats WHERE user_id=%s)", (uid,))
    for t in ("chats","memories","agents","gallery","sessions","agent_tasks"):
        try: cur.execute("DELETE FROM " + t + " WHERE user_id=%s", (uid,))
        except: pass
    cur.execute("DELETE FROM users WHERE id=%s", (uid,))
    c.commit(); cur.close(); c.close()


def get_stats():
    try:
        c = db(); cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM users WHERE is_guest=0")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM chats")
        chats = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM messages")
        msgs = cur.fetchone()[0]
        cur.close(); c.close()
        return {"users": users, "chats": chats, "messages": msgs}
    except:
        return {"users": 0, "chats": 0, "messages": 0}


def call_groq_for_user(history, user_groq_key, model_id="openai/gpt-oss-120b", system_prompt=None):
    """Call Groq using the USER'S OWN key."""
    if not user_groq_key or not user_groq_key.startswith("gsk_"):
        return {"ok": False, "error": "Groq API Key חסר. הוסף אותו בהגדרות."}
    if not Groq:
        return {"ok": False, "error": "groq package not installed"}
    if len(history) > 14:
        history = history[-14:]
    trimmed = []
    for h in history:
        c = h.get("content", "") or ""
        if len(c) > 2000:
            c = c[:2000] + "..."
        trimmed.append({"role": h.get("role", "user"), "content": c})
    msgs = []
    if system_prompt:
        if len(system_prompt) > 4000:
            system_prompt = system_prompt[:4000]
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(trimmed)
    try:
        client = Groq(api_key=user_groq_key)
        r = client.chat.completions.create(
            model=model_id, messages=msgs,
            temperature=0.7, max_tokens=2048)
        reply = r.choices[0].message.content or ""
        USAGE["messages"] += 1
        return {"ok": True, "reply": reply}
    except Exception as e:
        err = str(e)
        log("GROQ user: " + err, "ERROR")
        if "401" in err or "invalid_api_key" in err.lower():
            return {"ok": False, "error": "Groq API Key לא תקין"}
        if "413" in err or "too large" in err.lower() or "TPM" in err:
            return {"ok": False, "error": "ההודעה גדולה מדי. פתח שיחה חדשה."}
        return {"ok": False, "error": err[:200]}


def generate_image_for_user(prompt, user_nvidia_key):
    """Generate image using the USER'S OWN NVIDIA key."""
    if not user_nvidia_key or not user_nvidia_key.startswith("nvapi-"):
        return None, "NVIDIA API Key חסר. הוסף אותו בהגדרות כדי ליצור תמונות."
    if not requests:
        return None, "requests module missing"
    headers = {
        "Authorization": "Bearer " + user_nvidia_key,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"prompt": prompt[:1000], "mode": "base", "seed": 0, "steps": 4, "width": 1024, "height": 1024}
    for attempt in range(2):
        try:
            r = requests.post(NVIDIA_FLUX_SCHNELL, headers=headers, json=payload, timeout=90)
            if r.status_code == 200:
                d = r.json()
                artifacts = d.get("artifacts") or []
                if artifacts:
                    b64 = artifacts[0].get("base64", "")
                    if b64:
                        return "data:image/png;base64," + b64, None
                if d.get("image"):
                    return "data:image/png;base64," + d["image"], None
                if d.get("url"):
                    return d["url"], None
                return None, "NVIDIA returned unknown format"
            elif r.status_code == 401:
                return None, "NVIDIA API Key לא תקין"
            elif r.status_code == 429:
                time.sleep(3)
                continue
            else:
                return None, "NVIDIA HTTP " + str(r.status_code)
        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            return None, "NVIDIA error: " + str(e)[:100]
    return None, "NVIDIA timeout"


def enqueue_agent_command(uid, command):
    c = db(); cur = c.cursor()
    cur.execute("INSERT INTO agent_tasks (user_id,command,status,created_at) VALUES (%s,%s,'pending',%s)",
                (uid, command, now()))
    c.commit(); cur.close(); c.close()


def poll_agent_command(uid):
    c = db(); cur = c.cursor()
    cur.execute("""SELECT id,command FROM agent_tasks
                   WHERE user_id=%s AND status='pending' ORDER BY id ASC LIMIT 1""", (uid,))
    r = cur.fetchone()
    if not r:
        cur.close(); c.close()
        return None
    tid, cmd = r
    cur.execute("UPDATE agent_tasks SET status='running' WHERE id=%s", (tid,))
    c.commit(); cur.close(); c.close()
    return {"id": tid, "command": cmd}


def submit_agent_result(uid, tid, result):
    c = db(); cur = c.cursor()
    cur.execute("UPDATE agent_tasks SET status='done',result=%s,executed_at=%s WHERE id=%s AND user_id=%s",
                (result, now(), tid, uid))
    c.commit(); cur.close(); c.close()


def get_agent_tasks(uid, limit=50):
    try:
        c = db(); cur = c.cursor()
        cur.execute("""SELECT id,command,status,result,created_at
                       FROM agent_tasks WHERE user_id=%s ORDER BY id DESC LIMIT %s""", (uid, limit))
        rows = cur.fetchall(); cur.close(); c.close()
        return [{"id": r[0], "command": r[1], "status": r[2], "result": r[3], "created_at": r[4]} for r in rows]
    except:
        return []


def json_resp(h, data, status=200):
    raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(raw)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(raw)


def html_resp(h, html, status=200):
    raw = html.encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Length", str(len(raw)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(raw)


def read_json(h):
    t = h.headers.get("Content-Length", "0")
    n = int(t) if t else 0
    if n <= 0 or n > MAX_BODY:
        raise ValueError("Bad body")
    raw = h.rfile.read(n)
    return json.loads(raw.decode("utf-8"))


def get_token(h):
    a = h.headers.get("Authorization", "")
    return a[7:].strip() if a.startswith("Bearer ") else ""


def require_auth(h):
    u = verify_session(get_token(h))
    if not u:
        raise PermissionError("Unauthorized")
    return u


def require_admin(h):
    u = require_auth(h)
    if u.get("role") != "admin":
        raise PermissionError("Forbidden")
    return u


MANIFEST = {
    "name": "NOVA AI", "short_name": "NOVA",
    "start_url": "/", "display": "standalone",
    "background_color": "#0a0a0a", "theme_color": "#0a0a0a",
    "icons": [
        {"src": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxOTIgMTkyIj48cmVjdCB3aWR0aD0iMTkyIiBoZWlnaHQ9IjE5MiIgcng9IjI4IiBmaWxsPSIjMGEwYTBhIi8+PHRleHQgeD0iOTYiIHk9IjEzMCIgZm9udC1mYW1pbHk9IkFyaWFsIiBmb250LXNpemU9IjExMCIgZm9udC13ZWlnaHQ9ImJvbGQiIGZpbGw9IiNlZGVkZWQiIHRleHQtYW5jaG9yPSJtaWRkbGUiPk48L3RleHQ+PC9zdmc+",
         "sizes": "192x192", "type": "image/svg+xml"}
    ]
}

HTML = r"""<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#0a0a0a">
<title>NOVA</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--bg:#0a0a0a;--sb:#0f0f0f;--panel:#171717;--hover:#1f1f1f;--hover2:#262626;
--border:#242424;--border2:#333;--text:#ededed;--text2:#a3a3a3;
--muted:#737373;--muted2:#525252;--danger:#ef4444;--ok:#4ade80;
--accent:#ededed;--accent-fg:#0a0a0a;--r-sm:6px;--r-md:10px;--r-lg:14px;--r-full:9999px}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;font-size:15px;line-height:1.55}
button,input,textarea,select{font:inherit;color:inherit}
button{border:0;background:none;cursor:pointer;touch-action:manipulation}
.hidden{display:none!important}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:4px}
.loading{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;
justify-content:center;z-index:200;flex-direction:column;gap:20px}
.loading-logo{width:60px;height:60px;border-radius:var(--r-md);background:var(--panel);
display:grid;place-items:center;font-weight:600;font-size:24px;border:1px solid var(--border2);
animation:p 1.5s infinite}
@keyframes p{0%,100%{opacity:.5}50%{opacity:1}}
.auth{position:fixed;inset:0;background:var(--bg);display:flex;align-items:center;
justify-content:center;padding:20px;z-index:100;overflow-y:auto}
.auth-box{width:min(440px,100%);text-align:center}
.auth-logo{width:60px;height:60px;border-radius:var(--r-md);margin:0 auto 20px;
background:var(--panel);display:grid;place-items:center;font-weight:600;font-size:24px;
border:1px solid var(--border2)}
.auth-title{font-size:22px;font-weight:500;margin-bottom:4px;letter-spacing:6px}
.auth-sub{color:var(--muted);font-size:10.5px;margin-bottom:28px;letter-spacing:3px}
.auth-tabs{display:flex;margin-bottom:20px;border-bottom:1px solid var(--border)}
.auth-tab{flex:1;padding:10px;color:var(--muted);font-size:14px;font-weight:500;
border-bottom:2px solid transparent}
.auth-tab.active{color:var(--text);border-bottom-color:var(--text)}
.auth-form{display:flex;flex-direction:column;gap:10px;text-align:right}
.lbl{font-size:12px;color:var(--muted);margin-bottom:4px}
.inp{width:100%;padding:11px 12px;border-radius:var(--r-md);background:var(--panel);
border:1px solid var(--border);font-size:14.5px;outline:0;font-family:inherit}
.inp:focus{border-color:var(--border2)}
.btn{width:100%;padding:12px;border-radius:var(--r-md);background:var(--accent);
color:var(--accent-fg);font-size:14px;font-weight:600;font-family:inherit;
letter-spacing:1px;cursor:pointer;border:0;margin-top:6px}
.btn:disabled{opacity:.5;cursor:wait}
.msg-box{padding:10px 12px;border-radius:var(--r-md);font-size:13px;margin-bottom:12px;text-align:right}
.msg-box.err{background:rgba(239,68,68,.1);color:#fca5a5;border:1px solid rgba(239,68,68,.25)}
.msg-box.ok{background:rgba(74,222,128,.1);color:#86efac;border:1px solid rgba(74,222,128,.25)}
.help{font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.5}
.help a{color:#93c5fd;text-decoration:underline}
.app{display:flex;height:100vh;height:100dvh}
.sb{width:280px;background:var(--sb);display:flex;flex-direction:column;flex-shrink:0;border-left:1px solid var(--border)}
.sb-top{padding:8px}
.sb-row{display:flex;align-items:center;gap:10px;padding:8px 10px}
.sb-logo{width:32px;height:32px;border-radius:var(--r-md);background:var(--panel);
display:grid;place-items:center;font-weight:600;font-size:14px;border:1px solid var(--border2)}
.sb-info{flex:1;text-align:right;min-width:0}
.sb-name{font-size:13.5px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sb-brand{font-size:10px;color:var(--muted);letter-spacing:2px}
.sb-new{display:flex;align-items:center;justify-content:center;gap:10px;
padding:10px 12px;border-radius:var(--r-md);font-size:13.5px;font-weight:500;
border:1px solid var(--border);background:var(--panel);width:100%;margin-top:6px;color:var(--text)}
.sb-new:hover{background:var(--hover)}
.sb-label{padding:14px 14px 4px;font-size:10.5px;font-weight:600;color:var(--muted2);letter-spacing:1px}
.sb-list{flex:1;overflow-y:auto;padding:0 6px 8px}
.chat-item{width:100%;display:flex;gap:8px;padding:9px 10px;border-radius:var(--r-md);
color:var(--text2);text-align:right;font-size:13.5px;margin-bottom:1px}
.chat-item:hover{background:var(--hover);color:var(--text)}
.chat-item.active{background:var(--hover2);color:var(--text)}
.chat-item .t{flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.sb-bottom{padding:6px;border-top:1px solid var(--border)}
.sb-b{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:var(--r-md);
color:var(--text2);font-size:13.5px;width:100%;text-align:right}
.sb-b:hover{background:var(--hover);color:var(--text)}
.sb-b.danger{color:var(--danger)}
.sb-sign{padding:12px;font-size:10px;color:var(--muted2);text-align:center;letter-spacing:1px}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
.top{min-height:52px;display:flex;align-items:center;gap:8px;padding:0 12px;
border-bottom:1px solid var(--border);flex-wrap:wrap}
.menu{width:36px;height:36px;border-radius:var(--r-md);display:none;place-items:center;font-size:20px;color:var(--text)}
.title{font-size:14px;font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.ibtn{width:36px;height:36px;border-radius:var(--r-md);display:grid;place-items:center;
color:var(--muted);font-size:16px}
.ibtn:hover{background:var(--hover);color:var(--text)}
.avatar{width:32px;height:32px;border-radius:50%;background:var(--panel);
display:grid;place-items:center;border:1px solid var(--border2);font-size:12px;font-weight:500}
.chat{flex:1;overflow-y:auto;overflow-x:hidden}
.inner{width:100%;max-width:760px;margin:0 auto;padding:32px 24px 40px;display:flex;flex-direction:column;gap:24px}
.welcome{text-align:center;padding:60px 20px;max-width:600px;margin:auto}
.wlogo{width:56px;height:56px;border-radius:50%;margin:0 auto 20px;background:var(--panel);
display:grid;place-items:center;font-size:20px;border:1px solid var(--border2)}
.welcome h1{font-size:24px;font-weight:500;margin-bottom:8px}
.welcome p{color:var(--muted);font-size:14px}
.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;max-width:520px;margin:28px auto 0}
.card{padding:14px 16px;border:1px solid var(--border);border-radius:var(--r-md);
text-align:right;color:var(--text2);font-size:13px}
.card:hover{background:var(--hover);color:var(--text)}
.card .ct{font-weight:500;color:var(--text);font-size:13.5px;margin-bottom:2px}
.card .cd{color:var(--muted);font-size:12px}
.msg{display:flex;gap:14px;width:100%;position:relative}
.msg.user{justify-content:flex-start;flex-direction:row-reverse}
.msg .av{width:28px;height:28px;border-radius:50%;flex-shrink:0;display:grid;
place-items:center;font-size:11px;font-weight:600;background:var(--panel);border:1px solid var(--border2)}
.msg.user .av{display:none}
.msg .bd{min-width:0;flex:1;position:relative}
.msg.user .bd{display:flex;flex-direction:row-reverse;max-width:80%}
.msg.user .bub{background:var(--panel);padding:10px 16px;border-radius:20px;border:1px solid var(--border)}
.msg.assistant .bub{color:var(--text2);line-height:1.7}
.msg .actions{position:absolute;top:0;left:0;display:flex;gap:3px;opacity:0}
.msg:hover .actions{opacity:1}
.mini{width:26px;height:26px;border-radius:var(--r-sm);background:var(--panel);
border:1px solid var(--border2);color:var(--muted);display:grid;place-items:center;font-size:11px;padding:0}
.mini:hover{color:var(--text)}
.mini svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.8}
.bub{white-space:pre-wrap;overflow-wrap:anywhere}
.bub pre{direction:ltr;text-align:left;overflow:auto;padding:14px 16px;margin:12px 0;
border-radius:var(--r-md);background:#050505;border:1px solid var(--border);
font-family:ui-monospace,Consolas,monospace;font-size:13px}
.bub code{direction:ltr;background:var(--panel);padding:2px 6px;border-radius:var(--r-sm);
font-family:ui-monospace,monospace;font-size:13px}
.bub img{max-width:100%;border-radius:var(--r-md);margin:10px 0;display:block;border:1px solid var(--border);cursor:pointer}
.think{display:flex;gap:8px;align-items:center;color:var(--muted);font-size:14px;padding:4px 0}
.dots{display:inline-flex;gap:4px}
.dots i{width:5px;height:5px;border-radius:50%;background:var(--text2);animation:pl 1.2s infinite}
.dots i:nth-child(2){animation-delay:.15s}
.dots i:nth-child(3){animation-delay:.3s}
@keyframes pl{0%,100%{opacity:.3}50%{opacity:1}}
.err-box{padding:14px 16px;background:rgba(239,68,68,.08);
border:1px solid rgba(239,68,68,.3);border-radius:var(--r-md);color:#fca5a5;font-size:13px;margin:10px 0}
.err-box b{color:#fff}
.comp-wrap{position:sticky;bottom:0;padding:8px 16px 16px;
background:linear-gradient(180deg,transparent,var(--bg) 40%);pointer-events:none;z-index:4}
.comp-inner{max-width:760px;margin:0 auto;pointer-events:auto}
.comp{display:flex;align-items:flex-end;gap:6px;padding:8px 10px 8px 12px;
border:1px solid var(--border);border-radius:28px;background:var(--panel)}
.tool{width:36px;height:36px;border-radius:50%;display:grid;place-items:center;color:var(--muted)}
.tool:hover{background:var(--hover);color:var(--text)}
.tool.on{background:var(--accent);color:var(--accent-fg)}
textarea#input{flex:1;min-height:24px;max-height:200px;resize:none;border:0;
background:transparent;padding:7px 8px;font-size:16px;outline:0;font-family:inherit;color:var(--text)}
.send{width:34px;height:34px;border-radius:50%;background:var(--accent);
color:var(--accent-fg);display:grid;place-items:center;flex-shrink:0}
.send:disabled{opacity:.3;background:var(--muted2)}
.foot{text-align:center;color:var(--muted2);font-size:11px;padding-top:8px;letter-spacing:1px}
.md{position:fixed;inset:0;background:rgba(0,0,0,.7);display:none;align-items:center;
justify-content:center;z-index:90;padding:20px;backdrop-filter:blur(6px)}
.md.on{display:flex}
.mbox{width:min(560px,100%);max-height:88vh;overflow-y:auto;background:var(--sb);
border:1px solid var(--border2);border-radius:var(--r-lg)}
.mhead{display:flex;align-items:center;justify-content:space-between;padding:16px 20px;
border-bottom:1px solid var(--border);position:sticky;top:0;background:inherit;z-index:2}
.mtitle{font-size:15px;font-weight:500;letter-spacing:1px}
.mx{width:30px;height:30px;border-radius:var(--r-md);color:var(--muted);font-size:22px;line-height:1}
.mx:hover{background:var(--hover);color:var(--text)}
.mbody{padding:18px 20px}
.fld{padding:14px 0;border-bottom:1px solid var(--border)}
.fld:last-child{border-bottom:0}
.ft{font-size:14px;font-weight:500}
.fd{color:var(--muted);font-size:12.5px;margin-top:4px}
.li{display:flex;justify-content:space-between;padding:10px 12px;border:1px solid var(--border);
border-radius:var(--r-md);margin-bottom:6px;background:var(--panel);font-size:13.5px;color:var(--text2)}
.li button{color:var(--danger);font-size:16px;padding:0 4px}
.tag-colors{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.tag-color{width:24px;height:24px;border-radius:50%;cursor:pointer;border:2px solid transparent}
.tag-color.selected{border-color:var(--text)}
.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.gallery-grid img{width:100%;height:150px;object-fit:cover;border-radius:var(--r-md);cursor:pointer;border:1px solid var(--border)}
.cp-shell{width:min(1200px,97vw);height:92vh;display:flex;flex-direction:column;
background:var(--sb);border:1px solid var(--border2);border-radius:var(--r-lg);overflow:hidden}
.cp-head{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;
background:var(--panel);border-bottom:1px solid var(--border)}
.cp-body{flex:1;display:flex;min-height:0}
.cp-side{width:200px;background:var(--sb);border-left:1px solid var(--border);padding:12px 8px}
.cp-side button{padding:10px 12px;border-radius:var(--r-md);color:var(--muted);font-size:13px;
text-align:right;width:100%;margin-bottom:2px}
.cp-side button.on{background:var(--hover2);color:var(--text)}
.cp-main{flex:1;overflow-y:auto;padding:20px;color:var(--text2)}
.cp-main h3{font-size:16px;margin-bottom:14px;color:var(--text);font-weight:500}
.cp-card{background:var(--panel);border:1px solid var(--border);border-radius:var(--r-md);padding:16px;margin-bottom:14px}
.cp-card h4{font-size:14px;margin-bottom:10px;color:var(--text);font-weight:500}
.cp-inp{background:var(--bg);border:1px solid var(--border2);color:var(--text);
border-radius:var(--r-md);padding:10px 12px;font-size:13px;outline:0;width:100%;font-family:inherit}
.cp-btn{padding:9px 14px;background:var(--accent);color:var(--accent-fg);
border-radius:var(--r-md);font-size:12.5px;font-weight:500;font-family:inherit;cursor:pointer;border:0}
.cp-btn.sec{background:transparent;color:var(--text2);border:1px solid var(--border2)}
.cp-btn.vip{background:rgba(212,175,55,.2);color:#f5c842;border:1px solid rgba(212,175,55,.4)}
.cp-btn.vip.on{background:rgba(212,175,55,.9);color:#1a1a1a}
.cp-btn.ban{background:rgba(239,68,68,.15);color:#fca5a5;border:1px solid rgba(239,68,68,.3)}
.cp-btn.ban.on{background:#a03030;color:#fff}
.cp-btn.del{background:transparent;color:var(--danger);border:1px solid rgba(239,68,68,.3)}
.cp-table{width:100%;border-collapse:collapse;font-size:13px}
.cp-table th{text-align:right;padding:10px 8px;color:var(--muted);font-weight:500;
font-size:11.5px;border-bottom:1px solid var(--border)}
.cp-table td{padding:10px 8px;border-bottom:1px solid var(--border)}
.cp-table .u{color:var(--text);font-weight:500}
.cp-table .badge{display:inline-block;font-size:9.5px;padding:2px 6px;
border-radius:var(--r-full);font-weight:600;margin-right:3px}
.cp-table .badge.admin{background:rgba(239,68,68,.15);color:#fca5a5}
.cp-table .badge.vip{background:rgba(212,175,55,.15);color:#f5c842}
.cp-table .badge.banned{background:rgba(239,68,68,.2);color:#ff8888}
@media (max-width:800px){
.sb{position:fixed;top:0;right:0;bottom:0;width:290px;max-width:88vw;
transform:translateX(100%);z-index:30;transition:transform .22s;
padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom)}
.sb.on{transform:translateX(0)}
.bd{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:25;display:none}
.bd.on{display:block}
.menu{display:grid}
.top{padding:8px 10px;padding-top:calc(8px + env(safe-area-inset-top));min-height:auto}
.inner{padding:16px 12px 24px;gap:18px}
.comp-wrap{padding:6px 8px 10px;padding-bottom:calc(10px + env(safe-area-inset-bottom))}
.cards{grid-template-columns:1fr}
.md{padding:0;align-items:flex-end}
.mbox{border-radius:var(--r-lg) var(--r-lg) 0 0;max-height:95vh;width:100%}
.cp-shell{width:100vw;height:100dvh;border-radius:0}
.cp-side{width:56px;padding:6px 2px}
.cp-side button{font-size:0;padding:12px 0;text-align:center}
.help{font-size:11px}
}
</style>
</head>
<body>

<div class="loading" id="loadingScreen">
  <div class="loading-logo">N</div>
</div>

<!-- AUTH SCREEN -->
<div class="auth hidden" id="authScreen">
  <div class="auth-box">
    <div class="auth-logo">N</div>
    <div class="auth-title">NOVA</div>
    <div class="auth-sub">POWERED BY SK</div>

    <div class="auth-tabs">
      <button class="auth-tab active" id="tLogin" type="button">התחברות</button>
      <button class="auth-tab" id="tReg" type="button">הרשמה</button>
    </div>

    <div id="authErr" class="msg-box err hidden"></div>

    <!-- LOGIN -->
    <form id="fLogin" class="auth-form">
      <div><div class="lbl">שם משתמש</div>
      <input class="inp" id="lu" required autocomplete="username"></div>
      <div><div class="lbl">סיסמה</div>
      <input class="inp" id="lp" type="password" required autocomplete="current-password"></div>
      <button class="btn" id="loginBtn" type="submit">התחבר</button>
    </form>

    <!-- REGISTER -->
    <form id="fReg" class="auth-form hidden">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
        <div><div class="lbl">שם פרטי</div>
        <input class="inp" id="rfn" required minlength="2"></div>
        <div><div class="lbl">שם משפחה</div>
        <input class="inp" id="rln" required minlength="2"></div>
      </div>
      <div><div class="lbl">שם משתמש</div>
      <input class="inp" id="ru" required minlength="3" autocomplete="username"></div>
      <div><div class="lbl">סיסמה</div>
      <input class="inp" id="rp" type="password" required minlength="4" autocomplete="new-password"></div>

      <div style="border-top:1px solid var(--border);padding-top:14px;margin-top:6px">
        <div class="ft" style="margin-bottom:8px;font-size:13.5px">🔑 מפתחות API</div>
        <div class="fd" style="margin-bottom:12px">המפתחות נשמרים בחשבון שלך בלבד.</div>

        <div><div class="lbl">Groq API Key (חובה - לצ'אט)</div>
        <input class="inp" id="rgk" required placeholder="gsk_..." autocomplete="off">
        <div class="help">
          איך משיגים בחינם?
          <a href="https://console.groq.com/keys" target="_blank">https://console.groq.com/keys</a>
          → Sign up → Create API Key
        </div></div>

        <div style="margin-top:12px"><div class="lbl">NVIDIA API Key (אופציונלי - לתמונות)</div>
        <input class="inp" id="rnk" placeholder="nvapi-... (אפשר להשאיר ריק)" autocomplete="off">
        <div class="help">
          איך משיגים בחינם?
          <a href="https://build.nvidia.com/black-forest-labs/flux_1-schnell" target="_blank">https://build.nvidia.com</a>
          → Sign up → Get API Key
        </div></div>

        <div class="help" style="margin-top:14px;padding:10px;background:rgba(239,68,68,.08);
        border:1px solid rgba(239,68,68,.25);border-radius:var(--r-md);color:#fca5a5">
          ⚠️ בלי Groq Key - לא ניתן להשתמש בצ'אט.
        </div>
      </div>

      <button class="btn" id="regBtn" type="submit" style="margin-top:14px">הירשם</button>
    </form>

    <div class="sb-sign" style="margin-top:20px"><b>NOVA v16.0</b></div>
  </div>
</div>

<!-- MAIN APP -->
<div class="app hidden" id="app">
<div class="bd" id="bd"></div>

<aside class="sb" id="sb">
  <div class="sb-top">
    <div class="sb-row">
      <div class="sb-logo">N</div>
      <div class="sb-info">
        <div class="sb-name" id="sbUser">—</div>
        <div class="sb-brand">POWERED BY SK</div>
      </div>
    </div>
    <button class="sb-new" id="btnNew">+ שיחה חדשה</button>
  </div>
  <div class="sb-label">היסטוריה</div>
  <div class="sb-list" id="chatList"></div>
  <div class="sb-bottom">
    <button class="sb-b" id="btnSettings">⚙️ הגדרות</button>
    <button class="sb-b" id="btnKeys">🔑 עדכן מפתחות</button>
    <button class="sb-b" id="btnGallery">🖼️ הגלריה שלי</button>
    <button class="sb-b danger" id="btnLogout">↩️ התנתקות</button>
    <div class="sb-sign"><b>NOVA v16.0</b></div>
  </div>
</aside>

<main class="main">
  <header class="top">
    <button class="menu" id="menuBtn">☰</button>
    <div class="title" id="title">NOVA</div>
    <button class="ibtn" id="themeBtn">🌓</button>
    <div class="avatar" id="userBtn">?</div>
  </header>

  <section class="chat" id="chatArea"><div class="inner" id="chatInner"></div></section>

  <div class="comp-wrap">
    <div class="comp-inner">
      <div class="comp">
        <textarea id="input" rows="1" placeholder="הודעה ל-NOVA" autocomplete="off"></textarea>
        <button class="send" id="sendBtn" disabled>↑</button>
      </div>
      <div class="foot">NOVA יכול לטעות · <b>POWERED BY SK</b></div>
    </div>
  </div>
</main>
</div>

<!-- SETTINGS MODAL -->
<div class="md" id="mdSettings"><div class="mbox">
  <div class="mhead"><div class="mtitle">הגדרות</div><button class="mx" data-close="mdSettings">×</button></div>
  <div class="mbody">
    <div class="fld"><div class="ft">מודל</div>
      <select id="modelSelect" class="inp" style="margin-top:8px">
        <option value="openai/gpt-oss-120b">NOVA Core</option>
        <option value="openai/gpt-oss-20b">NOVA Fast</option>
      </select>
    </div>
    <div class="fld"><div class="ft">זיכרון אוטומטי</div>
      <div class="fd">שמירת העדפות</div>
      <label style="display:inline-block;margin-top:8px">
        <input type="checkbox" id="optMemory"> הפעל
      </label>
    </div>
  </div>
</div></div>

<!-- KEYS MODAL -->
<div class="md" id="mdKeys"><div class="mbox">
  <div class="mhead"><div class="mtitle">עדכון מפתחות API</div><button class="mx" data-close="mdKeys">×</button></div>
  <div class="mbody">
    <div class="fld">
      <div class="lbl">Groq API Key (חובה)</div>
      <input class="inp" id="updGroq" placeholder="gsk_...">
    </div>
    <div class="fld">
      <div class="lbl">NVIDIA API Key (אופציונלי)</div>
      <input class="inp" id="updNvidia" placeholder="nvapi-...">
    </div>
    <button class="btn" id="btnSaveKeys" style="margin-top:14px">שמור מפתחות</button>
  </div>
</div></div>

<!-- GALLERY MODAL -->
<div class="md" id="mdGallery"><div class="mbox" style="width:min(900px,96vw)">
  <div class="mhead"><div class="mtitle">הגלריה שלי</div><button class="mx" data-close="mdGallery">×</button></div>
  <div class="mbody"><div class="gallery-grid" id="galleryGrid"></div></div>
</div></div>

<!-- LIGHTBOX -->
<div class="md" id="mdLightbox" style="background:rgba(0,0,0,.95)">
  <img id="lightboxImg" style="max-width:96vw;max-height:96vh;object-fit:contain;border-radius:8px">
</div>

<script>
"use strict";

var S = {
  token: localStorage.getItem("nova_token") || "",
  user: null,
  chatId: null,
  model: localStorage.getItem("nova_model") || "openai/gpt-oss-120b",
  sending: false,
  isMobile: /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent)
};

function $(id){ return document.getElementById(id); }
function esc(s){ return String(s).replace(/[&<>"']/g, function(c){ return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[c]); }); }

async function api(p, o){
  o = o || {};
  var h = o.headers || {};
  if (!(o.body instanceof FormData)) h["Content-Type"] = h["Content-Type"] || "application/json";
  if (S.token) h["Authorization"] = "Bearer " + S.token;
  var r = await fetch(p, Object.assign({}, o, {headers: h}));
  var d = null;
  try { d = await r.json(); } catch (e) { throw new Error("Invalid response"); }
  if (!r.ok) throw new Error(d.error || ("HTTP " + r.status));
  if (d.ok === false) throw new Error(d.error || "Failed");
  return d;
}

function showAuth(){ $("loadingScreen").classList.add("hidden"); $("authScreen").classList.remove("hidden"); $("app").classList.add("hidden"); }
function hideAuth(){ $("authScreen").classList.add("hidden"); $("app").classList.remove("hidden"); }
function hideLoading(){ $("loadingScreen").classList.add("hidden"); }
function authErr(t, type){
  type = type || "err";
  var e = $("authErr");
  e.textContent = t; e.className = "msg-box " + type; e.classList.remove("hidden");
  setTimeout(function(){ e.classList.add("hidden"); }, 8000);
}

async function doLogin(e){
  if (e) e.preventDefault();
  var b = $("loginBtn"); var orig = b.textContent;
  b.disabled = true; b.textContent = "מתחבר...";
  try {
    var r = await api("/api/auth/login", {method:"POST", body: JSON.stringify({
      username: $("lu").value.trim(), password: $("lp").value
    })});
    S.token = r.session.token;
    localStorage.setItem("nova_token", S.token);
    await checkAuth(); await boot();
  } catch (err) { authErr("שגיאה: " + err.message); }
  finally { b.disabled = false; b.textContent = orig; }
}

async function doRegister(e){
  if (e) e.preventDefault();
  var b = $("regBtn"); var orig = b.textContent;
  b.disabled = true; b.textContent = "נרשם...";
  try {
    var r = await api("/api/auth/register", {method:"POST", body: JSON.stringify({
      username: $("ru").value.trim(),
      password: $("rp").value,
      first_name: $("rfn").value.trim(),
      last_name: $("rln").value.trim(),
      groq_key: $("rgk").value.trim(),
      nvidia_key: $("rnk").value.trim()
    })});
    S.token = r.session.token;
    localStorage.setItem("nova_token", S.token);
    await checkAuth(); await boot();
  } catch (err) { authErr("שגיאה: " + err.message); }
  finally { b.disabled = false; b.textContent = orig; }
}

async function checkAuth(){
  if (!S.token) { showAuth(); return false; }
  try {
    var r = await api("/api/auth/verify");
    S.user = r.user;
    return true;
  } catch (e) {
    S.token = ""; localStorage.removeItem("nova_token");
    showAuth(); return false;
  }
}

async function doLogout(){
  try { await api("/api/auth/logout", {method:"POST", body:"{}"}); } catch (e) {}
  localStorage.removeItem("nova_token");
  S.token = "";
  location.reload();
}

// UI EVENTS
$("tLogin").onclick = function(){
  $("tLogin").classList.add("active"); $("tReg").classList.remove("active");
  $("fLogin").classList.remove("hidden"); $("fReg").classList.add("hidden");
  $("authErr").classList.add("hidden");
};
$("tReg").onclick = function(){
  $("tReg").classList.add("active"); $("tLogin").classList.remove("active");
  $("fReg").classList.remove("hidden"); $("fLogin").classList.add("hidden");
  $("authErr").classList.add("hidden");
};
$("fLogin").onsubmit = doLogin;
$("fReg").onsubmit = doRegister;
$("btnLogout").onclick = doLogout;

function updateUserUI(){
  if (!S.user) return;
  var name = (S.user.first_name || "") + " " + (S.user.last_name || "");
  $("sbUser").textContent = name.trim() || S.user.username;
  $("userBtn").textContent = (S.user.first_name || S.user.username || "?").charAt(0).toUpperCase();
}

async function loadChats(){
  try {
    var r = await api("/api/chats");
    var list = $("chatList"); list.innerHTML = "";
    if (!r.chats.length) {
      list.innerHTML = '<div style="padding:12px;color:var(--muted2);font-size:12.5px;text-align:center">אין שיחות</div>';
      return;
    }
    r.chats.forEach(function(c){
      var b = document.createElement("button");
      b.className = "chat-item" + (c.id === S.chatId ? " active" : "");
      b.innerHTML = '<span class="t">' + esc(c.title) + '</span>';
      b.onclick = function(){ openChat(c.id); };
      list.appendChild(b);
    });
  } catch (e) { console.error(e); }
}

async function createNewChat(){
  try {
    var r = await api("/api/chats", {method:"POST", body: JSON.stringify({title:"שיחה חדשה"})});
    S.chatId = r.chat_id;
    $("title").textContent = "שיחה חדשה";
    renderWelcome();
    await loadChats();
    $("input").focus();
  } catch (e) { console.error(e); }
}

async function openChat(id){
  try {
    var r = await api("/api/chats/" + encodeURIComponent(id));
    S.chatId = id;
    $("chatInner").innerHTML = ""; $("title").textContent = r.chat.title;
    r.messages.forEach(function(m){ addMessage(m.role, m.content, m.id); });
    if (!r.messages.length) renderWelcome();
    scrollEnd(true);
  } catch (e) { console.error(e); }
}

function renderWelcome(){
  $("chatInner").innerHTML = '<div class="welcome"><div class="wlogo">N</div><h1>שלום, ' + esc(S.user.first_name || S.user.username) + '</h1><p>איך אפשר לעזור?</p></div>';
}

function addMessage(role, content, msgId){
  var w = $("chatInner").querySelector(".welcome"); if (w) w.remove();
  var stick = isNearBottom();
  var m = document.createElement("div");
  m.className = "msg " + (role === "user" ? "user" : "assistant");
  var av = '<div class="av">' + (role === "user" ? "" : "N") + '</div>';
  var bub = '<div class="bub">' + (role === "user" ? esc(content).replace(/\n/g,"<br>") : renderMD(content)) + '</div>';
  var actions = "";
  if (msgId) {
    actions = '<div class="actions">' +
      '<button class="mini" onclick="copyMsg(' + msgId + ',event)" title="העתק">📋</button>' +
      '<button class="mini" onclick="delMsg(' + msgId + ',event)" title="מחק">🗑️</button>' +
      '</div>';
  }
  m.innerHTML = av + '<div class="bd">' + bub + actions + '</div>';
  $("chatInner").appendChild(m);
  if (stick) scrollEnd();
}

function renderMD(text){
  var s = esc(text);
  s = s.replace(/```(\w*)\n?([\s\S]*?)```/g, function(_, l, c){ return "<pre><code>" + c.trim() + "</code></pre>"; });
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/\n/g, "<br>");
  return s;
}

function copyMsg(id, e){
  e.stopPropagation();
  var el = document.querySelector('[data-id="' + id + '"]');
  alert("העתק הודעה #" + id);
}

function delMsg(id, e){
  e.stopPropagation();
  if (!confirm("למחוק את ההודעה?")) return;
  api("/api/message/" + id, {method:"DELETE"}).then(function(){ location.reload(); });
}

function addThinking(){
  var w = $("chatInner").querySelector(".welcome"); if (w) w.remove();
  var m = document.createElement("div");
  m.className = "msg assistant";
  m.innerHTML = '<div class="av">N</div><div class="bd"><div class="bub"><div class="think">חושב<span class="dots"><i></i><i></i><i></i></span></div></div></div>';
  $("chatInner").appendChild(m);
  scrollEnd();
  return m;
}

function isNearBottom(){ var a = $("chatArea"); return a.scrollHeight - a.scrollTop - a.clientHeight < 150; }
function scrollEnd(f){ f = f || false; if (!f && !isNearBottom()) return; requestAnimationFrame(function(){ $("chatArea").scrollTop = $("chatArea").scrollHeight; }); }

function updateSend(){ $("sendBtn").disabled = !$("input").value.trim() || S.sending; }
$("input").oninput = function(){
  var t = $("input"); t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 200) + "px";
  updateSend();
};
$("input").onkeydown = function(e){
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
};
$("sendBtn").onclick = send;

async function send(){
  if (S.sending) return;
  var text = $("input").value.trim();
  if (!text) return;
  S.sending = true;
  updateSend();
  if (!S.chatId) await createNewChat();
  $("input").value = ""; $("input").style.height = "auto"; updateSend();
  addMessage("user", text);
  var thinking = addThinking();
  try {
    var r = await api("/api/chat", {method:"POST", body: JSON.stringify({
      chat_id: S.chatId, message: text, model: S.model
    })});
    thinking.remove();
    if (r.images && r.images.length) {
      r.images.forEach(function(img){ addImageCard(img.prompt, img.url); });
    }
    if (r.img_error) {
      var eb = document.createElement("div");
      eb.className = "msg assistant";
      eb.innerHTML = '<div class="av">N</div><div class="bd"><div class="err-box"><b>שגיאה:</b> ' + esc(r.img_error) + '</div></div>';
      $("chatInner").appendChild(eb);
      scrollEnd();
    }
    if (r.reply && r.reply.trim()) {
      addMessage("assistant", r.reply);
    }
    await loadChats();
  } catch (e) {
    thinking.remove();
    addMessage("assistant", "❌ שגיאה: " + e.message);
  }
  finally { S.sending = false; updateSend(); $("input").focus(); }
}

function addImageCard(prompt, url){
  var m = document.createElement("div");
  m.className = "msg assistant";
  m.innerHTML = '<div class="av">N</div><div class="bd"><div class="bub">' +
    '<div style="font-size:11.5px;color:var(--muted);margin-bottom:8px">' + esc(prompt) + '</div>' +
    '<img src="' + url + '" loading="lazy" onclick="lightbox(\'' + url.replace(/'/g, "\\'") + '\')"></div></div>';
  $("chatInner").appendChild(m);
  scrollEnd();
}

function lightbox(url){ $("lightboxImg").src = url; $("mdLightbox").classList.add("on"); }

// SETTINGS
$("btnNew").onclick = createNewChat;
$("btnSettings").onclick = function(){ $("mdSettings").classList.add("on"); closeSB(); };
$("modelSelect").value = S.model;
$("modelSelect").onchange = function(e){
  S.model = e.target.value;
  localStorage.setItem("nova_model", S.model);
};

// KEYS MODAL
$("btnKeys").onclick = function(){ $("mdKeys").classList.add("on"); closeSB(); };
$("btnSaveKeys").onclick = async function(){
  var gk = $("updGroq").value.trim();
  var nk = $("updNvidia").value.trim();
  if (!gk && !nk) { alert("הזן לפחות מפתח אחד"); return; }
  try {
    await api("/api/user/keys", {method:"POST", body: JSON.stringify({groq_key: gk, nvidia_key: nk})});
    alert("✅ המפתחות נשמרו");
    $("mdKeys").classList.remove("on");
  } catch (e) { alert("שגיאה: " + e.message); }
};

// GALLERY
$("btnGallery").onclick = async function(){
  try {
    var r = await api("/api/gallery");
    var g = $("galleryGrid"); g.innerHTML = "";
    if (!r.images.length) { g.innerHTML = '<div style="padding:20px;color:var(--muted);text-align:center">אין תמונות</div>'; }
    else {
      r.images.forEach(function(im){
        var img = document.createElement("img");
        img.src = im.url; img.alt = im.prompt;
        img.onclick = function(){ lightbox(im.url); };
        g.appendChild(img);
      });
    }
    $("mdGallery").classList.add("on"); closeSB();
  } catch (e) { alert(e.message); }
};

// MODAL CLOSE
Array.prototype.forEach.call(document.querySelectorAll("[data-close]"), function(b){
  b.onclick = function(){
    var id = b.dataset.close;
    $(id).classList.remove("on");
  };
});
["mdSettings","mdKeys","mdGallery"].forEach(function(id){
  $(id).onclick = function(e){ if (e.target === $(id)) $(id).classList.remove("on"); };
});
$("mdLightbox").onclick = function(){ $("mdLightbox").classList.remove("on"); };

// SIDEBAR MOBILE
function closeSB(){ if (window.innerWidth <= 800) { $("sb").classList.remove("on"); $("bd").classList.remove("on"); } }
$("menuBtn").onclick = function(){ $("sb").classList.toggle("on"); $("bd").classList.toggle("on"); };
$("bd").onclick = closeSB;

// THEME
$("themeBtn").onclick = function(){
  var isLight = document.body.style.background === "rgb(250, 250, 250)";
  if (isLight) {
    document.body.style.background = ""; document.body.style.color = "";
    document.documentElement.style.background = "";
  } else {
    document.body.style.background = "#fafafa"; document.body.style.color = "#0a0a0a";
    document.documentElement.style.background = "#fafafa";
  }
};

// BOOT
async function boot(){
  updateUserUI();
  renderWelcome();
  await loadChats();
  $("input").focus();
}

(async function init(){
  var ok = await checkAuth();
  if (ok) { hideAuth(); hideLoading(); await boot(); }
  else { hideLoading(); }
})();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "NOVA/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def _route(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        method = self.command

        # GET
        if method == "GET":
            if path == "/" or path == "/index.html":
                return html_resp(self, HTML)
            if path == "/manifest.json":
                return json_resp(self, MANIFEST)
            if path == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            if path == "/health":
                return json_resp(self, {"ok": True, "version": VERSION, "time": now()})

            if path == "/api/auth/verify":
                u = require_auth(self)
                return json_resp(self, {"ok": True, "user": u})
            if path == "/api/chats":
                u = require_auth(self)
                return json_resp(self, {"ok": True, "chats": get_chats(u["user_id"])})
            if path.startswith("/api/chats/"):
                u = require_auth(self)
                cid = urllib.parse.unquote(path[11:])
                data = get_chat(cid, u["user_id"])
                if not data:
                    return json_resp(self, {"ok": False, "error": "Not found"}, 404)
                return json_resp(self, {"ok": True, **data})
            if path == "/api/gallery":
                u = require_auth(self)
                return json_resp(self, {"ok": True, "images": get_user_gallery(u["user_id"])})
            if path == "/api/admin/users":
                require_admin(self)
                return json_resp(self, {"ok": True, "users": get_all_users()})
            if path == "/api/memory":
                u = require_auth(self)
                return json_resp(self, {"ok": True, "memories": get_memories(u["user_id"])})
            if path == "/api/agent/poll":
                u = require_auth(self)
                task = poll_agent_command(u["user_id"])
                return json_resp(self, {"ok": True, "task": task})

            return json_resp(self, {"ok": False, "error": "Not found"}, 404)

        # POST
        if method == "POST":
            try:
                body = read_json(self)
            except ValueError as e:
                return json_resp(self, {"ok": False, "error": str(e)}, 400)

            if path == "/api/auth/register":
                r = register_user(
                    body.get("username","").strip(),
                    body.get("password",""),
                    body.get("first_name","").strip(),
                    body.get("last_name","").strip(),
                    body.get("groq_key","").strip(),
                    body.get("nvidia_key","").strip()
                )
                if not r.get("ok"):
                    return json_resp(self, r, 400)
                return json_resp(self, {"ok": True, **r})

            if path == "/api/auth/login":
                r = login_user(body.get("username","").strip(), body.get("password",""))
                if not r.get("ok"):
                    return json_resp(self, r, 401)
                return json_resp(self, {"ok": True, **r})

            if path == "/api/auth/logout":
                return json_resp(self, {"ok": True})

            if path == "/api/user/keys":
                u = require_auth(self)
                ok = update_user_keys(u["user_id"], body.get("groq_key",""), body.get("nvidia_key",""))
                return json_resp(self, {"ok": ok})

            if path == "/api/chats":
                u = require_auth(self)
                cid = create_chat(u["user_id"], body.get("title","שיחה חדשה"))
                return json_resp(self, {"ok": True, "chat_id": cid})

            if path == "/api/chat":
                u = require_auth(self)
                return self._handle_chat(u, body)

            if path == "/api/memory":
                u = require_auth(self)
                add_memory(u["user_id"], body.get("text",""), body.get("category","Preference"))
                return json_resp(self, {"ok": True})

            if path == "/api/agent/result":
                u = require_auth(self)
                submit_agent_result(u["user_id"], int(body.get("task_id",0)), body.get("result",""))
                return json_resp(self, {"ok": True})

            if path == "/api/agent/command":
                u = require_auth(self)
                enqueue_agent_command(u["user_id"], body.get("command",""))
                return json_resp(self, {"ok": True})

            return json_resp(self, {"ok": False, "error": "Not found"}, 404)

        # DELETE
        if method == "DELETE":
            if path.startswith("/api/message/"):
                u = require_auth(self)
                mid = int(path[len("/api/message/"):])
                return json_resp(self, {"ok": delete_message(mid, u["user_id"])})
            if path.startswith("/api/chats/"):
                u = require_auth(self)
                cid = urllib.parse.unquote(path[11:])
                delete_chat(cid, u["user_id"])
                return json_resp(self, {"ok": True})
            return json_resp(self, {"ok": False, "error": "Not found"}, 404)

        return json_resp(self, {"ok": False, "error": "Method not allowed"}, 405)

    def _handle_chat(self, u, body):
        cid = body.get("chat_id","")
        msg = (body.get("message","") or "").strip()
        model = body.get("model") or "openai/gpt-oss-120b"

        if not cid:
            return json_resp(self, {"ok": False, "error": "chat_id required"}, 400)

        if msg:
            save_message(cid, "user", msg)

        # Image intent
        if msg and detect_image_intent(msg):
            if not u.get("nvidia_key"):
                return json_resp(self, {
                    "ok": True, "reply": "",
                    "img_error": "כדי ליצור תמונות, הוסף NVIDIA API Key בהגדרות. השג בחינם: https://build.nvidia.com"
                })
            url, err = generate_image_for_user(msg, u["nvidia_key"])
            if url:
                log_image(u["user_id"], msg, url)
                save_message(cid, "assistant", "[תמונה נוצרה: " + msg + "]")
                return json_resp(self, {
                    "ok": True, "reply": "הנה התמונה שיצרתי עבורך:",
                    "images": [{"prompt": msg, "url": url}]
                })
            return json_resp(self, {"ok": True, "reply": "", "img_error": err or "יצירת תמונה נכשלה"})

        # Chat
        if not u.get("groq_key"):
            return json_resp(self, {
                "ok": True, "reply": "",
                "img_error": "כדי להשתמש בצ'אט, הוסף Groq API Key בהגדרות. השג בחינם: https://console.groq.com/keys"
            })

        history = get_history(cid, limit=14) if msg else []

        sys_parts = [BASE_SYSTEM]

        lang = detect_lang(msg) if msg else "he"
        if lang == "he":
            sys_parts.append("Respond in Hebrew.")
        elif lang == "ru":
            sys_parts.append("Respond in Russian.")
        else:
            sys_parts.append("Respond in English.")

        # User name context
        if u.get("first_name"):
            sys_parts.append("\nThe user's name is " + u["first_name"] + " " + (u.get("last_name") or "") + ".")

        if u.get("vip"):
            sys_parts.append("\n=== VIP USER ===\nExtended responses allowed.")

        if body.get("memory_enabled", True):
            mem = memory_ctx(u["user_id"])
            if mem:
                sys_parts.append("\n=== USER MEMORY ===\n" + mem + "\n")

        system_prompt = "\n".join(sys_parts)
        result = call_groq_for_user(history, u["groq_key"], model, system_prompt=system_prompt)

        if not result.get("ok"):
            return json_resp(self, {
                "ok": True, "reply": "",
                "img_error": "AI error: " + result.get("error","unknown")
            })

        reply = result["reply"]
        save_message(cid, "assistant", reply)
        return json_resp(self, {"ok": True, "reply": reply, "lang": lang})

    def do_GET(self):
        try:
            self._route()
        except PermissionError as e:
            json_resp(self, {"ok": False, "error": str(e)}, 401)
        except Exception as e:
            log("GET " + self.path + ": " + str(e), "ERROR")
            log(traceback.format_exc(), "ERROR")
            try: json_resp(self, {"ok": False, "error": str(e)[:200]}, 500)
            except: pass

    def do_POST(self):
        try:
            self._route()
        except PermissionError as e:
            json_resp(self, {"ok": False, "error": str(e)}, 401)
        except Exception as e:
            log("POST " + self.path + ": " + str(e), "ERROR")
            log(traceback.format_exc(), "ERROR")
            try: json_resp(self, {"ok": False, "error": str(e)[:200]}, 500)
            except: pass

    def do_DELETE(self):
        try:
            self._route()
        except PermissionError as e:
            json_resp(self, {"ok": False, "error": str(e)}, 401)
        except Exception as e:
            try: json_resp(self, {"ok": False, "error": str(e)[:200]}, 500)
            except: pass


def main():
    log("=" * 60)
    log(" " + APP_NAME + " v" + VERSION)
    log("=" * 60)
    log(" DB: " + ("SQLite" if USE_SQLITE else "Postgres"))
    log(" Mode: Public Multi-User")
    log(" Port: " + str(PORT))
    try:
        init_db()
    except Exception as e:
        log("DB init failed: " + str(e), "ERROR")
        sys.exit(1)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log("Listening on http://" + HOST + ":" + str(PORT))
    log("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
