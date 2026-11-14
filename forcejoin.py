"""forcejoin.py — Force-join with direct pass for joined users + cache"""
from functools import wraps
import logging
import time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import (FORCE_JOIN_GROUP_ID, FORCE_JOIN_GROUP_LINK, FORCE_JOIN_CHANNEL_ID, FORCE_JOIN_CHANNEL_LINK, FORCE_JOIN_IMAGE)

logger = logging.getLogger("force_join")
_JOIN_CACHE = {}
_CACHE_TTL = 30

def _cache_get(uid):
    entry = _JOIN_CACHE.get(uid)
    if not entry: return None
    ok, exp = entry
    if time.time() > exp:
        _JOIN_CACHE.pop(uid, None); return None
    return ok

def _cache_set(uid, ok, ttl=_CACHE_TTL): _JOIN_CACHE[uid] = (ok, time.time() + ttl)
def _cache_invalidate(uid): _JOIN_CACHE.pop(uid, None)

async def _member(bot, chat_id, user_id):
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status
    except Exception as e: return None

async def is_user_joined(bot, user_id, use_cache=True):
    try:
        from database import is_user_banned
        if is_user_banned(user_id): return False
    except: pass

    if use_cache:
        cached = _cache_get(user_id)
        if cached is not None: return cached

    valid = ("member", "administrator", "creator")
    
    # ═══ SKIP IF EMPTY ═══
    if not FORCE_JOIN_GROUP_ID or not FORCE_JOIN_CHANNEL_ID:
        return True
        
    group_status = await _member(bot, FORCE_JOIN_GROUP_ID, user_id)
    if group_status not in valid:
        _cache_set(user_id, False, ttl=10); return False

    channel_status = await _member(bot, FORCE_JOIN_CHANNEL_ID, user_id)
    if channel_status not in valid:
        _cache_set(user_id, False, ttl=10); return False

    _cache_set(user_id, True, ttl=_CACHE_TTL); return True

# ... [Rest of the file stays identical to original: _send_join_prompt, force_join, check_joined_callback]
