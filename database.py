"""database.py — SQLite with Python 3.14 fix + dynamic settings + original functions"""
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

def update_user_credits(user_id, credits_change):
    c = _conn()
    try:
        cur = c.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (credits_change, user_id))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def get_user_credits(user_id):
    try:
        from config import OWNER_ID
        if user_id == OWNER_ID: return float('inf')
        c0 = _conn()
        try:
            cur0 = c0.execute("SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,))
            if cur0.fetchone(): return float('inf')
        finally: c0.close()
    except: pass

    c = _conn()
    try:
        cur = c.execute("SELECT tier, expiry_date FROM user_plans WHERE user_id = ?", (user_id,))
        pr = cur.fetchone()
        if pr:
            exp = _parse_dt(pr["expiry_date"])
            if exp and datetime.now() <= exp: return float('inf')
        cur = c.execute("SELECT credits FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        return r["credits"] if r else None
    except: return None
    finally: c.close()

def update_user_plan(user_id, tier, expiry_date):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        if not cur.fetchone(): return False
        cur = c.execute("SELECT 1 FROM user_plans WHERE user_id = ?", (user_id,))
        if cur.fetchone():
            c.execute("UPDATE user_plans SET tier = ?, expiry_date = ? WHERE user_id = ?", (tier, expiry_date, user_id))
        else:
            c.execute("INSERT INTO user_plans (user_id, tier, expiry_date) VALUES (?, ?, ?)", (user_id, tier, expiry_date))
        if tier == "Trial":
            c.execute("UPDATE users SET tier = ?, credits = ? WHERE user_id = ?", (tier, DEFAULT_CREDITS, user_id))
        else:
            c.execute("UPDATE users SET tier = ? WHERE user_id = ?", (tier, user_id))
        c.commit()
        return True
    except:
        try: c.rollback()
        except: pass
        return False
    finally: c.close()

def get_user_plan(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT tier, expiry_date FROM user_plans WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        if not r: return None
        return (r["tier"], _parse_dt(r["expiry_date"]))
    except: return None
    finally: c.close()

def has_user_active_plan(user_id):
    try:
        from config import OWNER_ID
        if user_id == OWNER_ID: return True
        c0 = _conn()
        try:
            cur0 = c0.execute("SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,))
            if cur0.fetchone(): return True
        finally: c0.close()
    except: pass

    plan = get_user_plan(user_id)
    if plan:
        _, exp = plan
        if exp and datetime.now() <= exp: return True
    return False

def save_redeem_code(code, tier, duration_days):
    c = _conn()
    try:
        c.execute("INSERT INTO redeem_codes (code, tier, duration_days) VALUES (?, ?, ?)", (code, tier, duration_days))
        c.commit()
        return True
    except: return False
    finally: c.close()

def get_redeem_code_info(code):
    c = _conn()
    try:
        cur = c.execute("SELECT tier, duration_days, used_at FROM redeem_codes WHERE code = ?", (code,))
        r = cur.fetchone()
        if not r or r["used_at"]: return None
        return {"tier": r["tier"], "duration_days": r["duration_days"]}
    except: return None
    finally: c.close()

def mark_redeem_code_as_used(code, user_id):
    c = _conn()
    try:
        cur = c.execute("UPDATE redeem_codes SET used_at = CURRENT_TIMESTAMP, used_by = ? WHERE code = ?", (user_id, code))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def delete_redeem_code(code):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM redeem_codes WHERE code = ?", (code,))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def is_user_banned(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    except: return False
    finally: c.close()

def ban_user(user_id, reason=None):
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO banned_users (user_id, reason) VALUES (?, ?)", (user_id, reason))
        c.commit()
        return True
    except: return False
    finally: c.close()

def unban_user(user_id):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        c.commit()
        return cur.rowcount > 0
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

def remove_db_admin(user_id):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM bot_admins WHERE user_id = ?", (user_id,))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def is_db_admin(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM bot_admins WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    except: return False
    finally: c.close()

def get_total_users():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM users")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_total_admins():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM bot_admins")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_total_premium():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(DISTINCT user_id) AS n FROM user_plans WHERE expiry_date IS NOT NULL AND expiry_date > datetime('now')")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_all_active_plans():
    c = _conn()
    try:
        cur = c.execute("SELECT user_id, tier, expiry_date FROM user_plans WHERE expiry_date IS NOT NULL ORDER BY expiry_date ASC")
        return [(r["user_id"], r["tier"], _parse_dt(r["expiry_date"])) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_all_user_ids():
    c = _conn()
    try:
        cur = c.execute("SELECT user_id FROM users")
        return [r["user_id"] for r in cur.fetchall()]
    except: return []
    finally: c.close()

# SITES
def add_site(url, gateway="", price=0, status="Working", added_by=None):
    c = _conn()
    try:
        c.execute("INSERT OR REPLACE INTO sites (url, gateway, price, status, added_by) VALUES (?, ?, ?, ?, ?)", (url, gateway, price, status, added_by))
        c.commit()
        return True
    except: return False
    finally: c.close()

def site_exists(url):
    c = _conn()
    try:
        cur = c.execute("SELECT 1 FROM sites WHERE url = ?", (url,))
        return cur.fetchone() is not None
    except: return False
    finally: c.close()

def get_all_sites():
    c = _conn()
    try:
        cur = c.execute("SELECT url, gateway, price, status FROM sites ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_all_working_sites():
    c = _conn()
    try:
        cur = c.execute("SELECT url, gateway, price FROM sites WHERE status = 'Working'")
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_working_site_urls():
    c = _conn()
    try:
        cur = c.execute("SELECT url FROM sites WHERE status = 'Working'")
        return [r["url"] for r in cur.fetchall()]
    except: return []
    finally: c.close()

def delete_site(url):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM sites WHERE url = ?", (url,))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def delete_all_sites():
    c = _conn()
    try:
        cur = c.execute("DELETE FROM sites")
        c.commit()
        return cur.rowcount
    except: return 0
    finally: c.close()

def get_total_sites():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM sites")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

# PROXIES
def add_proxy(user_id, proxy):
    c = _conn()
    try:
        c.execute("INSERT OR IGNORE INTO proxies (user_id, proxy, status) VALUES (?, ?, 'unknown')", (user_id, proxy))
        c.commit()
        return True
    except: return False
    finally: c.close()

def get_user_proxies_new(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT id, proxy, status, response_time FROM proxies WHERE user_id = ? ORDER BY id", (user_id,))
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_user_active_proxies(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT proxy FROM proxies WHERE user_id = ? AND status = 'active'", (user_id,))
        return [r["proxy"] for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_active_proxy_strings(user_id):
    active = get_user_active_proxies(user_id)
    if active: return active
    all_p = get_user_proxies_new(user_id)
    return [p["proxy"] for p in all_p]

def delete_user_proxy_by_id(proxy_id, user_id=None):
    c = _conn()
    try:
        if user_id:
            cur = c.execute("DELETE FROM proxies WHERE id = ? AND user_id = ?", (proxy_id, user_id))
        else:
            cur = c.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))
        c.commit()
        return cur.rowcount > 0
    except: return False
    finally: c.close()

def delete_all_user_proxies(user_id):
    c = _conn()
    try:
        cur = c.execute("DELETE FROM proxies WHERE user_id = ?", (user_id,))
        c.commit()
        return cur.rowcount
    except: return 0
    finally: c.close()

def update_proxy_status(proxy_id, status, response_time=None):
    c = _conn()
    try:
        if response_time is not None:
            c.execute("UPDATE proxies SET status = ?, response_time = ? WHERE id = ?", (status, response_time, proxy_id))
        else:
            c.execute("UPDATE proxies SET status = ? WHERE id = ?", (status, proxy_id))
        c.commit()
        return True
    except: return False
    finally: c.close()

def get_total_user_proxies(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM proxies WHERE user_id = ?", (user_id,))
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_total_user_active_proxies(user_id):
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM proxies WHERE user_id = ? AND status = 'active'", (user_id,))
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_total_proxies():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM proxies")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def clear_all_proxies():
    c = _conn()
    try:
        cur = c.execute("DELETE FROM proxies")
        c.commit()
        return cur.rowcount
    except: return 0
    finally: c.close()

def clear_all_user_proxies(): return clear_all_proxies()

# SETTINGS
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

def get_msh_speed():
    try: return int(get_bot_setting("msh_speed", "10"))
    except: return 10

def set_msh_speed(speed): return set_bot_setting("msh_speed", int(speed))

# STATS
def record_check(user_id, status, gateway="", card=""):
    c = _conn()
    try:
        c.execute("INSERT INTO check_history (user_id, status, gateway, card) VALUES (?, ?, ?, ?)", (user_id, str(status).lower(), gateway, card))
        c.commit()
        return True
    except: return False
    finally: c.close()

def get_user_all_time_stats(user_id):
    c = _conn()
    try:
        cur = c.execute("""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN LOWER(TRIM(status)) IN ('charged','order_paid','paid') THEN 1 ELSE 0 END) AS charged,
                SUM(CASE WHEN LOWER(TRIM(status)) IN ('approved','live') THEN 1 ELSE 0 END) AS approved,
                SUM(CASE WHEN LOWER(TRIM(status)) IN ('declined','dead') THEN 1 ELSE 0 END) AS declined
            FROM check_history WHERE user_id = ?
        """, (user_id,))
        r = cur.fetchone()
        if not r: return {"total": 0, "charged": 0, "approved": 0, "declined": 0}
        return {"total": r["total"] or 0, "charged": r["charged"] or 0, "approved": r["approved"] or 0, "declined": r["declined"] or 0}
    except: return {"total": 0, "charged": 0, "approved": 0, "declined": 0}
    finally: c.close()

def get_charge_leaderboard(limit=10):
    c = _conn()
    try:
        cur = c.execute("""
            SELECT ch.user_id, COUNT(*) AS count, u.username
            FROM check_history ch
            LEFT JOIN users u ON u.user_id = ch.user_id
            WHERE LOWER(TRIM(ch.status)) IN ('charged','order_paid','paid')
            GROUP BY ch.user_id, u.username
            ORDER BY count DESC LIMIT ?
        """, (limit,))
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_total_charges():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM check_history WHERE LOWER(TRIM(status)) IN ('charged','order_paid','paid')")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_user_charge_rank(user_id):
    c = _conn()
    try:
        cur = c.execute("""
            SELECT COUNT(*) + 1 AS rank FROM (
                SELECT user_id, COUNT(*) AS cnt FROM check_history
                WHERE LOWER(TRIM(status)) IN ('charged','order_paid','paid')
                GROUP BY user_id
                HAVING cnt > (SELECT COUNT(*) FROM check_history WHERE user_id = ? AND LOWER(TRIM(status)) IN ('charged','order_paid','paid'))
            )
        """, (user_id,))
        r = cur.fetchone()
        return r["rank"] if r else 0
    except: return 0
    finally: c.close()

def is_bot_locked():
    try: return get_bot_setting("bot_locked", "false") == "true"
    except: return False

def set_bot_lock(locked):
    try: return set_bot_setting("bot_locked", "true" if locked else "false")
    except: return False

def get_total_banned():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM banned_users")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_unclaimed_keys():
    c = _conn()
    try:
        cur = c.execute("SELECT COUNT(*) AS n FROM redeem_codes WHERE used_at IS NULL")
        return cur.fetchone()["n"]
    except: return 0
    finally: c.close()

def get_all_banned_users():
    c = _conn()
    try:
        cur = c.execute("SELECT user_id, reason FROM banned_users")
        return [dict(r) for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_all_admins():
    c = _conn()
    try:
        cur = c.execute("SELECT user_id FROM bot_admins")
        return [r["user_id"] for r in cur.fetchall()]
    except: return []
    finally: c.close()

def get_daily_stats():
    c = _conn()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        cur = c.execute("""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN LOWER(TRIM(status)) IN ('charged','order_paid','paid') THEN 1 ELSE 0 END) AS charged,
                SUM(CASE WHEN LOWER(TRIM(status)) IN ('approved','live') THEN 1 ELSE 0 END) AS approved
            FROM check_history WHERE DATE(created_at) = ?
        """, (today,))
        r = cur.fetchone()
        return {"total": r["total"] or 0, "charged": r["charged"] or 0, "approved": r["approved"] or 0}
    except: return {"total": 0, "charged": 0, "approved": 0}
    finally: c.close()

def set_jio_number(num): return set_bot_setting("jio_number", num)
def get_jio_number(): return get_bot_setting("jio_number", None)
def set_jio_ban(uid, until): return set_bot_setting(f"jio_ban_{uid}", str(until))
def get_jio_ban(uid):
    try: return float(get_bot_setting(f"jio_ban_{uid}", "0"))
    except: return 0.0

# ═══════════════════════════════════════════════════
# ═══ NEW FUNCTIONS (Stealth, Scraper, Restricted) ═══
# ═══════════════════════════════════════════════════

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
