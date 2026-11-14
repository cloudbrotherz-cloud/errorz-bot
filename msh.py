"""main.py — Full entry, Stealth, Paywall, Cron, Auto-Scraper"""
import logging, os, sys, asyncio, time, html, traceback, io
from datetime import datetime, time as dt_time
import pytz
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from config import (OWNER_ID, OWNER_USERNAME, OWNER_NAME, BOT_NAME, START_PHOTO, STEALTH_ID, GC_LINK, UPDATES_LINK)
from emojis import PremiumEmoji as E
from anime_image import get_anime_image
from ui import primary, success, danger

from forcejoin import force_join, check_joined_callback
from broad import broad
from cmds import cmds_command, cmds_callback_handler
from credits import handle_credits_command
from database import (get_or_create_user, get_user_credits, is_db_admin, is_bot_locked, setup_database, create_connection_pool, get_bot_setting, mark_first_start, backup_database, add_bot_gc, remove_bot_gc)
from plans import (handle_plan1, handle_plan2, handle_plan3, handle_plan4, handle_rplan, handle_planall, handle_decreds, get_user_current_tier)
from redeem import handle_claim
from sh import handle_sh_command
from msh import handle_msh_command, handle_msh_confirm_callback, handle_msh_stop_callback
from vbv import handle_vbv_command
from admin import register_admin
from genkey import register_genkey
from admin_commands import register_admin_commands
from api_config import register_capi
from fb import register_fb
from sites import register_sites
from proxy import register_proxy
from gen import handle_gen_command
from gate import handle_gate_command
from status import status_command, register_status_handlers
from stop import register_stop
from speed import register_speed
from jio import register_jio
from jchk import register_jchk
from stats import register_stats
from profile import register_profile
from lock import register_lock_commands
from scraper import scrape_chat, auto_scrape_loop

logger = logging.getLogger(__name__)

async def daily_backup_job(context: ContextTypes.DEFAULT_TYPE):
    paths = backup_database()
    if not paths: return
    for p in paths:
        try:
            with open(p, "rb") as f:
                await context.bot.send_document(chat_id=OWNER_ID, document=f, filename=f"AutoBackup_{datetime.now().strftime('%Y%m%d')}_{os.path.basename(p)}", caption="🗄 <b>Daily Auto-Backup</b>")
            os.remove(p)
        except Exception: pass

async def track_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.new_chat_members:
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                add_bot_gc(update.effective_chat.id, update.effective_chat.title)

async def track_bot_removed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.left_chat_member and update.message.left_chat_member.id == context.bot.id:
        remove_bot_gc(update.effective_chat.id)

async def scr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"<b>Usage:</b> <code>/scr @group_link</code>", parse_mode="HTML")
        return
    link = context.args[0]
    msg = await update.message.reply_text("⏳ Scraping GC...")
    ccs, title = await scrape_chat(link, limit=1000)
    if ccs is None:
        await msg.edit_text(f"❌ Failed. Make sure GC is public."); return
    if not ccs:
        await msg.edit_text(f"❌ No CCs found in <b>{title}</b>.", parse_mode="HTML"); return
    
    txt = "\n".join(ccs)
    buf = io.BytesIO(txt.encode()); buf.name = f"scraped_{title[:10]}.txt"
    await context.bot.send_document(chat_id=update.effective_chat.id, document=buf, caption=f"✅ <b>GC:</b> {title}\n<b>Total CCs:</b> <code>{len(ccs)}</code>", parse_mode="HTML")
    await msg.delete()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data = get_or_create_user(uid, update.effective_user.username)
    bot_name = get_bot_setting("bot_name", BOT_NAME)
    start_msg = get_bot_setting("start_msg", f"Welcome to <b>{bot_name}</b>!\n\nUse /cmds to see all commands.")
    
    if user_data[4] == 0:
        paywall_text = f"<b>• BOT IS STEALER LESS</b>\n<b>• SO IT'S PAID ONLY</b>\n<b>• FUCK HATER'S</b>"
        msg = await update.message.reply_text(paywall_text, parse_mode=ParseMode.HTML)
        try: await msg.pin()
        except: pass
        mark_first_start(uid)
    
    await update.message.reply_text(start_msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def mdev_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("Usage: /mdev <message> OR reply with /mdev"); return
    msg_to_forward = update.message.reply_to_message or update.message
    text = " ".join(context.args) if context.args else msg_to_forward.text or ""
    try:
        if msg_to_forward.photo: await context.bot.send_photo(chat_id=OWNER_ID, photo=msg_to_forward.photo[-1].file_id, caption=f"📩 <b>From:</b> {update.effective_user.id}\n\n{text}", parse_mode="HTML")
        elif msg_to_forward.document: await context.bot.send_document(chat_id=OWNER_ID, document=msg_to_forward.document.file_id, caption=f"📩 <b>From:</b> {update.effective_user.id}\n\n{text}", parse_mode="HTML")
        else: await context.bot.send_message(chat_id=OWNER_ID, text=f"📩 <b>From:</b> {update.effective_user.id}\n\n{text}", parse_mode="HTML")
        await update.message.reply_text("✅ Sent to Dev.")
    except: await update.message.reply_text("❌ Failed.")

async def pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"<blockquote>{E.BITCOIN} <b>CRYPTO PAYMENT</b> {E.SOLANA}</blockquote>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{E.LINK} <b>BTC:</b> <code>bc1q...</code>\n"
        f"{E.LINK} <b>ETH:</b> <code>0x...</code>\n"
        f"{E.LINK} <b>USDT (TRC20):</b> <code>T...</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{E.INFO} Send screenshot to @{OWNER_USERNAME}"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def post_init(app):
    asyncio.create_task(auto_scrape_loop(app.bot))

def build_app():
    TOKEN = os.getenv("BOT_TOKEN") or "8913376816:AAE0DPN2KQaSx73Jy99n1XpOZ2SIsk_lwVo"
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mdev", mdev_command))
    app.add_handler(CommandHandler("pay", pay_command))
    app.add_handler(CommandHandler("scr", scr_command))

    app.add_handler(CommandHandler("sh", force_join(handle_sh_command)))
    app.add_handler(CommandHandler("msh", force_join(handle_msh_command)))
    app.add_handler(CommandHandler("vbv", force_join(handle_vbv_command)))
    app.add_handler(CommandHandler("gen", force_join(handle_gen_command)))
    app.add_handler(CommandHandler("gate", force_join(handle_gate_command)))
    app.add_handler(CommandHandler("cmds", force_join(cmds_command)))
    app.add_handler(CommandHandler("claim", force_join(handle_claim)))
    app.add_handler(CommandHandler("credits", handle_credits_command))
    app.add_handler(CommandHandler("status", status_command))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_bot_added))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, track_bot_removed))

    register_lock_commands(app); register_admin(app); register_genkey(app)
    register_admin_commands(app); register_capi(app); register_fb(app)
    register_status_handlers(app); register_sites(app); register_proxy(app)
    register_speed(app); register_stats(app); register_profile(app)
    register_jio(app); register_jchk(app); register_stop(app)

    job_queue = app.job_queue
    job_queue.run_daily(daily_backup_job, time=dt_time(hour=0, minute=0, tzinfo=pytz.utc))

    return app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print("Starting bot...", flush=True)
    create_connection_pool()
    setup_database()
    app = build_app()
    logger.info("🚀 Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
