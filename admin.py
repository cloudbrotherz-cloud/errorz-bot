"""admin.py — Full dynamic admin panel"""
import time, logging, asyncio, os, io, sys, html
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters

from config import OWNER_ID, OWNER_USERNAME, OWNER_NAME, BOT_NAME, HIT_DETECTION_GROUP_ID
from database import (
    is_db_admin, is_restricted_admin, add_restricted_admin, add_db_admin, get_total_users, get_total_premium, 
    get_total_banned, get_total_admins, get_total_sites, get_unclaimed_keys,
    backup_database, get_bot_setting, set_bot_setting, clear_charged_log, get_charged_logs
)
from emojis import PremiumEmoji as E
from ui import primary, success, danger

logger = logging.getLogger(__name__)

def get_admin_keyboard(uid):
    m_on = get_bot_setting("maintenance", "off") == "on"
    lb_vps = get_bot_setting("lb_vps", "off") == "on"
    lb_heroku = get_bot_setting("lb_heroku", "off") == "on"
    
    cc_set = get_bot_setting("cc_set", "Fuck hater's mother")
    gc_pvt = get_bot_setting("gc_pvt", "Not Set")
    dc_pvt = get_bot_setting("dc_pvt", "Not Set")
    gc_pub = get_bot_setting("gc_pub", "Not Set")
    pb_chan = get_bot_setting("pb_chan", "Not Set")
    hit_gc = get_bot_setting("hit_gc", str(HIT_DETECTION_GROUP_ID))
    bot_name = get_bot_setting("bot_name", BOT_NAME)
    
    hit_logs = get_bot_setting("hit_logs", "off") == "on"
    is_full = (uid == OWNER_ID or is_db_admin(uid))

    rows = [[primary(f"🟢 MAINTENANCE: {m_on.upper()}", "adm_set_maintenance")]]
    
    if is_full:
        rows.extend([
            [success(f"🔋 LB VPS: {lb_vps.upper()}", "adm_set_lbvps"), success(f"🔋 LB HEROKU: {lb_heroku.upper()}", "adm_set_lbheroku")],
            [primary(f"🤖 BOT NAME: {bot_name}", "adm_edit_botname")],
            [primary("📝 EDIT START MESSAGE", "adm_edit_startmsg")],
            [primary(f"📁 HIT DETECTION GC: {hit_gc}", "adm_set_hitgc")],
            [primary(f"✅ CC SET: {cc_set}", "adm_set_cc")],
            [primary(f"📁 PVT FILE GC: {gc_pvt}", "adm_set_gcpvt")],
            [primary(f"📁 PVT DC GC: {dc_pvt}", "adm_set_dcpvt")],
            [primary(f"📁 PUBLIC GC: {gc_pub}", "adm_set_gcpub")],
            [primary(f"📁 PB CHANNEL: {pb_chan}", "adm_set_pbchan")],
            [primary("📁 CHARGED HITS (.txt)", "adm_charged_txt"), danger("🗑️ CLEAR LOGS", "adm_clear_logs")],
            [success(f"✅ HIT LOGS: {hit_logs}", "adm_set_hitlogs")],
            [success("👤 ADD FULL ADMIN", "adm_addadmin"), success("👤 ADD LTD ADMIN", "adm_addltd")],
            [success("💾 BACKUP NOW", "adm_backup_now")],
        ])
        
    rows.extend([
        [success("⚙️ GENERATE CODE", "adm_gencode")],
        [success("📁 BROADCAST", "admin_broad_all")],
        [danger("❌ CLOSE", "close_menu")]
    ])
    return InlineKeyboardMarkup(rows)

async def admin(update, context):
    user = update.effective_user
    if user.id != OWNER_ID and not is_db_admin(user.id) and not is_restricted_admin(user.id):
        await update.message.reply_text(f"{E.ERROR} Admin only.", parse_mode="HTML")
        return

    text = (
        f"<blockquote>{E.SHIELD} <b>ADMIN PANEL</b> {E.SHIELD}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{E.USER} <b>Dev:</b> @{OWNER_USERNAME}\n"
        f"{E.STATS} <b>Users:</b> <code>{get_total_users()}</code>\n"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=get_admin_keyboard(user.id))

async def admin_callbacks(update, context):
    q = update.callback_query
    data = q.data
    uid = q.from_user.id
    try: await q.answer()
    except: pass

    is_full = (uid == OWNER_ID or is_db_admin(uid))
    is_ltd = is_restricted_admin(uid)

    if not (is_full or is_ltd): return

    if data == "adm_set_maintenance":
        curr = get_bot_setting("maintenance", "off")
        set_bot_setting("maintenance", "on" if curr == "off" else "off")
        await q.edit_message_reply_markup(reply_markup=get_admin_keyboard(uid)); return

    if not is_full: return

    if data == "adm_set_lbvps":
        set_bot_setting("lb_vps", "off" if get_bot_setting("lb_vps", "off") == "on" else "on")
        await q.edit_message_reply_markup(reply_markup=get_admin_keyboard(uid)); return

    if data == "adm_set_lbheroku":
        set_bot_setting("lb_heroku", "off" if get_bot_setting("lb_heroku", "off") == "on" else "on")
        await q.edit_message_reply_markup(reply_markup=get_admin_keyboard(uid)); return

    if data == "adm_set_hitlogs":
        set_bot_setting("hit_logs", "off" if get_bot_setting("hit_logs", "off") == "on" else "on")
        await q.edit_message_reply_markup(reply_markup=get_admin_keyboard(uid)); return

    edits = {
        "adm_edit_botname": ("bot_name", "Send new Bot Name:"),
        "adm_edit_startmsg": ("start_msg", "Send new /start Message (HTML allowed):"),
        "adm_set_hitgc": ("hit_gc", "Send new Hit Detection GC ID (e.g. -100...):"),
        "adm_set_cc": ("cc_set", "Send new text for Charged CC:"),
        "adm_set_gcpvt": ("gc_pvt", "Send new PVT File GC link:"),
        "adm_set_dcpvt": ("dc_pvt", "Send new PVT DC GC link:"),
        "adm_set_gcpub": ("gc_pub", "Send new Public GC link:"),
        "adm_set_pbchan": ("pb_chan", "Send new PB Channel link:"),
        "adm_addadmin": ("add_admin_input", "Send UID to make FULL admin:"),
        "adm_addltd": ("add_ltd_input", "Send UID to make LTD admin:"),
    }
    
    if data in edits:
        key, prompt = edits[data]
        context.user_data['admin_awaiting'] = key
        msg = await context.bot.send_message(chat_id=uid, text=f"<b>{prompt}</b>\n<i>Reply to this message.</i>", parse_mode="HTML", reply_markup=ForceReply(selective=True))
        context.user_data['admin_prompt_id'] = msg.message_id
        return

    if data == "adm_charged_txt":
        logs = get_charged_logs()
        if not logs: await context.bot.send_message(chat_id=uid, text="No hits."); return
        txt = "CHARGED HITS\n" + "-"*40 + "\n" + "\n".join(f"{l['card']} | {l['response']} | {l['gateway']}" for l in logs)
        buf = io.BytesIO(txt.encode()); buf.name = "charged_hits.txt"
        await context.bot.send_document(chat_id=uid, document=buf); return

    if data == "adm_clear_logs":
        clear_charged_log(); await context.bot.send_message(chat_id=uid, text="✅ Logs cleared."); return
        
    if data == "adm_backup_now":
        msg = await context.bot.send_message(chat_id=uid, text="⏳ Generating GZIP backup...")
        paths = backup_database()
        if not paths: await msg.edit_text("❌ Failed."); return
        for p in paths:
            with open(p, "rb") as f:
                await context.bot.send_document(chat_id=uid, document=f, filename=os.path.basename(p))
            os.remove(p)
        await msg.delete(); return

    if data == "admin_back":
        await q.edit_message_text("Admin panel refreshed.", reply_markup=get_admin_keyboard(uid)); return

async def handle_admin_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message or update.message.reply_to_message.message_id != context.user_data.get('admin_prompt_id'): return
    key = context.user_data.pop('admin_awaiting', None)
    if not key: return
    
    val = update.message.text.strip()
    if key == "add_admin_input":
        add_db_admin(int(val)); msg = f"✅ Added {val} as Full Admin."
    elif key == "add_ltd_input":
        add_restricted_admin(int(val)); msg = f"✅ Added {val} as Limited Admin."
    else:
        set_bot_setting(key, val); msg = f"✅ Updated {key}."
        
    context.user_data.pop('admin_prompt_id', None)
    await update.message.reply_text(msg, parse_mode="HTML")
    await context.bot.send_message(chat_id=update.effective_user.id, text="Admin Panel:", reply_markup=get_admin_keyboard(update.effective_user.id))

def register_admin(app: Application):
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(admin_callbacks, pattern=r"^adm_|^admin_"))
    app.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, handle_admin_edit))
