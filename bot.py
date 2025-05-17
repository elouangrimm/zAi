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
    from atproto.exceptions import AtProtocolError
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
BOT_NAME = "zAi Bluesky Bot"
BOT_VERSION = "1.2.2"

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
        HEADER = OKGREEN = INFO = WARNING = FAIL = RESET = DIM = BLUE = ""


def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def get_terminal_width():
    try:
        if MISSING_MODULES: return 80
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
    ("Initializing core components", 0.1),
    ("Loading environment variables", 0.05),
    ("Loading system prompt", 0.05),
    ("Connecting to Bluesky & Logging in", 0.5),
    ("Loading processed notification cache", 0.2),
    ("Bot is ready and monitoring!", 0.1)
]

GPT_MODEL_IN_USE = ""


def display_startup_screen(current_step_idx, error_msg=None, success_msg=None):
    clear_console()
    print_centered(f"---- {BOT_NAME} v{BOT_VERSION} ----", text_color=Colors.HEADER)
    
    model_label = "Using AI Model: "
    model_value = GPT_MODEL_IN_USE
    print_centered(f"{model_label}{model_value}", text_color=Colors.DIM, add_newline=True)
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
        
        print_centered(task_name, prefix_text=status_icon_text, 
                       text_color=task_display_color, prefix_color=icon_color,
                       add_newline=True)

    print()

    bar_width_chars = get_terminal_width() // 2 
    if bar_width_chars < 10: bar_width_chars = 10
    
    progress_percentage = (current_progress_weight / total_weight) if total_weight > 0 else 0
    filled_length = int(bar_width_chars * progress_percentage)
    bar_str = '▓' * filled_length + '░' * (bar_width_chars - filled_length)
    
    progress_bar_full_text = f"[{bar_str}] {int(progress_percentage * 100)}%"
    print_centered(progress_bar_full_text, text_color=Colors.OKGREEN, add_newline=True)

    if error_msg:
        print()
        print_centered(f"ERROR: {error_msg}", text_color=Colors.FAIL)
        print_centered("Bot cannot start. Please check logs or fix the issue.", text_color=Colors.FAIL)
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
if not MISSING_MODULES:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("atproto_core").setLevel(logging.WARNING)
    logging.getLogger("atproto_client").setLevel(logging.WARNING)


def load_processed_uris():
    if not os.path.exists(PROCESSED_URIS_FILE):
        logging.info(f"'{PROCESSED_URIS_FILE}' not found. Starting with an empty set.")
        return set()
    with open(PROCESSED_URIS_FILE, "r") as f:
        processed = set(line.strip() for line in f if line.strip())
        logging.info(f"Loaded {len(processed)} processed URIs from '{PROCESSED_URIS_FILE}'.")
        return processed

def append_processed_uri(uri):
    with open(PROCESSED_URIS_FILE, "a") as f:
        f.write(f"{uri}\n")

if not MISSING_MODULES:
    load_dotenv() 

BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
DEFAULT_GPT_MODEL = os.getenv("GPT_MODEL", "meta-llama/llama-4-maverick:free")

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MENTION_CHECK_INTERVAL_SECONDS = int(os.getenv("MENTION_CHECK_INTERVAL_SECONDS", 30))
NOTIFICATION_FETCH_LIMIT = int(os.getenv("NOTIFICATION_FETCH_LIMIT", 30))

SYSTEM_PROMPT_TEMPLATE = ""

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
        logging.info(f"System prompt loaded successfully from {SYSTEM_PROMPT_FILE}")
        return True
    except FileNotFoundError:
        msg = f"Critical: System prompt file '{SYSTEM_PROMPT_FILE}' not found."
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg)
        return False
    except Exception as e:
        msg = f"Error loading system prompt from {SYSTEM_PROMPT_FILE}: {e}"
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg, exc_info=True)
        return False


def initialize_bluesky_client(step_idx_ref):
    display_startup_screen(step_idx_ref[0])
    time.sleep(0.5) 

    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        msg = "Bluesky credentials (BLUESKY_HANDLE, BLUESKY_PASSWORD) missing."
        display_startup_screen(step_idx_ref[0], error_msg=msg)
        logging.error(msg + " Check .env file.")
        return None
    if not OPENROUTER_KEY:
        logging.warning("OPENROUTER_KEY missing. Bot will run but cannot generate replies.")

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
    if hasattr(post, "record") and hasattr(post.record, "text"):
        return post.record.text
    return ""

def fetch_thread_context(client, uri, original_mentioner_handle=""):
    logging.info(f"Fetching thread context for URI: {uri} (Mention from: @{original_mentioner_handle})")
    try:
        params = GetPostThreadParams(uri=uri)
        thread_response = client.app.bsky.feed.get_post_thread(params=params)
        thread_posts = []
        def traverse_thread(node):
            if not node: return
            if hasattr(node, "parent") and node.parent:
                traverse_thread(node.parent)
            if hasattr(node, "post") and node.post:
                author_handle = "unknown_author"
                if hasattr(node.post, "author") and hasattr(node.post.author, "handle"):
                    author_handle = node.post.author.handle
                text = get_post_text(node.post)
                thread_posts.append(f"@{author_handle}: {text}")
        if hasattr(thread_response, "thread"):
             traverse_thread(thread_response.thread)
        else:
            logging.warning(f"Thread response for {uri} did not have 'thread' attribute.")
            print_centered(f"Could not fetch full thread for @{original_mentioner_handle}", text_color=Colors.WARNING)
            return "", ""
        
        most_recent_post_text = thread_posts[-1] if thread_posts else ""
        thread_history_text = "\n".join(thread_posts[:-1])
        
        if most_recent_post_text:
            if original_mentioner_handle:
                 print_centered(f"Thread context fetched for @{original_mentioner_handle}", text_color=Colors.DIM)
            else:
                 print_centered("Thread context fetched.", text_color=Colors.DIM)
        else:
            logging.warning(f"Thread fetched for {uri}, but no posts extracted.")
            print_centered(f"Empty thread context for @{original_mentioner_handle}", text_color=Colors.WARNING)
        return thread_history_text, most_recent_post_text
    except Exception as e:
        logging.error(f"Error fetching thread ({uri}): {e}", exc_info=True)
        print_centered(f"Error fetching thread for @{original_mentioner_handle}", text_color=Colors.FAIL)
        return "", ""

def get_openrouter_reply(thread_history, most_recent_post_to_reply_to):
    global GPT_MODEL_IN_USE
    if not SYSTEM_PROMPT_TEMPLATE:
        logging.error("System prompt template is not loaded. Cannot generate reply.")
        return ""
    
    final_system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{{BLUESKY_HANDLE}}", BLUESKY_HANDLE if BLUESKY_HANDLE else "your_bot_handle")
    
    mrp_parts = most_recent_post_to_reply_to.split(':', 1)
    mrp_author = mrp_parts[0] if len(mrp_parts) > 1 else "@unknown_user"
    mrp_text = mrp_parts[1].strip() if len(mrp_parts) > 1 else most_recent_post_to_reply_to

    user_content = f"""Here is the conversation thread history:
<thread_history>
{thread_history}
</thread_history>

This is the most recent post in the thread, which you should reply to:
<most_recent_post>
{mrp_author}: {mrp_text}
</most_recent_post>

Generate your reply now.
"""

    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    payload = {
        "model": GPT_MODEL_IN_USE,
        "messages": [
            {"role": "system", "content": final_system_prompt},
            {"role": "user", "content": user_content}
        ]
    }
    log_post_snippet = mrp_text.replace('\n', ' ')[:50]
    
    print_status_line(f"Requesting reply from OpenRouter ({GPT_MODEL_IN_USE}) for: \"{log_post_snippet}...\"", Colors.INFO)
    
    try:
        resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=45)
        resp.raise_for_status()
        response_json = resp.json()
        if "choices" in response_json and len(response_json["choices"]) > 0 and \
           "message" in response_json["choices"][0] and "content" in response_json["choices"][0]["message"]:
            reply_content = response_json["choices"][0]["message"]["content"].strip()
            log_reply_snippet = reply_content.replace('\n', ' ')[:50]
            print_status_line(f"OpenRouter reply received. Snippet: \"{log_reply_snippet}...\"", Colors.OKGREEN)
            print() 
            return reply_content
        else:
            logging.error(f"OpenRouter API response format unexpected: {response_json}")
            print_status_line("OpenRouter API response format unexpected.", Colors.FAIL, clear_previous=True)
            print()
            return ""
    except requests.exceptions.HTTPError as http_err:
        response_text = http_err.response.text if http_err.response is not None else "No response body"
        logging.error(f"OpenRouter API HTTP error: {http_err}. Response: {response_text}")
        print_status_line(f"OpenRouter API HTTP error: {http_err.response.status_code}", Colors.FAIL, clear_previous=True)
        print()
        return ""
    except Exception as e:
        logging.error(f"OpenRouter API request failed: {e}", exc_info=True)
        print_status_line("OpenRouter API request failed.", Colors.FAIL, clear_previous=True)
        print()
        return ""

def main():
    global GPT_MODEL_IN_USE 

    parser = argparse.ArgumentParser(description=f"{BOT_NAME} - A Bluesky Bot")
    parser.add_argument(
        "-m", "--model",
        type=str,
        default=DEFAULT_GPT_MODEL,
        help=f"Specify the OpenRouter AI model to use (default: {DEFAULT_GPT_MODEL})"
    )
    args = parser.parse_args()
    GPT_MODEL_IN_USE = args.model

    current_startup_step = [0] 

    display_startup_screen(current_startup_step[0])
    time.sleep(0.3) 
    current_startup_step[0] += 1

    display_startup_screen(current_startup_step[0])
    time.sleep(0.1)
    current_startup_step[0] += 1

    if not load_system_prompt(current_startup_step):
        sys.exit(1)
    current_startup_step[0] += 1
    
    client = initialize_bluesky_client(current_startup_step)
    if not client:
        sys.exit(1)
    current_startup_step[0] += 1

    display_startup_screen(current_startup_step[0])
    time.sleep(0.2)
    processed_uris = load_processed_uris()
    current_startup_step[0] += 1

    display_startup_screen(current_startup_step[0], success_msg=f"@{BLUESKY_HANDLE} is online and monitoring!")
    time.sleep(2) 

    clear_console()
    print_centered(f"--- {BOT_NAME} v{BOT_VERSION} - Running ---", text_color=Colors.HEADER)
    print_centered(f"Using AI Model: {GPT_MODEL_IN_USE}", prefix_text="", text_color=Colors.BLUE, prefix_color=Colors.DIM)
    print_centered(f"Bot Handle: @{BLUESKY_HANDLE}", text_color=Colors.INFO)
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
                    logging.warning("Received an empty or malformed notification response from Bluesky.")
                    time.sleep(MENTION_CHECK_INTERVAL_SECONDS)
                    continue

                notifications = notifications_response.notifications
                notifications_fetched_count = len(notifications)
                
                skipped_this_round = 0
                for notif in notifications:
                    if not all(hasattr(notif, attr) for attr in ['uri', 'author', 'reason', 'cid']) or \
                       not hasattr(notif.author, 'handle'):
                        logging.warning(f"Skipping malformed notification: {str(notif)[:100]}...")
                        if hasattr(notif, 'uri'):
                            append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    if notif.uri in processed_uris: 
                        skipped_this_round +=1
                        continue
                    if notif.author.handle == BLUESKY_HANDLE:
                        append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    if notif.reason not in ["mention", "reply"]:
                        append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    
                    if consecutive_idle_cycles > 0 or last_status_message:
                        print_status_line("", clear_previous=True)
                        print() 
                    last_status_message = ""
                    consecutive_idle_cycles = 0
                    
                    print_centered(f"New {notif.reason} from @{notif.author.handle}", text_color=Colors.INFO, prefix_text="", suffix_text="", prefix_color=Colors.BLUE)

                    thread_history, most_recent_post = fetch_thread_context(client, notif.uri, notif.author.handle)

                    thread_history, most_recent_post = fetch_thread_context(client, notif.uri)
                    if not most_recent_post:
                        logging.warning(f"Could not extract context for {notif.uri}. Skipping.")
                        append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                        continue

                    reply_text = get_openrouter_reply(thread_history, most_recent_post)
                    if not reply_text:
                        append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                        continue

                    if len(reply_text) > 300:
                        reply_text = reply_text[:297] + "..."
                    
                    parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri)
                    root_ref = parent_ref
                    if hasattr(notif, 'record') and hasattr(notif.record, "reply") and \
                       notif.record.reply and hasattr(notif.record.reply, "root") and notif.record.reply.root:
                        if hasattr(notif.record.reply.root, 'cid') and hasattr(notif.record.reply.root, 'uri'):
                            root_ref = notif.record.reply.root
                    
                    print_centered(f"Sending reply to @{notif.author.handle}...", text_color=Colors.INFO)
                    client.send_post(
                        text=reply_text,
                        reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref),
                    )
                    append_processed_uri(notif.uri); processed_uris.add(notif.uri)
                    new_notifications_processed_this_cycle += 1
                    log_reply_sent = reply_text.replace('\n',' ')[:40]
                    print_centered(f"Replied: \"{log_reply_sent}...\"", text_color=Colors.OKGREEN)
                    print() 

                if notifications_fetched_count > 0 and skipped_this_round == notifications_fetched_count:
                    all_skipped = True

            except AtProtocolError as e: 
                if consecutive_idle_cycles > 0 or last_status_message: print_status_line("", clear_previous=True); print()
                logging.error(f"AT Protocol error: {e}", exc_info=True)
                print_centered(f"AT Protocol Error Occurred.", text_color=Colors.FAIL)
                last_status_message = ""
                consecutive_idle_cycles = 0
            except Exception as e: 
                if consecutive_idle_cycles > 0 or last_status_message: print_status_line("", clear_previous=True); print()
                logging.error(f"Unexpected error in main loop: {e}", exc_info=True)
                print_centered(f"An Unexpected Error Occurred.", text_color=Colors.FAIL)
                last_status_message = ""
                consecutive_idle_cycles = 0
            
            if new_notifications_processed_this_cycle > 0:
                consecutive_idle_cycles = 0
                last_status_message = "" 
            elif all_skipped:
                consecutive_idle_cycles += 1
                status_msg = f"Fetched {notifications_fetched_count}, all skipped. Idle checks: {consecutive_idle_cycles}. Waiting..."
                print_status_line(status_msg, Colors.DIM)
                last_status_message = status_msg
            elif notifications_fetched_count == 0:
                consecutive_idle_cycles += 1
                status_msg = f"Still quiet. No new notifications. Idle checks: {consecutive_idle_cycles}. Waiting..."
                print_status_line(status_msg, Colors.DIM)
                last_status_message = status_msg

            time.sleep(MENTION_CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        if last_status_message: print_status_line("", clear_previous=True); print()
        print_centered("\n\nShutdown requested (Ctrl+C). Quitting...", text_color=Colors.WARNING)
    finally:
        clear_console()
        sys.exit(0)

if __name__ == "__main__":
    main()