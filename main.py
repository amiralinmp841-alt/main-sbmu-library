# --- مخصوص FATHER  ---
# --- مخصوص FATHER  ---
# --- مخصوص FATHER  ---
# --- مخصوص FATHER  ---
# --- مخصوص FATHER  ---
# --- مخصوص FATHER  ---
import logging
import json
import os
#import io
import io as iolib
import uuid
import zipfile
import html
from datetime import datetime
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    MessageReactionUpdated
)

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
    ApplicationHandlerStop,
    MessageReactionHandler
)

import copy
from flask import Flask
import threading
import asyncio
from aiohttp import web
import requests
from telethon import TelegramClient
from telethon.sessions import StringSession
from smart_search import smart_search
from html import escape
from telegram.ext import MessageReactionHandler
from telegram import MessageReactionUpdated


def delete_node_recursive(db, node_id):
    # اگر نود وجود نداشت
    if node_id not in db:
        return

    # اول بچه‌هاش رو حذف کن
    children = db[node_id].get("children", [])
    for child_id in children:
        delete_node_recursive(db, child_id)

    # بعد خود نود
    del db[node_id]



MAX_HISTORY = 20  # 🔹 بیرون تابع (بالای فایل)

def push_admin_history(context, db):
    history = context.user_data.setdefault("admin_history", [])
    future = context.user_data.setdefault("admin_future", [])

    history.append(copy.deepcopy(db))

    if len(history) > MAX_HISTORY:
        history.pop(0)

    # وقتی تغییر جدید داریم، redo باطل می‌شود
    future.clear()

# --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION --- --- CONFIGURATION ---

# ------ DEFAULT_START_TEXT -------
DEFAULT_START_TEXT = """🕊 به ربات کتابخانه دانشگاه خوش آمدید."""

# --- wewb port ---
PORT = int(os.environ.get("PORT", 10000))

# ------ userdata -------
USERDATA_FILE = "/tmp/userdata.json"

ADMIN_IDS = []
if os.getenv("ADMIN_IDS"):
    ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))

# بررسی اینکه حداقل یک ادمین تعریف شده
if not ADMIN_IDS:
    print("Error: ADMIN_IDS not set in environment variables.")
    exit(1)

# فایل دیتابیس
DB_FILE = "/tmp/database.json"

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- STATES FOR CONVERSATION ---
(
    CHOOSING,
    WAITING_BUTTON_NAME,
    WAITING_CONTENT,
    WAITING_RESTORE_FILE,
    WAITING_RENAME_BUTTON,
    WAITING_ADMIN_PASSWORD_EDIT,
    WAITING_USERDATA_UPLOAD,
    WAITING_ADD_ADMIN,
    WAITING_REMOVE_ADMIN,
    WAITING_BAN_USER,
    WAITING_UNBAN_USER,
    WAITING_BROADCAST_CONTENT,
    WAITING_SINGLE_USER_CONTENT,
    WAITING_PICK_USER_FOR_MSG,
    WAITING_CHAT_MESSAGE, 
    WAITING_REPORT_TEXT,
    WAITING_START_PAGE_CONTENT
) = range(17)

# ============ TELEGRAM USER API BACKUP CONFIG ============
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TOKEN = os.getenv("TOKEN")
DB_FILE = "/tmp/database.json"
USERDATA_FILE = "/tmp/userdata.json"

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING")

DB_BACKUP_CHAT_ID = int(os.getenv("DB_BACKUP_CHAT_ID", "0"))
USERDATA_BACKUP_CHAT_ID = int(os.getenv("USERDATA_BACKUP_CHAT_ID", "0"))

ADMIN_ACCESSIBILITY_NAME = os.getenv("ADMIN_ACCESSIBILITY_NAME")

REPORT_GROUP_ID = int(os.getenv("REPORT_GROUP_ID", "0") or "0")
MASSAGE_GROUP_ID = int(os.getenv("MASSAGE_GROUP_ID", "0") or "0")

# ============ TELETHON SEPARATE EVENT LOOP ============

telethon_loop = asyncio.new_event_loop()
telethon_client = None
telethon_ready = threading.Event()


def start_telethon_loop():
    """
    Telethon client runs in a separate thread and separate event loop.
    This prevents conflicts with bot async loop.
    """
    global telethon_client

    asyncio.set_event_loop(telethon_loop)

    telethon_client = TelegramClient(
        StringSession(TG_SESSION_STRING),
        TG_API_ID,
        TG_API_HASH,
        loop=telethon_loop
    )

    async def init_client():
        await telethon_client.start()
        print("✅ Telethon User API client started")
        telethon_ready.set()

    telethon_loop.run_until_complete(init_client())
    telethon_loop.run_forever()


telethon_thread = threading.Thread(target=start_telethon_loop, daemon=True)
telethon_thread.start()


def run_telethon(coro):
    """
    Run async Telethon functions from normal sync code.
    """
    telethon_ready.wait(timeout=30)

    if not telethon_ready.is_set():
        print("❌ Telethon client not ready")
        return None

    future = asyncio.run_coroutine_threadsafe(coro, telethon_loop)
    return future.result(timeout=120)


# ============ TELEGRAM FILE BACKUP HELPERS ============

async def _upload_file_to_telegram(chat_id, file_path, caption=None, parse_mode=None):
    try:
        if not os.path.exists(file_path):
            print(f"❌ File not found for upload: {file_path}")
            return None

        sent_message = await telethon_client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption or f"backup: {os.path.basename(file_path)}",
            parse_mode=parse_mode
        )

        print(f"⬆️ Uploaded to Telegram group: {file_path}")

        # مهم: خود پیام آپلودشده را برگردان
        return sent_message

    except Exception as e:
        print(f"❌ Failed to upload file to Telegram: {e}")
        return None



async def _download_latest_file_from_telegram(chat_id, filename, save_path):
    try:
        print(f"🔍 Searching latest {filename} in Telegram group {chat_id}...")

        async for message in telethon_client.iter_messages(chat_id, limit=200):
            if not message.file:
                continue

            original_name = message.file.name if message.file.name else None
            caption = message.message or ""

            if original_name == filename or filename in caption:
                await message.download_media(file=save_path)
                print(f"⬇️ Downloaded latest {filename} from Telegram group")
                return True

        print(f"⚠️ No file named {filename} found in Telegram group")
        return False

    except Exception as e:
        print(f"❌ Failed to download file from Telegram: {e}")
        return False


def upload_file_to_telegram(chat_id, file_path, caption=None, parse_mode=None):
    return run_telethon(
        _upload_file_to_telegram(chat_id, file_path, caption, parse_mode)
    )


def download_latest_file_from_telegram(chat_id, filename, save_path):
    return run_telethon(
        _download_latest_file_from_telegram(chat_id, filename, save_path)
    )


# ============ DATABASE BACKUP WITH TELEGRAM ============

def download_db_from_telegram():
    return download_latest_file_from_telegram(
        chat_id=DB_BACKUP_CHAT_ID,
        filename="database.json",
        save_path=DB_FILE
    )


def upload_db_to_telegram(caption="database.json"):
    return upload_file_to_telegram(
        chat_id=DB_BACKUP_CHAT_ID,
        file_path=DB_FILE,
        caption=caption,
        parse_mode="HTML"
    )


def load_db():
    # اگر فایل محلی وجود ندارد، از گروه تلگرام دانلود کن
    if not os.path.exists(DB_FILE):
        print("⚠️ Local DB not found. Restoring from Telegram group...")

        if not download_db_from_telegram():
            print("⚠️ Telegram DB backup not found, creating new DB")

            initial_db = {
                "root": {
                    "name": "خانه",
                    "parent": None,
                    "children": [],
                    "contents": []
                }
            }

            save_db(initial_db)
            return initial_db

    # فایل محلی را لود کن
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print("❌ Failed to load local DB:", e)
        return {}


def save_db(data, context=None):
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("💾 DB saved locally")
    except Exception as e:
        print("❌ Failed to save DB locally:", e)
        return False

    log_caption = None
    backup_caption = None

    if context:
        log_caption = pop_pending_caption(context)
        backup_caption = pop_pending_backup_caption(context)

    if not backup_caption:
        backup_caption = "database.json"

    # اول فایل بکاپ را آپلود کن و خود پیام آپلودشده را بگیر
    backup_msg = upload_db_to_telegram(caption=backup_caption)

    if not backup_msg:
        print("❌ Database file upload failed")
        return False

    backup_msg_id = getattr(backup_msg, "id", None)

    if not backup_msg_id:
        print("❌ Uploaded backup message has no message id")
        return False

    # بعد لاگ کامل را به صورت چندتکه، ریپلای روی همان فایل بکاپ بفرست
    if log_caption:
        chunks = split_html_message_by_lines(log_caption, max_len=3000)
        total_parts = len(chunks)

        for i, chunk_text in enumerate(chunks, 1):
            footer = (
                f"\n\n<i>📄 ادامه لاگ "
                f"بخش {i} از {total_parts}</i>"
                if total_parts > 1
                else ""
            )

            final_text = f"{chunk_text}{footer}"

            try:
                run_telethon(
                    telethon_client.send_message(
                        entity=DB_BACKUP_CHAT_ID,
                        message=final_text,
                        parse_mode="HTML",
                        link_preview=False,
                        reply_to=backup_msg_id
                    )
                )
            except Exception as e:
                print(f"❌ Error sending log part {i}: {e}")

    return True



# ============ USERDATA BACKUP WITH TELEGRAM ============

def download_userdata_from_telegram():
    return download_latest_file_from_telegram(
        chat_id=USERDATA_BACKUP_CHAT_ID,
        filename="userdata.json",
        save_path=USERDATA_FILE
    )


def upload_userdata_to_telegram():
    return upload_file_to_telegram(
        chat_id=USERDATA_BACKUP_CHAT_ID,
        file_path=USERDATA_FILE,
        caption="userdata.json"
    )


def load_userdata():
    # اگر فایل محلی نبود، از گروه تلگرام بگیر
    if not os.path.exists(USERDATA_FILE):
        print("⚠️ Local userdata not found. Restoring from Telegram group...")

        if not download_userdata_from_telegram():
            print("⚠️ No userdata backup in Telegram. Creating new userdata.")

            save_userdata({})
            return {}

    try:
        with open(USERDATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print("❌ Failed to load userdata:", e)
        return {}


def save_userdata(data, upload=True):
    # ذخیره لوکال
    try:
        with open(USERDATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("💾 Userdata saved locally")

    except Exception as e:
        print("❌ Failed to save userdata locally:", e)
        return False

    # ارسال به گروه تلگرام فقط وقتی لازم داریم
    if upload:
        return upload_userdata_to_telegram()

    return True

def track_user_activity(update: Update, count_message=True):
    """
    ثبت اطلاعات کاربران داخل userdata:
    - نام
    - یوزرنیم
    - آیدی عددی
    - تعداد پیام‌ها / دستورها
    - وضعیت بن
    - حفظ سایر تنظیمات مثل smart_search_disabled
    """

    user = update.effective_user
    if not user:
        return

    user_id = str(user.id)

    # 🔥 مهم: اگر بن شده باشد، هیچ تغییر در userdata و فایل‌ها انجام نمی‌شود.
    if is_user_banned(user.id):
        return

    userdata = load_userdata()
    users = userdata.setdefault("users", {})

    old_data = users.get(user_id, {})
    old_count = int(old_data.get("message_count", 0))

    full_name = user.full_name or "بدون نام"
    username = user.username

    # به جای overwrite کامل، از اطلاعات قبلی کپی بگیر
    user_record = old_data.copy()

    # فقط فیلدهای لازم را آپدیت کن
    user_record["id"] = user.id
    user_record["full_name"] = full_name
    user_record["username"] = username
    user_record["message_count"] = old_count + 1 if count_message else old_count
    user_record["banned"] = bool(old_data.get("banned", False))
    user_record["first_seen"] = old_data.get("first_seen") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_record["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # اگر از قبل وجود نداشت، پیش‌فرض سرچ هوشمند را روشن نگه دار
    user_record["smart_search_disabled"] = old_data.get("smart_search_disabled", False)

    users[user_id] = user_record

    new_count = user_record["message_count"]

    # برای سبک شدن:
    # هر پیام فقط لوکال ذخیره می‌شود.
    # پیام اول و هر 10 پیام یک بار، بکاپ userdata هم در تلگرام آپلود می‌شود.
    should_upload = (old_count == 0) or (new_count % 10 == 0)

    save_userdata(userdata, upload=should_upload)

def is_user_banned(user_id: int) -> bool:
    userdata = load_userdata()
    user_data = userdata.get("users", {}).get(str(user_id), {})
    return bool(user_data.get("banned", False))


def get_sorted_users_for_management(filter_mode="all"):
    """
    filter_mode:
      - all
      - banned
      - not_banned
    """
    userdata = load_userdata()
    users = userdata.get("users", {})

    result = []

    for user_id, data in users.items():
        try:
            count = int(data.get("message_count", 0))
        except Exception:
            count = 0

        item = {
            "id": int(data.get("id", user_id)),
            "full_name": data.get("full_name") or "بدون نام",
            "username": data.get("username"),
            "message_count": count,
            "banned": bool(data.get("banned", False))
        }

        if filter_mode == "banned" and not item["banned"]:
            continue
        if filter_mode == "not_banned" and item["banned"]:
            continue

        result.append(item)

    result.sort(key=lambda x: x["message_count"], reverse=True)
    return result


def build_user_action_keyboard(users_list, action="ban", page=0, page_size=8):
    """
    action = ban | unban | send_msg
    """
    total = len(users_list)
    start = page * page_size
    end = start + page_size
    page_users = users_list[start:end]

    keyboard = []

    row = []

    for user in page_users:
        uid = user["id"]
        name = str(user.get("full_name", "بدون نام"))[:20]

        if action == "ban":
            prefix = "🚫"
            callback_data = f"admin_ban_pick_{uid}"

        elif action == "unban":
            prefix = "✅"
            callback_data = f"admin_unban_pick_{uid}"

        elif action == "send_msg":
            prefix = "✉️"
            callback_data = f"admin_send_msg_to_{uid}"

        else:
            continue

        row.append(
            InlineKeyboardButton(
                f"{prefix} {name}",
                callback_data=callback_data
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    nav_row = []

    if page > 0:
        if action == "send_msg":
            prev_callback = f"admin_msg_pick_page_{page - 1}"
        else:
            prev_callback = f"admin_{action}_page_{page - 1}"

        nav_row.append(
            InlineKeyboardButton("⬅️ صفحه قبل", callback_data=prev_callback)
        )

    if end < total:
        if action == "send_msg":
            next_callback = f"admin_msg_pick_page_{page + 1}"
        else:
            next_callback = f"admin_{action}_page_{page + 1}"

        nav_row.append(
            InlineKeyboardButton("➡️ صفحه بعد", callback_data=next_callback)
        )

    if nav_row:
        keyboard.append(nav_row)

    if action == "send_msg":
        back_callback = "admin_users_message"
    else:
        back_callback = "admin_users"

    keyboard.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data=back_callback)
    ])

    return InlineKeyboardMarkup(keyboard)

# ساخت لینک پوشه (دیپ‌لینک)
def get_link(node_id, text, bot_username):
    url = f"https://t.me/{bot_username}?start={node_id}"
    return f"<a href='{url}'>{text}</a>"

# ساخت لینک پروفایل ادمین
def get_admin_link(admin_user):
    return f"<a href='tg://user?id={admin_user.id}'>{admin_user.full_name}</a>"

# ذخیره کپشن در کانتکست (برای استفاده داخل save_db)
def set_pending_caption(context, caption):
    context.user_data["pending_caption"] = caption

def pop_pending_caption(context):
    return context.user_data.pop("pending_caption", None)

def set_pending_backup_caption(context, caption):
    context.user_data["pending_backup_caption"] = caption

def pop_pending_backup_caption(context):
    return context.user_data.pop("pending_backup_caption", None)

def format_backup_caption(admin_user, action_type):
    admin_link = get_admin_link(admin_user)
    username = f"@{admin_user.username}" if admin_user.username else "بدون یوزرنیم"

    text = (
        f"👑 <b>بکاپ دیتابیس</b>\n"
        f"👤 ادمین: {admin_link}\n"
        f"🆔: <code>{admin_user.id}</code>\n"
        f"👤 یوزرنیم: {username}\n"
        f"⚙️ عملیات: <b>{action_type}</b>"
    )

    # برای اطمینان از محدودیت کپشن فایل تلگرام
    return text[:1000]

#------ دکمه های رنگی ----------
async def set_node_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    userdata = load_userdata()

    is_admin = (user_id in ADMIN_IDS) or (user_id in userdata.get("sub_admins", []))

    if not is_admin:
        return

    command = update.message.text.lower().split()[0]

    styles = {
        "/blue": "primary",
        "/green": "success",
        "/red": "danger",
        "/none": None
    }

    color_names = {
        "primary": "آبی",
        "success": "سبز",
        "danger": "قرمز",
        None: "بدون رنگ"
    }

    command_color_names = {
        "/green": "سبز",
        "/blue": "آبی",
        "/red": "قرمز",
        "/none": "بدون رنگ"
    }

    if command not in styles:
        await update.message.reply_text("❌ دستور رنگ نامعتبر است.")
        return

    current_node_id = context.user_data.get("current_node", "root")

    if current_node_id == "root":
        await update.message.reply_text("❌ امکان تغییر رنگ صفحه اصلی وجود ندارد.")
        return

    db = load_db()

    if current_node_id not in db:
        await update.message.reply_text("❌ پوشه فعلی در دیتابیس پیدا نشد.")
        return

    push_admin_history(context, db)

    old_style = db[current_node_id].get("style")
    old_color_name = color_names.get(old_style, "بدون رنگ")

    new_style = styles[command]
    new_color_name = command_color_names[command]

    if new_style is None:
        db[current_node_id].pop("style", None)
    else:
        db[current_node_id]["style"] = new_style

    bot_username = context.bot.username
    node_name = db[current_node_id]["name"]
    node_link = get_link(current_node_id, node_name, bot_username)

    desc = (
        f"🎨 رنگ پوشه {node_link} "
        f"از «{old_color_name}» به «{new_color_name}» تغییر کرد."
    )

    log_caption = format_admin_log(update.effective_user, desc)
    backup_caption = format_backup_caption(update.effective_user, "تغییر رنگ پوشه")
    
    set_pending_caption(context, log_caption)
    set_pending_backup_caption(context, backup_caption)
    
    save_db(db, context=context)

    parent_id = db[current_node_id].get("parent", "root")
    context.user_data["current_node"] = parent_id

    await update.message.reply_text(
        f"✅ رنگ این پوشه به «{new_color_name}» تغییر یافت.",
        reply_markup=get_keyboard(parent_id, True, user_id=user_id)
    )

    return CHOOSING

# ========= favorite folder ===============

def add_to_favorites(user_id, node_id, content_index):
    userdata = load_userdata()
    users = userdata.setdefault("users", {})
    user_record = users.setdefault(str(user_id), {})
    favorites = user_record.setdefault("favorites", [])

    item = {"node_id": node_id, "content_index": content_index}
    
    # جلوگیری از تکراری بودن
    if item not in favorites:
        favorites.append(item)
        save_userdata(userdata, upload=False)
        return True
    return False

def remove_from_favorites(user_id, node_id, content_index):
    userdata = load_userdata()
    users = userdata.setdefault("users", {})
    user_record = users.get(str(user_id), {})
    favorites = user_record.get("favorites", [])

    item = {"node_id": node_id, "content_index": content_index}
    if item in favorites:
        favorites.remove(item)
        save_userdata(userdata, upload=False)
        return True
    return False

def clear_all_favorites(user_id):
    userdata = load_userdata()
    users = userdata.setdefault("users", {})
    user_record = users.get(str(user_id), {})
    if "favorites" in user_record:
        user_record["favorites"] = []
        save_userdata(userdata, upload=True) # ذخیره و آپلود نهایی


async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):

    
    try:
        print("REACTION UPDATE:", update.to_dict())
    except:
        pass

    reaction = update.message_reaction
    if not reaction:
        #print("NO message_reaction")
        return

    user_id = reaction.user.id if reaction.user else None
    chat_id = reaction.chat.id
    msg_id = reaction.message_id

    userdata = load_userdata()
    current=context.user_data.get("current_node", "root")
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از این بخش را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING

    if not user_id:
        #print("NO user_id")
        return

    #print("REACTION FROM:", user_id, "ON MSG:", msg_id)

    # دقیقاً مثل deeplink
    sent_mapping = context.user_data.get("sent_mapping", {})
    target = sent_mapping.get(msg_id)

    #print("META:", target)

    if not target:
        #print("NO META FOUND IN sent_mapping")
        return

    node_id = target.get("node_id")
    content_index = target.get("content_index")

    if node_id is None or content_index is None:
        #print("Meta incomplete.")
        return

    db = load_db()

    if node_id not in db or "contents" not in db[node_id]:
        #print("Node/content not found in db")
        return

    contents = db[node_id].get("contents", [])

    try:
        idx = int(content_index)
    except (TypeError, ValueError):
        #print("Invalid content_index:", content_index)
        return

    if not (0 <= idx < len(contents)):
        #print("content index out of range")
        return

    target_item = contents[idx]
    msg_type = target_item.get("type", "text")
    media_group_id = target_item.get("media_group_id")
    groupable_types = {"photo", "video", "document", "audio"}

    matched_items = []

    # دقیقاً مثل deeplink: اگر عضو گروه فایل بود، همه اعضای گروه را پیدا کن
    if media_group_id and msg_type in groupable_types:
        start = idx
        while start > 0:
            prev_item = contents[start - 1]
            if (
                prev_item.get("media_group_id") == media_group_id
                and prev_item.get("type") in groupable_types
            ):
                start -= 1
            else:
                break

        end = idx
        while end + 1 < len(contents):
            next_item = contents[end + 1]
            if (
                next_item.get("media_group_id") == media_group_id
                and next_item.get("type") in groupable_types
            ):
                end += 1
            else:
                break

        for i in range(start, end + 1):
            matched_items.append((i, contents[i]))
    else:
        matched_items.append((idx, target_item))

    new_emojis = [r.emoji for r in reaction.new_reaction]
    old_emojis = [r.emoji for r in reaction.old_reaction]

    HEARTS = {"❤", "❤️"}
    DISLIKES = {"👎"}

    added_heart = any(e in HEARTS for e in new_emojis) and not any(e in HEARTS for e in old_emojis)
    removed_heart = any(e in HEARTS for e in old_emojis) and not any(e in HEARTS for e in new_emojis)
    added_dislike = any(e in DISLIKES for e in new_emojis) and not any(e in DISLIKES for e in old_emojis)

    affected_count = 0

    if added_heart:
        already_exists = 0
        added_count = 0
    
        for item_index, item in matched_items:
            item_type = item.get("type", "text")
            if item_type == "text":
                continue
    
            result = add_to_favorites(user_id, node_id, item_index)
    
            if result:
                added_count += 1
            else:
                already_exists += 1
    
        if added_count > 0 and already_exists == 0:
            if len(matched_items) == 1:
                text = "✅ به پوشه دلخواه اضافه شد."
            else:
                text = f"✅ {added_count} فایل از این گروه به پوشه دلخواه اضافه شد."
    
        elif added_count == 0 and already_exists > 0:
            if len(matched_items) == 1:
                text = "ℹ️ این فایل از قبل به پوشه دلخواه اضافه شده است."
            else:
                text = "ℹ️ همه این فایل‌ها از قبل در پوشه دلخواه بودند."
    
        else:
            text = f"⚠️ {added_count} اضافه شد، {already_exists} مورد از قبل وجود داشت."
    
        await context.bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=msg_id,
            text=text,
            reply_markup=get_keyboard(current, is_admin, user_id=user_id)
        )
        return

    if removed_heart:
        for item_index, item in matched_items:
            item_type = item.get("type", "text")
            if item_type == "text":
                continue

            if remove_from_favorites(user_id, node_id, item_index):
                affected_count += 1

        if affected_count > 0:
            if len(matched_items) == 1:
                text = "🗑 از پوشه دلخواه حذف شد."
            else:
                text = f"🗑 {affected_count} فایل از این گروه از پوشه دلخواه حذف شد."

            await context.bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=text,
                reply_markup=get_keyboard(current, is_admin, user_id=user_id)
            )
        return

    if added_dislike:
        for item_index, item in matched_items:
            item_type = item.get("type", "text")
            if item_type == "text":
                continue

            if remove_from_favorites(user_id, node_id, item_index):
                affected_count += 1

        if affected_count > 0:
            if len(matched_items) == 1:
                text = "🗑 حذف شد."
            else:
                text = f"🗑 {affected_count} فایل از این گروه حذف شد."

            await context.bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=text,
                reply_markup=get_keyboard(current, is_admin, user_id=user_id)
            )
        return

async def clear_favorites_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    user_id = update.effective_user.id
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    clear_all_favorites(user_id)
    current=context.user_data.get("current_node", "root")

    await update.message.reply_text(
        "✅ پوشه دلخواه پاکسازی شد.",
        reply_markup=get_keyboard(current, is_admin, user_id=user_id)
    )
    return CHOOSING

#def save_reaction_mapping(chat_id, message_id, node_id, content_index):
#    userdata = load_userdata()
#    reaction_map = userdata.setdefault("reaction_map", {})
#
#    key = f"{chat_id}:{message_id}"
#    reaction_map[key] = {
#        "node_id": node_id,
#        "content_index": content_index
#    }
#
#    save_userdata(userdata, upload=False)
#
#
#def get_reaction_mapping(chat_id, message_id):
#    userdata = load_userdata()
#    reaction_map = userdata.get("reaction_map", {})
#
#    key = f"{chat_id}:{message_id}"
#    return reaction_map.get(key)

# --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS -

def get_keyboard(node_id, is_admin, user_id=None):
    db = load_db()
    node = db.get(node_id)

    if not node:
        return ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

    keyboard = []

    # --- دکمه‌های فرزند (همان کدی که داشتی) ---
    children_ids = node.get("children", [])
    row = []

    for child_id in children_ids:
        child_node = db.get(child_id)
        if child_node:
            btn_style = child_node.get("style")
            if btn_style:
                button = KeyboardButton(
                    text=child_node["name"],
                    api_kwargs={"style": btn_style}
                )
            else:
                button = KeyboardButton(text=child_node["name"])
            row.append(button)
            
            if len(row) == 2:
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)

    # ========= favorite folder ===============
    if user_id:
        userdata = load_userdata()
        favorites = userdata.get("users", {}).get(str(user_id), {}).get("favorites", [])
        if favorites:
            favorite_btn = KeyboardButton(
                text="📁 پوشه دلخواه",
                api_kwargs={"style": "primary"}
            )
            keyboard.insert(0, [favorite_btn])

    # --- دکمه‌های کنترلی ادمین ---
    if is_admin:
        # برای ادمین هم اگر می‌خواهی رنگی باشند، باید مشابه بالا از KeyboardButton استفاده کنی
        # فعلاً به همون شکلی که داشتی گذاشتم که بهم نریزه
        keyboard.append(["➕ افزودن دکمه", "➕ افزودن محتوا"])
        keyboard.append(["🗑 حذف دکمه", "🧹 حذف محتوای صفحه"])
        keyboard.append(["✏️ ویرایش‌نام‌دکمه", "🔑 دریافت ‌هش‌ولینک‌دکمه", "🔀 جابه‌جایی‌چیدمان"])
        keyboard.append(["📥 دریافت بکاپ", "📤 وارد کردن بکاپ"])
        keyboard.append(["↩️", "↪️"])

    # --- دکمه‌های بازگشت و خانه (اصلاح شده برای رنگی شدن) ---
    nav_row = []
    
    # دکمه بازگشت
    if node.get("parent"):
        back_btn = KeyboardButton(
            text="🔙 بازگشت",
            api_kwargs={"style": "primary"}
        )
        nav_row.append(back_btn)
    
    # دکمه خانه
    home_btn = KeyboardButton(
        text="🏠 صفحه اصلی",
        api_kwargs={"style": "primary"}
    )
    nav_row.append(home_btn)
    
    keyboard.append(nav_row)

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- HELPER FUNCTIONS --- --- --- --- --- ---

def get_admin_access_inline_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👑 مدیریت ادمین‌ها", callback_data="admin_mgmt"),
            InlineKeyboardButton("👥 مدیریت کاربران", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("📤 دریافت userdata", callback_data="admin_get_userdata"),
            InlineKeyboardButton("📥 وارد کردن userdata", callback_data="admin_import_userdata")
        ],
        
        [
            InlineKeyboardButton("🕊 ویرایش پیام استارت", callback_data="admin_edit_start_page"),
            InlineKeyboardButton("❌ بستن پنل", callback_data="admin_close")
        ]
    ])

def get_start_page_edit_inline_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ ثبت", callback_data="admin_save_start_page"),
            InlineKeyboardButton("❌ لغو", callback_data="admin_cancel_start_page"),
        ]
    ])

def get_admin_mgmt_inline_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 تنظیم رمز ادمینی", callback_data="admin_password")
        ],
        [
            InlineKeyboardButton("➕ افزودن ادمین", callback_data="admin_add_sub"),
            InlineKeyboardButton("➖ حذف ادمین", callback_data="admin_remove_sub")
        ],
        [
            InlineKeyboardButton("📋 لیست ادمین‌ها", callback_data="admin_list")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back_access")
        ]
    ])

def get_user_mgmt_inline_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 لیست کاربران", callback_data="admin_users_list")
        ],
        [
            InlineKeyboardButton("🚫 بن کردن کاربر", callback_data="admin_users_ban"),
            InlineKeyboardButton("✅ خارج کردن از بن", callback_data="admin_users_unban")
        ],
        [
            InlineKeyboardButton("📨 پیام به کاربران", callback_data="admin_users_message")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back_access")
        ]
    ])

def get_admin_password_inline_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ ویرایش رمز", callback_data="admin_edit_password")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin_mgmt")
        ]
    ])

def get_node_path_text(db, node_id, separator=" ⬅️ "):
    """
    مسیر کامل یک نود را از ریشه تا خودش می‌سازد.
    مثال:
    ترم 1 ⬅️ آناتومی اندام ⬅️ عملی ⬅️ جلسه اول
    """

    if node_id not in db:
        return "مسیر نامشخص"

    path = []
    current_id = node_id
    visited = set()

    while current_id and current_id in db:
        # جلوگیری از حلقه بی‌نهایت اگر دیتابیس خراب شده باشد
        if current_id in visited:
            break

        visited.add(current_id)

        node = db[current_id]

        # root را داخل مسیر نشان نده
        if current_id != "root":
            path.append(node.get("name", "بدون نام"))

        current_id = node.get("parent")

    # چون از پایین به بالا جمع کردیم، باید برعکس شود
    path.reverse()

    if not path:
        return db.get("root", {}).get("name", "خانه")

    return separator.join(path)

def get_node_path_html(db, node_id, bot_username, separator=" ⬅️ "):
    """
    مسیر کامل یک نود را از ریشه تا خودش به‌صورت لینک‌دار HTML می‌سازد.
    اسم پوشه‌ها آبی و کلیک‌دار می‌شوند.
    """

    if node_id not in db:
        return "مسیر نامشخص"

    path = []
    current_id = node_id
    visited = set()

    while current_id and current_id in db:
        if current_id in visited:
            break

        visited.add(current_id)
        node = db[current_id]

        if current_id != "root":
            name = escape(node.get("name", "بدون نام"))
            link = f"https://t.me/{bot_username}?start={current_id}"
            path.append(f'<a href="{link}">{name}</a>')

        current_id = node.get("parent")

    path.reverse()

    if not path:
        root_name = escape(db.get("root", {}).get("name", "خانه"))
        return f'<a href="https://t.me/{bot_username}?start=root">{root_name}</a>'

    return separator.join(path)

def set_report_page(context: ContextTypes.DEFAULT_TYPE, node_id: str):
    """
    ذخیره صفحه فعلی برای قابلیت /report
    """
    context.user_data["current_report_node"] = node_id

def set_pending_report(context: ContextTypes.DEFAULT_TYPE, report_data: dict):
    context.user_data["pending_report"] = report_data


def get_pending_report(context: ContextTypes.DEFAULT_TYPE):
    return context.user_data.get("pending_report")


def clear_pending_report(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("pending_report", None)


async def send_pending_report(update: Update, context: ContextTypes.DEFAULT_TYPE, report_message_text: str | None = None):
    pending = get_pending_report(context)

    if not pending:
        await update.message.reply_text("❌ گزارشی برای ارسال پیدا نشد.")
        return CHOOSING

    user = update.effective_user
    report_message_text = (report_message_text or "").strip()

    report_text = pending["report_text"]
    user_reply = pending["user_reply"]

    if report_message_text:
        escaped_report_message = html.escape(report_message_text)

        report_text += (
            f"\n\n📝 <b>متن گزارش:</b>\n{escaped_report_message}"
        )

        user_reply += (
            f"\n\n📝 <b>متن گزارش:</b>\n{escaped_report_message}"
        )

    try:
        await context.bot.send_message(
            chat_id=REPORT_GROUP_ID,
            text=report_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

        await update.message.reply_text(user_reply, parse_mode="HTML")
    except Exception as e:
        print("Failed to send report:", e)
        await update.message.reply_text("❌ ارسال گزارش با خطا مواجه شد.")
    finally:
        clear_pending_report(context)

    return CHOOSING


async def report_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user = update.effective_user
    user_id = user.id

    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING

    if not REPORT_GROUP_ID:
        await update.message.reply_text("❌ گروه گزارشات برای ربات تنظیم نشده است.")
        return CHOOSING

    db = load_db()
    bot_username = context.bot.username

    full_name = html.escape(user.full_name or "بدون نام")
    username = user.username

    if username:
        username_text = f"@{html.escape(username)}"
    else:
        username_text = "ندارد"

    user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'

    try:
        # حالت 1: گزارش فایل/محتوا با ریپلای
        if update.message.reply_to_message:
            replied_msg_id = update.message.reply_to_message.message_id
            sent_mapping = context.user_data.get("sent_mapping", {})
            target = sent_mapping.get(replied_msg_id)

            if not target:
                await update.message.reply_text(
                    "❌ امکان گزارش این فایل وجود ندارد.\n\n"
                    "فقط فایل‌های مربوط به آخرین پوشه‌ای که باز کرده‌اید، قابل شناسایی و گزارش هستند. "
                    "پس از باز کردن پوشه‌های دیگر، اطلاعات پوشه‌های قبلی از حافظه ربات حذف می‌شوند.\n\n"
                    "برای گزارش یک فایل، ابتدا پوشه حاوی آن را باز کنید، سپس روی پیام همان فایل ریپلای کرده و دستور /report را ارسال کنید."
                )
                return CHOOSING

            node_id = target.get("node_id")
            content_index = target.get("content_index")

            if node_id not in db:
                await update.message.reply_text("❌ پوشه مربوط به این محتوا پیدا نشد.")
                return CHOOSING

            contents = db[node_id].get("contents", [])
            if not (0 <= content_index < len(contents)):
                await update.message.reply_text("❌ فایل یا محتوای موردنظر دیگر در این پوشه وجود ندارد.")
                return CHOOSING

            node = db[node_id]
            item = contents[content_index]

            page_name = html.escape(node.get("name", "بدون نام"))
            path_text = html.escape(get_node_path_text(db, node_id))

            content_type = item.get("type", "unknown")
            content_type_map = {
                "text": "متن",
                "photo": "عکس",
                "video": "ویدیو",
                "document": "فایل",
                "audio": "صوت",
                "voice": "ویس",
            }
            content_type_text = content_type_map.get(content_type, content_type)

            deep_link = f"https://t.me/{bot_username}?start=file_{node_id}_{content_index}"

            caption_or_text = ""
            if content_type == "text":
                caption_or_text = item.get("text", "") or ""
            else:
                caption_or_text = item.get("caption", "") or ""

            caption_or_text = caption_or_text.strip()
            if len(caption_or_text) > 700:
                caption_or_text = caption_or_text[:700] + "..."

            content_preview = html.escape(caption_or_text) if caption_or_text else "ندارد"
            file_hash = html.escape(f"{node_id}__{content_index}")

            report_text = (
                "🚨 <b>گزارش فایل / محتوا</b>\n\n"
                f"👤 <b>کاربر گزارش‌دهنده:</b> {user_link}\n"
                f"🆔 <b>آیدی عددی کاربر:</b> <code>{user.id}</code>\n"
                f"🔗 <b>یوزرنیم:</b> {username_text}\n\n"
                f"📂 <b>نام پوشه:</b> {page_name}\n"
                f"📍 <b>مسیر پوشه:</b>\n{path_text}\n\n"
                f"🗂 <b>نوع محتوا:</b> {html.escape(content_type_text)}\n"
                f"🔢 <b>اندیس محتوا:</b> <code>{content_index}</code>\n"
                f"🔑 <b>هش محتوا:</b>\n<code>{file_hash}</code>\n\n"
                f"📝 <b>متن/کپشن:</b>\n{content_preview}\n\n"
                f"🔗 <b>دیپ‌لینک فایل:</b>\n{html.escape(deep_link)}"
            )

            user_reply = (
                "✅ گزارش فایل برای مدیریت ارسال شد.\n\n"
                f"🗂 <b>نوع محتوا:</b> {html.escape(content_type_text)}\n"
                f"📂 <b>نام پوشه:</b> {page_name}\n"
                f"📍 <b>مسیر پوشه:</b>\n{path_text}\n\n"
                f"🔗 <b>دیپ‌لینک فایل:</b>\n<code>{html.escape(deep_link)}</code>"
            )

            set_pending_report(context, {
                "report_text": report_text,
                "user_reply": user_reply,
            })

            await update.message.reply_text(
                "📝 متن گزارش را ارسال کنید.\n"
                "اگر نمی‌خواهید متنی اضافه شود، دستور /no_messager را بزنید.\n"
                "برای لغو کامل گزارش، دستور /cansel را بزنید."
            )
            return WAITING_REPORT_TEXT

        # حالت 2: گزارش صفحه/پوشه
        node_id = context.user_data.get("current_report_node")
        if not node_id:
            node_id = context.user_data.get("current_node", "root")

        if node_id not in db:
            await update.message.reply_text("❌ صفحه فعلی برای گزارش پیدا نشد.")
            return CHOOSING

        node = db[node_id]
        deep_link = f"https://t.me/{bot_username}?start={node_id}"

        page_name = html.escape(node.get("name", "بدون نام"))
        path_text = html.escape(get_node_path_text(db, node_id))

        report_text = (
            "🚨 <b>گزارش صفحه</b>\n\n"
            f"👤 <b>کاربر گزارش‌دهنده:</b> {user_link}\n"
            f"🆔 <b>آیدی عددی کاربر:</b> <code>{user.id}</code>\n"
            f"🔗 <b>یوزرنیم:</b> {username_text}\n\n"
            f"📄 <b>نام صفحه:</b> {page_name}\n"
            f"📂 <b>مسیر صفحه:</b>\n{path_text}\n\n"
            f"🔑 <b>هش صفحه:</b>\n<code>{html.escape(node_id)}</code>\n\n"
            f"🔗 <b>دیپ‌لینک صفحه:</b>\n{html.escape(deep_link)}"
        )

        user_reply = (
            "✅ گزارش شما با موفقیت برای مدیریت ارسال شد.\n\n"
            f"📄 <b>نام صفحه:</b> {page_name}\n"
            f"📂 <b>مسیر صفحه:</b>\n{path_text}\n\n"
            f"🔗 <b>دیپ‌لینک صفحه:</b>\n<code>{html.escape(deep_link)}</code>"
        )

        set_pending_report(context, {
            "report_text": report_text,
            "user_reply": user_reply,
        })

        await update.message.reply_text(
            "📝 متن گزارش را ارسال کنید.\n"
            "اگر نمی‌خواهید متنی اضافه شود، دستور /no_messager را بزنید.\n"
            "برای لغو کامل گزارش، دستور /cansel را بزنید."
        )
        return WAITING_REPORT_TEXT

    except Exception as e:
        print("Failed to prepare report:", e)
        clear_pending_report(context)
        await update.message.reply_text("❌ آماده‌سازی گزارش با خطا مواجه شد.")
        return CHOOSING

async def receive_report_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user_id = update.effective_user.id
    if is_user_banned(user_id):
        clear_pending_report(context)
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING

    if not get_pending_report(context):
        await update.message.reply_text("❌ گزارشی در انتظار ارسال نیست.")
        return CHOOSING

    report_message_text = (update.message.text or "").strip()
    return await send_pending_report(update, context, report_message_text)


async def report_without_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user_id = update.effective_user.id
    if is_user_banned(user_id):
        clear_pending_report(context)
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING

    if not get_pending_report(context):
        await update.message.reply_text("❌ گزارشی در انتظار ارسال نیست.")
        return CHOOSING

    return await send_pending_report(update, context, "")


async def cancel_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    clear_pending_report(context)
    await update.message.reply_text("❌ ارسال گزارش لغو شد.")
    return CHOOSING


async def send_single_content_by_item(message, item):
    msg_type = item["type"]
    saved_entities = item.get("entities")

    if msg_type == "text":
        if saved_entities is not None:
            return await message.reply_text(
                text=item["text"],
                entities=saved_entities
            )
        else:
            return await message.reply_text(
                text=item["text"],
                parse_mode="HTML"
            )

    file_id = item["file_id"]
    caption = item.get("caption", "")

    send_args = {"caption": caption}
    if saved_entities is not None:
        send_args["caption_entities"] = saved_entities
    else:
        send_args["parse_mode"] = "HTML"

    if msg_type == "photo":
        return await message.reply_photo(photo=file_id, **send_args)
    elif msg_type == "video":
        return await message.reply_video(video=file_id, **send_args)
    elif msg_type == "document":
        return await message.reply_document(document=file_id, **send_args)
    elif msg_type == "audio":
        return await message.reply_audio(audio=file_id, **send_args)
    elif msg_type == "voice":
        return await message.reply_voice(voice=file_id, **send_args)

    return None

async def deeplink_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text("⛔️ شما بن شده‌اید.")
        return CHOOSING

    db = load_db()
    bot_username = context.bot.username
    msg = update.message

    # اگر روی پیام ربات ریپلای شده باشد => دیپ‌لینک فایل / گروه فایل
    if msg.reply_to_message:
        replied_msg_id = msg.reply_to_message.message_id
        sent_mapping = context.user_data.get("sent_mapping", {})
        target = sent_mapping.get(replied_msg_id)

        if not target:
            await msg.reply_text("❌ این پیام فایلِ قابل‌شناسایی از حافظه ربات نیست.")
            return CHOOSING

        node_id = target.get("node_id")
        content_index = target.get("content_index")

        if node_id is None or content_index is None:
            await msg.reply_text("❌ اطلاعات این پیام ناقص است.")
            return CHOOSING

        if node_id not in db or "contents" not in db[node_id]:
            await msg.reply_text("❌ فایل مورد نظر در دیتابیس پیدا نشد.")
            return CHOOSING

        contents = db[node_id].get("contents", [])

        try:
            idx = int(content_index)
        except (TypeError, ValueError):
            await msg.reply_text("❌ اطلاعات این پیام نامعتبر است.")
            return CHOOSING

        if not (0 <= idx < len(contents)):
            await msg.reply_text("❌ فایل مورد نظر در دیتابیس پیدا نشد.")
            return CHOOSING

        target_item = contents[idx]
        msg_type = target_item.get("type", "text")
        media_group_id = target_item.get("media_group_id")
        groupable_types = {"photo", "video", "document", "audio"}

        matched_items = []

        # اگر عضو گروه فایل باشد، مثل handle_reply_delete همه اعضای گروه را پیدا کن
        if media_group_id and msg_type in groupable_types:
            start = idx
            while start > 0:
                prev_item = contents[start - 1]
                if (
                    prev_item.get("media_group_id") == media_group_id
                    and prev_item.get("type") in groupable_types
                ):
                    start -= 1
                else:
                    break

            end = idx
            while end + 1 < len(contents):
                next_item = contents[end + 1]
                if (
                    next_item.get("media_group_id") == media_group_id
                    and next_item.get("type") in groupable_types
                ):
                    end += 1
                else:
                    break

            for i in range(start, end + 1):
                matched_items.append((i, contents[i]))
        else:
            matched_items.append((idx, target_item))

        page_name = html.escape(db[node_id].get("name", "بدون نام"))

        # اگر فقط یک مورد بود
        if len(matched_items) == 1:
            only_index, _ = matched_items[0]
            deep_link = f"https://t.me/{bot_username}?start=file_{node_id}_{only_index}"

            text_msg = (
                f"🔗 <b>دیپ‌لینک فایل:</b> <code>{page_name}</code>\n\n"
                f"برای اشتراک‌گذاری، روی لینک زیر بزنید:\n"
                f"<code>{html.escape(deep_link)}</code>"
            )
            await msg.reply_text(text_msg, parse_mode="HTML")
            return CHOOSING

        # اگر گروه فایل بود، دیپ‌لینک همه را بده
        parts = [f"🔗 <b>دیپ‌لینک فایل‌های این گروه از صفحه:</b> <code>{page_name}</code>\n"]

        shown_count = 0
        for item_index, item in matched_items:
            item_type = item.get("type", "text")

            if item_type == "text":
                continue

            deep_link = f"https://t.me/{bot_username}?start=file_{node_id}_{item_index}"
            shown_count += 1

            parts.append(
                f"📎 <b>فایل {shown_count} ({html.escape(item_type)}):</b>\n"
                f"<code>{html.escape(deep_link)}</code>"
            )

        if shown_count == 0:
            await msg.reply_text("❌ در این گروه فایل، مورد قابل اشتراک‌گذاری پیدا نشد.")
            return CHOOSING

        text_msg = "\n\n".join(parts)
        await msg.reply_text(text_msg, parse_mode="HTML")
        return CHOOSING

    # حالت عادی: دیپ‌لینک پوشه
    node_id = context.user_data.get("current_report_node") or context.user_data.get("current_node", "root")

    if node_id not in db:
        await update.message.reply_text("❌ صفحه فعلی پیدا نشد.")
        return CHOOSING

    deep_link = f"https://t.me/{bot_username}?start={node_id}"
    page_name = html.escape(db[node_id].get("name", "بدون نام"))

    text_msg = (
        f"🔗 <b>دیپ‌لینک صفحه:</b> <code>{page_name}</code>\n\n"
        f"برای اشتراک‌گذاری، روی لینک زیر بزنید:\n"
        f"<code>{html.escape(deep_link)}</code>"
    )

    await update.message.reply_text(text_msg, parse_mode="HTML")
    return CHOOSING

async def start_chat_with_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user = update.effective_user
    user_id = user.id

    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از این بخش را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING

    # اگر گروه تنظیم نشده باشد
    if not MASSAGE_GROUP_ID:
        await update.message.reply_text("❌ گروه مدیریت تنظیم نشده است.")
        return CHOOSING

    # شروع فرایند چت
    await update.message.reply_text(
        "✉️ پیام خود را برای مدیریت ارسال کنید.\n"
        "اگر منصرف شدید /cancel را بزنید.",
        reply_markup=ReplyKeyboardRemove()
    )

    return WAITING_CHAT_MESSAGE

async def receive_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message

    # بررسی اینکه آیا کاربر می‌خواهد چت را لغو کند
    if message.text and message.text.strip() == "/cancel":
        current_node = context.user_data.get("current_node", "root")
        await message.reply_text(
            "❌ چت با مدیریت پایان یافت. به منوی اصلی بازگشتید.",
            # در صورت وجود تابع ساخت کیبورد، کیبورد منوی اصلی را اینجا قرار دهید:
            # reply_markup=get_keyboard(current_node) 
        )
        return CHOOSING

    full_name = html.escape(user.full_name or "بدون نام")
    username = user.username
    username_text = f"@{html.escape(username)}" if username else "ندارد"
    user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'

    header = (
        "📨 <b>پیام جدید برای مدیریت</b>\n\n"
        f"👤 <b>کاربر:</b> {user_link}\n"
        f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n"
        f"🔗 <b>یوزرنیم:</b> {username_text}\n\n"
        "📩 <b>محتوا:</b>"
    )

    try:
        # ۱. ارسال مشخصات کاربر به گروه
        await context.bot.send_message(
            chat_id=MASSAGE_GROUP_ID,
            text=header,
            parse_mode="HTML"
        )

        # ۲. کپی پیام کاربر (کپی کامل هرگونه فایل، عکس، ویدیو، گیف، استیکر و متن)
        await context.bot.copy_message(
            chat_id=MASSAGE_GROUP_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        await update.message.reply_text(
            "✅ پیام شما برای مدیریت ارسال شد.\n"
            "می‌توانید پیام‌های بعدی خود را بفرستید یا برای اتمام چت دستور /cancel را ارسال کنید."
        )

    except Exception as e:
        print("Failed to send chat message:", e)
        await update.message.reply_text("❌ ارسال پیام با خطا مواجه شد.")

    # بسیار مهم: کاربر در وضعیت چت باقی می‌ماند تا زمانی که /cancel بفرستد
    return WAITING_CHAT_MESSAGE


async def send_node_contents(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: str):
    """محتواهای موجود در نود فعلی را ارسال می‌کند."""
    set_report_page(context, node_id)

    db = load_db()
    contents = db.get(node_id, {}).get("contents", [])

    if not contents:
        return

    if "sent_mapping" not in context.user_data:
        context.user_data["sent_mapping"] = {}

    sent_mapping = context.user_data["sent_mapping"]

    # typeهایی که واقعاً می‌توانند داخل media_group بروند
    groupable_types = {"photo", "video", "document", "audio"}

    i = 0
    while i < len(contents):
        item = contents[i]

        try:
            msg_type = item.get("type")
            media_group_id = item.get("media_group_id")

            # تلاش برای بازسازی آلبوم فقط وقتی:
            # 1) media_group_id داشته باشیم
            # 2) type از انواع groupable باشد
            if media_group_id and msg_type in groupable_types:
                group_items = []
                group_indices = []

                j = i
                while j < len(contents):
                    next_item = contents[j]
                    next_type = next_item.get("type")

                    if (
                        next_item.get("media_group_id") == media_group_id
                        and next_type in groupable_types
                    ):
                        group_items.append(next_item)
                        group_indices.append(j)
                        j += 1
                    else:
                        break

                # اگر بیشتر از یک مورد پشت‌سرهم بود، به صورت media group بفرست
                if len(group_items) > 1:
                    media = []
                    valid_group = True
                
                    # پیدا کردن اولین caption معتبر داخل گروه
                    group_caption = None
                    group_entities = None
                
                    for gi in group_items:
                        cap = gi.get("caption")
                        if cap:
                            group_caption = cap
                            group_entities = gi.get("entities")
                            break
                
                    for idx2, group_item in enumerate(group_items):
                        input_media = build_input_media(
                            group_item,
                            is_first=(idx2 == 0),
                            forced_caption=group_caption if idx2 == 0 else None,
                            forced_entities=group_entities if idx2 == 0 else None,
                        )
                        if input_media is None:
                            valid_group = False
                            break
                        media.append(input_media)
                
                    if valid_group and media:
                        try:
                            sent_messages = await update.message.reply_media_group(media=media)
                
                            for sent, original_index in zip(sent_messages, group_indices):
                                sent_mapping[sent.message_id] = {
                                    "node_id": node_id,
                                    "content_index": original_index,
                                }
                
                            i = j
                            continue
                
                        except Exception as group_error:
                            logging.error(f"Error sending media group: {group_error}")
                
                            for group_item, original_index in zip(group_items, group_indices):
                                try:
                                    sent_msg = await send_single_content(update.message, group_item)
                                    if sent_msg:
                                        sent_mapping[sent_msg.message_id] = {
                                            "node_id": node_id,
                                            "content_index": original_index,
                                        }
                                except Exception as single_error:
                                    logging.error(f"Fallback single send failed: {single_error}")
                
                            i = j
                            continue

            # حالت عادی: ارسال تکی
            sent_msg = await send_single_content(update.message, item)

            if sent_msg:
                sent_mapping[sent_msg.message_id] = {
                    "node_id": node_id,
                    "content_index": i,
                }

        except Exception as e:
            logging.error(f"Error sending content: {e}")

        i += 1
# ==========================================
# ۱) تابع کمکی اصلاح شده برای تولید ساختار لاگ ادمین
# ==========================================

# تابع کمکی استخراج جزئیات دقیق فایل‌ها و متون برای بخش لاگ
def get_item_log_details(item, index: int, bot_username: str = None) -> str:
    msg_type = item.get("type", "text")
    if msg_type == "text":
        text_content = item.get("text", "")
        text_escaped = escape(text_content)
        preview = text_escaped[:200] + "..." if len(text_escaped) > 200 else text_escaped
        return f"📝 <b>پیام متنی {index}:</b>\n<blockquote expandable>{preview}</blockquote>"
    
    file_id = item.get("file_id", "")
    caption = item.get("caption", "")
    caption_escaped = escape(caption) if caption else "بدون کپشن"
    
    return (
        f"📎 <b>فایل {index} ({msg_type}):</b>\n"
        f"📥 متن دریافت مستقیم:\n<code>file-id:{file_id}</code>\n"
        f"🔑 شناسه فایل:\n<code>{file_id}</code>\n"
        f"✍️ کپشن: <blockquote expandable>{caption_escaped}</blockquote>"
    )

def format_admin_log(admin_user, description):
    admin_link = get_admin_link(admin_user)
    username = f"@{admin_user.username}" if admin_user.username else "بدون یوزرنیم"
    
    header = (
        f"👑 <b>گزارش تغییرات دیتابیس</b>\n"
        f"👤 ادمین: {admin_link} | 🆔: <code>{admin_user.id}</code>\n"
        f"👤 یوزرنیم: {username}\n"
        f"--------------------------\n"
    )
    return f"{header}{description}"

def split_html_message_by_lines(text: str, max_len: int = 3000) -> list:
    if not text:
        return []
    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_length = 0
    
    for line in lines:
        line_len = len(line) + 1  # طول خط به همراه کاراکتر newline
        if current_length + line_len > max_len:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_length = line_len
        else:
            current_chunk.append(line)
            current_length += line_len
            
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

def format_backup_caption(admin_user, action_type):
    admin_link = get_admin_link(admin_user)
    username = f"@{admin_user.username}" if admin_user.username else "بدون یوزرنیم"

    return (
        f"👑 <b>بکاپ دیتابیس</b>\n"
        f"👤 ادمین: {admin_link}\n"
        f"🆔: <code>{admin_user.id}</code>\n"
        f"👤 یوزرنیم: {username}\n"
        f"⚙️ نوع پروسه: <b>{action_type}</b>"
    )

def set_pending_backup_caption(context, caption):
    context.user_data["pending_backup_caption"] = caption

def pop_pending_backup_caption(context):
    return context.user_data.pop("pending_backup_caption", None)


async def handle_direct_getfile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    هندلر دریافت مستقیم فایل با متن ساده:
    file-id:FILE_ID
    """
    track_user_activity(update, count_message=True)
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text("⛔️ شما بن شده‌اید.")
        return CHOOSING

    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    logging.info(f"[direct_getfile] received text: {text[:80]}")

    if not text.startswith("file-id:"):
        return

    file_id = text[len("file-id:"):].strip()

    if not file_id:
        await update.message.reply_text("❌ شناسه فایل خالی است.")
        raise ApplicationHandlerStop

    chat_id = update.effective_chat.id
    bot = context.bot

    logging.info(f"[direct_getfile] extracted file_id: {file_id}")

    methods = [
        (bot.send_photo, "photo"),
        (bot.send_video, "video"),
        (bot.send_document, "document"),
        (bot.send_audio, "audio"),
        (bot.send_voice, "voice"),
        (bot.send_animation, "animation"),
    ]

    last_error = None

    for method, arg_name in methods:
        try:
            kwargs = {arg_name: file_id}
            await method(chat_id=chat_id, **kwargs)

            logging.info(f"[direct_getfile] sent successfully as {arg_name}")

            # خیلی مهم: نگذار هندلرهای بعدی هم همین پیام را پردازش کنند
            raise ApplicationHandlerStop

        except ApplicationHandlerStop:
            raise

        except Exception as e:
            last_error = e
            logging.warning(f"[direct_getfile] failed as {arg_name}: {e}")
            continue

    logging.error(f"[direct_getfile] all methods failed. last_error={last_error}")

    await update.message.reply_text(
        "❌ خطا: فایل یافت نشد یا شناسه نامعتبر است.\n"
        "ممکن است این file_id مربوط به نوع فایل دیگری باشد یا برای این بات قابل استفاده نباشد."
    )

    raise ApplicationHandlerStop

async def file_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=False)

    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text("⛔️ شما بن شده‌اید.")
        return CHOOSING

    msg = update.message

    if not msg.reply_to_message:
        await msg.reply_text(
            "⚠️ لطفاً این دستور را روی یکی از پیام‌های ارسال‌شده توسط ربات ریپلای کنید."
        )
        return CHOOSING

    replied_msg_id = msg.reply_to_message.message_id
    sent_mapping = context.user_data.get("sent_mapping", {})
    target = sent_mapping.get(replied_msg_id)

    if not target:
        await msg.reply_text(
            "❌ این پیام قابل شناسایی نیست.\n"
            "فقط فایل‌های آخرین پوشه‌ای که ربات برای شما ارسال کرده قابل تشخیص هستند."
        )
        return CHOOSING

    node_id = target.get("node_id")
    content_index = target.get("content_index")

    if node_id is None or content_index is None:
        await msg.reply_text("❌ اطلاعات این پیام ناقص است و قابل بررسی نیست.")
        return CHOOSING

    node_id = target.get("node_id")
    content_index = target.get("content_index")
    
    if node_id is None or content_index is None:
        await msg.reply_text("❌ اطلاعات این پیام ناقص است و قابل بررسی نیست.")
        return CHOOSING
    

    #current_node = context.user_data.get("current_node", "root")
    #if node_id != current_node:
    #    await msg.reply_text(
    #        "❌ فقط فایل‌های مربوط به پوشه فعلی قابل شناسایی هستند."
    #    )
    #    return CHOOSING

    db = load_db()

    if node_id not in db or "contents" not in db[node_id]:
        await msg.reply_text("❌ فایل مورد نظر در دیتابیس پیدا نشد.")
        return CHOOSING

    contents = db[node_id].get("contents", [])

    try:
        idx = int(content_index)
    except (TypeError, ValueError):
        await msg.reply_text("❌ اطلاعات این پیام نامعتبر است.")
        return CHOOSING

    if not (0 <= idx < len(contents)):
        await msg.reply_text("❌ فایل مورد نظر در دیتابیس پیدا نشد.")
        return CHOOSING

    target_item = contents[idx]
    msg_type = target_item.get("type", "text")

    # اگر پیام متنی باشد
    if msg_type == "text":
        await msg.reply_text(
            "📝 این پیام متنی است و از طرف تلگرام file_id ندارد."
        )
        return CHOOSING

    media_group_id = target_item.get("media_group_id")
    groupable_types = {"photo", "video", "document", "audio"}

    matched_items = []

    # اگر عضو آلبوم/گروه رسانه‌ای باشد، مثل handle_reply_delete کل گروه را پیدا کن
    if media_group_id and msg_type in groupable_types:
        start = idx
        while start > 0:
            prev_item = contents[start - 1]
            if (
                prev_item.get("media_group_id") == media_group_id
                and prev_item.get("type") in groupable_types
            ):
                start -= 1
            else:
                break

        end = idx
        while end + 1 < len(contents):
            next_item = contents[end + 1]
            if (
                next_item.get("media_group_id") == media_group_id
                and next_item.get("type") in groupable_types
            ):
                end += 1
            else:
                break

        matched_items = contents[start:end + 1]
    else:
        matched_items = [target_item]

    valid_items = []
    text_count = 0
    no_id_count = 0

    for item in matched_items:
        item_type = item.get("type", "text")

        if item_type == "text":
            text_count += 1
            continue

        file_id = (item.get("file_id") or "").strip()
        if not file_id:
            no_id_count += 1
            continue

        valid_items.append({
            "type": item_type,
            "file_id": file_id,
        })

    if not valid_items:
        if len(matched_items) > 1:
            await msg.reply_text(
                "❌ در این گروه فایل، هیچ شناسه فایل معتبری پیدا نشد."
            )
        else:
            await msg.reply_text(
                "❌ شناسه فایل در دیتابیس پیدا نشد."
            )
        return CHOOSING

    page_name = html.escape(db[node_id].get("name", "بدون نام"))

    if len(valid_items) == 1:
        file_id = html.escape(valid_items[0]["file_id"])
        text_msg = (
            f"🆔 <b>کد اختصاصی فایل از صفحه {page_name}:</b>\n\n"
            f"📥 متن کادر <code>file-id:...</code> را کپی کرده و برای ربات ارسال کنید تا فایل مربوطه را دریافت کنید.\n\n"
            f"📥 متن دریافت مستقیم از ربات:\n"
            f"<code>file-id:{file_id}</code>\n\n"
            f"🔑 شناسه فایل تلگرام:\n"
            f"<code>{file_id}</code>"
        )
    else:
        parts = [
            f"🆔 <b>کد اختصاصی فایل‌های این گروه از صفحه {page_name}:</b>\n\n"
            "📥 متن کادر <code>file-id:...</code> را کپی کرده و برای ربات ارسال کنید تا فایل مربوطه را دریافت کنید.\n"
        ]
        for i, item in enumerate(valid_items, start=1):
            escaped_file_id = html.escape(item["file_id"])
            parts.append(
                f"📎 <b>فایل {i} ({html.escape(item['type'])}):</b>\n"
                f"📥 متن دریافت مستقیم از ربات:\n"
                f"<code>file-id:{escaped_file_id}</code>\n"
                f"🔑 شناسه فایل تلگرام:\n"
                f"<code>{escaped_file_id}</code>"
            )

        if text_count > 0:
            parts.append(f"📝 {text_count} پیام متنی در این گروه بود که file_id ندارد.")

        if no_id_count > 0:
            parts.append(f"⚠️ برای {no_id_count} مورد شناسه فایل ذخیره نشده بود.")

        text_msg = "\n\n".join(parts)

    await msg.reply_text(text_msg, parse_mode="HTML")
    return CHOOSING




# ==========================================
# ۴) اصلاح تابع handle_reply_delete (بروزرسانی لاگ‌ها با bot_username برای ساخت لینک صحیح)
# ==========================================
async def handle_reply_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    if not is_admin:
        return

    if not msg.reply_to_message:
        await msg.reply_text("⚠️ لطفاً این دستور را روی یکی از پیام‌های ارسالی ربات ریپلای کنید.")
        return

    target_msg_id = msg.reply_to_message.message_id
    mapping = context.user_data.get("sent_mapping", {}).get(target_msg_id)

    if not mapping:
        await msg.reply_text("⚠️ این فایل در حافظه موقت ربات پیدا نشد.")
        return

    db = load_db()
    node_id = mapping.get("node_id")
    idx = mapping.get("content_index")
    
    if node_id is None or idx is None:
        await msg.reply_text("⚠️ اطلاعات این فایل در حافظه موقت ناقص است.")
        return
    
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        await msg.reply_text("⚠️ اطلاعات این فایل در حافظه موقت نامعتبر است.")
        return
    

    if node_id not in db or "contents" not in db[node_id]:
        await msg.reply_text("⚠️ خطا در دسترسی به اطلاعات فایل در دیتابیس.")
        return

    contents = db[node_id]["contents"]
    if not (0 <= idx < len(contents)):
        await msg.reply_text("⚠️ خطا در دسترسی به اطلاعات فایل در دیتابیس.")
        return

    push_admin_history(context, db)

    bot_username = context.bot.username
    node_name = db[node_id]["name"]
    node_link = get_link(node_id, node_name, bot_username)

    target_item = contents[idx]
    media_group_id = target_item.get("media_group_id")
    groupable_types = {"photo", "video", "document", "audio"}

    removed_items = []

    if media_group_id and target_item.get("type") in groupable_types:
        start = idx
        while start > 0:
            prev_item = contents[start - 1]
            if (
                prev_item.get("media_group_id") == media_group_id
                and prev_item.get("type") in groupable_types
            ):
                start -= 1
            else:
                break

        end = idx
        while end + 1 < len(contents):
            next_item = contents[end + 1]
            if (
                next_item.get("media_group_id") == media_group_id
                and next_item.get("type") in groupable_types
            ):
                end += 1
            else:
                break

        removed_items = contents[start:end + 1]
        del contents[start:end + 1]

        # ساخت لاگ حذف گروه رسانه‌ای
        log_desc_parts = [f"🗑 <b>حذف شدن فایل ها (آلبوم) از پوشه {node_link}:</b>\n"]
        for i, r_item in enumerate(removed_items, start=1):
            log_desc_parts.append(get_item_log_details(r_item, i, bot_username))
        desc = "\n\n".join(log_desc_parts)
        removed_count = len(removed_items)
    else:
        removed_item = contents.pop(idx)
        removed_items.append(removed_item)
        
        # ساخت لاگ حذف تک محتوا
        desc = f"🗑 <b>حذف شدن فایل ها (تک مورد) از پوشه {node_link}:</b>\n\n" + get_item_log_details(removed_item, 1, bot_username)
        removed_count = 1

    log_caption = format_admin_log(update.effective_user, desc)
    backup_caption = format_backup_caption(update.effective_user, "حذف فایل/محتوا")
    
    set_pending_caption(context, log_caption)
    set_pending_backup_caption(context, backup_caption)
    
    save_db(db, context=context)
    

    # پاک‌کردن مپینگ این نود به دلیل تغییر ایندکس‌ها
    context.user_data["sent_mapping"] = {
        k: v
        for k, v in context.user_data.get("sent_mapping", {}).items()
        if v["node_id"] != node_id
    }

    if removed_count > 1:
        await msg.reply_text(f"✅ {removed_count} مورد با موفقیت از دیتابیس این پوشه حذف شد.")
    else:
        await msg.reply_text("✅ فایل با موفقیت از دیتابیس این پوشه حذف شد.")


async def handle_reply_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    if not is_admin:
        return

    if not msg.reply_to_message:
        await msg.reply_text("⚠️ لطفاً این دستور را روی یکی از پیام‌های ارسالی ربات ریپلای کنید.")
        return

    target_msg_id = msg.reply_to_message.message_id
    mapping = context.user_data.get("sent_mapping", {}).get(target_msg_id)

    if not mapping:
        await msg.reply_text("⚠️ این فایل در حافظه موقت ربات پیدا نشد.")
        return

    db = load_db()
    node_id = mapping.get("node_id")
    idx = mapping.get("content_index")
    
    if node_id is None or idx is None:
        await msg.reply_text("⚠️ اطلاعات این فایل در حافظه موقت ناقص است.")
        return CHOOSING
    
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        await msg.reply_text("⚠️ اطلاعات این فایل در حافظه موقت نامعتبر است.")
        return CHOOSING
    

    replace_start = idx
    replace_count = 1

    if node_id in db and "contents" in db[node_id] and 0 <= idx < len(db[node_id]["contents"]):
        contents = db[node_id]["contents"]
        target_item = contents[idx]
        media_group_id = target_item.get("media_group_id")
        groupable_types = {"photo", "video", "document", "audio"}

        if media_group_id and target_item.get("type") in groupable_types:
            start = idx
            while start > 0:
                prev_item = contents[start - 1]
                if (
                    prev_item.get("media_group_id") == media_group_id
                    and prev_item.get("type") in groupable_types
                ):
                    start -= 1
                else:
                    break

            end = idx
            while end + 1 < len(contents):
                next_item = contents[end + 1]
                if (
                    next_item.get("media_group_id") == media_group_id
                    and next_item.get("type") in groupable_types
                ):
                    end += 1
                else:
                    break

            replace_start = start
            replace_count = end - start + 1

    context.user_data["change_target"] = {
        "node_id": node_id,
        "content_index": replace_start,
        "replace_count": replace_count,
    }

    context.user_data["temp_content"] = []

    await msg.reply_text(
        "🔄 فایل یا فایل‌های جدیدی که می‌خواهید جایگزین این مورد شوند را ارسال کنید.\n"
        "پس از اتمام، دکمه '✅ ثبت نهایی' را بزنید.",
        reply_markup=ReplyKeyboardMarkup([["✅ ثبت نهایی", "❌ لغو"]], resize_keyboard=True)
    )

    return WAITING_CONTENT


def get_subtree_db(db, root_node_id):
    subtree = {}

    def build_search_context(node_id):
        parts = []
        current = node_id

        while current and current in db:
            if current != "root":
                parts.append(db[current].get("name", ""))
            current = db[current].get("parent")

        parts.reverse()
        return " ".join(parts)

    def add_node_recursive(node_id):
        if node_id not in db:
            return

        node = copy.deepcopy(db[node_id])

        search_context = build_search_context(node_id)

        # متن جستجو را مستقیماً داخل name قرار می‌دهیم
        node["name"] = search_context

        subtree[node_id] = node

        for child in db[node_id].get("children", []):
            add_node_recursive(child)

    add_node_recursive(root_node_id)
    return subtree

async def handle_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, is_admin: bool):
    full_db = load_db()
    current_node = context.user_data.get("current_node", "root")
    
    # محدود کردن جستجو فقط به زیرشاخه فعلی
    subtree_db = get_subtree_db(full_db, current_node)
    
    # جستجو در زیرشاخه
    results = smart_search(subtree_db, text, limit=5, min_score=45)

    help_text = (
        "💡 برای خاموش یا روشن کردن جستجوی هوشمند، از دستور /on_of_search استفاده کنید."
    )

    if not results:
        await update.message.reply_text(
            f"""🔍 نتیجه‌ای در این پوشه یافت نشد.
        
        ⚠️ توجه!
        فقط مسیرهای موجود در پوشه فعلی جستجو می‌شوند.
        برای جستجوی کل کتابخانه، ابتدا به صفحه اصلی بروید.
        
        {help_text}""",
            parse_mode="HTML"
        )
        return CHOOSING

    bot_username = context.bot.username
    msg = "🔍 نتایج یافت شده در این پوشه:\n\n"

    for item in results:
        node_id = item["node_id"]
        # دریافت مسیر کامل از دیتابیس اصلی
        path_text = get_node_path_text(full_db, node_id)
        
        # ساخت دیپ‌لینک
        deep_link = f"https://t.me/{bot_username}?start={node_id}"
        
        # فرمت‌دهی با لینک HTML (قابل کلیک)
        msg += f"📂 <a href='{deep_link}'>{path_text}</a>\n"
        msg += f"درصد تطابق: {int(item['score'])}٪\n\n"

    msg += " 🪄 روی مسیر آبی‌رنگ کلیک کنید تا مستقیم به آنجا بروید. \n"
    msg += help_text

    await update.message.reply_text(
        msg, 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )

    return CHOOSING

async def toggle_smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return CHOOSING
    
    user_id = str(user.id)
    userdata = load_userdata()
    users = userdata.setdefault("users", {})
    
    # اگر کاربر در دیتابیس نبود، ابتدا او را ثبت یا داده‌ی پیش‌فرض می‌گذاریم
    if user_id not in users:
        # برای ثبت مشخصات اولیه
        track_user_activity(update, count_message=False)
        userdata = load_userdata()  # بازخوانی دیتای جدید
        users = userdata.get("users", {})

    # خواندن وضعیت (اگر مقدار نبود، پیش‌فرض False است؛ یعنی سرچ هوشمند فعال/روشن است)
    is_disabled = users.get(user_id, {}).get("smart_search_disabled", False)
    
    # تغییر وضعیت (معکوس کردن)
    new_disabled_status = not is_disabled
    users[user_id]["smart_search_disabled"] = new_disabled_status
    
    # ذخیره در فایل
    save_userdata(userdata, upload=True)
    
    # پیام به کاربر بر اساس وضعیت جدید
    if new_disabled_status:
        await update.message.reply_text("🔴 سرچ هوشمند برای شما <b>خاموش</b> شد.", parse_mode="HTML")
    else:
        await update.message.reply_text("🟢 سرچ هوشمند برای شما مجدداً <b>روشن</b> شد.", parse_mode="HTML")
        
    return CHOOSING

# --- HANDLERS ---
async def not_started(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    text = update.message.text
    # ✅ اگر start هست (با payload یا بدون payload)، دخالت نکن
    if text.startswith("/start"):
        return
    # اگر قبلاً استارت کرده، دخالت نکن
    if "current_node" in context.user_data:
        return
    await update.message.reply_text(
        "♻️ ربات بروزرسانی شده است.\n"
        "برای ادامه لطفاً دستور /start را بزنید."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=True)
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    # پاک‌سازی کامل وضعیت قبلی
    #old_current_node = context.user_data.get("current_node", "root")

    # فقط داده‌های موقتی پاک شوند
    context.user_data.pop("temp_content", None)
    context.user_data.pop("change_target", None)
    context.user_data.pop("current_report_node", None)

    db = load_db()

    args = context.args  # 👈 payload اینجاست
    # اگر دیپ‌لینک فایل باشد
    if args:
        payload = args[0]

        node_id = None
        content_index = None

        # لینک قدیمی: file__{node_id}__{index}
        if payload.startswith("file__"):
            parts = payload.split("__", 2)
            if len(parts) == 3:
                _, node_id, index_str = parts
                try:
                    content_index = int(index_str)
                except ValueError:
                    node_id = None
                    content_index = None

        # لینک جدید: file_{node_id}_{index}
        elif payload.startswith("file_"):
            raw = payload[len("file_"):]
            try:
                node_id, index_str = raw.rsplit("_", 1)
                content_index = int(index_str)
            except ValueError:
                node_id = None
                content_index = None

        if node_id is not None and content_index is not None:
            if node_id in db:
                contents = db[node_id].get("contents", [])
                if 0 <= content_index < len(contents):
                    bot_username = context.bot.username
                    path_text = get_node_path_html(db, node_id, bot_username)

                    current_node = context.user_data.get("current_node", "root")

                    await update.message.reply_text(
                        f"📂 مسیر فایل:\n{path_text}",
                        reply_markup=get_keyboard(current_node, is_admin, user_id=user_id),
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )

                    item = contents[content_index]
                    try:
                        msg_type = item["type"]
                        saved_entities = item.get("entities")

                        if msg_type == "text":
                            if saved_entities is not None:
                                await update.message.reply_text(
                                    text=item["text"],
                                    entities=saved_entities
                                )
                            else:
                                await update.message.reply_text(
                                    text=item["text"],
                                    parse_mode="HTML"
                                )
                        else:
                            file_id = item["file_id"]
                            caption = item.get("caption", "")

                            send_args = {"caption": caption}
                            if saved_entities is not None:
                                send_args["caption_entities"] = saved_entities
                            else:
                                send_args["parse_mode"] = "HTML"

                            if msg_type == "photo":
                                await update.message.reply_photo(photo=file_id, **send_args)
                            elif msg_type == "video":
                                await update.message.reply_video(video=file_id, **send_args)
                            elif msg_type == "document":
                                await update.message.reply_document(document=file_id, **send_args)
                            elif msg_type == "audio":
                                await update.message.reply_audio(audio=file_id, **send_args)
                            elif msg_type == "voice":
                                await update.message.reply_voice(voice=file_id, **send_args)

                    except Exception as e:
                        logging.error(f"Error sending deeplink file: {e}")
                        await update.message.reply_text("❌ خطا در ارسال فایل.")

                    if "current_node" not in context.user_data:
                        context.user_data["current_node"] = "root"

                    return CHOOSING

            await update.message.reply_text("❌ لینک فایل نامعتبر است.")
            return CHOOSING

    # 🔗 اگر start با هش اومده
    if args:
        target_id = args[0]

        if target_id in db:
            set_report_page(context, target_id)
            target_node = db[target_id]
            has_children = bool(target_node.get("children"))

            # 👤 کاربر عادی + نود بدون فرزند => فقط محتوا نمایش بده
            if not is_admin and not has_children:
                parent_id = target_node.get("parent") or "root"
                context.user_data["current_node"] = parent_id
                bot_username = context.bot.username
                path_text = get_node_path_html(db, target_id, bot_username)

                await update.message.reply_text(
                    f"📂 مسیر:\n{path_text}",
                    reply_markup=get_keyboard(parent_id, is_admin, user_id=user_id),
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )

                await send_node_contents(update, context, target_id)
                return CHOOSING

            # 👑 ادمین، یا نودی که فرزند دارد => خود پوشه باز شود
            context.user_data["current_node"] = target_id
            bot_username = context.bot.username
            path_text = get_node_path_html(db, target_id, bot_username)

            await update.message.reply_text(
                f"📂 مسیر:\n{path_text}",
                reply_markup=get_keyboard(target_id, is_admin, user_id=user_id),
                parse_mode="HTML",
                disable_web_page_preview=True
            )

            await send_node_contents(update, context, target_id)
            return CHOOSING

    # 🏠 start عادی
    context.user_data["current_node"] = "root"
    set_report_page(context, "root")
    
    await send_start_page(update, context)
    return CHOOSING

def get_start_page_contents():
    userdata = load_userdata()
    return userdata.get("start_page_contents", [])


def save_start_page_contents(contents):
    userdata = load_userdata()
    userdata["start_page_contents"] = contents
    save_userdata(userdata)


async def send_start_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    contents = userdata.get("start_page_contents", [])
    root_keyboard = get_keyboard("root", is_admin, user_id=user_id)

    # 1. اگر هیچ محتوایی تنظیم نشده بود، فقط پیام پیش‌فرض به همراه کیبورد را بفرست
    if not contents:
        await update.message.reply_text(
            DEFAULT_START_TEXT,
            reply_markup=root_keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    # 2. اگر محتوا وجود داشت، آخرین آیتم معتبر را پیدا کن تا کیبورد روی آن ست شود
    valid_items = [
        item for item in contents 
        if item.get("type") in ("text", "photo", "video", "document", "audio", "voice")
    ]
    
    if not valid_items:
        # اگر لیست محتوا پر بود ولی فرمت‌های داخل آن نامعتبر بودند، به عنوان فال‌بک پیام پیش‌فرض را بفرست
        await update.message.reply_text(
            DEFAULT_START_TEXT,
            reply_markup=root_keyboard,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return

    last_index = len(valid_items) - 1

    # ارسال تمامی آیتم‌ها به ترتیب
    for i, item in enumerate(valid_items):
        try:
            msg_type = item["type"]
            saved_entities = item.get("entities")
            
            # فقط و فقط روی آخرین پیام ارسالی، کیبورد اصلی (root) را الصاق کن
            reply_markup_to_use = root_keyboard if i == last_index else None

            if msg_type == "text":
                kwargs = {"disable_web_page_preview": True}
                if reply_markup_to_use is not None:
                    kwargs["reply_markup"] = reply_markup_to_use

                if saved_entities is not None:
                    kwargs["entities"] = saved_entities
                    await update.message.reply_text(text=item["text"], **kwargs)
                else:
                    kwargs["parse_mode"] = "HTML"
                    await update.message.reply_text(text=item["text"], **kwargs)
            else:
                file_id = item["file_id"]
                caption = item.get("caption", "")
                send_args = {"caption": caption}

                if reply_markup_to_use is not None:
                    send_args["reply_markup"] = reply_markup_to_use

                if saved_entities is not None:
                    send_args["caption_entities"] = saved_entities
                else:
                    send_args["parse_mode"] = "HTML"

                if msg_type == "photo":
                    await update.message.reply_photo(photo=file_id, **send_args)
                elif msg_type == "video":
                    await update.message.reply_video(video=file_id, **send_args)
                elif msg_type == "document":
                    await update.message.reply_document(document=file_id, **send_args)
                elif msg_type == "audio":
                    await update.message.reply_audio(audio=file_id, **send_args)
                elif msg_type == "voice":
                    await update.message.reply_voice(voice=file_id, **send_args)

        except Exception as e:
            logging.error(f"Error sending start page content index {i}: {e}")


async def receive_start_page_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "start_page_buffer" not in context.user_data:
        context.user_data["start_page_buffer"] = []

    item = extract_message_content(update.message)

    if not item:
        await update.message.reply_text(
            "⚠️ این نوع پیام پشتیبانی نمی‌شود. فقط متن، عکس، ویدیو، فایل، صوت و ویس بفرستید.",
            reply_markup=get_start_page_edit_inline_keyboard()
        )
        return WAITING_START_PAGE_CONTENT

    context.user_data["start_page_buffer"].append(item)

    await update.message.reply_text(
        "📥 دریافت شد. اگر محتوای دیگری هم دارید بفرستید، وگرنه روی «✅ ثبت» بزنید.",
        reply_markup=get_start_page_edit_inline_keyboard()
    )

    return WAITING_START_PAGE_CONTENT


async def inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    user_id = query.from_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])

    # ----  عمومی ---- ---- ----

    if data == "reply_to_admin":
        await query.message.reply_text("📝 پیام خود را بنویسید تا برای مدیریت ارسال شود:")
        context.user_data["waiting_for_user_reply"] = True
        return CHOOSING
    
    # ---- مخصوص ادمین ---- ---- ----
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    if not is_admin:
        await query.answer("⛔️ شما دسترسی ادمین ندارید.", show_alert=True)
        return CHOOSING

    # ---------------- پنل اصلی ادمین ----------------
    if data == "admin_access":
        context.user_data["admin_panel"] = "access"

        await query.message.edit_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )
        return CHOOSING

    # ---------------  پیام استارت ----------------
    if data == "admin_edit_start_page":
        context.user_data["start_page_buffer"] = []
        context.user_data["admin_panel"] = "edit_start_page"

        await query.message.reply_text(
            "🕊 محتوای جدید پیام استارت را بفرستید.\n"
            "می‌توانید چند پیام، عکس، ویدیو، فایل، صوت یا ویس بفرستید.\n"
            "وقتی تمام شد، روی «✅ ثبت» بزنید.\n"
            "اگر منصرف شدید، «❌ لغو» را بزنید.",
            reply_markup=get_start_page_edit_inline_keyboard()
        )

        return WAITING_START_PAGE_CONTENT

    if data == "admin_save_start_page":
        buffer = context.user_data.get("start_page_buffer", [])

        if not buffer:
            await query.answer("⚠️ هنوز هیچ محتوایی ارسال نشده.", show_alert=True)
            return WAITING_START_PAGE_CONTENT

        save_start_page_contents(buffer)

        context.user_data.pop("start_page_buffer", None)
        context.user_data["admin_panel"] = "access"

        await query.message.reply_text(
            "✅ پیام استارت با موفقیت به‌روزرسانی شد."
        )

        await query.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )

        return CHOOSING

    if data == "admin_cancel_start_page":
        context.user_data.pop("start_page_buffer", None)
        context.user_data["admin_panel"] = "access"

        await query.message.reply_text("❌ ویرایش پیام استارت لغو شد.")

        await query.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )

        return CHOOSING

    # ---------------- مدیریت ادمین‌ها ----------------
    if data == "admin_mgmt":
        context.user_data["admin_panel"] = "admin_mgmt"

        await query.message.edit_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
        return CHOOSING

    # ---------------- بازگشت به پنل اصلی ----------------
    if data == "admin_back_access":
        await remove_temp_reply_keyboard_from_callback(query)
    
        context.user_data["admin_panel"] = "access"
    
        await query.message.edit_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )
        return CHOOSING
    
    # ---------------- بستن پنل ----------------
    if data == "admin_close":
        context.user_data.pop("admin_panel", None)
    
        current = context.user_data.get("current_node", "root")
    
        try:
            await query.message.delete()
        except:
            await query.message.edit_text("✅ پنل بسته شد.")
    
        await query.message.reply_text(
            "✅ پنل بسته شد.",
            reply_markup=get_keyboard(current, is_admin, user_id=update.effective_user.id)
        )
    
        return CHOOSING
    
    # ---------------- دریافت userdata ----------------
    if data == "admin_get_userdata":
        userdata = load_userdata()

        json_bytes = json.dumps(
            userdata,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8")

        zip_buffer = iolib.BytesIO()

        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr("userdata.json", json_bytes)

        zip_buffer.seek(0)

        await query.message.reply_document(
            document=zip_buffer,
            filename=".userdata.zip",
            caption="📦 بکاپ userdata"
        )

        return CHOOSING

    # ---------------- وارد کردن userdata ----------------
    if data == "admin_import_userdata":
        context.user_data["admin_waiting_from"] = "access"
    
        await query.message.reply_text(
            "📥 فایل .userdata.zip را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
    
        return WAITING_USERDATA_UPLOAD
    
    # ---------------- نمایش رمز ادمینی ----------------
    if data == "admin_password":
        admin_pass = userdata.get("admin_password", "تعریف نشده")

        await query.message.edit_text(
            f"🔐 رمز ادمینی فعلی:\n\n<code>{admin_pass}</code>",
            parse_mode="HTML",
            reply_markup=get_admin_password_inline_keyboard()
        )

        return CHOOSING

    # ---------------- ویرایش رمز ادمینی ----------------
    if data == "admin_edit_password":
        context.user_data["admin_waiting_from"] = "admin_mgmt"
    
        await query.message.reply_text(
            "✏️ رمز جدید ادمینی را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )

        return WAITING_ADMIN_PASSWORD_EDIT

    # ---------------- افزودن ادمین ----------------
    if data == "admin_add_sub":
        context.user_data["admin_waiting_from"] = "admin_mgmt"
    
        await query.message.reply_text(
            "📝 آیدی عددی فرد مورد نظر را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
    
        return WAITING_ADD_ADMIN

    # ---------------- حذف ادمین ----------------
    if data == "admin_remove_sub":
        context.user_data["admin_waiting_from"] = "admin_mgmt"
    
        await query.message.reply_text(
            "📝 آیدی عددی ادمینی که می‌خواهید حذف کنید را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
    
        return WAITING_REMOVE_ADMIN

    # ---------------- مدیریت کاربران ----------------
    if data == "admin_users":
        await remove_temp_reply_keyboard_from_callback(query)
    
        context.user_data["admin_panel"] = "users"
    
        await query.message.edit_text(
            "👥 مدیریت کاربران:",
            reply_markup=get_user_mgmt_inline_keyboard()
        )
        return CHOOSING
    
    # ---------------- لیست کاربران + صفحه‌بندی ----------------
    if data == "admin_users_list" or data.startswith("admin_users_list_page_"):
        return await list_users_inline(update, context)

    # ---------------- پنل بن کاربران ----------------
    if data == "admin_users_ban":
        return await show_ban_users_page(update, context, page=0)

    # ---------------- پنل خارج کردن از بن ----------------
    if data == "admin_users_unban":
        return await show_unban_users_page(update, context, page=0)

    # ---------------- صفحه‌بندی بن ----------------
    if data.startswith("admin_ban_page_"):
        page = int(data.split("_")[-1])
        return await show_ban_users_page(update, context, page=page)

    # ---------------- صفحه‌بندی آن‌بن ----------------
    if data.startswith("admin_unban_page_"):
        page = int(data.split("_")[-1])
        return await show_unban_users_page(update, context, page=page)

    # ---------------- انتخاب کاربر برای بن ----------------
    if data.startswith("admin_ban_pick_"):
        target_user_id = int(data.split("_")[-1])
        ok, message = await ban_user_by_id(target_user_id, context)
    
        await query.message.reply_text(
            message,
            parse_mode="HTML"
        )
    
        await remove_temp_reply_keyboard_from_callback(query)
    
        await query.message.edit_text(
            "👥 مدیریت کاربران:",
            reply_markup=get_user_mgmt_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "users"
        return CHOOSING
    
    # ---------------- انتخاب کاربر برای خارج کردن از بن ----------------
    if data.startswith("admin_unban_pick_"):
        target_user_id = int(data.split("_")[-1])
        ok, message = await unban_user_by_id(target_user_id, context)
    
        await query.message.reply_text(
            message,
            parse_mode="HTML"
        )
    
        await remove_temp_reply_keyboard_from_callback(query)
    
        await query.message.edit_text(
            "👥 مدیریت کاربران:",
            reply_markup=get_user_mgmt_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "users"
        return CHOOSING

    # ---------------- مدیریت پیام به کاربران ----------------
    if data == "admin_users_message":
        keyboard = [
            [InlineKeyboardButton("📢 پیام به همه کاربران", callback_data="admin_msg_all")],
            [InlineKeyboardButton("👤 پیام به کاربر خاص", callback_data="admin_msg_pick_page_0")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")]
        ]
        await query.message.edit_text("📧 یکی از گزینه‌های ارسال پیام را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
        return CHOOSING

    # ---------------- ارسال به همه ----------------
    if data == "admin_msg_all":
        await query.message.reply_text(
            "📢 لطفاً پیام خود را ارسال کنید (متن، عکس، ویدیو، گیف و...).\nپس از اتمام، دکمه «تایید و ارسال» را بزنید.",
            reply_markup=ReplyKeyboardMarkup([["✅ تایید و ارسال عمومی"], ["❌ لغو"]], resize_keyboard=True)
        )
        context.user_data["broadcast_messages"] = [] # لیستی از پیام‌ها برای ارسال تکی یا مولتی
        return WAITING_BROADCAST_CONTENT

    # ---------------- انتخاب کاربر برای پیام ----------------
    if data.startswith("admin_msg_pick_page_"):
        page = int(data.split("_")[-1])
        # تابعی شبیه لیست کاربران ولی با دکمه‌های انتخاب
        return await show_msg_users_pick_page(update, context, page)

    if data.startswith("admin_send_msg_to_"):
        return await handle_user_id_input(update, context)

    # ---------------- لیست ادمین‌ها ----------------
    if data == "admin_list":
        return await list_admins_inline(update, context)

    return CHOOSING

async def receive_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=get_keyboard("root", True, user_id=update.effective_user.id))
        return CHOOSING
    
    if text == "✅ تایید و ارسال عمومی" or text == "✅ تایید و ارسال به کاربر":
        messages = context.user_data.get("broadcast_messages", [])
        if not messages:
            await update.message.reply_text("⚠️ شما هیچ پیامی برای ارسال نفرستاده‌اید!")
            return 
        
        target_mode = "all" if text == "✅ تایید و ارسال عمومی" else context.user_data.get("msg_target_id")
        userdata = load_userdata()
        targets = userdata.get("users", {}).keys() if target_mode == "all" else [target_mode]

        # دکمه پاسخ به ادمین
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ پاسخ به ادمین", callback_data="reply_to_admin")]])

        count = 0
        for uid in targets:
            try:
                await context.bot.send_message(chat_id=uid, text="🔔 <b>پیام از طرف ادمین:</b>", parse_mode="HTML")
                for msg in messages:
                    await context.bot.copy_message(chat_id=uid, from_chat_id=msg.chat_id, message_id=msg.message_id, reply_markup=reply_markup)
                count += 1
            except: continue
        
        await update.message.reply_text(f"✅ پیام شما با موفقیت به {count} کاربر ارسال شد.", reply_markup=get_keyboard("root", True, user_id=update.effective_user.id))
        return CHOOSING

    # ذخیره پیام برای ارسال انبوه
    context.user_data["broadcast_messages"].append(update.message)
    await update.message.reply_text("📥 پیام دریافت شد. می‌توانید پیام‌های بیشتری بفرستید یا تایید را بزنید.")
    return context.user_data.get("current_state") # ماندن در همان وضعیت

async def show_msg_users_pick_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    query = update.callback_query

    users_list = get_sorted_users_for_management(filter_mode="all")

    if not users_list:
        await query.message.edit_text(
            "📭 هیچ کاربری برای ارسال پیام وجود ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users_message")]
            ])
        )
        return CHOOSING

    page_size = 8
    total_pages = (len(users_list) + page_size - 1) // page_size

    if page < 0:
        page = 0

    if page >= total_pages:
        page = total_pages - 1

    start = page * page_size
    end = start + page_size
    page_users = users_list[start:end]

    msg = "✉️ انتخاب کاربر برای ارسال پیام:\n\n"
    msg += "روی دکمه نام کاربر بزنید یا آیدی عددی او را ارسال کنید.\n\n"
    msg += "نام | آیدی | تعداد دستور\n"
    msg += "━━━━━━━━━━━━━━\n"

    for user in page_users:
        uid = user["id"]
        name = html.escape(str(user.get("full_name", "بدون نام")))
        username = user.get("username")
        count = user.get("message_count", 0)

        if username:
            safe_username = html.escape(str(username).lstrip("@"))
            name_link = f'<a href="https://t.me/{safe_username}">{name}</a>'
        else:
            name_link = f'<a href="tg://user?id={uid}">{name}</a>'

        msg += f"{name_link} | <code>{uid}</code> | {count}\n"

    msg += f"\n📄 صفحه {page + 1} از {total_pages}"

    await query.message.edit_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=build_user_action_keyboard(
            users_list,
            action="send_msg",
            page=page,
            page_size=page_size
        )
    )

    await query.message.reply_text(
        "📝 اگر خواستی دستی انتخاب کنی، آیدی عددی کاربر را بفرست.\nیا روی یکی از دکمه‌های بالا بزن.",
        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
    )

    context.user_data["admin_panel"] = "users"

    return WAITING_PICK_USER_FOR_MSG

async def handle_user_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # حالت ۱: انتخاب با دکمه اینلاین
    if update.callback_query:
        query = update.callback_query
        await query.answer()

        data = query.data

        if not data.startswith("admin_send_msg_to_"):
            return CHOOSING

        target_id = data.split("_")[-1]

        userdata = load_userdata()
        users = userdata.get("users", {})

        if str(target_id) not in users:
            await query.message.reply_text("❌ این کاربر در لیست کاربران ثبت نشده است.")
            return WAITING_PICK_USER_FOR_MSG

        context.user_data["msg_target_id"] = str(target_id)
        context.user_data["broadcast_messages"] = []

        await query.message.reply_text(
            f"👤 کاربر <code>{target_id}</code> انتخاب شد.\n\n"
            f"حالا پیام خود را برای او ارسال کنید.\n"
            f"پس از اتمام، دکمه «✅ تایید و ارسال به کاربر» را بزنید.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                [["✅ تایید و ارسال به کاربر"], ["❌ لغو"]],
                resize_keyboard=True
            )
        )

        return WAITING_SINGLE_USER_CONTENT

    # حالت ۲: وارد کردن آیدی عددی دستی
    text = update.message.text.strip()

    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "📧 یکی از گزینه‌های ارسال پیام را انتخاب کنید:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 پیام به همه کاربران", callback_data="admin_msg_all")],
                [InlineKeyboardButton("👤 پیام به کاربر خاص", callback_data="admin_msg_pick_page_0")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")]
            ])
        )

        return CHOOSING

    target_user_id = ensure_numeric_id(text)

    if target_user_id is None:
        await update.message.reply_text("❌ فقط آیدی عددی معتبر بفرستید یا روی دکمه‌های اینلاین بزنید.")
        return WAITING_PICK_USER_FOR_MSG

    userdata = load_userdata()
    users = userdata.get("users", {})

    if str(target_user_id) not in users:
        await update.message.reply_text("❌ این کاربر در لیست کاربران ثبت نشده است.")
        return WAITING_PICK_USER_FOR_MSG

    context.user_data["msg_target_id"] = str(target_user_id)
    context.user_data["broadcast_messages"] = []

    await update.message.reply_text(
        f"👤 کاربر <code>{target_user_id}</code> انتخاب شد.\n\n"
        f"حالا پیام خود را برای او ارسال کنید.\n"
        f"پس از اتمام، دکمه «✅ تایید و ارسال به کاربر» را بزنید.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            [["✅ تایید و ارسال به کاربر"], ["❌ لغو"]],
            resize_keyboard=True
        )
    )

    return WAITING_SINGLE_USER_CONTENT

async def list_admins_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    buttons_count = userdata.get("sub_admins_buttons", {})

    main_admins = [int(x) for x in ADMIN_IDS]
    sub_admins = [int(x) for x in sub_admins]

    msg = "👑 ادمین‌های اصلی:\n\n"

    sorted_main_admins = sorted(
        main_admins,
        key=lambda x: buttons_count.get(str(x), 0),
        reverse=True
    )

    for aid in sorted_main_admins:
        count = buttons_count.get(str(aid), 0)

        try:
            chat = await context.bot.get_chat(aid)
            name = chat.full_name
        except Exception:
            name = str(aid)

        name_link = f'<a href="tg://user?id={aid}">{name}</a>'

        msg += f'{name_link} | <code>{aid}</code> | تعداد دکمه : {count}\n'

    msg += "\n👤 ادمین‌های فرعی:\n\n"

    sorted_sub_admins = sorted(
        sub_admins,
        key=lambda x: buttons_count.get(str(x), 0),
        reverse=True
    )

    for aid in sorted_sub_admins:
        count = buttons_count.get(str(aid), 0)

        try:
            chat = await context.bot.get_chat(aid)
            name = chat.full_name
        except Exception:
            name = str(aid)

        name_link = f'<a href="tg://user?id={aid}">{name}</a>'

        msg += f'{name_link} | <code>{aid}</code> | تعداد دکمه : {count}\n'

    await query.message.edit_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔙 بازگشت", callback_data="admin_mgmt")
            ]
        ])
    )

    return CHOOSING

async def list_users_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    userdata = load_userdata()
    users = userdata.get("users", {})

    if not users:
        await query.message.edit_text(
            "📭 هنوز هیچ کاربری ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")
                ]
            ])
        )
        return CHOOSING

    # ---------------- دریافت شماره صفحه ----------------
    page = 0

    if data.startswith("admin_users_list_page_"):
        try:
            page = int(data.split("_")[-1])
        except Exception:
            page = 0

    # جلوگیری از صفحه منفی
    if page < 0:
        page = 0

    users_list = []

    for user_id, user_data in users.items():
        try:
            count = int(user_data.get("message_count", 0))
        except Exception:
            count = 0

        users_list.append({
            "id": str(user_data.get("id", user_id)),
            "full_name": user_data.get("full_name") or "بدون نام",
            "username": user_data.get("username"),
            "message_count": count,
            "banned": bool(user_data.get("banned", False))
        })

    # مرتب‌سازی از بیشترین دستور به کمترین
    users_list.sort(key=lambda x: x["message_count"], reverse=True)

    # ---------------- تنظیمات صفحه‌بندی ----------------
    per_page = 15
    total_users = len(users_list)
    total_pages = (total_users + per_page - 1) // per_page

    # اگر صفحه از تعداد صفحات بیشتر شد، برگرد آخرین صفحه
    if page >= total_pages:
        page = total_pages - 1

    start_index = page * per_page
    end_index = start_index + per_page

    page_users = users_list[start_index:end_index]

    # ---------------- ساخت متن پیام ----------------
    msg = f"👥 لیست کاربران ربات\n\n"
    msg += f"📄 صفحه {page + 1} از {total_pages}\n"
    msg += f"👤 تعداد کل کاربران: {total_users}\n\n"
    msg += "نام | آیدی عددی | تعداد دستور | وضعیت\n"
    msg += "━━━━━━━━━━━━━━\n"

    for user in page_users:
        uid = str(user["id"])
        name = html.escape(str(user["full_name"]))
        username = user.get("username")
        count = user["message_count"]
        banned = user["banned"]

        # ✅ یعنی آزاد / ❌ یعنی بن
        status_icon = "❌" if banned else "✅"

        # اگر یوزرنیم داشت، لینک t.me بده
        # اگر نداشت، لینک مستقیم با tg://user?id
        if username:
            safe_username = str(username).strip().lstrip("@")

            # برای لینک t.me فقط حروف، عدد و آندرلاین مجاز است
            if safe_username.replace("_", "").isalnum() and 5 <= len(safe_username) <= 32:
                name_link = f'<a href="https://t.me/{safe_username}">{name}</a>'
            else:
                name_link = f'<a href="tg://user?id={uid}">{name}</a>'
        else:
            name_link = f'<a href="tg://user?id={uid}">{name}</a>'

        msg += f'{name_link} | <code>{uid}</code> | {count} دستور | {status_icon}\n'

    # ---------------- ساخت دکمه‌های قبلی/بعدی ----------------
    keyboard = []

    nav_buttons = []

    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ قبلی",
                callback_data=f"admin_users_list_page_{page - 1}"
            )
        )

    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                "بعدی ➡️",
                callback_data=f"admin_users_list_page_{page + 1}"
            )
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")
    ])

    await query.message.edit_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return CHOOSING

async def show_ban_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    query = update.callback_query

    users_list = get_sorted_users_for_management(filter_mode="not_banned")

    if not users_list:
        await query.message.edit_text(
            "📭 هیچ کاربر آزادی برای بن کردن وجود ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")]
            ])
        )
        return CHOOSING

    page_size = 8
    total_pages = (len(users_list) + page_size - 1) // page_size
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * page_size
    end = start + page_size
    page_users = users_list[start:end]

    msg = "🚫 انتخاب کاربر برای بن:\n\n"
    msg += "روی دکمه نام کاربر بزنید یا آیدی عددی او را ارسال کنید.\n\n"
    msg += "نام | آیدی | تعداد دستور\n"
    msg += "━━━━━━━━━━━━━━\n"

    for user in page_users:
        uid = user["id"]
        name = html.escape(user["full_name"])
        username = user.get("username")
        count = user["message_count"]

        if username:
            safe_username = html.escape(username.lstrip("@"))
            name_link = f'<a href="https://t.me/{safe_username}">{name}</a>'
        else:
            name_link = f'<a href="tg://user?id={uid}">{name}</a>'

        msg += f"{name_link} | <code>{uid}</code> | {count}\n"

    msg += f"\n📄 صفحه {page + 1} از {total_pages}"

    await query.message.edit_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=build_user_action_keyboard(users_list, action="ban", page=page, page_size=page_size)
    )

    await query.message.reply_text(
        "📝 اگر خواستی دستی انتخاب کنی، آیدی عددی کاربر را بفرست.\nیا روی یکی از دکمه‌های بالا بزن.",
        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
    )

    context.user_data["admin_panel"] = "users"
    return WAITING_BAN_USER

async def ban_user_by_id(target_user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if target_user_id in ADMIN_IDS:
        return False, "❌ نمی‌توان ادمین اصلی را بن کرد."

    userdata = load_userdata()
    sub_admins = [int(x) for x in userdata.get("sub_admins", [])]

    if target_user_id in sub_admins:
        return False, "❌ نمی‌توان ادمین فرعی را بن کرد."

    users = userdata.setdefault("users", {})
    target_key = str(target_user_id)

    if target_key not in users:
        return False, "❌ این کاربر در لیست کاربران ثبت نشده است."

    if users[target_key].get("banned", False):
        return False, "⚠️ این کاربر از قبل بن شده است."

    users[target_key]["banned"] = True
    users[target_key]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_userdata(userdata, upload=True)

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="⛔️ شما از ربات بن شدید و دیگر امکان استفاده از ربات را ندارید."
        )
    except Exception as e:
        print("Failed to notify banned user:", e)

    return True, f"✅ کاربر <code>{target_user_id}</code> با موفقیت بن شد."

async def receive_ban_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "👥 مدیریت کاربران:",
            reply_markup=get_user_mgmt_inline_keyboard()
        )
        context.user_data["admin_panel"] = "users"
        return CHOOSING

    target_user_id = ensure_numeric_id(text)
    if target_user_id is None:
        await update.message.reply_text("❌ فقط آیدی عددی معتبر بفرستید یا روی دکمه‌های اینلاین بزنید.")
        return WAITING_BAN_USER

    ok, message = await ban_user_by_id(target_user_id, context)

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )

    await update.message.reply_text(
        "👥 مدیریت کاربران:",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        "👥 مدیریت کاربران:",
        reply_markup=get_user_mgmt_inline_keyboard()
    )

    context.user_data["admin_panel"] = "users"
    return CHOOSING

async def receive_unban_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
        await update.message.reply_text(
            "👥 مدیریت کاربران:",
            reply_markup=get_user_mgmt_inline_keyboard()
        )
        context.user_data["admin_panel"] = "users"
        return CHOOSING

    target_user_id = ensure_numeric_id(text)
    if target_user_id is None:
        await update.message.reply_text("❌ فقط آیدی عددی معتبر بفرستید یا روی دکمه‌های اینلاین بزنید.")
        return WAITING_UNBAN_USER

    ok, message = await unban_user_by_id(target_user_id, context)

    await update.message.reply_text(
        message,
        parse_mode="HTML"
    )

    await update.message.reply_text(
        "👥 مدیریت کاربران:",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        "👥 مدیریت کاربران:",
        reply_markup=get_user_mgmt_inline_keyboard()
    )

    context.user_data["admin_panel"] = "users"
    return CHOOSING

async def unban_user_by_id(target_user_id: int, context: ContextTypes.DEFAULT_TYPE):
    userdata = load_userdata()
    users = userdata.setdefault("users", {})
    target_key = str(target_user_id)

    if target_key not in users:
        return False, "❌ این کاربر در لیست کاربران ثبت نشده است."

    if not users[target_key].get("banned", False):
        return False, "⚠️ این کاربر بن نیست."

    users[target_key]["banned"] = False
    users[target_key]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_userdata(userdata, upload=True)

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="✅ شما از بن خارج شدید و دوباره می‌توانید از ربات استفاده کنید."
        )
    except Exception as e:
        print("Failed to notify unbanned user:", e)

    return True, f"✅ کاربر <code>{target_user_id}</code> از بن خارج شد."

async def show_unban_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    query = update.callback_query

    users_list = get_sorted_users_for_management(filter_mode="banned")

    if not users_list:
        await query.message.edit_text(
            "📭 هیچ کاربر بن‌شده‌ای وجود ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")]
            ])
        )
        return CHOOSING

    page_size = 8
    total_pages = (len(users_list) + page_size - 1) // page_size
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * page_size
    end = start + page_size
    page_users = users_list[start:end]

    msg = "✅ انتخاب کاربر برای خارج کردن از بن:\n\n"
    msg += "روی دکمه نام کاربر بزنید یا آیدی عددی او را ارسال کنید.\n\n"
    msg += "نام | آیدی | تعداد دستور\n"
    msg += "━━━━━━━━━━━━━━\n"

    for user in page_users:
        uid = user["id"]
        name = html.escape(user["full_name"])
        username = user.get("username")
        count = user["message_count"]

        if username:
            safe_username = html.escape(username.lstrip("@"))
            name_link = f'<a href="https://t.me/{safe_username}">{name}</a>'
        else:
            name_link = f'<a href="tg://user?id={uid}">{name}</a>'

        msg += f"{name_link} | <code>{uid}</code> | {count}\n"

    msg += f"\n📄 صفحه {page + 1} از {total_pages}"

    await query.message.edit_text(
        msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=build_user_action_keyboard(users_list, action="unban", page=page, page_size=page_size)
    )

    await query.message.reply_text(
        "📝 اگر خواستی دستی انتخاب کنی، آیدی عددی کاربر را بفرست.\nیا روی یکی از دکمه‌های بالا بزن.",
        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
    )

    context.user_data["admin_panel"] = "users"
    return WAITING_UNBAN_USER

async def remove_temp_reply_keyboard_from_callback(query, text="⌨️ کیبورد موقت بسته شد."):
    """
    برای زمانی که از داخل CallbackQuery می‌خواهیم
    ReplyKeyboard موقت مثل «❌ لغو» را حذف کنیم.
    """
    try:
        await query.message.reply_text(
            text,
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        print("Failed to remove temp reply keyboard:", e)

async def show_admin_access_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, text="🔐 پنل مدیریت:"):
    """
    اول ReplyKeyboard قبلی مثل ❌ لغو را حذف می‌کند،
    بعد پنل اصلی ادمین را به صورت Inline نشان می‌دهد.
    """
    await update.message.reply_text(
        "⌨️ کیبورد موقت بسته شد.",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        text,
        reply_markup=get_admin_access_inline_keyboard()
    )

    context.user_data["admin_panel"] = "access"
    return CHOOSING

async def show_admin_mgmt_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, text="👑 مدیریت ادمین‌ها:"):
    """
    اول ReplyKeyboard قبلی مثل ❌ لغو را حذف می‌کند،
    بعد پنل مدیریت ادمین‌ها را به صورت Inline نشان می‌دهد.
    """
    await update.message.reply_text(
        "⌨️ کیبورد موقت بسته شد.",
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        text,
        reply_markup=get_admin_mgmt_inline_keyboard()
    )

    context.user_data["admin_panel"] = "admin_mgmt"
    return CHOOSING

def find_nearest_valid_node(db, target_node_id):
    """
    بررسی می‌کند آیا نود در دیتابیس جدید وجود دارد یا خیر.
    اگر وجود نداشت، به والد آن نود مراجعه می‌کند تا اولین والد معتبری که در دیتابیس جدید وجود دارد را پیدا کند.
    اگر هیچ‌کدام پیدا نشد، 'root' را برمی‌گرداند.
    """
    # اگر نود در دیتابیس جدید موجود است
    if target_node_id in db:
        return target_node_id

    # پیدا کردن والد نود در کل دیتابیس (پیمایش معکوس)
    current = target_node_id
    while True:
        parent_id = None
        # پیدا کردن والدی که این نود فرزند آن بوده است
        for node_id, node_data in db.items():
            if current in node_data.get("children", []):
                parent_id = node_id
                break
        
        if parent_id and parent_id in db:
            return parent_id  # والد معتبر پیدا شد
        elif parent_id:
            current = parent_id  # والد را به عنوان نود بعدی برای جستجو قرار بده
        else:
            break  # والدی پیدا نشد
            
    return "root"

async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=True)
    text = update.message.text

    # 🛑 اگر پیام مربوط به دریافت مستقیم فایل بود، پردازش ناوبری را متوقف کن
    if text and text.strip().startswith("file-id:"):
        return CHOOSING

    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text(
            "⛔️ شما از ربات بن شدید و امکان استفاده از ربات را ندارید.",
            reply_markup=ReplyKeyboardRemove()
        )
        return CHOOSING
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    # --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- 
    admin_pass = userdata.get("admin_password")
    if admin_pass and text == admin_pass:
        if user_id not in ADMIN_IDS and user_id not in userdata.get("sub_admins", []):
            userdata.setdefault("sub_admins", []).append(user_id)
            save_userdata(userdata)
            current = context.user_data.get("current_node", "root")
    
            await update.message.reply_text("✅ رمز تایید شد.\nشما اکنون ادمین هستید 😎",
                reply_markup=get_keyboard(current, True, user_id=user_id) 
                )
    
            # اطلاع به ادمین‌ها
            for aid in ADMIN_IDS:
                if aid != user_id:
                    await context.bot.send_message(
                        aid,
                        f"🚨 ادمین جدید اضافه شد!\n\n"
                        f"👤 {update.effective_user.full_name}\n"
                        f"🆔 {user_id}\n"
                        f"🔗 @{update.effective_user.username}"
                    )
        if user_id in ADMIN_IDS:
            await update.message.reply_text("شما از ادمین‌های اصلی هستید!")
        if user_id in sub_admins:
            await update.massage.reply_text("شما قبلا ادمین شده‌اید!")

        return CHOOSING
    # --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- --- Check Admin Password --- 

    # بازیابی نود فعلی
    current_node_id = context.user_data.get('current_node', 'root')
    db = load_db()
    
    # ⛔ لغو عملیات‌های موقت (حذف / هش / ویرایش و ...)
    if text == "❌ لغو":
        current = context.user_data.get("current_node", "root")
        await update.message.reply_text(
            "لغو شد.",
            reply_markup=get_keyboard(current, is_admin, user_id=user_id)
        )
        return CHOOSING

    # --- 2. هندل کردن دستورات ادمین ---  --- هندل کردن دستورات ادمین --- --- هندل کردن دستورات ادمین --- --- هندل کردن دستورات ادمین --- --- هندل کردن دستورات ادمین ---

    # --- Admin panel back handling ------------------------------------------------------------------------------------------
    if text == "🔙 بازگشت" and context.user_data.get("admin_panel"):
        panel = context.user_data["admin_panel"]

        if panel in ["admin_mgmt", "users"]:
            context.user_data["admin_panel"] = "access"

            await update.message.reply_text(
                "🔐 پنل مدیریت:",
                reply_markup=get_admin_access_inline_keyboard()
            )
            return CHOOSING

        if panel == "access":
            context.user_data.pop("admin_panel", None)

            await update.message.reply_text(
                "بازگشت به صفحه اصلی",
                reply_markup=get_keyboard("root", is_admin, user_id=user_id)
            )
            return CHOOSING

    # 1. هندل کردن بازگشت و خانه
    if text == "🏠 صفحه اصلی":
        context.user_data['current_node'] = 'root'
        set_report_page(context, "root")
        await update.message.reply_text("به صفحه اصلی بازگشتید.", reply_markup=get_keyboard('root', is_admin, user_id=user_id))
        return CHOOSING
    
    if text.startswith("🔙 بازگشت"):
        parent = db[current_node_id].get("parent")
    
        # تعیین نود مقصد
        target_node = parent if parent else "root"
        context.user_data["current_node"] = target_node
        set_report_page(context, target_node)
    
        if target_node == "root":
            return_message = "🏠 خانه"
        else:
            bot_username = context.bot.username
            path_str = get_breadcrumb_path(target_node, db, bot_username)
    
            folder_name = db[target_node]["name"]
    
            return_message = (
                f"📂 بازگشت به {folder_name}\n"
                f"<blockquote expandable>🗺 مسیر: {path_str}</blockquote>"
            )
    
        await update.message.reply_text(
            return_message,
            reply_markup=get_keyboard(target_node, is_admin, user_id=user_id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return CHOOSING

    
    # --- Admin Accessibility --- 
    if is_admin and text == os.getenv("ADMIN_ACCESSIBILITY_NAME"):
        context.user_data["admin_panel"] = "access"
    
        await update.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )
    
        return CHOOSING

    # در handle_navigation یا یک پیام‌گیر عمومی:
    if context.user_data.get("waiting_for_user_reply"):
        MASSAGE_GROUP_ID = os.getenv("MASSAGE_GROUP_ID")
        user = update.effective_user
    
        safe_name = html.escape(user.full_name or "کاربر")
        user_link = f'<a href="tg://user?id={user.id}">{safe_name}</a>'
    
        await context.bot.send_message(
            chat_id=MASSAGE_GROUP_ID,
            text=f"📩 پاسخ جدید از طرف {user_link} (<code>{user.id}</code>):",
            parse_mode="HTML"
        )
    
        await context.bot.copy_message(
            chat_id=MASSAGE_GROUP_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
    
        await update.message.reply_text("✅ پیام شما به مدیریت ارسال شد.")
        context.user_data["waiting_for_user_reply"] = False
        return CHOOSING
    
    # ========= favorite folder ======================== favorite folder ======================== favorite folder ===============
    if text == "📁 پوشه دلخواه":
        userdata = load_userdata()
        favorites = userdata.get("users", {}).get(str(user_id), {}).get("favorites", [])
        
        if not favorites:
            await update.message.reply_text("پوشه دلخواه شما خالی است.")
            return CHOOSING
        current = context.user_data.get("current_node", "root")
        await update.message.reply_text(
            "📁 پوشه دلخواه\n"
            "جهت حذف هر کدام از فایل ها، همینجا روی آن فایل ری اکت 👎 بزنین.\n"
            "جهت حذف همه محتوای صفحه و پنهان شدن آیکون پوشه دلخواه، دستور /clear را بزنید!",
            reply_markup=get_keyboard(current, is_admin, user_id=user_id)
        )

        # ========= ارسال پوشه دلخواه با پشتیبانی کامل آلبوم =========
        db = load_db()
        
        # ساخت لیست موارد واقعی دیتابیس
        resolved = []
        for fav in favorites:
            node_id = fav["node_id"]
            idx = fav["content_index"]
        
            node = db.get(node_id)
            if not node:
                continue
        
            contents = node.get("contents", [])
            if 0 <= idx < len(contents):
                resolved.append({
                    "node_id": node_id,
                    "content_index": idx,
                    "item": contents[idx]
                })
        
        
        # مپ واکنش‌ها
        sent_mapping = context.user_data.setdefault("sent_mapping", {})
        
        # انواع قابل گروه‌بندی
        groupable = {"photo", "video", "document", "audio"}
        
        i = 0
        while i < len(resolved):
            entry = resolved[i]
            item = entry["item"]
        
            msg_type = item.get("type")
            mgid = item.get("media_group_id")
        
            # اگر عضو media_group است → گروه کامل را پیدا کن
            if mgid and msg_type in groupable:
                group_entries = []
                j = i
        
                # جمع کردن اعضای پشت‌سر‌هم مربوط به همین گروه
                while j < len(resolved):
                    x = resolved[j]["item"]
                    if (
                        x.get("media_group_id") == mgid
                        and x.get("type") in groupable
                    ):
                        group_entries.append(resolved[j])
                        j += 1
                    else:
                        break
        
                # اگر واقعا چند آیتم است → آلبوم بفرست
                if len(group_entries) > 1:
                    media_objs = []
        
                    # اولین کپشن معتبر
                    first_caption = None
                    first_entities = None
                    for g in group_entries:
                        cap = g["item"].get("caption")
                        if cap:
                            first_caption = cap
                            first_entities = g["item"].get("entities")
                            break
        
                    for idx2, g in enumerate(group_entries):
                        media_objs.append(
                            build_input_media(
                                g["item"],
                                is_first=(idx2 == 0),
                                forced_caption=first_caption if idx2 == 0 else None,
                                forced_entities=first_entities if idx2 == 0 else None,
                            )
                        )
        
                    sent = await update.message.reply_media_group(media_objs)
        
                    for sent_msg, g in zip(sent, group_entries):
                        sent_mapping[sent_msg.message_id] = {
                            "node_id": g["node_id"],
                            "content_index": g["content_index"],
                        }
        
                    i = j
                    continue
        
                # اگر تک‌عضوی بود → ارسال تکی
                sm = await send_single_content(update.message, item)
                if sm:
                    sent_mapping[sm.message_id] = {
                        "node_id": entry["node_id"],
                        "content_index": entry["content_index"],
                    }
        
                i += 1
                continue
        
            # موارد غیرگروهی
            sm = await send_single_content(update.message, item)
            if sm:
                sent_mapping[sm.message_id] = {
                    "node_id": entry["node_id"],
                    "content_index": entry["content_index"],
                }
        
            i += 1
            
        return CHOOSING
        

    # ======= Admin panel handling END ======= ======= Admin panel handling END ======= ======= Admin panel handling END ======= ======= Admin panel handling END ======= ===
            
    if is_admin:
        if text == "➕ افزودن دکمه":
            await update.message.reply_text("نام دکمه جدید را بنویسید:", reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True))
            return WAITING_BUTTON_NAME
        
        if text == "➕ افزودن محتوا":
            await update.message.reply_text(
                "هر تعداد فایل، عکس، متن یا PDF که می‌خواهید بفرستید.\nدر پایان دکمه '✅ ثبت نهایی' را بزنید.",
                reply_markup=ReplyKeyboardMarkup([["✅ ثبت نهایی", "❌ لغو"]], resize_keyboard=True)
            )
            context.user_data['temp_content'] = []
            return WAITING_CONTENT
            
        if text == "🗑 حذف دکمه":
            # نمایش دکمه‌های زیرمجموعه برای حذف
            children = db[current_node_id].get("children", [])
            if not children:
                await update.message.reply_text("دکمه‌ای برای حذف وجود ندارد.")
                return CHOOSING
            
            # ساخت کیبورد موقت برای حذف
            del_keyboard = []
            for child_id in children:
                child_name = db[child_id]['name']
                del_keyboard.append([KeyboardButton(f"❌ حذف {child_name}")])
            del_keyboard.append(["❌ لغو"])
            
            await update.message.reply_text("روی دکمه‌ای که می‌خواهید حذف شود بزنید:", reply_markup=ReplyKeyboardMarkup(del_keyboard, resize_keyboard=True))
            return CHOOSING # در همین حالت می‌مانیم تا انتخاب کند (اما لاجیکش رو باید هندل کنیم)

        if text.startswith("❌ حذف "):
            # پروسه حذف واقعی
            target_name = text.replace("❌ حذف ", "")
            children = db[current_node_id].get("children", [])
            target_id = None
            
            for child_id in children:
                if db[child_id]['name'] == target_name:
                    target_id = child_id
                    break
            
            if target_id:
                # ثبت تاریخچه
                push_admin_history(context, db)
            
                # حذف از لیست فرزندان والد
                db[current_node_id]['children'].remove(target_id)
            
                # حذف بازگشتی کل درخت
                delete_node_recursive(db, target_id)

                bot_username = context.bot.username
                parent_name = db[current_node_id]["name"]
                parent_link = get_link(current_node_id, parent_name, bot_username)
                child_link = get_link(target_id, target_name, bot_username)
                desc = f"❌ پوشه {child_link} از {parent_link} حذف شد."
                
                log_caption = format_admin_log(update.effective_user, desc)
                backup_caption = format_backup_caption(update.effective_user, "حذف دکمه")
                
                set_pending_caption(context, log_caption)
                set_pending_backup_caption(context, backup_caption)
                
                save_db(db, context=context)
                
                await update.message.reply_text(
                    f"دکمه '{target_name}' و تمام زیرمجموعه‌هایش حذف شد.",
                    reply_markup=get_keyboard(current_node_id, is_admin, user_id=user_id)
                )
            else:
                await update.message.reply_text("دکمه یافت نشد.", reply_markup=get_keyboard(current_node_id, is_admin, user_id=user_id))
            return CHOOSING

        if text == "📥 دریافت بکاپ":
            # ساخت فایل زیپ از دیتابیس
            mem_zip = iolib.BytesIO()
            with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.write(DB_FILE)
            mem_zip.seek(0)
            
            await update.message.reply_document(
                document=InputFile(mem_zip, filename=f"backup_{datetime.now().strftime('%Y%m%d')}.zip"),
                caption="این فایل حاوی تمام ساختار دکمه‌ها و لینک فایل‌هاست."
            )
            return CHOOSING

        if text == "📤 وارد کردن بکاپ":
            await update.message.reply_text("فایل ZIP بکاپ را ارسال کنید:", reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True))
            return WAITING_RESTORE_FILE

        if text == "✏️ ویرایش‌نام‌دکمه":
            children = db[current_node_id].get("children", [])
            if not children:
                await update.message.reply_text("دکمه‌ای برای ویرایش وجود ندارد.")
                return CHOOSING

            kb = []
            for cid in children:
                kb.append([KeyboardButton(f"✏️ {db[cid]['name']}")])
            kb.append(["❌ لغو"])

            await update.message.reply_text(
                "دکمه‌ای که می‌خواهید ویرایش شود را انتخاب کنید:",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
            return CHOOSING


        if text.startswith("✏️ "):
            target_name = text.replace("✏️ ", "")
            for cid in db[current_node_id]["children"]:
                if db[cid]["name"] == target_name:
                    context.user_data["rename_target"] = cid
                    await update.message.reply_text(
                        "نام جدید دکمه را وارد کنید:",
                        reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
                    )
                    return WAITING_RENAME_BUTTON

        if text == "🧹 حذف محتوای صفحه":
            # ۱. گرفتن نسخه کپی از محتویات قبل از حذف (Snapshot)
            removed_items = list(db[current_node_id].get("contents", []))
            
            if not removed_items:
                await update.message.reply_text("⚠️ این پوشه فاقد هرگونه محتوا است.")
                return CHOOSING

            # ۲. پوش کردن در تاریخچه و حذف محتوا از دیتابیس
            push_admin_history(context, db)
            db[current_node_id]["contents"] = []

            bot_username = context.bot.username
            node_name = db[current_node_id]["name"]
            node_link = get_link(current_node_id, node_name, bot_username)
            path_html = get_node_path_html(db, current_node_id, bot_username)

            # ۳. آماده‌سازی بدنه لاگ تفصیلی
            desc_parts = [
                f"🧹 <b>کل محتویات پوشه حذف شد</b>\n",
                f"📁 <b>پوشه مقصد:</b> {node_link}",
                f"🗂 <b>مسیر کامل:</b> {path_html}",
                f"📊 <b>تعداد کل موارد حذف شده:</b> {len(removed_items)} مورد",
                "───────────────────\n<b>📋 جزئیات موارد حذف شده:</b>"
            ]

            for i, item in enumerate(removed_items, 1):
                desc_parts.append(get_item_log_details(item, i, bot_username))

            desc = "\n\n".join(desc_parts)

            # ۴. قالب‌بندی با هدر ادمین و ست کردن برای save_db
            log_caption = format_admin_log(update.effective_user, desc)
            backup_caption = format_backup_caption(update.effective_user, "حذف محتوای صفحه")
            
            set_pending_caption(context, log_caption)
            set_pending_backup_caption(context, backup_caption)
            
            save_db(db, context=context)
            

            await update.message.reply_text(
                "🧹 محتوای این صفحه حذف شد و گزارش تفصیلی آن برای کانال مدیریت ارسال گردید.",
                reply_markup=get_keyboard(current_node_id, True, user_id=user_id)
            )
            return CHOOSING

        if text == "🔑 دریافت ‌هش‌ولینک‌دکمه":
            children = db[current_node_id].get("children", [])
            if not children:
                await update.message.reply_text("دکمه‌ای وجود ندارد.")
                return CHOOSING

            kb = []
            for cid in children:
                kb.append([KeyboardButton(f"🔑 {db[cid]['name']}")])
            kb.append(["❌ لغو"])

            await update.message.reply_text(
                "دکمه‌ای که می‌خواهید هش و لینک آن را بگیرید، انتخاب کنید:",
                reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
            )
            return CHOOSING

        if text.startswith("🔑 "):
            target_name = text.replace("🔑 ", "")
            for cid in db[current_node_id]["children"]:
                if db[cid]["name"] == target_name:
                    bot_username = context.bot.username
        
                    # --- escape کردن کاراکترهای خاص برای MarkdownV2 ---
                    def escape_md(text: str) -> str:
                        escape_chars = r"_*[]()~`>#+-=|{}.!"""
                        for char in escape_chars:
                            text = text.replace(char, f"\\{char}")
                        return text
        
                    escaped_cid = escape_md(cid)
                    deep_link = f"https://t.me/{bot_username}?start={cid}"
        
                    # --- پیام با هش و لینک مستقیم ---
                    await update.message.reply_text(
                        f"🔑 هش این دکمه:\n\n`{escaped_cid}`\n\n"
                        f"🔗 لینک مستقیم:\n`{deep_link}`",
                        parse_mode="MarkdownV2"
                    )
                    return CHOOSING
        

        if text == "🔀 جابه‌جایی‌چیدمان":
            children = db[current_node_id].get("children", [])
            if len(children) < 2:
                await update.message.reply_text("برای جابه‌جایی حداقل دو دکمه لازم است.")
                return CHOOSING
        
            context.user_data["reorder_remaining"] = children.copy()
            context.user_data["reorder_result"] = []
            context.user_data["reorder_mode"] = True
        
            await show_reorder_keyboard(update, context, db)
            return CHOOSING
        
        if text == "❌ لغو" and context.user_data.get("reorder_mode"):
            for key in ["reorder_remaining", "reorder_result", "reorder_mode"]:
                context.user_data.pop(key, None)
        
            await update.message.reply_text(
                "لغو شد.",
                reply_markup=get_keyboard(current_node_id, is_admin, user_id=user_id)
            )
            return CHOOSING
        
        if context.user_data.get("reorder_mode") and context.user_data.get("reorder_remaining"):
            remaining = context.user_data["reorder_remaining"]
            result = context.user_data["reorder_result"]
        
            selected_id = None
            for cid in remaining:
                if text == f"🔀 {db[cid]['name']}":  # ✅ فقط وقتی با ایموجی انتخاب شد
                    selected_id = cid
                    break
        
            if selected_id:
                remaining.remove(selected_id)
                result.append(selected_id)
        
                if remaining:
                    await show_reorder_keyboard(update, context, db)
                    return CHOOSING
        
                # ✅ پایان انتخاب و ذخیره چیدمان جدید
                push_admin_history(context, db)
                db[current_node_id]["children"] = result

                bot_username = context.bot.username
                node_name = db[current_node_id]["name"]
                node_link = get_link(current_node_id, node_name, bot_username)
                desc = f"🔀 چیدمان دکمه‌های پوشه {node_link} تغییر کرد."
                
                log_caption = format_admin_log(update.effective_user, desc)
                backup_caption = format_backup_caption(update.effective_user, "جابه‌جایی چیدمان")
                
                set_pending_caption(context, log_caption)
                set_pending_backup_caption(context, backup_caption)
                
                save_db(db, context=context)
                
                for key in ["reorder_remaining", "reorder_result", "reorder_mode"]:
                    context.user_data.pop(key, None)
        
                await update.message.reply_text(
                    "✅ چیدمان جدید ذخیره شد.",
                    reply_markup=get_keyboard(current_node_id, True, user_id=user_id)
                )
                return CHOOSING

        if text == "↩️" and is_admin:
            history = context.user_data.get("admin_history", [])
            future = context.user_data.get("admin_future", [])
        
            if not history:
                await update.message.reply_text("⛔️ چیزی برای بازگشت وجود ندارد.")
                return CHOOSING
        
            future.append(copy.deepcopy(load_db()))
        
            last_db = history.pop()
        
            current_node = context.user_data.get("current_node", "root")
            valid_node = find_nearest_valid_node(last_db, current_node)
            context.user_data["current_node"] = valid_node
        
            bot_username = context.bot.username
            path_str = get_breadcrumb_path(valid_node, last_db, bot_username)
            node_name = last_db.get(valid_node, {}).get("name", "خانه")
            node_link = get_link(valid_node, node_name, bot_username)
        
            desc = f"↩️ آخرین تغییر بازگردانده شد. پوشه فعلی: {node_link}"
            
            log_caption = format_admin_log(update.effective_user, desc)
            backup_caption = format_backup_caption(update.effective_user, "بازگشت به تغییر قبل (Undo)")
            
            set_pending_caption(context, log_caption)
            set_pending_backup_caption(context, backup_caption)
            
            save_db(last_db, context=context)
            
        
            await update.message.reply_text(
                f"↩️ آخرین تغییر بازگردانده شد.\n"
                f"📂 پوشه فعلی: {node_name}\n"
                f"🗺 مسیر: {path_str}",
                reply_markup=get_keyboard(valid_node, True, user_id=user_id),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return CHOOSING
        
        if text == "↪️" and is_admin:
            history = context.user_data.get("admin_history", [])
            future = context.user_data.get("admin_future", [])
        
            if not future:
                await update.message.reply_text("⛔️ چیزی برای جلو رفتن نیست.")
                return CHOOSING
        
            # ذخیره وضعیت فعلی دیتابیس در history
            history.append(copy.deepcopy(load_db()))
        
            # لود کردن دیتابیس بعدی
            next_db = future.pop()
        
            # پیدا کردن مناسب‌ترین نود پس از بازگرداندن بکاپ
            current_node = context.user_data.get("current_node", "root")
            valid_node = find_nearest_valid_node(next_db, current_node)
            
            # ذخیره نود معتبر جدید در سشن کاربر
            context.user_data["current_node"] = valid_node
        
            # دریافت نام پوشه و مسیر آن
            bot_username = context.bot.username
            path_str = get_breadcrumb_path(valid_node, next_db, bot_username)
            node_name = next_db.get(valid_node, {}).get("name", "خانه")
            node_link = get_link(valid_node, node_name, bot_username)
            desc = f"↪️ تغییر دوباره اعمال شد. پوشه فعلی: {node_link}"
            
            log_caption = format_admin_log(update.effective_user, desc)
            backup_caption = format_backup_caption(update.effective_user, "بازگردانی دوباره (Redo)")
            
            set_pending_caption(context, log_caption)
            set_pending_backup_caption(context, backup_caption)
            
            save_db(next_db, context=context)
            
            
            await update.message.reply_text(
                f"↪️ تغییر دوباره اعمال شد.\n"
                f"📂 پوشه فعلی: {node_name}\n"
                f"🗺 مسیر: {path_str}",
                reply_markup=get_keyboard(valid_node, True, user_id=user_id),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return CHOOSING


    # 3. هندل کردن ناوبری (کلیک روی دکمه‌های پوشه)
    # چک کنیم آیا تکست کاربر نام یکی از دکمه‌های زیرمجموعه است؟
    children = db[current_node_id].get("children", [])
    for child_id in children:
        child_node = db.get(child_id)
    
        if child_node and child_node["name"] == text:
    
            # ✅ این صفحه برای report ذخیره شود
            set_report_page(context, child_id)

            bot_username = context.bot.username
            path_str = get_breadcrumb_path(child_id, db, bot_username)
            
            # 👤 کاربر عادی + دکمه بدون فرزند
            if not is_admin and not child_node.get("children"):
                # فقط محتوا را نمایش بده، بدون تغییر صفحه
                await send_node_contents(update, context, child_id)
                return CHOOSING
    
            # 👑 ادمین یا دکمه دارای فرزند
            context.user_data['current_node'] = child_id
    
            await update.message.reply_text(
                f"📂 {child_node['name']}\n"
                f"<blockquote expandable>🗺 مسیر: {path_str}</blockquote>",
                reply_markup=get_keyboard(child_id, is_admin, user_id=user_id),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            await send_node_contents(update, context, child_id)
            return CHOOSING

    # 🔍 چک کردن وضعیت سرچ هوشمند کاربر قبل از جستجو
    user_id = str(update.effective_user.id)
    userdata = load_userdata()
    users = userdata.get("users", {})
    # پیش‌فرض برای همه True (روشن) است. اگر فیلد smart_search_disabled معادل True باشد یعنی خاموش است.
    is_disabled = users.get(user_id, {}).get("smart_search_disabled", False)
    
    if is_disabled:
        ## اگر سرچ خاموش باشد، پاسخی ارسال نمی‌شود یا می‌توانید یک پیام ساده دهید:
        #await update.message.reply_text(
        #    "⚠️ سرچ هوشمند برای شما غیرفعال است.\n"
        #    "برای فعال کردن مجدد آن از دستور /on_of_search استفاده کنید."
        #)
        return CHOOSING

    # ✅ اگر دکمه نبود و سرچ هوشمند روشن بود، سرچ کن
    return await handle_smart_search(update, context, text, is_admin)

async def rename_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        current = context.user_data.get("current_node", "root")
        await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(current, True, user_id=update.effective_user.id))
        return CHOOSING

    new_name = update.message.text
    target_id = context.user_data.get("rename_target")

    db = load_db()
    if target_id in db:
        push_admin_history(context, db)
    
        old_name = db[target_id]["name"]
        new_name = update.message.text
    
        db[target_id]["name"] = new_name
    
        bot_username = context.bot.username
        old_link = get_link(target_id, old_name, bot_username)
        new_link = get_link(target_id, new_name, bot_username)
        desc = f"📝 نام پوشه {old_link} به {new_link} تغییر کرد."
    
        log_caption = format_admin_log(update.effective_user, desc)
        backup_caption = format_backup_caption(update.effective_user, "ویرایش نام پوشه")
    
        set_pending_caption(context, log_caption)
        set_pending_backup_caption(context, backup_caption)
    
        save_db(db, context=context)
    
    current = context.user_data.get("current_node", "root")
    await update.message.reply_text("✅ نام دکمه ویرایش شد.", reply_markup=get_keyboard(current, True, user_id=update.effective_user.id))
    return CHOOSING



# === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS === 
async def set_admin_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ❌ اگر کاربر منصرف شد
    if text in ["🔙 بازگشت", "❌ لغو"]:
        context.user_data.pop("admin_waiting_from", None)
    
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
    
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "admin_mgmt"
        return CHOOSING

    if len(text) < 2:
        await update.message.reply_text("❌ رمز خیلی کوتاه است.")
        return WAITING_ADMIN_PASSWORD_EDIT

    userdata = load_userdata()   # 👈 پایین توضیح دادم
    userdata["admin_password"] = text
    save_userdata(userdata)

    context.user_data.pop("admin_waiting_from", None)
    
    await update.message.reply_text(
        "✅ رمز ادمینی با موفقیت تغییر کرد.",
        reply_markup=ReplyKeyboardRemove()
    )
    
    await update.message.reply_text(
        "👑 مدیریت ادمین‌ها:",
        reply_markup=get_admin_mgmt_inline_keyboard()
    )
    
    context.user_data["admin_panel"] = "admin_mgmt"
    return CHOOSING
    
async def restore_userdata(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    if text in ["❌ لغو", "🔙 بازگشت"]:
        context.user_data.pop("admin_waiting_from", None)
    
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
    
        await update.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "access"
        return CHOOSING
    

    # ------------------------------
    # 📌 مرحله دریافت فایل
    # ------------------------------
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ لطفاً یک فایل ZIP یا JSON بفرستید.")
        return WAITING_USERDATA_UPLOAD

    filename = doc.file_name.lower()
    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()

    try:
        # ============================
        # CASE 1: فایل مستقیم JSON
        # ============================
        if filename.endswith(".json"):
            userdata = json.loads(file_bytes.decode("utf-8"))
            save_userdata(userdata)

            context.user_data.pop("admin_waiting_from", None)
            
            await update.message.reply_text(
                "✅ userdata.json با موفقیت بازیابی شد.",
                reply_markup=ReplyKeyboardRemove()
            )
            
            await update.message.reply_text(
                "🔐 پنل مدیریت:",
                reply_markup=get_admin_access_inline_keyboard()
            )
            context.user_data["admin_panel"] = "access"

            context.user_data.setdefault("current_node", "root")
            return CHOOSING

        # ============================
        # CASE 2: فایل ZIP شامل userdata.json
        # ============================
        if filename.endswith(".zip"):

            with zipfile.ZipFile(iolib.BytesIO(file_bytes)) as zipf:
                if "userdata.json" not in zipf.namelist():
                    await update.message.reply_text("❌ فایل ZIP فاقد userdata.json است.")
                    return WAITING_USERDATA_UPLOAD

                userdata = json.loads(zipf.read("userdata.json").decode("utf-8"))

            save_userdata(userdata)

            context.user_data.pop("admin_waiting_from", None)

            await update.message.reply_text(
                "✅ userdata از ZIP بازیابی شد.",
                reply_markup=ReplyKeyboardRemove()
            )

            await update.message.reply_text(
                "🔐 پنل مدیریت:",
                reply_markup=get_admin_access_inline_keyboard()
            )

            context.user_data["admin_panel"] = "access"
            context.user_data.setdefault("current_node", "root")
            return CHOOSING

        # اگر هیچکدام نبود:
        await update.message.reply_text("❌ فقط ZIP یا JSON قابل قبول است.")
        return WAITING_USERDATA_UPLOAD

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بازیابی:\n{e}")
        return WAITING_USERDATA_UPLOAD

def ensure_numeric_id(text: str):
    text = text.strip()
    if not text.isdigit():
        return None
    return int(text)   # 👈 این مهمه

async def add_sub_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ لغو":
        context.user_data.pop("admin_waiting_from", None)
    
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
    
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "admin_mgmt"
        return CHOOSING
    
    new_admin = ensure_numeric_id(text)
    if new_admin is None:
        await update.message.reply_text("❌ فقط آیدی عددی معتبر است. دوباره وارد کنید:")
        return WAITING_ADD_ADMIN

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    sub_admins = [int(x) for x in sub_admins]

    if new_admin in ADMIN_IDS:
        await update.message.reply_text("❌ این فرد قبلاً ادمین اصلی است.")
        return WAITING_ADD_ADMIN

    if new_admin not in sub_admins:
        sub_admins.append(new_admin)
        userdata["sub_admins"] = sub_admins

        if "sub_admins_buttons" not in userdata:
            userdata["sub_admins_buttons"] = {}

        userdata["sub_admins_buttons"][str(new_admin)] = 0

        save_userdata(userdata)

        context.user_data.pop("admin_waiting_from", None)
        
        await update.message.reply_text(
            f"✅ ادمین {new_admin} با موفقیت اضافه شد.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
        
        context.user_data["admin_panel"] = "admin_mgmt"
        current=context.user_data.get("current_node", "root")
        user_id=update.effective_user.id
        
        # 📩 ارسال پیام به ادمین جدید
        try:
            await context.bot.send_message(
                chat_id=new_admin,
                text="🎉 شما به عنوان ادمین فرعی ربات منصوب شدید.",
                reply_markup=get_keyboard(current, True, user_id=user_id) 
            )
        except Exception as e:
            print("Failed to notify new admin:", e)
        return CHOOSING
    else:
        await update.message.reply_text("❌ این فرد قبلاً ادمین فرعی است.")
        return WAITING_ADD_ADMIN

async def remove_sub_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == "❌ لغو":
        context.user_data.pop("admin_waiting_from", None)
    
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardRemove()
        )
    
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
    
        context.user_data["admin_panel"] = "admin_mgmt"
        return CHOOSING
    
    admin_id = ensure_numeric_id(text)
    if admin_id is None:
        await update.message.reply_text("❌ فقط آیدی عددی معتبر است. دوباره ارسال کنید:")
        return WAITING_REMOVE_ADMIN

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    sub_admins = [int(x) for x in sub_admins]

    if admin_id in ADMIN_IDS:
        await update.message.reply_text("❌ نمی‌توان ادمین اصلی را حذف کرد.")
        return WAITING_REMOVE_ADMIN

    if admin_id in sub_admins:
        sub_admins.remove(admin_id)
        userdata["sub_admins"] = sub_admins

        if "sub_admins_buttons" in userdata:
            userdata["sub_admins_buttons"].pop(str(admin_id), None)

        save_userdata(userdata)

        context.user_data.pop("admin_waiting_from", None)
        
        await update.message.reply_text(
            f"✅ ادمین {admin_id} حذف شد.",
            reply_markup=ReplyKeyboardRemove()
        )
        
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=get_admin_mgmt_inline_keyboard()
        )
        
        context.user_data["admin_panel"] = "admin_mgmt"
        current=context.user_data.get("current_node", "root")
        user_id=update.effective_user.id
        
        # 📩 ارسال پیام به کاربر حذف‌شده
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text="⚠️ شما از لیست ادمین‌های ربات حذف شدید.",
                reply_markup=get_keyboard("root", False, user_id=user_id) 
            )
        except Exception as e:
            print("Failed to notify removed admin:", e)
        return CHOOSING
    else:
        await update.message.reply_text("❌ این فرد ادمین نیست.")
        return WAITING_REMOVE_ADMIN



async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    buttons_count = userdata.get("sub_admins_buttons", {})

    # ✅ همه ID ها رو int کن
    main_admins = [int(x) for x in ADMIN_IDS]
    sub_admins = [int(x) for x in sub_admins]

    msg = "👑 ادمین‌های اصلی:\n\n"

    # مرتب سازی اصلی‌ها
    sorted_main_admins = sorted(
        main_admins,
        key=lambda x: buttons_count.get(str(x), 0),
        reverse=True
    )

    for aid in sorted_main_admins:
        count = buttons_count.get(str(aid), 0)

        try:
            chat = await context.bot.get_chat(aid)
            name = chat.full_name
            username = chat.username
        except Exception:
            name = str(aid)
            username = None

        # لینک مستقیم به پروفایل
        name_link = f'<a href="tg://user?id={aid}">{name}</a>'

        msg += f'{name_link} | <code>{aid}</code> | تعداد دکمه : {count}\n'

    msg += "\n👤 ادمین‌های فرعی:\n\n"

    # مرتب سازی فرعی‌ها
    sorted_sub_admins = sorted(
        sub_admins,
        key=lambda x: buttons_count.get(str(x), 0),
        reverse=True
    )

    for aid in sorted_sub_admins:
        count = buttons_count.get(str(aid), 0)

        try:
            chat = await context.bot.get_chat(aid)
            name = chat.full_name
            username = chat.username
        except Exception:
            name = str(aid)
            username = None

        name_link = f'<a href="tg://user?id={aid}">{name}</a>'

        msg += f'{name_link} | <code>{aid}</code> | تعداد دکمه : {count}\n'

    await update.message.reply_text(
        msg,
        reply_markup=get_keyboard("admin_mgmt", True, user_id=update.effective_user.id),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    return CHOOSING

# === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END= 

def get_breadcrumb_path(node_id, db, bot_username):
    """تولید مسیر لینک‌دار از روت تا نود فعلی"""
    path_parts = []
    curr_id = node_id
    
    # پیمایش به سمت بالا تا رسیدن به روت
    while curr_id and curr_id != 'root' and curr_id in db:
        name = db[curr_id]['name']
        # لینک مستقیم به نود
        link = f"https://t.me/{bot_username}?start={curr_id}"
        path_parts.append(f'<a href="{link}">{name}</a>')
        curr_id = db[curr_id].get('parent')
    
    # اضافه کردن آیکون خانه
    path_parts.append(f'<a href="https://t.me/{bot_username}?start=root">🏠</a>')
    
    # معکوس کردن لیست برای نمایش درست (از روت به فرزند)
    return " ⬅️ ".join(reversed(path_parts))


def is_valid_node_id(text, db):
    return text in db and isinstance(db[text], dict)


async def show_reorder_keyboard(update, context, db):
    current_node_id = context.user_data.get("current_node", "root")
    remaining = context.user_data["reorder_remaining"]
    kb = [[KeyboardButton(f"🔀 {db[cid]['name']}")] for cid in remaining]
    kb.append(["❌ لغو"])

    await update.message.reply_text(
        f"ترتیب جدید را انتخاب کنید ({len(remaining)} دکمه باقی مانده):",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )


async def add_button_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "❌ لغو":
        current = context.user_data.get('current_node', 'root')
        await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(current, True, user_id=update.effective_user.id))
        return CHOOSING

    db = load_db()
    current_node_id = context.user_data.get('current_node', 'root')
    bot_username = context.bot.username

    # 🧠 اگر هش معتبر بود → کپی کامل نود
    if is_valid_node_id(text, db):
        source_id = text

        def clone_node(old_id, new_parent):
            new_id = str(uuid.uuid4())
            old = db[old_id]
            
            # 💡 کپی کردن تمام فیلدها از جمله style
            db[new_id] = {
                "name": old["name"],
                "parent": new_parent,
                "children": [],
                "contents": old.get("contents", []).copy(),
                "style": old.get("style")
            }
            
            for child in old.get("children", []):
                child_new_id = clone_node(child, new_id)
                db[new_id]["children"].append(child_new_id)

            return new_id

        push_admin_history(context, db)
        new_root_id = clone_node(source_id, current_node_id)
        db[current_node_id]["children"].append(new_root_id)
        
        # --- سیستم لاگ‌گیری برای حالت کپی با هش ---
        parent_name = db[current_node_id]["name"]
        parent_link = get_link(current_node_id, parent_name, bot_username)
        
        copied_node_name = db[new_root_id]["name"]
        copied_node_link = get_link(new_root_id, copied_node_name, bot_username)
        
        desc = f"📋 پوشه {copied_node_link} (کپی‌شده از روی هش) به {parent_link} اضافه شد."
        
        log_caption = format_admin_log(update.effective_user, desc)
        backup_caption = format_backup_caption(update.effective_user, "کپی پوشه با هش")
        
        set_pending_caption(context, log_caption)
        set_pending_backup_caption(context, backup_caption)
        
        save_db(db, context=context)
        

        # افزایش آمار دکمه‌های ادمین
        userdata = load_userdata()
        if "sub_admins_buttons" not in userdata:
            userdata["sub_admins_buttons"] = {}
        user_id = update.effective_user.id
        current_count = userdata["sub_admins_buttons"].get(str(user_id), 0)
        userdata["sub_admins_buttons"][str(user_id)] = current_count + 1
        save_userdata(userdata)

        await update.message.reply_text(
            "✅ دکمه با تمام زیرمجموعه‌ها کپی شد.",
            reply_markup=get_keyboard(current_node_id, True, user_id=user_id)
        )
        return CHOOSING

    # ✏️ در غیر اینصورت → دکمه جدید معمولی
    new_id = str(uuid.uuid4())
    db[new_id] = {
        "name": text,
        "parent": current_node_id,
        "children": [],
        "contents": []
    }

    push_admin_history(context, db)
    db[current_node_id]["children"].append(new_id)

    # --- سیستم لاگ‌گیری برای دکمه جدید معمولی ---
    parent_name = db[current_node_id]["name"]
    parent_link = get_link(current_node_id, parent_name, bot_username)
    child_name = text
    child_link = get_link(new_id, child_name, bot_username)
    
    desc = f"➕ پوشه جدید {child_link} به {parent_link} اضافه شد."
    
    log_caption = format_admin_log(update.effective_user, desc)
    backup_caption = format_backup_caption(update.effective_user, "افزودن دکمه")
    
    set_pending_caption(context, log_caption)
    set_pending_backup_caption(context, backup_caption)
    
    save_db(db, context=context)
    
    # افزایش آمار دکمه‌های ادمین
    userdata = load_userdata()
    if "sub_admins_buttons" not in userdata:
        userdata["sub_admins_buttons"] = {}
    
    user_id = update.effective_user.id
    current_count = userdata["sub_admins_buttons"].get(str(user_id), 0)
    userdata["sub_admins_buttons"][str(user_id)] = current_count + 1
    save_userdata(userdata)
    
    await update.message.reply_text(
        f"✅ دکمه '{text}' ساخته شد.",
        reply_markup=get_keyboard(current_node_id, True, user_id=user_id)
    )
    return CHOOSING

def extract_message_content(msg):
    raw_text = msg.text
    raw_caption = msg.caption

    msg_entities = [e.to_dict() for e in msg.entities] if msg.entities else None
    msg_caption_entities = [e.to_dict() for e in msg.caption_entities] if msg.caption_entities else None
    media_group_id = msg.media_group_id

    if msg.photo:
        return {
            "type": "photo",
            "file_id": msg.photo[-1].file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.video:
        return {
            "type": "video",
            "file_id": msg.video.file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.document:
        return {
            "type": "document",
            "file_id": msg.document.file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.audio:
        return {
            "type": "audio",
            "file_id": msg.audio.file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.voice:
        return {
            "type": "voice",
            "file_id": msg.voice.file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.animation:
        return {
            "type": "animation",
            "file_id": msg.animation.file_id,
            "caption": raw_caption,
            "entities": msg_caption_entities,
            "media_group_id": media_group_id,
        }

    if msg.video_note:
        return {
            "type": "video_note",
            "file_id": msg.video_note.file_id,
            "media_group_id": media_group_id,
        }

    if msg.sticker:
        return {
            "type": "sticker",
            "file_id": msg.sticker.file_id,
            "media_group_id": media_group_id,
        }

    if msg.text and not msg.text.startswith('/'):
        return {
            "type": "text",
            "text": raw_text,
            "entities": msg_entities,
        }

    return None

# ==========================================
# ۳) اصلاح تابع receive_content (بروزرسانی لاگ‌ها با bot_username برای ساخت لینک صحیح)
# ==========================================
async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text

    temp_content = context.user_data.get("temp_content")
    if temp_content is None:
        current = context.user_data.get("current_node", "root")
        await msg.reply_text(
            "ابتدا از طریق دکمه '➕ افزودن محتوا' وارد حالت افزودن محتوا شوید.",
            reply_markup=get_keyboard(current, True, user_id=update.effective_user.id),
        )
        return CHOOSING

    if text == "❌ لغو":
        current = context.user_data.get("current_node", "root")
        context.user_data.pop("temp_content", None)
        context.user_data.pop("change_target", None)
        await msg.reply_text(
            "عملیات لغو شد.",
            reply_markup=get_keyboard(current, True, user_id=update.effective_user.id),
        )
        return CHOOSING

    if text == "✅ ثبت نهایی":
        if not temp_content:
            current = context.user_data.get("current_node", "root")
            context.user_data.pop("temp_content", None)
            context.user_data.pop("change_target", None)
            await msg.reply_text(
                "چیزی برای ذخیره وجود نداشت.",
                reply_markup=get_keyboard(current, True, user_id=update.effective_user.id),
            )
            return CHOOSING

        current_node_id = context.user_data.get("current_node", "root")
        db = load_db()
        push_admin_history(context, db)

        if "contents" not in db[current_node_id]:
            db[current_node_id]["contents"] = []

        final_contents = []
        for item in temp_content:
            saved_item = {k: v for k, v in item.items() if k != "message_id"}
            final_contents.append(saved_item)

        change_target = context.user_data.get("change_target")
        log_desc_parts = []
        
        bot_username = context.bot.username
        node_name = db[current_node_id]["name"]
        node_link = get_link(current_node_id, node_name, bot_username)

        if change_target:
            target_node = change_target["node_id"]
            idx = change_target["content_index"]
            replace_count = change_target.get("replace_count", 1)

            if (
                target_node == current_node_id
                and target_node in db
                and "contents" in db[target_node]
                and 0 <= idx < len(db[target_node]["contents"])
            ):
                # گرفتن کپی از موارد قدیمی پیش از حذف برای ثبت در لاگ
                old_items = db[target_node]["contents"][idx: idx + replace_count]
                
                # حذف موارد قبلی دیتابیس
                del db[target_node]["contents"][idx: idx + replace_count]

                # قراردادن موارد جدید
                for item in reversed(final_contents):
                    db[target_node]["contents"].insert(idx, item)

                msg_text = f"🔄 {replace_count} مورد قبلی حذف و {len(final_contents)} مورد جدید جایگزین شد."
                
                # ساخت لاگ جایگزینی
                log_desc_parts.append(f"🔄 <b>جایگزینی محتوا در پوشه {node_link}:</b>\n")
                log_desc_parts.append("<b>❌ موارد حذف شده:</b>")
                for i, o_item in enumerate(old_items, start=1):
                    log_desc_parts.append(get_item_log_details(o_item, i, bot_username))
                
                log_desc_parts.append("\n<b>📥 موارد جدید جایگزین شده:</b>")
                for i, n_item in enumerate(final_contents, start=1):
                    log_desc_parts.append(get_item_log_details(n_item, i, bot_username))
            else:
                db[current_node_id]["contents"].extend(final_contents)
                msg_text = "⚠️ خطا در تطابق مسیر! فایل‌ها به عنوان محتوای جدید به انتهای پوشه اضافه شدند."
                
                log_desc_parts.append(f"📥 <b>افزودن محتوا (به دلیل خطای مسیر جایگزینی) در پوشه {node_link}:</b>")
                for i, n_item in enumerate(final_contents, start=1):
                    log_desc_parts.append(get_item_log_details(n_item, i, bot_username))

            context.user_data.pop("change_target", None)
        else:
            db[current_node_id]["contents"].extend(final_contents)
            msg_text = f"{len(final_contents)} مورد ذخیره شد."
            
            # ساخت لاگ اضافه شدن محتوای جدید
            log_desc_parts.append(f"📥 <b>اضافه شدن فایل/محتوای جدید به پوشه {node_link}:</b>\n")
            for i, n_item in enumerate(final_contents, start=1):
                log_desc_parts.append(get_item_log_details(n_item, i, bot_username))

        context.user_data.pop("sent_mapping", None)

        # ساخت و تنظیم لاگ نهایی ادمین
        desc = "\n\n".join(log_desc_parts)
        
        if change_target:
            action_type = "جایگزینی محتوا"
        else:
            action_type = "افزودن محتوا"
        
        log_caption = format_admin_log(update.effective_user, desc)
        backup_caption = format_backup_caption(update.effective_user, action_type)
        
        set_pending_caption(context, log_caption)
        set_pending_backup_caption(context, backup_caption)
        
        save_db(db, context=context)
        
        context.user_data.pop("temp_content", None)

        await msg.reply_text(
            msg_text,
            reply_markup=get_keyboard(current_node_id, True, user_id=update.effective_user.id),
        )
        return CHOOSING

    if text == "حذف" and msg.reply_to_message:
        del_id = msg.reply_to_message.message_id
        before = len(temp_content)
        temp_content[:] = [item for item in temp_content if item.get("message_id") != del_id]

        if len(temp_content) != before:
            await msg.reply_text("محتوا از لیست موقت حذف شد.")
        else:
            await msg.reply_text("این پیام در لیست موقت پیدا نشد.")

        return WAITING_CONTENT

    content = extract_message_content(msg)
    if content:
        content["message_id"] = msg.message_id
        temp_content.append(content)

        try:
            await msg.set_reaction("👍")
        except Exception:
            pass

    return WAITING_CONTENT

def build_input_media(item, is_first=False, forced_caption=None, forced_entities=None):
    """
    از روی item ذخیره‌شده، آبجکت InputMedia مناسب می‌سازد.
    فقط برای typeهای قابل استفاده در media_group استفاده شود.
    """
    msg_type = item.get("type")
    file_id = item.get("file_id")

    kwargs = {"media": file_id}

    if is_first:
        caption = forced_caption if forced_caption is not None else (item.get("caption") or "")
        entities = forced_entities if forced_entities is not None else item.get("entities")

        if caption:
            kwargs["caption"] = caption

            if entities:
                kwargs["caption_entities"] = entities
            else:
                kwargs["parse_mode"] = "HTML"

    if msg_type == "photo":
        return InputMediaPhoto(**kwargs)

    if msg_type == "video":
        return InputMediaVideo(**kwargs)

    if msg_type == "document":
        return InputMediaDocument(**kwargs)

    if msg_type == "audio":
        return InputMediaAudio(**kwargs)

    return None


async def send_single_content(message, item):
    """
    یک آیتم را به‌صورت تکی ارسال می‌کند و Message برمی‌گرداند.
    """
    msg_type = item.get("type")
    saved_entities = item.get("entities")

    if msg_type == "text":
        if saved_entities is not None:
            return await message.reply_text(
                text=item["text"],
                entities=saved_entities,
                disable_web_page_preview=True,
            )
        return await message.reply_text(
            text=item["text"],
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    if msg_type in {"photo", "video", "document", "audio", "voice", "animation"}:
        file_id = item["file_id"]
        caption = item.get("caption", "")

        send_args = {"caption": caption}
        if saved_entities is not None:
            send_args["caption_entities"] = saved_entities
        else:
            send_args["parse_mode"] = "HTML"

        if msg_type == "photo":
            return await message.reply_photo(photo=file_id, **send_args)

        if msg_type == "video":
            return await message.reply_video(video=file_id, **send_args)

        if msg_type == "document":
            return await message.reply_document(document=file_id, **send_args)

        if msg_type == "audio":
            return await message.reply_audio(audio=file_id, **send_args)

        if msg_type == "voice":
            return await message.reply_voice(voice=file_id, **send_args)

        if msg_type == "animation":
            return await message.reply_animation(animation=file_id, **send_args)

    if msg_type == "video_note":
        return await message.reply_video_note(video_note=item["file_id"])

    if msg_type == "sticker":
        return await message.reply_sticker(sticker=item["file_id"])

    return None


async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edited = update.edited_message
    if not edited:
        return

    temp_content = context.user_data.get("temp_content")
    if not temp_content:
        return

    for index, item in enumerate(temp_content):
        if item.get("message_id") != edited.message_id:
            continue

        new_content = extract_message_content(edited)

        if new_content is None:
            temp_content.pop(index)
        else:
            new_content["message_id"] = edited.message_id
            temp_content[index] = new_content

        try:
            await edited.set_reaction("✏️")
        except Exception:
            pass

        break


async def restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # لغو
    if update.message.text == "❌ لغو":
        current = context.user_data.get('current_node', 'root')
        await update.message.reply_text(
            "لغو شد.",
            reply_markup=get_keyboard(current, True, user_id=update.effective_user.id)
        )
        return CHOOSING

    document = update.message.document
    if not document:
        await update.message.reply_text("❌ لطفاً ZIP یا JSON ارسال کنید.")
        return WAITING_RESTORE_FILE

    filename = document.file_name.lower()
    file = await document.get_file()
    byte_array = await file.download_as_bytearray()

    try:
        # ۱. آماده‌سازی کپشن لاگ‌گیری در ابتدا
        desc = "📥 بکاپ جدید (ریستور دیتابیس) وارد شد."
        log_caption = format_admin_log(update.effective_user, desc)
        backup_caption = format_backup_caption(update.effective_user, "ریستور بکاپ")
        
        set_pending_caption(context, log_caption)
        set_pending_backup_caption(context, backup_caption)
        

        # ============================
        # CASE 1: فایل JSON مستقیم
        # ============================
        if filename.endswith(".json"):
            with open(DB_FILE, "wb") as f:
                f.write(byte_array)

            # آپلود بکاپ در تلگرام با کپشن گزارش تغییرات ادمین
            restored_db = load_db()
            save_db(restored_db, context=context)

            # پاکسازی تاریخچه و ریستارت نود به root
            context.user_data.pop("admin_history", None)
            context.user_data.pop("admin_future", None)
            context.user_data["current_node"] = "root"

            await update.message.reply_text(
                "✅ database.json با موفقیت وارد شد.",
                reply_markup=get_keyboard("root", True, user_id=update.effective_user.id)
            )
            return CHOOSING

        # ============================
        # CASE 2: فایل ZIP شامل database.json
        # ============================
        if filename.endswith(".zip"):
            with zipfile.ZipFile(iolib.BytesIO(byte_array)) as zf:
                db_name = None
                for name in zf.namelist():
                    if name.endswith("database.json"):
                        db_name = name
                        break

                if not db_name:
                    await update.message.reply_text("❌ فایل ZIP فاقد database.json است.")
                    return WAITING_RESTORE_FILE

                with open(DB_FILE, "wb") as f:
                    f.write(zf.read(db_name))

            # آپلود بکاپ در تلگرام با کپشن گزارش تغییرات ادمین
            restored_db = load_db()
            save_db(restored_db, context=context)

            # پاکسازی تاریخچه و ریستارت نود به root
            context.user_data.pop("admin_history", None)
            context.user_data.pop("admin_future", None)
            context.user_data["current_node"] = "root"
            
            await update.message.reply_text(
                "✅ بکاپ ZIP با موفقیت وارد شد.",
                reply_markup=get_keyboard("root", True, user_id=update.effective_user.id)
            )
            return CHOOSING

        # اگر هیچکدام نبود:
        await update.message.reply_text("❌ فقط ZIP یا JSON قابل قبول است.")
        return WAITING_RESTORE_FILE

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بازگردانی:\n{e}")
        return WAITING_RESTORE_FILE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get('current_node', 'root')

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = update.effective_user.id in ADMIN_IDS or update.effective_user.id in sub_admins
    
    await update.message.reply_text(
        "لغو شد.",
        reply_markup=get_keyboard(current, is_admin, user_id=update.effective_user.id)
    )
    
    return CHOOSING

def build_application():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"^\s*file-id\s*:"),
            handle_direct_getfile
        ),
        group=-1
    )

    # هندلرهای واقعاً سراسری و تک‌مرحله‌ای
    application.add_handler(CommandHandler("green", set_node_style), group=0)
    application.add_handler(CommandHandler("blue", set_node_style), group=0)
    application.add_handler(CommandHandler("red", set_node_style), group=0)
    application.add_handler(CommandHandler("none", set_node_style), group=0)
    application.add_handler(CommandHandler("clear", clear_favorites_cmd), group=0)
    application.add_handler(CommandHandler("on_of_search", toggle_smart_search), group=0)
    application.add_handler(
        MessageReactionHandler(
            handle_reaction,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION
        ),
        group=0
    )

    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), not_started),
        group=0
    )
    
    # ادیت پیام
    application.add_handler(
        MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edit),
        group=2
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSING: [
                CommandHandler("report", report_page),
                CommandHandler("deeplink", deeplink_command),
                CommandHandler("chat", start_chat_with_admin),
                CommandHandler("file_id", file_id_command), # 👈 اضافه شدن کامند جدید به منو
                CommandHandler("change", handle_reply_change),
                CommandHandler("del", handle_reply_delete),
                #CommandHandler("clear", clear_favorites_cmd),

                CallbackQueryHandler(inline_handler, pattern="^reply_to_admin$"),
                CallbackQueryHandler(inline_handler, pattern="^admin_"),

                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_navigation),
            ],

            WAITING_BUTTON_NAME: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), add_button_name)
            ],

            WAITING_CONTENT: [
                # اگر خواستی در این state هم /del یا /change قابل استفاده باشد:
                CommandHandler("change", handle_reply_change),
                CommandHandler("del", handle_reply_delete),
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_content)
            ],

            WAITING_RESTORE_FILE: [
                MessageHandler(filters.Document.ALL, restore_backup),
                MessageHandler(filters.TEXT, restore_backup)
            ],

            WAITING_RENAME_BUTTON: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), rename_button)
            ],

            WAITING_REPORT_TEXT: [
                CommandHandler("no_messager", report_without_message),
                CommandHandler("cansel", cancel_report),
                MessageHandler(filters.TEXT & (~filters.COMMAND), receive_report_text),
            ],
            
            WAITING_ADMIN_PASSWORD_EDIT: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), set_admin_password)
            ],

            WAITING_USERDATA_UPLOAD: [
                MessageHandler(filters.Document.ALL, restore_userdata),
                MessageHandler(filters.TEXT & (~filters.COMMAND), restore_userdata)
            ],

            WAITING_ADD_ADMIN: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), add_sub_admin)
            ],

            WAITING_REMOVE_ADMIN: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), remove_sub_admin)
            ],

            WAITING_BAN_USER: [
                CallbackQueryHandler(inline_handler, pattern="^admin_"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), receive_ban_user_id)
            ],

            WAITING_UNBAN_USER: [
                CallbackQueryHandler(inline_handler, pattern="^admin_"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), receive_unban_user_id)
            ],

            WAITING_BROADCAST_CONTENT: [
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_broadcast_content)
            ],

            WAITING_PICK_USER_FOR_MSG: [
                CallbackQueryHandler(inline_handler, pattern="^admin_msg_pick_page_"),
                CallbackQueryHandler(handle_user_id_input, pattern="^admin_send_msg_to_"),
                CallbackQueryHandler(inline_handler, pattern="^admin_users_message$"),
                CallbackQueryHandler(inline_handler, pattern="^admin_users$"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_id_input),
            ],

            WAITING_SINGLE_USER_CONTENT: [
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_broadcast_content)
            ],

            WAITING_START_PAGE_CONTENT: [
                CallbackQueryHandler(inline_handler, pattern="^admin_save_start_page$"),
                CallbackQueryHandler(inline_handler, pattern="^admin_cancel_start_page$"),
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_start_page_content)
            ],

            WAITING_CHAT_MESSAGE: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_chat_message),
            ]
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("change", handle_reply_change),
            CommandHandler("del", handle_reply_delete),
            CommandHandler("cansel", cancel_report),
            #CommandHandler("clear", clear_favorites_cmd),
        ],
        allow_reentry=True,
    )

    application.add_handler(conv_handler, group=1)

    return application

# ================= HEALTH & WEBHOOK =================
async def health(request):
    return web.Response(text="OK")

async def webhook_handler(request):
    app = request.app["tg"]
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return web.Response(text="OK")

# ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======  ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======
# ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======  ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======
# ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======  ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======
# ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======  ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======
# ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======  ===👆🏻=== COMMEN CODE FOR BABIES/FATHER ===☝🏻=== COMMEN CODE FOR BABIES/FATHER =======




# ======= MAIN for FATHER ======= ======= MAIN for FATHER ======= ======= MAIN for FATHER ======= ======= MAIN for FATHER ======= ======= MAIN for FATHER ======= ======= MAIN for FATHER =

# ================= MAIN ================
async def main():
    tg_app = build_application()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.set_webhook(
        f"{WEBHOOK_URL}/{TOKEN}",
        allowed_updates=[
            "message",
            "edited_message",
            "callback_query",
            "message_reaction",
            "message_reaction_count",
        ],
        drop_pending_updates=True,
    )
    # aiohttp web app برای Health check و Webhook
    webapp = web.Application()
    webapp["tg"] = tg_app
    webapp.router.add_get("/", health)
    webapp.router.add_get("/health", health)
    webapp.router.add_post(f"/{TOKEN}", webhook_handler)

    runner = web.AppRunner(webapp)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()

    # ❌ دیگر tg_app.start() نیاز نیست
    # await tg_app.start()

    # برنامه همیشه اجرا باقی بماند
    await asyncio.Event().wait()

if __name__=="__main__":
    asyncio.run(main())
