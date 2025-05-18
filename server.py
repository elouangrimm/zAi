#!/usr/bin/env python3
import os
import sys
import time
import argparse
import logging

try:
    import requests
    from dotenv import load_dotenv
    from atproto import Client, models
    from atproto.exceptions import AtProtocolError, NetworkError, RequestException, InvokeTimeoutError
    from atproto_client.models.app.bsky.notification.list_notifications import (
        Params as ListNotificationsParams,
    )
    from atproto_client.models.app.bsky.feed.get_post_thread import (
        Params as GetPostThreadParams,
    )
except ImportError as e:
    missing_module_name = "a required Python package"
    if hasattr(e, 'name') and e.name:
        missing_module_name = f"the '{e.name}' package"
    print(f"SERVER MODE - Dependency Error: {missing_module_name} is not installed.")
    print("Please install dependencies: pip install -r requirements.txt")
    print(f"Details: {e}")
    sys.exit(1)

PROCESSED_URIS_FILE = "processed_uris.txt"
SYSTEM_PROMPT_FILE = "system_prompt.md"
MODELS_FILE = "models.txt"
IGNORED_USERS_FILE = "ignored_users.txt"
BOT_NAME = "zAi Bluesky Bot (Server Mode)"
BOT_VERSION = "1.3.1"

AI_MODEL_LIST = []
IGNORED_DIDS = set()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(filename)s:%(lineno)d] - %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("atproto_core").setLevel(logging.WARNING)
logging.getLogger("atproto_client").setLevel(logging.WARNING)

def load_processed_uris():
    if not os.path.exists(PROCESSED_URIS_FILE):
        print(f"'{PROCESSED_URIS_FILE}' not found. Starting with an empty set.")
        return set()
    with open(PROCESSED_URIS_FILE, "r") as f:
        processed = set(line.strip() for line in f if line.strip())
        print(f"Loaded {len(processed)} processed URIs from '{PROCESSED_URIS_FILE}'.")
        return processed

def append_processed_uri(uri):
    with open(PROCESSED_URIS_FILE, "a") as f:
        f.write(f"{uri}\n")

def load_ignored_dids():
    global IGNORED_DIDS
    if not os.path.exists(IGNORED_USERS_FILE):
        print(f"'{IGNORED_USERS_FILE}' not found. No users will be ignored by default.")
        IGNORED_DIDS = set()
        return True
    try:
        with open(IGNORED_USERS_FILE, "r", encoding="utf-8") as f:
            dids = {line.strip() for line in f if line.strip() and not line.startswith("#")}
        IGNORED_DIDS = dids
        print(f"Loaded {len(IGNORED_DIDS)} DIDs from '{IGNORED_USERS_FILE}'.")
        return True
    except Exception as e:
        logging.error(f"Error loading ignored users from {IGNORED_USERS_FILE}: {e}", exc_info=True)
        return False


load_dotenv()
BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
MENTION_CHECK_INTERVAL_SECONDS = int(os.getenv("MENTION_CHECK_INTERVAL_SECONDS", 30))
NOTIFICATION_FETCH_LIMIT = int(os.getenv("NOTIFICATION_FETCH_LIMIT", 30))
SYSTEM_PROMPT_TEMPLATE = ""

def load_ai_models_from_file(cli_model_override=None):
    global AI_MODEL_LIST
    
    if cli_model_override:
        AI_MODEL_LIST = [cli_model_override]
        print(f"Using CLI specified model: {cli_model_override}")
        return True

    try:
        with open(MODELS_FILE, "r", encoding="utf-8") as f:
            models_in_file = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        if not models_in_file:
            logging.error(f"{MODELS_FILE} is empty. Please provide models or use -m.")
            return False

        AI_MODEL_LIST = models_in_file
        print(f"AI models loaded: {AI_MODEL_LIST}")
        return True
    except FileNotFoundError:
        logging.error(f"AI models file '{MODELS_FILE}' not found and no CLI model provided.")
        return False
    except Exception as e:
        logging.error(f"Error loading AI models from {MODELS_FILE}: {e}", exc_info=True)
        return False

def load_system_prompt():
    global SYSTEM_PROMPT_TEMPLATE
    try:
        with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
            SYSTEM_PROMPT_TEMPLATE = f.read()
        if not SYSTEM_PROMPT_TEMPLATE:
            logging.error(f"{SYSTEM_PROMPT_FILE} is empty.")
            return False
        print("System prompt loaded successfully.")
        return True
    except FileNotFoundError:
        logging.error(f"Critical: System prompt file '{SYSTEM_PROMPT_FILE}' not found.")
        return False
    except Exception as e:
        logging.error(f"Error loading system prompt from {SYSTEM_PROMPT_FILE}: {e}", exc_info=True)
        return False

def initialize_bluesky_client():
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logging.error("Bluesky credentials (BLUESKY_HANDLE, BLUESKY_PASSWORD) missing.")
        return None
    if not OPENROUTER_KEY:
        logging.warning("OPENROUTER_KEY missing. Bot will run but cannot generate replies.")
    try:
        print(f"Attempting to log in to Bluesky as {BLUESKY_HANDLE}...")
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
        print(f"Successfully logged in to Bluesky as {BLUESKY_HANDLE}")
        return client
    except Exception as e:
        logging.error(f"Bluesky login failed: {e}", exc_info=True)
        return None

def get_post_text(post):
    return post.record.text if hasattr(post, "record") and hasattr(post.record, "text") else ""

def fetch_thread_context(client, uri, original_mentioner_handle=""):
    print(f"Fetching thread context for URI: {uri} (Mention from: @{original_mentioner_handle})")
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
            logging.warning(f"No 'thread' attribute in response for {uri}.")
            return "", ""
        most_recent_post_text = thread_posts[-1] if thread_posts else ""
        thread_history_text = "\n".join(thread_posts[:-1])
        if most_recent_post_text:
            print(f"Thread context successfully fetched for @{original_mentioner_handle}.")
        else:
            logging.warning(f"Empty thread context for @{original_mentioner_handle} from URI {uri}.")
        return thread_history_text, most_recent_post_text
    except Exception as e:
        logging.error(f"Error fetching thread ({uri}) for @{original_mentioner_handle}: {e}", exc_info=True)
        return "", ""

def get_openrouter_reply(thread_history, most_recent_post_to_reply_to):
    global AI_MODEL_LIST
    if not SYSTEM_PROMPT_TEMPLATE:
        logging.error("System prompt template is not loaded. Cannot generate reply.")
        return ""
    if not AI_MODEL_LIST:
        logging.error("AI model list is empty. Cannot generate reply.")
        return ""

    final_system_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{{BLUESKY_HANDLE}}", BLUESKY_HANDLE or "bot_handle")
    mrp_parts = most_recent_post_to_reply_to.split(':', 1)
    mrp_author = mrp_parts[0] if len(mrp_parts) > 1 else "@unknown"
    mrp_text = mrp_parts[1].strip() if len(mrp_parts) > 1 else most_recent_post_to_reply_to
    user_content = f"<thread_history>\n{thread_history}\n</thread_history>\n<most_recent_post>\n{mrp_author}: {mrp_text}\n</most_recent_post>\nReply now."
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    
    for model_to_try in AI_MODEL_LIST:
        print(f"Attempting reply with AI model: {model_to_try}...")
        payload = {"model": model_to_try, "messages": [{"role": "system", "content": final_system_prompt}, {"role": "user", "content": user_content}]}
        
        try:
            resp = requests.post(OPENROUTER_API_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            response_json = resp.json()
            if "choices" in response_json and response_json["choices"] and \
               "message" in response_json["choices"][0] and "content" in response_json["choices"][0]["message"]:
                reply_content = response_json["choices"][0]["message"]["content"].strip()
                print(f"Successfully received reply from model {model_to_try}. Snippet: \"{reply_content.replace(chr(10),' ')[:40]}...\"")
                return reply_content
            else:
                logging.warning(f"Unexpected API response format from {model_to_try}: {response_json}")
        except requests.exceptions.Timeout:
            logging.warning(f"Timeout occurred with model {model_to_try}.")
        except requests.exceptions.HTTPError as http_err:
            logging.warning(f"HTTP error {http_err.response.status_code} with model {model_to_try}: {http_err.response.text}")
        except Exception as e:
            logging.error(f"An unexpected error occurred with model {model_to_try}: {e}", exc_info=True)
        
        print(f"Model {model_to_try} failed. Trying next model if available...")
        time.sleep(1) 

    logging.error("All configured AI models failed to generate a reply.")
    return ""

def main():
    global AI_MODEL_LIST, IGNORED_DIDS

    parser = argparse.ArgumentParser(description=f"{BOT_NAME}")
    parser.add_argument("-m", "--model", type=str, default=None, help="Specify a single OpenRouter AI model to use, overriding models.txt")
    args = parser.parse_args()
    
    cli_model_override = args.model

    print(f"{BOT_NAME} v{BOT_VERSION}...")

    if not load_system_prompt():
        logging.critical("Failed to load system prompt. Exiting.")
        sys.exit(1)
    if not load_ai_models_from_file(cli_model_override):
        logging.critical("Failed to load AI models. Exiting.")
        sys.exit(1)
    if not load_ignored_dids():
        logging.warning("Failed to load ignored DIDs list, but continuing.") # Non-fatal
    
    client = initialize_bluesky_client()
    if not client:
        logging.critical("Failed to initialize Bluesky client. Exiting.")
        sys.exit(1)
    
    processed_uris = load_processed_uris()
    print(f"Bot is online and monitoring for mentions/replies as @{BLUESKY_HANDLE}.")
    if cli_model_override:
        print(f"Using AI model (CLI override): {cli_model_override}")
    else:
        print(f"Using AI model list (from {MODELS_FILE}): {AI_MODEL_LIST}")
    if IGNORED_DIDS:
        print(f"Ignoring {len(IGNORED_DIDS)} DIDs from '{IGNORED_USERS_FILE}'.")


    consecutive_idle_cycles = 0
    
    try:
        while True:
            new_notifications_processed_this_cycle = 0
            notifications_fetched_count = 0
            all_skipped = False
            try:
                logging.debug("Checking for new notifications...")
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
                    if not all(hasattr(notif, attr) for attr in ['uri','author','reason','cid','record']) or \
                       not hasattr(notif.author,'handle') or not hasattr(notif.author, 'did'):
                        logging.warning(f"Skipping malformed notification: {str(notif)[:100]}...") 
                        if hasattr(notif,'uri'):
                            append_processed_uri(notif.uri)
                            processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue

                    author_did = notif.author.did
                    if author_did in IGNORED_DIDS:
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    
                    if notif.uri in processed_uris:
                        logging.debug(f"Skipping already processed URI: {notif.uri}")
                        skipped_this_round +=1
                        continue
                    if notif.author.handle == BLUESKY_HANDLE:
                        logging.debug(f"Skipping self-notification: {notif.uri}")
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    if notif.reason not in ["mention", "reply"]:
                        logging.debug(f"Skipping notification (reason '{notif.reason}'): {notif.uri}")
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        skipped_this_round +=1
                        continue
                    
                    print(f"Processing new {notif.reason} from @{notif.author.handle} (URI: {notif.uri})")
                    
                    thread_history, most_recent_post = fetch_thread_context(client, notif.uri, notif.author.handle)
                    if not most_recent_post:
                        logging.warning(f"Could not extract most recent post for {notif.uri}. Skipping.")
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        continue

                    reply_text = get_openrouter_reply(thread_history, most_recent_post)
                    if not reply_text:
                        logging.warning(f"Failed to generate reply for {notif.uri}. Skipping.")
                        append_processed_uri(notif.uri)
                        processed_uris.add(notif.uri)
                        continue

                    if len(reply_text) > 300:
                        logging.warning(f"Reply text too long ({len(reply_text)} chars), truncating.")
                        reply_text = reply_text[:297] + "..."
                    
                    parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=notif.cid, uri=notif.uri)
                    root_ref = parent_ref
                    if hasattr(notif,'record') and hasattr(notif.record,"reply") and \
                       notif.record.reply and hasattr(notif.record.reply,"root") and \
                       notif.record.reply.root:
                        if hasattr(notif.record.reply.root,'cid') and hasattr(notif.record.reply.root,'uri'):
                            root_ref = notif.record.reply.root
                    
                    print(f"Sending reply to @{notif.author.handle} for post {notif.uri}...")
                    client.send_post(text=reply_text,reply_to=models.AppBskyFeedPost.ReplyRef(root=root_ref,parent=parent_ref))
                    append_processed_uri(notif.uri)
                    processed_uris.add(notif.uri)
                    new_notifications_processed_this_cycle += 1
                    print(f"Successfully replied to @{notif.author.handle}. Reply snippet: \"{reply_text.replace(chr(10),' ')[:40]}...\"")
                
                if notifications_fetched_count > 0 and skipped_this_round == notifications_fetched_count:
                    all_skipped = True
            
            except InvokeTimeoutError as e:
                logging.warning(f"Bluesky API request timed out: {e}")
            except AtProtocolError as e: 
                logging.error(f"An AT Protocol error occurred: {e}", exc_info=True)
            except Exception as e: 
                logging.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            
            if new_notifications_processed_this_cycle > 0:
                print(f"Processed {new_notifications_processed_this_cycle} new notification(s) this cycle.")
                consecutive_idle_cycles = 0
            elif all_skipped:
                consecutive_idle_cycles += 1
                print(f"... ({consecutive_idle_cycles})")
            elif notifications_fetched_count == 0:
                consecutive_idle_cycles += 1
                print(f"No new notifications. Idle cycle: {consecutive_idle_cycles}.")
            
            logging.debug(f"Waiting {MENTION_CHECK_INTERVAL_SECONDS} seconds...")
            time.sleep(MENTION_CHECK_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("Shutdown requested by user (Ctrl+C). Bot is stopping...")
    finally:
        sys.exit(0)

if __name__ == "__main__":
    main()