#!/usr/bin/env python3
import os
import sys
import time
import argparse
import logging

MISSING_MODULES = False
try:
    import requests
    import shutil
    from dotenv import load_dotenv
    from atproto import Client, models
    from atproto.exceptions import AtProtocolError, NetworkError, RequestException, InvokeTimeoutError
    from atproto_client.models.app.bsky.notification.list_notifications import (
        Params as ListNotificationsParams,
    )
    from atproto_client.models.app.bsky.feed.get_post_thread import (
        Params as GetPostThreadParams,
    )
    from colorama import Fore, Style, init as colorama_init
except ImportError as e:
    MISSING_MODULES = True
    missing_module_name = "a required Python package"
    if hasattr(e, 'name') and e.name:
        missing_module_name = f"the '{e.name}' package"

    print("------------------------------------------------------------")
    print("               zAi Bluesky Bot - Dependency Error           ")
    print("------------------------------------------------------------")
    print(f"\nError: {missing_module_name} is not installed.")
    print("This bot relies on several external Python libraries to function.")
    print("\nPlease install the required dependencies by running:")
    print("  pip install -r requirements.txt")
    print("\nIf you don't have pip, please install Python's package manager.")
    print("Make sure 'requirements.txt' is in the same directory as the bot.")
    print(f"\nDetails: {e}")
    sys.exit(1)

if not MISSING_MODULES:
    colorama_init(autoreset=True)

PROCESSED_URIS_FILE = "processed_uris.txt"
SYSTEM_PROMPT_FILE = "system_prompt.md"
MODELS_FILE = "models.txt"
IGNORED_USERS_FILE = "ignored_users.txt"
BOT_NAME = "zAi Bluesky Bot"
BOT_VERSION = "1.3.1"

class Colors:
    if not MISSING_MODULES:
        HEADER = Fore.MAGENTA + Style.BRIGHT
        OKGREEN = Fore.GREEN + Style.BRIGHT
        INFO = Fore.CYAN + Style.BRIGHT
        WARNING = Fore.YELLOW + Style.BRIGHT
        FAIL = Fore.RED + Style.BRIGHT
        RESET = Style.RESET_ALL
        DIM = Fore.LIGHTBLACK_EX
        BLUE = Fore.BLUE + Style.BRIGHT
    else:
        HEADER = ""
        OKGREEN = ""
        INFO = ""
        WARNING = ""
        FAIL = ""
        RESET = ""
        DIM = ""
        BLUE = ""

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_terminal_width():
    try:
        if MISSING_MODULES:
            return 80
        return shutil.get_terminal_size().columns
    except OSError:
        return 80

def strip_ansi_codes(text):
    if MISSING_MODULES or not text:
        return text
    import re
    ansi_escape_pattern = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\x9B[0-?]*[ -/]*[@-~]')
    return ansi_escape_pattern.sub('', text)

def print_centered(text_to_center, prefix_text="", suffix_text="",
                   text_color="", prefix_color="", suffix_color="",
                   add_newline=True):
    width = get_terminal_width()
    visible_prefix_len = len(strip_ansi_codes(prefix_text))
    visible_text_len = len(strip_ansi_codes(text_to_center))
    visible_suffix_len = len(strip_ansi_codes(suffix_text))
    total_visible_content_len = visible_prefix_len + visible_text_len + visible_suffix_len
    padding_len = (width - total_visible_content_len) // 2
    padding = " " * max(0, padding_len)
    
    final_prefix = f"{prefix_color}{prefix_text}{Colors.RESET if prefix_color and not MISSING_MODULES else ''}"
    final_text = f"{text_color}{text_to_center}{Colors.RESET if text_color and not MISSING_MODULES else ''}"
    final_suffix = f"{suffix_color}{suffix_text}{Colors.RESET if suffix_color and not MISSING_MODULES else ''}"
    
    full_line = f"{padding}{final_prefix}{final_text}{final_suffix}"
    
    if add_newline:
        print(full_line)
    else:
        print(full_line, end='')

startup_tasks_config = [
    ("Initializing core components", 0.05),
    ("Loading environment variables", 0.05),
    ("Loading system prompt", 0.05),
    ("Loading AI model list", 0.05),
    ("Loading ignored users list", 0.05),
    ("Connecting to Bluesky & Logging in", 0.4),
    ("Loading processed notification cache", 0.2),
    ("Bot is ready and monitoring!", 0.1)
]
GPT_MODEL_IN_USE_DISPLAY = "" 
AI_MODEL_LIST = []
IGNORED_DIDS = set()

def display_startup_screen(current_step_idx, error_msg=None, success_msg=None):
    clear_console()
    print_centered(f"---- {BOT_NAME} v{BOT_VERSION} ----", text_color=Colors.HEADER)
    
    model_display_text = GPT_MODEL_IN_USE_DISPLAY
    if not model_display_text: 
        if AI_MODEL_LIST:
             model_display_text = f"AI Models: {Colors.BLUE}{AI_MODEL_LIST[0].split('/')[-1]}{Colors.RESET}..." if len(AI_MODEL_LIST) > 0 else "AI Model: (Not loaded)"
        else:
            model_display_text = "AI Model: (Configuring...)"

    print_centered(model_display_text, text_color=Colors.DIM, add_newline=True)
    print() 

    total_weight = sum(w for _, w in startup_tasks_config)
    current_progress_weight = sum(startup_tasks_config[i][1] for i in range(current_step_idx))
    if current_step_idx < len(startup_tasks_config):
        current_progress_weight += (startup_tasks_config[current_step_idx][1] / 2)
    
    for i, (task_name, _) in enumerate(startup_tasks_config):
        status_icon_text = "  "
        icon_color = Colors.DIM
        task_display_color = Colors.DIM
        if i < current_step_idx:
            status_icon_text = "✔ "
            icon_color = Colors.OKGREEN
            task_display_color = Colors.RESET 
        elif i == current_step_idx:
            status_icon_text = "➜ "
            icon_color = Colors.INFO
            task_display_color = Colors.INFO + (Style.BRIGHT if not MISSING_MODULES else "")
        print_centered(task_name, prefix_text=status_icon_text, text_color=task_display_color, prefix_color=icon_color, add_newline=True)
    
    print() 
    bar_width_chars = get_terminal_width() // 2
    bar_width_chars = max(10, bar_width_chars)
    progress_percentage = (current_progress_weight / total_weight) if total_weight > 0 else 0
    filled_length = int(bar_width_chars * progress_percentage)
    bar_str = '▓' * filled_length + '░' * (bar_width_chars - filled_length)
    progress_bar_full_text = f"[{bar_str}] {int(progress_percentage * 100)}%"
    print_centered(progress_bar_full_text, text_color=Colors.OKGREEN, add_newline=True)
    
    if error_msg:
        print()
        print_centered(f"ERROR: {error_msg}", text_color=Colors.FAIL)
        print_centered("Bot cannot start...", text_color=Colors.FAIL)
    elif success_msg:
        print()
        print_centered(success_msg, text_color=Colors.OKGREEN)
    
    print()
    print_centered(f"----------------------------------------------", text_color=Colors.DIM, add_newline=True)

def print_status_line(message, color_prefix="", clear_previous=True):
    width = get_terminal_width()
    if clear_previous:
        sys.stdout.write('\r' + ' ' * width + '\r') 
    print_centered(message, text_color=color_prefix, add_newline=False)
    sys.stdout.flush()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s")
if not MISSING_MODULES:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("atproto_core").setLevel(logging.WARNING)
    logging.getLogger("atproto_client").setLevel(logging.WARNING)

def load_processed_uris():
    if not os.path.exists(PROCESSED_URIS_FILE):
        logging.info(f"'{PROCESSED_URIS_FILE}' not found.")
        return set()
    with open(PROCESSED_URIS_FILE, "r") as f:
        processed = set(line.strip() for line in f if line.strip())
        logging.info(f"Loaded {len(processed)} URIs.")
        return processed

def append_processed_uri(uri):
    with open(PROCESSED_URIS_FILE, "a") as f:
        f.write(f"{uri}\n")

def load_ignored_dids(step_idx_ref):
    display_startup_screen(step_idx_ref[0])
    time.sleep(0.1)
    global IGNORED_DIDS
    if not os.path.exists(IGNORED_USERS_FILE):
        logging.info(f"'{IGNORED_USERS_FILE}' not found. No users will be ignored by default.")
        IGNORED_DIDS = set()
        return True
    try:
        with open(IGNORED_USERS_FILE, "r", encoding="utf-8") as f:
            dids = {line.strip() for line in f if line.strip() and not line.startswith("#")}
        IGNORED_DIDS = dids
        logging.info(f"Loaded {len(IGNORED_DIDS)} DIDs from '{IGNORED_USERS_FILE}'.")
        return True
    except Exception as e:
        msg = f"Error loading ignored users from {IGNORED_USERS_FILE}: {e}"
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg, exc_info=True)
        return False

if not MISSING_MODULES:
    load_dotenv() 

BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MENTION_CHECK_INTERVAL_SECONDS = int(os.getenv("MENTION_CHECK_INTERVAL_SECONDS", 30))
NOTIFICATION_FETCH_LIMIT = int(os.getenv("NOTIFICATION_FETCH_LIMIT", 30))
SYSTEM_PROMPT_TEMPLATE = ""

def load_ai_models_from_file(step_idx_ref, cli_model_override=None):
    display_startup_screen(step_idx_ref[0])
    time.sleep(0.2)
    global AI_MODEL_LIST, GPT_MODEL_IN_USE_DISPLAY
    
    if cli_model_override:
        AI_MODEL_LIST = [cli_model_override]
        GPT_MODEL_IN_USE_DISPLAY = f"AI Model: {Colors.BLUE}{cli_model_override.split('/')[-1]}{Colors.RESET} (CLI)"
        logging.info(f"Using CLI model: {cli_model_override}")
        return True
    try:
        with open(MODELS_FILE, "r", encoding="utf-8") as f:
            models_in_file = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        if not models_in_file:
            msg = f"{MODELS_FILE} empty."
            display_startup_screen(step_idx_ref[0], error_msg=msg)
            logging.error(msg)
            return False
        AI_MODEL_LIST = models_in_file
        if AI_MODEL_LIST:
            dn = AI_MODEL_LIST[0].split('/')[-1]
            GPT_MODEL_IN_USE_DISPLAY = f"AI Models: {Colors.BLUE}{dn}{Colors.RESET}{f' (+{len(AI_MODEL_LIST)-1})' if len(AI_MODEL_LIST) > 1 else ''}"
        logging.info(f"AI models loaded: {AI_MODEL_LIST}")
        return True
    except FileNotFoundError:
        msg = f"'{MODELS_FILE}' not found & no CLI model."
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg)
        return False
    except Exception as e:
        msg = f"Error loading models: {e}"
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg, exc_info=True)
        return False

def load_system_prompt(step_idx_ref):
    display_startup_screen(step_idx_ref[0])
    time.sleep(0.2)
    global SYSTEM_PROMPT_TEMPLATE
    try:
        with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
            SYSTEM_PROMPT_TEMPLATE = f.read()
        if not SYSTEM_PROMPT_TEMPLATE:
            msg = f"{SYSTEM_PROMPT_FILE} is empty."
            display_startup_screen(step_idx_ref[0], error_msg=msg)
            logging.error(msg)
            return False
        logging.info("System prompt loaded.")
        return True
    except FileNotFoundError:
        msg = f"Critical: {SYSTEM_PROMPT_FILE} not found."
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg)
        return False
    except Exception as e:
        msg = f"Error loading system prompt: {e}"
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg, exc_info=True)
        return False

def initialize_bluesky_client(step_idx_ref):
    display_startup_screen(step_idx_ref[0])
    time.sleep(0.5) 
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        msg = "Bluesky credentials missing."
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg)
        return None
    if not OPENROUTER_KEY:
        logging.warning("OPENROUTER_KEY missing.")
    try:
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
        return client
    except Exception as e:
        msg = f"Bluesky login failed: {e}"
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg, exc_info=True)
        return None

def get_post_text(post):
    return post.record.text if hasattr(post, "record") and hasattr(post.record, "text") else ""

def fetch_thread_context(client, uri, original_mentioner_handle=""):
    logging.info(f"Fetching thread context for {uri} (Mention: @{original_mentioner_handle})")
    try:
        params = GetPostThreadParams(uri=uri)
        thread_response = client.app.bsky.feed.get_post_thread(params=params)
        thread_posts = []
        def traverse_thread(node):
            if not node: return
            if hasattr(node, "parent") and node.parent:
                traverse_thread(node.parent)
            if hasattr(node, "post") and node.post:
                author_handle = node.post.author.handle if hasattr(node.post, "author") and hasattr(node.post.author, "handle") else "unknown"
                text = get_post_text(node.post)
                thread_posts.append(f"@{author_handle}: {text}")
        if hasattr(thread_response, "thread"):
            traverse_thread(thread_response.thread)
        else:
            logging.warning(f"No 'thread' in response for {uri}.")
            print_centered(f"Could not fetch thread for @{original_mentioner_handle}", text_color=Colors.WARNING)
            return "", ""
        most_recent_post_text = thread_posts[-1] if thread_posts else ""
        thread_history_text = "\n".join(thread_posts[:-1])
        if most_recent_post_text:
            print_centered(f"Context fetched for @{original_mentioner_handle}", text_color=Colors.DIM)
        else:
            logging.warning(f"Empty thread for {uri}.")
            print_centered(f"Empty thread for @{original_mentioner_handle}", text_color=Colors.WARNING)
        return thread_history_text, most_recent_post_text
    except Exception as e:
        logging.error(f"Error fetching thread ({uri}): {e}", exc_info=True)
        print_centered(f"Error fetching thread for @{original_mentioner_handle}", text_color=Colors.FAIL)
        return "", ""

def get_openrouter_reply(thread_history, most_recent_post_to_reply_to):
    global AI_MODEL_LIST
    if not SYSTEM_PROMPT_TEMPLATE:
        logging.error("System prompt not loaded.")
        return ""
    if not AI_MODEL_LIST:
        logging.error("AI model list is empty.")
        return ""

    final_system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{{BLUESKY_HANDLE}}", BLUESKY_HANDLE or "bot_handle")
    mrp_parts = most_recent_post_to_reply_to.split(':', 1)
    mrp_author = mrp_parts[0] if len(mrp_parts) > 1 else "@unknown_user"
    mrp_text = mrp_parts[1].strip() if len(mrp_parts) > 1 else most_recent_post_to_reply_to
    user_content = f"<thread_history>\n{thread_history}\n</thread_history>\n<most_recent_post>\n{mrp_author}: {mrp_text}\n</most_recent_post>\nReply now."
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    
    for model_to_try in AI_MODEL_LIST:
        current_model_display_name = model_to_try.split('/')[-1]
        print_status_line(f"Attempting reply with {current_model_display_name}...", Colors.INFO)
        payload = {"model": model_to_try, "messages": [{"role": "system", "content": final_system_prompt}, {"role": "user", "content": user_content}]}
        
        try:
            resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            response_json = resp.json()
            if "choices" in response_json and response_json["choices"] and \
               "message" in response_json["choices"][0] and "content" in response_json["choices"][0]["message"]:
                reply_content = response_json["choices"][0]["message"]["content"].strip()
                print_status_line(f"Reply from {current_model_display_name}: \"{reply_content.replace(chr(10),' ')[:30]}...\"", Colors.OKGREEN)
                print() 
                return reply_content
            else:
                logging.warning(f"Unexpected response from {model_to_try}: {response_json}")
                print_status_line(f"Format err: {current_model_display_name}.", Colors.WARNING, clear_previous=False)
                print()
        except requests.exceptions.Timeout:
            logging.warning(f"Timeout: {model_to_try}.")
            print_status_line(f"Timeout: {current_model_display_name}. Next...", Colors.WARNING, clear_previous=False)
            print()
        except requests.exceptions.HTTPError as http_err:
            logging.warning(f"HTTP {http_err.response.status_code} with {model_to_try}: {http_err.response.text}")
            print_status_line(f"HTTP {http_err.response.status_code}: {current_model_display_name}. Next...", Colors.WARNING, clear_previous=False)
            print()
        except Exception as e:
            logging.error(f"Error with {model_to_try}: {e}", exc_info=True)
            print_status_line(f"Error: {current_model_display_name}. Next...", Colors.WARNING, clear_previous=False)
            print()
        
        time.sleep(1) 

    print_status_line("All AI models failed.", Colors.FAIL)
    print()
    return ""

def main():
    global AI_MODEL_LIST, GPT_MODEL_IN_USE_DISPLAY, IGNORED_DIDS

    parser = argparse.ArgumentParser(description=f"{BOT_NAME} - A Bluesky Bot")
    parser.add_argument("-m", "--model", type=str, default=None, help="Specify a single OpenRouter AI model to use, overriding models.txt")
    args = parser.parse_args()
    
    cli_model_override = args.model
    if cli_model_override:
        GPT_MODEL_IN_USE_DISPLAY = f"AI Model: {Colors.BLUE}{cli_model_override.split('/')[-1]}{Colors.RESET} (CLI override)"

    current_startup_step = [0] 
    def next_step():
        current_startup_step[0] += 1
    
    display_startup_screen(current_startup_step[0])
    time.sleep(0.1)
    next_step()
    
    display_startup_screen(current_startup_step[0])
    time.sleep(0.1)
    next_step()
    
    if not load_system_prompt(current_startup_step):
        sys.exit(1)
    next_step()
    
    if not load_ai_models_from_file(current_startup_step, cli_model_override):
        sys.exit(1)
    next_step()
    
    if not load_ignored_dids(current_startup_step):
        sys.exit(1)
    next_step()
    
    client = initialize_bluesky_client(current_startup_step)
    if not client:
        sys.exit(1)
    next_step()
    
    display_startup_screen(current_startup_step[0])
    time.sleep(0.2)
    processed_uris = load_processed_uris()
    next_step()
    
    display_startup_screen(current_startup_step[0], success_msg=f"@{BLUESKY_HANDLE} is online and monitoring!")
    time.sleep(2) 

    clear_console()
    print_centered(f"--- {BOT_NAME} v{BOT_VERSION} - Running ---", text_color=Colors.HEADER)
    final_model_display = GPT_MODEL_IN_USE_DISPLAY
    if not final_model_display and AI_MODEL_LIST:
        dn = AI_MODEL_LIST[0].split('/')[-1]
        final_model_display = f"AI Models: {Colors.BLUE}{dn}{Colors.RESET}{f' (+{len(AI_MODEL_LIST)-1})' if len(AI_MODEL_LIST) > 1 else ''}"
    elif not final_model_display:
        final_model_display = "AI Model: (Config Error)"
    print_centered(final_model_display, text_color=Colors.DIM)
    print_centered(f"Bot Handle: @{BLUESKY_HANDLE}", text_color=Colors.INFO)
    if IGNORED_DIDS:
        print_centered(f"Ignoring {len(IGNORED_DIDS)} DIDs.", text_color=Colors.DIM)
    print_centered("----------------------------------------------", text_color=Colors.DIM)
    print() 
    
    consecutive_idle_cycles = 0
    last_status_message = "" 

    try:
        while True:
            new_notifications_processed_this_cycle = 0
            notifications_fetched_count = 0
            all_skipped = False
            try:
                params = ListNotificationsParams(limit=NOTIFICATION_FETCH_LIMIT)
                notifications_response = client.app.bsky.notification.list_notifications(params=params)
                if not notifications_response or not hasattr(notifications_response, 'notifications'):
                    logging.warning("Malformed notification response.")
                    time.sleep(MENTION_CHECK_INTERVAL_SECONDS)
                    continue
                
                notifications = notifications_response.notifications
                notifications_fetched_count = len(notifications)
                skipped_this_round = 0
                
                for notif in notifications:
                    if not all(hasattr(notif, attr) for attr in ['uri','author','reason','cid','record']) or \
                       not hasattr(notif.author,'handle') or not hasattr(notif.author, 'did'):
                        logging.warning(f"Malformed notif: {str(notif)[:100]}...") 
                        if hasattr(notif,'uri'):
                            append_processed_uri(notif.uri)
                            processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    
                    author_did = notif.author.did
                    if author_did in IGNORED_DIDS:
                        logging.info(f"Skipping notification from ignored DID: {author_did} (@{notif.author.handle})")
                        print_centered(f"Skipped ignored user @{notif.author.handle}", text_color=Colors.DIM)
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue

                    if notif.uri in processed_uris:
                        skipped_this_round +=1
                        continue
                    if notif.author.handle == BLUESKY_HANDLE:
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    if notif.reason not in ["mention", "reply"]:
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    
                    if consecutive_idle_cycles > 0 or last_status_message:
                        print_status_line("",clear_previous=True)
                        print() 
                    last_status_message = ""
                    consecutive_idle_cycles = 0
                    
                    print_centered(f"New {notif.reason} from @{notif.author.handle}",text_color=Colors.INFO,prefix_text="", suffix_text="", prefix_color=Colors.BLUE)
                    
                    thread_history, most_recent_post = fetch_thread_context(client, notif.uri, notif.author.handle)
                    if not most_recent_post:
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        continue
                    
                    reply_text = get_openrouter_reply(thread_history, most_recent_post)
                    if not reply_text:
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        continue
                    
                    if len(reply_text) > 300:
                        reply_text = reply_text[:297] + "..."
                    
                    parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri)
                    root_ref = parent_ref
                    if hasattr(notif.record,"reply") and notif.record.reply and hasattr(notif.record.reply,"root") and \
                       notif.record.reply.root:
                        if hasattr(notif.record.reply.root,'cid') and hasattr(notif.record.reply.root,'uri'):
                            root_ref = notif.record.reply.root
                    
                    print_centered(f"Sending reply to @{notif.author.handle}...",text_color=Colors.INFO)
                    client.send_post(text=reply_text,reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref,parent=parent_ref))
                    append_processed_uri(notif.uri)
                    processed_uris.add(notif.uri)
                    new_notifications_processed_this_cycle += 1
                    log_reply_sent = reply_text.replace('\n',' ')[:40]
                    print_centered(f"Replied: \"{log_reply_sent}...\"",text_color=Colors.OKGREEN)
                    print() 
                
                if notifications_fetched_count > 0 and skipped_this_round == notifications_fetched_count:
                    all_skipped = True
            
            except InvokeTimeoutError as e:
                if consecutive_idle_cycles > 0 or last_status_message:
                    print_status_line("",clear_previous=True)
                    print()
                logging.warning(f"Bluesky API request timed out: {e}")
                print_centered(f"Bluesky API Timeout.",text_color=Colors.WARNING)
                last_status_message = ""
                consecutive_idle_cycles = 0
            except AtProtocolError as e: 
                if consecutive_idle_cycles > 0 or last_status_message:
                    print_status_line("",clear_previous=True)
                    print()
                logging.error(f"AT Protocol error: {e}", exc_info=True)
                print_centered(f"AT Protocol Error.",text_color=Colors.FAIL)
                last_status_message = ""
                consecutive_idle_cycles = 0
            except Exception as e: 
                if consecutive_idle_cycles > 0 or last_status_message:
                    print_status_line("",clear_previous=True)
                    print()
                logging.error(f"Loop error: {e}", exc_info=True)
                print_centered(f"Unexpected Error.",text_color=Colors.FAIL)
                last_status_message = ""
                consecutive_idle_cycles = 0
            
            if new_notifications_processed_this_cycle > 0:
                consecutive_idle_cycles = 0
                last_status_message = "" 
            elif all_skipped:
                consecutive_idle_cycles += 1
                status_msg = f"Fetched {notifications_fetched_count}, all skipped. Idle: {consecutive_idle_cycles}. Waiting..."
                print_status_line(status_msg, Colors.DIM)
                last_status_message = status_msg
            elif notifications_fetched_count == 0:
                consecutive_idle_cycles += 1
                status_msg = f"Quiet. No new notifications. Idle: {consecutive_idle_cycles}. Waiting..."
                print_status_line(status_msg, Colors.DIM)
                last_status_message = status_msg
            
            time.sleep(MENTION_CHECK_INTERVAL_SECONDS)
    
    except KeyboardInterrupt:
        if last_status_message:
            print_status_line("",clear_previous=True)
            print()
        print_centered("\n\nShutdown (Ctrl+C). Quitting...",text_color=Colors.WARNING)
    finally:
        clear_console()
        sys.exit(0)

if __name__ == "__main__":
    main()