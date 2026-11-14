"""database.py — SQLite with Python 3.14 fix + dynamic settings"""
import sqlite3, json, logging, os, random, traceback, time, shutil, tempfile, gzip
from datetime import datetime

sqlite3.register_adapter(datetime, lambda dt: dt.strftime("%Y-%m-%d %H:%M:%S"))
sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "bot.db")
DEFAULT_CREDITS = 250

def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        c.execute("PRAGMA synchronous=NORMAL")
    except: pass
    return c

class _FakeConn:
    def __init__(self): self._c = _conn()
    def cursor(self): return self._c.cursor()
    def commit(self): self._c.commit()
    def rollback(self): self._c.rollback()
    def close(self):
        try: self._c.close()
        except: pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close(); return False

class _FakePool:
    def getconn(self): return _FakeConn()
    def putconn(self, c):
        try: c.close()
        except: pass
    def closeall(self): pass

connection_pool = _FakePool()

def create_connection_pool():
    try:
        c = _conn(); c.close()
        logger.info(f"✅ SQLite: {DB_PATH}")
        return True
    except Exception as e:
        logger.error(f"❌ SQLite: {e}")
        return False

def close_connection_pool(): pass

def setup_database():
    c = _conn()
    try:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tier TEXT DEFAULT 'Trial', credits INTEGER DEFAULT 250, first_start INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS user_plans (
                user_id INTEGER PRIMARY KEY, tier TEXT NOT NULL, expiry_date TIMESTAMP);
            CREATE TABLE IF NOT EXISTS redeem_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL,
                tier TEXT NOT NULL, duration_days INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_at TIMESTAMP, used_by INTEGER);
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY, banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reason TEXT);
            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id INTEGER PRIMARY KEY, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS restricted_admins (
                user_id INTEGER PRIMARY KEY, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS sites (
                url TEXT PRIMARY KEY, gateway TEXT DEFAULT '', price REAL DEFAULT 0, status TEXT DEFAULT 'Unknown', added_by INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS proxies (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, proxy TEXT NOT NULL, status TEXT DEFAULT 'unknown', response_time REAL DEFAULT 0, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, proxy));
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS check_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, status TEXT, gateway TEXT, card TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS charged_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, card TEXT, response TEXT, gateway TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS bot_gcs (
                chat_id TEXT PRIMARY KEY, title TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        """)
        c.commit()
    except Exception as e:
        logger.error(f"❌ setup: {e}")
    finally:
        c.close()

def _parse_dt(v):
    if v is None or isinstance(v, datetime): return v
    try:
        s = str(v).replace("Z", "").split(".")[0]
        return datetime.fromisoformat(s)
    except:
        try: return datetime.strptime(str(v).split(".")[0], "%Y-%m-%d %H:%M:%S")
        except: return None

def get_or_create_user(user_id, username):
    c = _conn()
    try:
        cur = c.execute("SELECT username, joined_at, tier, credits, first_start FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        if r: return (r["username"], _parse_dt(r["joined_at"]), r["tier"] or "Trial", r["credits"] if r["credits"] is not None else DEFAULT_CREDITS, r["first_start"])
        ja = datetime.now()
        c.execute("INSERT INTO users (user_id, username, joined_at, tier, credits, first_start) VALUES (?, ?, ?, ?, ?, 0)", (user_id, username, ja, "Trial", DEFAULT_CREDITS))
        c.commit()
        return (username, ja, "Trial", DEFAULT_CREDITS, 0)
    except Exception: return (username, datetime.now(), "Trial", DEFAULT_CREDITS, 0)
    finally: c.close()

def mark_first_start(user_id):
    c = _conn()
    try:
        c.execute("UPDATE users SET first_start = 1 WHERE user_id = ?", (user_id,))
        c.commit()
    finally: c.close()

def is_db_admin(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    except: return False
    finally: c.close()

def is_restricted_admin(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM restricted_admins WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    except: return False
    finally: c.close()

def add_restricted_admin(user_id):
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO restricted_admins (user_id) VALUES (?)", (user_id,))
        c.commit()
        return True
    except: return False
    finally: c.close()

def add_db_admin(user_id):
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO bot_admins (user_id) VALUES (?)", (user_id,))
        c.commit()
        return True
    except: return False
    finally: c.close()

def get_bot_setting(key, default=None):
    c = _conn()
    try:
        cur = c.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        r = cur.fetchone()
        return r["value"] if r else default
    except: return default
    finally: c.close()

def set_bot_setting(key, value):
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(value)))
        c.commit()
        return True
    except: return False
    finally: c.close()

def log_charged_hit(user_id, card, response, gateway):
    c = _conn()
    try:
        c.execute("INSERT INTO charged_log (user_id, card, response, gateway) VALUES (?, ?, ?, ?)", (user_id, card, response, gateway))
        c.commit()
    except: pass
    finally: c.close()

def get_charged_logs():
    c = _conn()
    try:
        cur = c.execute("SELECT card, response, gateway, created_at FROM charged_log ORDER BY id DESC LIMIT 100")
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def clear_charged_log():
    c = _conn()
    try:
        c.execute("DELETE FROM charged_log")
        c.commit()
    except: pass
    finally: c.close()

def add_bot_gc(chat_id, title):
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO bot_gcs (chat_id, title) VALUES (?, ?)", (str(chat_id), title))
        c.commit()
    except: pass
    finally: c.close()

def get_bot_gcs():
    c = _conn()
    try:
        cur = c.execute("SELECT chat_id, title FROM bot_gcs")
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def remove_bot_gc(chat_id):
    c = _conn()
    try:
        c.execute("DELETE FROM bot_gcs WHERE chat_id = ?", (str(chat_id),))
        c.commit()
    except: pass
    finally: c.close()

def backup_database():
    c = _conn()
    try:
        raw_path = os.path.join(_THIS_DIR, "backup_temp.db")
        if os.path.exists(raw_path): os.remove(raw_path)
        c.execute(f"VACUUM INTO '{raw_path}'")
        c.close()

        gz_path = raw_path + ".gz"
        with open(raw_path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(raw_path)

        size = os.path.getsize(gz_path)
        if size > 20 * 1024 * 1024:
            part_paths = []
            with open(gz_path, 'rb') as f:
                part_num = 1
                while True:
                    chunk = f.read(18 * 1024 * 1024)
                    if not chunk: break
                    part_path = os.path.join(_THIS_DIR, f"backup_part{part_num}.gz")
                    with open(part_path, 'wb') as pf: pf.write(chunk)
                    part_paths.append(part_path)
                    part_num += 1
            os.remove(gz_path)
            return part_paths
        return [gz_path]
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        return None
