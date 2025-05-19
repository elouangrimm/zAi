#!/usr/bin/env python3
import os
import sys
import time
import argparse
import logging
import json

try:
    import requests
    from dotenv import load_dotenv
    from atproto import Client, models
    from atproto.exceptions import (
        AtProtocolError,
        NetworkError,
        RequestException,
        InvokeTimeoutError,
    )
    from atproto_client.models.app.bsky.notification.list_notifications import (
        Params as ListNotificationsParams,
    )
    from atproto_client.models.app.bsky.feed.get_post_thread import (
        Params as GetPostThreadParams,
    )
    import google.generativeai as genai
except ImportError as e:
    missing_module_name = "a required Python package"
    if hasattr(e, "name") and e.name:
        missing_module_name = f"the '{e.name}' package"
    print(f"SERVER MODE - Dependency Error: {missing_module_name} is not installed.")
    print("Please install dependencies: pip install -r requirements.txt")
    print(f"Details: {e}")
    sys.exit(1)

try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    SCRIPT_DIR = os.getcwd()

SYSTEM_PROMPT_FILE = os.path.join(SCRIPT_DIR, "system_prompt.md")
MODELS_FILE = os.path.join(SCRIPT_DIR, "models.txt")

BOT_NAME = "zAi Bluesky Bot (Server Mode)"
BOT_VERSION = "1.5.0"

AI_MODEL_LIST = []
IGNORED_DIDS = set()
PROCESSED_NOTIFS_THIS_RUN = set()

IGNORED_DIDS_ENV_KEY = "IGNORED_DIDS_LIST"
DEFAULT_IGNORED_DIDS_STRING = "did:plc:x42llmbgu3kkget3yrymddwl,did:plc:57na4nqoqohad5wk47jlu4rk,did:plc:kzgyiufe7vfppsg3i5pc24u7,did:plc:uzc4pbfr7mxp7pzml3vljhif,did:plc:fwvdvvwstpxpftnbnjiwcsux,did:plc:3g5vzalrfym4keklsbz6fnfy"

BLUESKY_HANDLE = None
BLUESKY_PASSWORD = None
OPENROUTER_API_KEY_PRIMARY = None
OPENROUTER_API_KEY_SECONDARY = None
GEMINI_API_KEY_ENV = None
MENTION_CHECK_INTERVAL_SECONDS = 30
NOTIFICATION_FETCH_LIMIT = 30
SYSTEM_PROMPT_TEMPLATE = ""

USE_GEMINI_DIRECTLY = True
GEMINI_DIRECT_MODEL_NAME = "gemini-1.5-flash-latest"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("atproto_core").setLevel(logging.WARNING)
logging.getLogger("atproto_client").setLevel(logging.WARNING)
logging.getLogger("google.generativeai").setLevel(logging.WARNING)

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_env_and_config_files():
    global BLUESKY_HANDLE, BLUESKY_PASSWORD, OPENROUTER_API_KEY_PRIMARY, OPENROUTER_API_KEY_SECONDARY, \
           GEMINI_API_KEY_ENV, USE_GEMINI_DIRECTLY, GEMINI_DIRECT_MODEL_NAME, \
           MENTION_CHECK_INTERVAL_SECONDS, NOTIFICATION_FETCH_LIMIT, \
           SYSTEM_PROMPT_TEMPLATE, IGNORED_DIDS, AI_MODEL_LIST

    env_path = os.path.join(SCRIPT_DIR, ".env")
    if os.path.exists(env_path):
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path=env_path, override=True)
            logging.debug(f"Loaded environment variables from: {env_path}")
        except ImportError:
            logging.warning(
                "python-dotenv module not found, cannot load .env file. Relying on platform env vars."
            )
        except Exception as e_dotenv:
            logging.warning(
                f"Error loading .env file from {env_path}: {e_dotenv}. Relying on platform env vars."
            )
    else:
        logging.debug(
            f".env file not found at {env_path}. Relying on platform environment variables."
        )

    BLUESKY_HANDLE = os.getenv("BLUESKY_HANDLE")
    BLUESKY_PASSWORD = os.getenv("BLUESKY_PASSWORD")
    OPENROUTER_API_KEY_PRIMARY = os.getenv("OPENROUTER_API_KEY_PRIMARY")
    OPENROUTER_API_KEY_SECONDARY = os.getenv("OPENROUTER_API_KEY_SECONDARY")
    GEMINI_API_KEY_ENV = os.getenv("GEMINI_API_KEY")

    use_gemini_direct_str = os.getenv(
        "USE_GEMINI_DIRECTLY", str(USE_GEMINI_DIRECTLY)
    ).lower()
    if use_gemini_direct_str == "true":
        USE_GEMINI_DIRECTLY = True
    elif use_gemini_direct_str == "false":
        USE_GEMINI_DIRECTLY = False

    GEMINI_DIRECT_MODEL_NAME = os.getenv(
        "GEMINI_DIRECT_MODEL_NAME", GEMINI_DIRECT_MODEL_NAME
    )

    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logging.critical("Bluesky credentials missing. Exiting.")
        sys.exit(1)
    if USE_GEMINI_DIRECTLY and not GEMINI_API_KEY_ENV:
        logging.critical(
            "USE_GEMINI_DIRECTLY is true, but GEMINI_API_KEY is missing. Exiting."
        )
        sys.exit(1)
    if not USE_GEMINI_DIRECTLY and not OPENROUTER_API_KEY_PRIMARY:
        logging.critical(
            "USE_GEMINI_DIRECTLY is false, but OPENROUTER_API_KEY_PRIMARY is missing. Exiting."
        )
        sys.exit(1)

    MENTION_CHECK_INTERVAL_SECONDS = int(
        os.getenv("MENTION_CHECK_INTERVAL_SECONDS", "30")
    )
    NOTIFICATION_FETCH_LIMIT = int(os.getenv("NOTIFICATION_FETCH_LIMIT", "30"))

    try:
        with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
            SYSTEM_PROMPT_TEMPLATE = f.read()
        if not SYSTEM_PROMPT_TEMPLATE:
            logging.error(f"{SYSTEM_PROMPT_FILE} is empty.")
        else:
            logging.debug(f"System prompt loaded successfully from {SYSTEM_PROMPT_FILE}.")
    except FileNotFoundError:
        logging.critical(
            f"System prompt file '{SYSTEM_PROMPT_FILE}' not found. Exiting."
        )
        sys.exit(1)
    except Exception as e:
        logging.error(
            f"Error loading system prompt from {SYSTEM_PROMPT_FILE}: {e}", exc_info=True
        )
        sys.exit(1)

    ignored_dids_str = os.getenv(IGNORED_DIDS_ENV_KEY, DEFAULT_IGNORED_DIDS_STRING)
    if ignored_dids_str:
        IGNORED_DIDS = {
            did.strip() for did in ignored_dids_str.split(",") if did.strip()
        }
    else:
        IGNORED_DIDS = set()
    logging.debug(f"Loaded {len(IGNORED_DIDS)} ignored DIDs (from env or default).")

    return True


def load_ai_models_from_file(cli_model_override=None):
    global AI_MODEL_LIST
    if USE_GEMINI_DIRECTLY:
        AI_MODEL_LIST = [GEMINI_DIRECT_MODEL_NAME]
        logging.info(
            f"Configured for direct Gemini use with model: {GEMINI_DIRECT_MODEL_NAME}"
        )
        return True

    if cli_model_override:
        AI_MODEL_LIST = [cli_model_override]
        logging.info(f"Using CLI specified OpenRouter model: {cli_model_override}")
        return True
    try:
        with open(MODELS_FILE, "r", encoding="utf-8") as f:
            models_in_file = [
                line.strip() for line in f if line.strip() and not line.startswith("#")
            ]
        if not models_in_file:
            logging.error(f"{MODELS_FILE} is empty (for OpenRouter). Using fallback.")
            AI_MODEL_LIST = ["google/gemini-2.0-flash-exp:free"]
            return True
        AI_MODEL_LIST = models_in_file
        logging.debug(
            f"OpenRouter AI models loaded from '{MODELS_FILE}': {AI_MODEL_LIST}"
        )
        return True
    except FileNotFoundError:
        logging.warning(
            f"OpenRouter models file '{MODELS_FILE}' not found. Using fallback."
        )
        AI_MODEL_LIST = ["google/gemini-2.0-flash-exp:free"]
        return True
    except Exception as e:
        logging.error(
            f"Error loading OpenRouter models from {MODELS_FILE}: {e}", exc_info=True
        )
        return False


def initialize_bluesky_client():
    if not BLUESKY_HANDLE or not BLUESKY_PASSWORD:
        logging.error("Bluesky credentials missing.")
        return None
    if USE_GEMINI_DIRECTLY and not GEMINI_API_KEY_ENV:
        logging.warning("Gemini API Key missing, but set to use Gemini directly.")
    elif not USE_GEMINI_DIRECTLY and not OPENROUTER_API_KEY_PRIMARY:
        logging.warning("Primary OpenRouter API Key missing.")
    try:
        logging.debug(f"Attempting to log in to Bluesky as {BLUESKY_HANDLE}...")
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_PASSWORD)
        logging.info(f"Successfully logged in to Bluesky as {BLUESKY_HANDLE}")
        return client
    except Exception as e:
        logging.error(f"Bluesky login failed: {e}", exc_info=True)
        return None


def get_post_text(post):
    return (
        post.record.text
        if hasattr(post, "record") and hasattr(post.record, "text")
        else ""
    )


def fetch_thread_context(client, uri, original_mentioner_handle=""):
    logging.debug(
        f"Fetching thread context for URI: {uri} (Mention from: @{original_mentioner_handle})"
    )
    try:
        params = GetPostThreadParams(uri=uri, depth=10)
        thread_response = client.app.bsky.feed.get_post_thread(params=params)
        thread_posts = []

        def traverse_thread(node):
            if not node:
                return
            if hasattr(node, "parent") and node.parent:
                traverse_thread(node.parent)
            if hasattr(node, "post") and node.post:
                author_handle = (
                    node.post.author.handle
                    if hasattr(node.post, "author")
                    and hasattr(node.post.author, "handle")
                    else "unknown"
                )
                text = get_post_text(node.post)
                thread_posts.append(f"@{author_handle}: {text}")

        if hasattr(thread_response, "thread") and isinstance(
            thread_response.thread, models.AppBskyFeedDefs.ThreadViewPost
        ):
            traverse_thread(thread_response.thread)
        else:
            logging.warning(f"No valid 'thread' in response for {uri}.")
            return "", ""
        most_recent_post_text = thread_posts[-1] if thread_posts else ""
        thread_history_text = "\n".join(thread_posts[:-1])
        if most_recent_post_text:
            logging.debug(
                f"Thread context successfully fetched for @{original_mentioner_handle}."
            )
        else:
            logging.warning(
                f"Empty thread context for @{original_mentioner_handle} from URI {uri}."
            )
        return thread_history_text, most_recent_post_text
    except Exception as e:
        logging.error(
            f"Error fetching thread ({uri}) for @{original_mentioner_handle}: {e}",
            exc_info=True,
        )
        return "", ""


def get_gemini_direct_reply(
    system_prompt, thread_history, most_recent_post_to_reply_to
):
    global GEMINI_API_KEY_ENV, GEMINI_DIRECT_MODEL_NAME
    if not GEMINI_API_KEY_ENV:
        logging.error("GEMINI_API_KEY not configured for direct use.")
        return ""
    try:
        genai.configure(api_key=GEMINI_API_KEY_ENV)
        model = genai.GenerativeModel(GEMINI_DIRECT_MODEL_NAME)
    except Exception as e:
        logging.error(
            f"Failed to initialize Google Gemini model ({GEMINI_DIRECT_MODEL_NAME}): {e}",
            exc_info=True,
        )
        return ""
    mrp_parts = most_recent_post_to_reply_to.split(":", 1)
    mrp_author = mrp_parts[0] if len(mrp_parts) > 1 else "@unknown"
    mrp_text = (
        mrp_parts[1].strip() if len(mrp_parts) > 1 else most_recent_post_to_reply_to
    )
    full_user_prompt = f"{system_prompt}\n\n<thread_history>\n{thread_history}\n</thread_history>\n\n<most_recent_post_to_reply_to>\n{mrp_author}: {mrp_text}\n</most_recent_post_to_reply_to>\n\nGenerate your reply now:"
    logging.info(f"Attempting direct reply with Gemini model: {GEMINI_DIRECT_MODEL_NAME}...")
    try:
        response = model.generate_content(full_user_prompt)
        reply_text = response.text.strip()
        logging.info(
            f"Successfully received reply from Gemini ({GEMINI_DIRECT_MODEL_NAME}). Snippet: \"{reply_text.replace(chr(10),' ')[:40]}...\""
        )
        return reply_text
    except Exception as e:
        logging.error(
            f"Error during direct Gemini API call with model {GEMINI_DIRECT_MODEL_NAME}: {e}",
            exc_info=True,
        )
        if hasattr(e, "response") and hasattr(e.response, "prompt_feedback"):
            logging.error(f"Gemini prompt feedback: {e.response.prompt_feedback}")
        return ""


def get_openrouter_reply(system_prompt, thread_history, most_recent_post_to_reply_to):
    global AI_MODEL_LIST, OPENROUTER_API_KEY_PRIMARY, OPENROUTER_API_KEY_SECONDARY
    if not AI_MODEL_LIST:
        logging.error("OpenRouter AI model list empty.")
        return ""
    if not OPENROUTER_API_KEY_PRIMARY:
        logging.error("Primary OpenRouter API Key not set.")
        return ""

    final_system_prompt = system_prompt
    mrp_parts = most_recent_post_to_reply_to.split(":", 1)
    mrp_author = mrp_parts[0] if len(mrp_parts) > 1 else "@unknown"
    mrp_text = (
        mrp_parts[1].strip() if len(mrp_parts) > 1 else most_recent_post_to_reply_to
    )
    user_content = f"<thread_history>\n{thread_history}\n</thread_history>\n<most_recent_post>\n{mrp_author}: {mrp_text}\n</most_recent_post>\nReply now."

    api_keys_to_try = [OPENROUTER_API_KEY_PRIMARY]
    if OPENROUTER_API_KEY_SECONDARY:
        api_keys_to_try.append(OPENROUTER_API_KEY_SECONDARY)

    for key_index, current_api_key in enumerate(api_keys_to_try):
        if not current_api_key:
            continue
        key_label = "Primary" if key_index == 0 else "Secondary"
        for model_to_try in AI_MODEL_LIST:
            logging.info(
                f"Attempting reply with OpenRouter model: {model_to_try} using {key_label} Key..."
            )
            headers = {"Authorization": f"Bearer {current_api_key}"}
            payload = {
                "model": model_to_try,
                "messages": [
                    {"role": "system", "content": final_system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            try:
                resp = requests.post(
                    OPENROUTER_API_URL, headers=headers, json=payload, timeout=60
                )
                if resp.status_code == 429:
                    logging.warning(
                        f"Rate limit hit with {key_label} Key on model {model_to_try} (OpenRouter)."
                    )
                    if key_index == 0 and OPENROUTER_API_KEY_SECONDARY:
                        break
                    else:
                        continue
                resp.raise_for_status()
                response_json = resp.json()
                if (
                    "choices" in response_json
                    and response_json["choices"]
                    and "message" in response_json["choices"][0]
                    and "content" in response_json["choices"][0]["message"]
                ):
                    reply_content = response_json["choices"][0]["message"][
                        "content"
                    ].strip()
                    logging.info(
                        f"Reply from OpenRouter ({model_to_try}, {key_label} Key). Snippet: \"{reply_content.replace(chr(10),' ')[:40]}...\""
                    )
                    return reply_content
                else:
                    logging.warning(
                        f"Unexpected API response from {model_to_try} ({key_label} Key, OpenRouter): {response_json}"
                    )
            except requests.exceptions.HTTPError as http_err:
                logging.warning(
                    f"HTTP error {http_err.response.status_code} ({key_label} Key) with {model_to_try} (OpenRouter): {http_err.response.text}"
                )
                if (
                    http_err.response.status_code == 429
                    and key_index == 0
                    and OPENROUTER_API_KEY_SECONDARY
                ):
                    break
            except requests.exceptions.Timeout:
                logging.warning(
                    f"Timeout occurred ({key_label} Key) with model {model_to_try} (OpenRouter)."
                )
            except Exception as e:
                logging.error(
                    f"An unexpected error occurred ({key_label} Key) with model {model_to_try} (OpenRouter): {e}",
                    exc_info=True,
                )
            logging.debug(
                f"Model {model_to_try} ({key_label} Key, OpenRouter) failed. Next..."
            )
            time.sleep(0.5)
        if key_index == 0 and OPENROUTER_API_KEY_SECONDARY:
            logging.info(
                "Primary OR key failed or was rate limited with all models. Trying Secondary."
            )
        elif key_index > 0:
            logging.warning("Secondary OR key also failed with all models.")

    logging.error("All OpenRouter models/keys failed.")
    return ""


def get_ai_reply(thread_history, most_recent_post_to_reply_to):
    global USE_GEMINI_DIRECTLY, SYSTEM_PROMPT_TEMPLATE, BLUESKY_HANDLE
    final_system_prompt = SYSTEM_PROMPT_TEMPLATE.replace(
        "{{BLUESKY_HANDLE}}", BLUESKY_HANDLE or "bot_handle"
    )
    if USE_GEMINI_DIRECTLY:
        return get_gemini_direct_reply(
            final_system_prompt, thread_history, most_recent_post_to_reply_to
        )
    else:
        return get_openrouter_reply(
            final_system_prompt, thread_history, most_recent_post_to_reply_to
        )


def has_bot_already_replied(client, bot_handle, parent_post_uri):
    try:
        params = GetPostThreadParams(uri=parent_post_uri, depth=1)
        thread_response = client.app.bsky.feed.get_post_thread(params=params)
        if not isinstance(
            thread_response.thread, models.AppBskyFeedDefs.ThreadViewPost
        ):
            logging.warning(
                f"[Dupe Check] Could not fetch thread for {parent_post_uri}."
            )
            return False
        if thread_response.thread and thread_response.thread.replies:
            for reply_view in thread_response.thread.replies:
                if (
                    isinstance(reply_view, models.AppBskyFeedDefs.ThreadViewPost)
                    and reply_view.post
                    and reply_view.post.author
                    and reply_view.post.author.handle == bot_handle
                ):
                    logging.debug(
                        f"[Dupe Check] Bot already replied ({reply_view.post.uri}) to {parent_post_uri}."
                    )
                    return True
        return False
    except Exception as e:
        logging.error(
            f"[Dupe Check] Error for {parent_post_uri}: {e}", exc_info=True
        )
        return False


def main():
    global AI_MODEL_LIST, IGNORED_DIDS, PROCESSED_NOTIFS_THIS_RUN, USE_GEMINI_DIRECTLY, GEMINI_DIRECT_MODEL_NAME

    parser = argparse.ArgumentParser(description=f"{BOT_NAME}")
    parser.add_argument(
        "-m", "--model", type=str, default=None, help="Override AI model"
    )
    parser.add_argument(
        "--use-gemini", action="store_true", help="Force direct Gemini API use"
    )
    parser.add_argument(
        "--use-openrouter", action="store_true", help="Force OpenRouter use"
    )
    args = parser.parse_args()

    logging.info(f"Starting {BOT_NAME} v{BOT_VERSION}...")
    if not load_env_and_config_files():
        sys.exit(1)

    if args.use_gemini:
        USE_GEMINI_DIRECTLY = True
        logging.info("CLI override: Forcing direct Gemini API.")
        if not GEMINI_API_KEY_ENV:
            logging.critical(
                "Forced Gemini, but GEMINI_API_KEY missing. Exiting."
            )
            sys.exit(1)
    elif args.use_openrouter:
        USE_GEMINI_DIRECTLY = False
        logging.info("CLI override: Forcing OpenRouter.")
        if not OPENROUTER_API_KEY_PRIMARY:
            logging.critical(
                "Forced OpenRouter, but OPENROUTER_API_KEY_PRIMARY missing. Exiting."
            )
            sys.exit(1)

    cli_model_to_load = args.model
    if USE_GEMINI_DIRECTLY and args.model:
        GEMINI_DIRECT_MODEL_NAME = args.model
        logging.info(
            f"CLI override: Using Gemini direct model: {GEMINI_DIRECT_MODEL_NAME}"
        )
        cli_model_to_load = None

    if not load_ai_models_from_file(cli_model_to_load):
        logging.critical("Failed to load AI models. Exiting.")
        sys.exit(1)

    client = initialize_bluesky_client()
    if not client:
        logging.critical("Failed to initialize Bluesky client. Exiting.")
        sys.exit(1)

    logging.info(f"Bot online as @{BLUESKY_HANDLE}.")
    if USE_GEMINI_DIRECTLY:
        logging.info(f"Using direct Gemini API with model: {GEMINI_DIRECT_MODEL_NAME}")
    else:
        logging.info(f"Using OpenRouter with model list: {AI_MODEL_LIST}")
    if IGNORED_DIDS:
        logging.info(f"Ignoring {len(IGNORED_DIDS)} DIDs.")

    last_seen_notification_timestamp = None
    consecutive_idle_cycles = 0
    try:
        while True:
            new_notifications_processed_this_cycle = 0
            notifications_fetched_count = 0
            try:
                logging.debug("Checking for new notifications...")
                params = ListNotificationsParams(limit=NOTIFICATION_FETCH_LIMIT)
                notifications_response = (
                    client.app.bsky.notification.list_notifications(params=params)
                )
                current_batch_latest_timestamp = None
                if notifications_response and notifications_response.notifications:
                    notifications = sorted(
                        notifications_response.notifications, key=lambda n: n.indexed_at
                    )
                    notifications_fetched_count = len(notifications)
                    if notifications_fetched_count > 0:
                        logging.debug(
                            f"Fetched {notifications_fetched_count} notifications."
                        )
                        current_batch_latest_timestamp = notifications[-1].indexed_at
                    for notif in notifications:
                        if (
                            notif.is_read
                            and notif.uri not in PROCESSED_NOTIFS_THIS_RUN
                        ):
                            logging.debug(f"Skipping read notif {notif.uri}.")
                            continue
                        if notif.uri in PROCESSED_NOTIFS_THIS_RUN:
                            logging.debug(
                                f"Skipping {notif.uri} (processed this run)."
                            )
                            continue
                        if not all(
                            hasattr(notif, x)
                            for x in ["uri", "author", "reason", "cid", "record"]
                        ) or not all(
                            hasattr(notif.author, y) for y in ["handle", "did"]
                        ):
                            logging.warning(f"Malformed notif: {str(notif)[:100]}...")
                            PROCESSED_NOTIFS_THIS_RUN.add(notif.uri)
                            continue
                        if notif.author.did in IGNORED_DIDS:
                            logging.debug(
                                f"Ignoring DID: {notif.author.did} (@{notif.author.handle})"
                            )
                            PROCESSED_NOTIFS_THIS_RUN.add(notif.uri)
                            continue
                        if notif.author.handle == BLUESKY_HANDLE:
                            logging.debug(f"Self-notif: {notif.uri}")
                            PROCESSED_NOTIFS_THIS_RUN.add(notif.uri)
                            continue
                        if notif.reason not in ["mention", "reply"]:
                            logging.debug(
                                f"Skip reason '{notif.reason}': {notif.uri}"
                            )
                            PROCESSED_NOTIFS_THIS_RUN.add(notif.uri)
                            continue
                        PROCESSED_NOTIFS_THIS_RUN.add(notif.uri)
                        logging.info(
                            f"Processing {notif.reason} from @{notif.author.handle} (URI: {notif.uri})"
                        )
                        if has_bot_already_replied(
                            client, BLUESKY_HANDLE, notif.uri
                        ):
                            logging.debug(
                                f"Duplicate: Already replied to {notif.uri}."
                            )
                            continue
                        thread_history, most_recent_post = fetch_thread_context(
                            client, notif.uri, notif.author.handle
                        )
                        if not most_recent_post:
                            logging.warning(f"No post context for {notif.uri}.")
                            continue
                        reply_text = get_ai_reply(
                            thread_history, most_recent_post
                        )
                        if not reply_text:
                            logging.warning(f"No AI reply for {notif.uri}.")
                            continue
                        if len(reply_text) > 300:
                            logging.warning("Reply too long, truncating.")
                            reply_text = reply_text[:297] + "..."
                        parent_ref = models.ComAtprotoRepoStrongRef.Main(
                            cid=notif.cid, uri=notif.uri
                        )
                        root_ref = parent_ref
                        if (
                            hasattr(notif.record, "reply")
                            and notif.record.reply
                            and hasattr(notif.record.reply, "root")
                            and notif.record.reply.root
                        ):
                            if hasattr(
                                notif.record.reply.root, "cid"
                            ) and hasattr(notif.record.reply.root, "uri"):
                                root_ref = notif.record.reply.root
                        logging.debug(
                            f"Sending reply to @{notif.author.handle} for {notif.uri}..."
                        )
                        client.send_post(
                            text=reply_text,
                            reply_to=models.AppBskyFeedPost.ReplyRef(
                                root=root_ref, parent=parent_ref
                            ),
                        )
                        new_notifications_processed_this_cycle += 1
                        logging.info(
                            f"Replied to @{notif.author.handle}. Snippet: \"{reply_text.replace(chr(10),' ')[:40]}...\""
                        )
                else:
                    logging.debug("No new notifications in this fetch.")
                if current_batch_latest_timestamp:
                    try:
                        client.app.bsky.notification.update_seen(
                            {"seenAt": current_batch_latest_timestamp}
                        )
                        logging.debug(
                            f"Updated seen to: {current_batch_latest_timestamp}"
                        )
                        last_seen_notification_timestamp = (
                            current_batch_latest_timestamp
                        )
                    except Exception as e:
                        logging.error(f"Error update_seen: {e}", exc_info=True)
            except InvokeTimeoutError as e:
                logging.warning(f"Bluesky API timeout: {e}")
            except AtProtocolError as e:
                logging.error(f"ATProto error: {e}", exc_info=True)
            except Exception as e:
                logging.error(f"Main loop error: {e}", exc_info=True)
            if new_notifications_processed_this_cycle > 0:
                logging.info(
                    f"Processed {new_notifications_processed_this_cycle} new notif(s)."
                )
                consecutive_idle_cycles = 0
            elif notifications_fetched_count > 0:
                logging.info(
                    f"Fetched {notifications_fetched_count}, none replied. Idle: {consecutive_idle_cycles+1}."
                )
                consecutive_idle_cycles += 1
            else:
                logging.info(
                    f"No new notifs. Idle: {consecutive_idle_cycles+1}."
                )
                consecutive_idle_cycles += 1
            if consecutive_idle_cycles > 0 and consecutive_idle_cycles % 10 == 0:
                logging.info(f"Bot remains idle for {consecutive_idle_cycles} cycles.")
            logging.debug(f"Waiting {MENTION_CHECK_INTERVAL_SECONDS}s...")
            time.sleep(MENTION_CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logging.info("Shutdown (Ctrl+C)...")
    finally:
        logging.info(f"{BOT_NAME} shut down.")
        sys.exit(0)


if __name__ == "__main__":
    main()