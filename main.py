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
    InlineKeyboardMarkup
)

from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ConversationHandler,
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

# --- wewb port ---
PORT = int(os.environ.get("PORT", 10000))

# ------ userdata -------
USERDATA_FILE = "/tmp/userdata.json"

# --- admin pannel
ADMIN_ACCESSIBILITY_NAME = os.getenv("ADMIN_ACCESSIBILITY_NAME")

# --- webhook_url مخصوص رندر
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# توکن و آیدی عددی ادمین از متغیرهای محیطی خوانده می‌شود
TOKEN = os.getenv("TOKEN")

REPORT_GROUP_ID = int(os.getenv("REPORT_GROUP_ID", "0") or "0")

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
    WAITING_CHAT_MESSAGE 
) = range(15)

# ============ TELEGRAM USER API BACKUP CONFIG ============

DB_FILE = "/tmp/database.json"
USERDATA_FILE = "/tmp/userdata.json"

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH")
TG_SESSION_STRING = os.getenv("TG_SESSION_STRING")

DB_BACKUP_CHAT_ID = int(os.getenv("DB_BACKUP_CHAT_ID", "0"))
USERDATA_BACKUP_CHAT_ID = int(os.getenv("USERDATA_BACKUP_CHAT_ID", "0"))


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

async def _upload_file_to_telegram(chat_id, file_path, caption=None):
    try:
        if not os.path.exists(file_path):
            print(f"❌ File not found for upload: {file_path}")
            return False

        await telethon_client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption or f"backup: {os.path.basename(file_path)}"
        )

        print(f"⬆️ Uploaded to Telegram group: {file_path}")
        return True

    except Exception as e:
        print(f"❌ Failed to upload file to Telegram: {e}")
        return False


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


def upload_file_to_telegram(chat_id, file_path, caption=None):
    return run_telethon(
        _upload_file_to_telegram(chat_id, file_path, caption)
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


def upload_db_to_telegram():
    return upload_file_to_telegram(
        chat_id=DB_BACKUP_CHAT_ID,
        file_path=DB_FILE,
        caption="database.json"
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


def save_db(data):
    # ذخیره لوکال
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print("💾 DB saved locally")

    except Exception as e:
        print("❌ Failed to save DB locally:", e)
        return False

    # ارسال به گروه تلگرام
    return upload_db_to_telegram()


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
    """

    user = update.effective_user
    if not user:
        return

    user_id = str(user.id)

    userdata = load_userdata()
    users = userdata.setdefault("users", {})

    old_data = users.get(user_id, {})
    old_count = int(old_data.get("message_count", 0))

    full_name = user.full_name or "بدون نام"
    username = user.username

    users[user_id] = {
        "id": user.id,
        "full_name": full_name,
        "username": username,
        "message_count": old_count + 1 if count_message else old_count,
        "banned": bool(old_data.get("banned", False)),
        "first_seen": old_data.get("first_seen") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    new_count = users[user_id]["message_count"]

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

    new_style = styles[command]

    if new_style is None:
        db[current_node_id].pop("style", None)
    else:
        db[current_node_id]["style"] = new_style

    save_db(db)

    parent_id = db[current_node_id].get("parent", "root")
    context.user_data["current_node"] = parent_id

    color_names = {
        "/green": "سبز",
        "/blue": "آبی",
        "/red": "قرمز",
        "/none": "بدون رنگ"
    }

    await update.message.reply_text(
        f"✅ رنگ این پوشه به «{color_names[command]}» تغییر یافت.",
        reply_markup=get_keyboard(parent_id, True)
    )


# --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS --- --- KEYBOARD BUILDERS -

def get_keyboard(node_id, is_admin):
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

    # --- دکمه‌های کنترلی ادمین ---
    if is_admin:
        # برای ادمین هم اگر می‌خواهی رنگی باشند، باید مشابه بالا از KeyboardButton استفاده کنی
        # فعلاً به همون شکلی که داشتی گذاشتم که بهم نریزه
        keyboard.append(["➕ افزودن دکمه", "➕ افزودن محتوا"])
        keyboard.append(["🗑 حذف دکمه", "🧹 حذف محتوای صفحه"])
        keyboard.append(["✏️ ویرایش نام دکمه", "🔑 دریافت هش و لینک دکمه", "🔀 جابه‌جایی چیدمان"])
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
            InlineKeyboardButton("❌ بستن پنل", callback_data="admin_close")
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

def set_report_page(context: ContextTypes.DEFAULT_TYPE, node_id: str):
    """
    ذخیره صفحه فعلی برای قابلیت /report
    """
    context.user_data["current_report_node"] = node_id

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

    # اولویت با صفحه‌ای است که برای ریپورت ذخیره شده
    node_id = context.user_data.get("current_report_node")

    # اگر نبود، از current_node استفاده کن
    if not node_id:
        node_id = context.user_data.get("current_node", "root")

    if node_id not in db:
        await update.message.reply_text("❌ صفحه فعلی برای گزارش پیدا نشد.")
        return CHOOSING

    node = db[node_id]

    bot_username = context.bot.username
    deep_link = f"https://t.me/{bot_username}?start={node_id}"

    page_name = html.escape(node.get("name", "بدون نام"))
    path_text = html.escape(get_node_path_text(db, node_id))

    full_name = html.escape(user.full_name or "بدون نام")
    username = user.username

    if username:
        username_text = f"@{html.escape(username)}"
    else:
        username_text = "ندارد"

    user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'

    report_text = (
        "🚨 <b>گزارش صفحه</b>\n\n"
        f"👤 <b>کاربر گزارش‌دهنده:</b> {user_link}\n"
        f"🆔 <b>آیدی عددی کاربر:</b> <code>{user.id}</code>\n"
        f"🔗 <b>یوزرنیم:</b> <code>{username_text}</code>\n\n"
        f"📄 <b>نام صفحه:</b> {page_name}\n"
        f"📂 <b>مسیر صفحه:</b>\n{path_text}\n\n"
        f"🔑 <b>هش صفحه:</b>\n<code>{html.escape(node_id)}</code>\n\n"
        f"🔗 <b>دیپ‌لینک صفحه:</b>\n{html.escape(deep_link)}"
    )

    try:
        await context.bot.send_message(
            chat_id=REPORT_GROUP_ID,
            text=report_text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

        await update.message.reply_text("✅ گزارش شما برای مدیریت ارسال شد.")

    except Exception as e:
        print("Failed to send report:", e)
        await update.message.reply_text("❌ ارسال گزارش با خطا مواجه شد.")

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
    if not REPORT_GROUP_ID:
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

    full_name = html.escape(user.full_name or "بدون نام")
    username = user.username
    username_text = f"@{html.escape(username)}" if username else "ندارد"
    user_link = f'<a href="tg://user?id={user.id}">{full_name}</a>'

    header = (
        "📨 <b>پیام جدید برای مدیریت</b>\n\n"
        f"👤 <b>کاربر:</b> {user_link}\n"
        f"🆔 <b>آیدی عددی:</b> <code>{user.id}</code>\n"
        f"🔗 <b>یوزرنیم:</b> <code>{username_text}</code>\n\n"
        "📩 <b>محتوا:</b>"
    )

    try:
        await context.bot.send_message(
            chat_id=REPORT_GROUP_ID,
            text=header,
            parse_mode="HTML"
        )

        # پیام اصلی را کپی کن
        await context.bot.copy_message(
            chat_id=REPORT_GROUP_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id
        )

        await update.message.reply_text("✅ پیام شما برای مدیریت ارسال شد.")

    except Exception as e:
        print("Failed to send chat message:", e)
        await update.message.reply_text("❌ ارسال پیام با خطا مواجه شد.")

    return CHOOSING

async def send_node_contents(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: str):
    """محتواهای موجود در نود فعلی را ارسال می‌کند"""
    set_report_page(context, node_id)
    db = load_db()
    contents = db[node_id].get("contents", [])
    
    if not contents:
        return

    for item in contents:
        try:
            msg_type = item['type']
            if msg_type == 'text':
                await update.message.reply_text(item['text'], parse_mode="HTML")
            else:
                file_id = item['file_id']
                caption = item.get('caption', '')
            
                if msg_type == 'photo':
                    await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="HTML")
                elif msg_type == 'video':
                    await update.message.reply_video(video=file_id, caption=caption, parse_mode="HTML")
                elif msg_type == 'document':
                    await update.message.reply_document(document=file_id, caption=caption, parse_mode="HTML")
                elif msg_type == 'audio':
                    await update.message.reply_audio(audio=file_id, caption=caption, parse_mode="HTML")
                elif msg_type == 'voice':
                    await update.message.reply_voice(voice=file_id, caption=caption, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error sending content: {e}")
            
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

    if not results:
        await update.message.reply_text("🔍 نتیجه‌ای در این پوشه یافت نشد.")
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
        msg += f"امتیاز تطابق: {int(item['score'])}٪\n\n"

    msg += "روی مسیر آبی‌رنگ کلیک کنید تا مستقیم به آنجا بروید."

    await update.message.reply_text(
        msg, 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )

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
    context.user_data.clear()

    db = load_db()

    args = context.args  # 👈 payload اینجاست

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

                path_text = get_node_path_text(db, target_id)

                await update.message.reply_text(
                    f"📂 مسیر:\n{path_text}",
                    reply_markup=get_keyboard(parent_id, is_admin)
                )

                await send_node_contents(update, context, target_id)
                return CHOOSING

            # 👑 ادمین، یا نودی که فرزند دارد => خود پوشه باز شود
            context.user_data["current_node"] = target_id

            path_text = get_node_path_text(db, target_id)

            await update.message.reply_text(
                f"📂 مسیر:\n{path_text}",
                reply_markup=get_keyboard(target_id, is_admin)
            )

            await send_node_contents(update, context, target_id)
            return CHOOSING

    # 🏠 start عادی
    context.user_data["current_node"] = "root"
    set_report_page(context, "root")
    
    await update.message.reply_text(
        """🕊 به ربات دانشگاه خوش آمدید. (V_4.5.2)
    
    🔍 برای یافتن فایل مورد نظر، میتوانید به صورت متنی سرچ کنید.
    مثل: وویس جلسه اول باکتری شناسی بهمن 403، جزوه فیزیولوژی کلیه و...
    
    یا اینکه از دکمه‌های آماده استفاده کنید.""",
        reply_markup=get_keyboard("root", is_admin)
    )
    return CHOOSING

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
            reply_markup=get_keyboard(current, is_admin)
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
        await update.message.reply_text("❌ عملیات لغو شد.", reply_markup=get_keyboard("root", True))
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
        
        await update.message.reply_text(f"✅ پیام شما با موفقیت به {count} کاربر ارسال شد.", reply_markup=get_keyboard("root", True))
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


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    track_user_activity(update, count_message=True)
    text = update.message.text
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
    
            await update.message.reply_text("✅ رمز تایید شد.\nشما اکنون ادمین هستید 😎")
    
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
            reply_markup=get_keyboard(current, is_admin)
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
                reply_markup=get_keyboard("root", is_admin)
            )
            return CHOOSING

    # 1. هندل کردن بازگشت و خانه
    if text == "🏠 صفحه اصلی":
        context.user_data['current_node'] = 'root'
        set_report_page(context, "root")
        await update.message.reply_text("به صفحه اصلی بازگشتید.", reply_markup=get_keyboard('root', is_admin))
        return CHOOSING
    
    if text == "🔙 بازگشت":
        parent = db[current_node_id].get('parent')
    
        if parent:
            context.user_data['current_node'] = parent
            set_report_page(context, parent)
    
            await update.message.reply_text(
                "بازگشت به عقب.",
                reply_markup=get_keyboard(parent, is_admin)
            )
        else:
            context.user_data['current_node'] = 'root'
            set_report_page(context, "root")
    
            await update.message.reply_text(
                "شما در صفحه اصلی هستید.",
                reply_markup=get_keyboard('root', is_admin)
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
        REPORT_GROUP_ID = os.getenv("REPORT_GROUP_ID")
        user = update.effective_user
    
        safe_name = html.escape(user.full_name or "کاربر")
        user_link = f'<a href="tg://user?id={user.id}">{safe_name}</a>'
    
        await context.bot.send_message(
            chat_id=REPORT_GROUP_ID,
            text=f"📩 پاسخ جدید از طرف {user_link} (<code>{user.id}</code>):",
            parse_mode="HTML"
        )
    
        await context.bot.copy_message(
            chat_id=REPORT_GROUP_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )
    
        await update.message.reply_text("✅ پیام شما به مدیریت ارسال شد.")
        context.user_data["waiting_for_user_reply"] = False
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
            
                save_db(db)
                await update.message.reply_text(
                    f"دکمه '{target_name}' و تمام زیرمجموعه‌هایش حذف شد.",
                    reply_markup=get_keyboard(current_node_id, is_admin)
                )
            else:
                await update.message.reply_text("دکمه یافت نشد.", reply_markup=get_keyboard(current_node_id, is_admin))
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

        if text == "✏️ ویرایش نام دکمه":
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
            push_admin_history(context, db)
            db[current_node_id]["contents"] = []
            save_db(db)
            await update.message.reply_text(
                "🧹 محتوای این صفحه حذف شد.",
                reply_markup=get_keyboard(current_node_id, True)
            )
            return CHOOSING

        if text == "🔑 دریافت هش و لینک دکمه":
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
        

        if text == "🔀 جابه‌جایی چیدمان":
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
                reply_markup=get_keyboard(current_node_id, is_admin)
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
                save_db(db)
        
                for key in ["reorder_remaining", "reorder_result", "reorder_mode"]:
                    context.user_data.pop(key, None)
        
                await update.message.reply_text(
                    "✅ چیدمان جدید ذخیره شد.",
                    reply_markup=get_keyboard(current_node_id, True)
                )
                return CHOOSING

        if text == "↩️" and is_admin:
            history = context.user_data.get("admin_history", [])
            future = context.user_data.get("admin_future", [])
        
            if not history:
                await update.message.reply_text("⛔️ چیزی برای بازگشت وجود ندارد.")
                return CHOOSING
        
            # وضعیت فعلی میره تو future
            future.append(copy.deepcopy(load_db()))
        
            # آخرین snapshot
            last_db = history.pop()
        
            save_db(last_db)
        
            # 🔒 برای جلوگیری از کرش
            context.user_data["current_node"] = "root"
        
            await update.message.reply_text(
                "↩️ آخرین تغییر بازگردانده شد.",
                reply_markup=get_keyboard("root", True)
            )
            return CHOOSING
        
        

        if text == "↪️" and is_admin:
            history = context.user_data.get("admin_history", [])
            future = context.user_data.get("admin_future", [])
        
            if not future:
                await update.message.reply_text("⛔️ چیزی برای جلو رفتن نیست.")
                return CHOOSING
        
            # وضعیت فعلی بره history
            history.append(copy.deepcopy(load_db()))
        
            next_db = future.pop()
            save_db(next_db)
        
            context.user_data["current_node"] = "root"
        
            await update.message.reply_text(
                "↪️ تغییر دوباره اعمال شد.",
                reply_markup=get_keyboard("root", True)
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
    
            # 👤 کاربر عادی + دکمه بدون فرزند
            if not is_admin and not child_node.get("children"):
                # فقط محتوا را نمایش بده، بدون تغییر صفحه
                await send_node_contents(update, context, child_id)
                return CHOOSING
    
            # 👑 ادمین یا دکمه دارای فرزند
            context.user_data['current_node'] = child_id
    
            await update.message.reply_text(
                f"📂 {child_node['name']}",
                reply_markup=get_keyboard(child_id, is_admin)
            )
    
            await send_node_contents(update, context, child_id)
            return CHOOSING

    # ✅ اگر دکمه نبود، سرچ کن
    return await handle_smart_search(update, context, text, is_admin)

async def rename_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "❌ لغو":
        current = context.user_data.get("current_node", "root")
        await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(current, True))
        return CHOOSING

    new_name = update.message.text
    target_id = context.user_data.get("rename_target")

    db = load_db()
    if target_id in db:
        push_admin_history(context, db)  # 👈 اینجا
        db[target_id]["name"] = new_name
        save_db(db)

    current = context.user_data.get("current_node", "root")
    await update.message.reply_text("✅ نام دکمه ویرایش شد.", reply_markup=get_keyboard(current, True))
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
    
    # بقیه کد ریستور userdata که قبلاً نوشته بودیم

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".zip"):
        await update.message.reply_text("❌ فایل ZIP معتبر نیست")
        return WAITING_USERDATA_UPLOAD

    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()

    try:
        with zipfile.ZipFile(iolib.BytesIO(file_bytes)) as zipf:
            if "userdata.json" not in zipf.namelist():
                await update.message.reply_text("❌ userdata.json داخل فایل نیست")
                return WAITING_USERDATA_UPLOAD

            userdata = json.loads(zipf.read("userdata.json").decode("utf-8"))

        save_userdata(userdata)

        context.user_data.pop("admin_waiting_from", None)
        
        await update.message.reply_text(
            "✅ userdata با موفقیت بازیابی شد",
            reply_markup=ReplyKeyboardRemove()
        )
        
        await update.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=get_admin_access_inline_keyboard()
        )
        
        context.user_data["admin_panel"] = "access"
        
        # current_node را دست نزن؛ اگر نبود، root بگذار
        context.user_data.setdefault("current_node", "root")
        
        return CHOOSING
        
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
        
        # 📩 ارسال پیام به ادمین جدید
        try:
            await context.bot.send_message(
                chat_id=new_admin,
                text="🎉 شما به عنوان ادمین فرعی ربات منصوب شدید."
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
        
        # 📩 ارسال پیام به کاربر حذف‌شده
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text="⚠️ شما از لیست ادمین‌های ربات حذف شدید."
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
        reply_markup=get_keyboard("admin_mgmt", True),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

    return CHOOSING

# === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END === ADMIN ACTIONS HANDLERS END= 

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
        await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(current, True))
        return CHOOSING

    db = load_db()
    current_node_id = context.user_data.get('current_node', 'root')

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
                "style": old.get("style")  # <--- این خط را اضافه کن
            }
            
            for child in old.get("children", []):
                child_new_id = clone_node(child, new_id)
                db[new_id]["children"].append(child_new_id)

            return new_id

        push_admin_history(context, db)  # 👈 اینجا
        new_root_id = clone_node(source_id, current_node_id)
        db[current_node_id]["children"].append(new_root_id)
        save_db(db)

        await update.message.reply_text(
            "✅ دکمه با تمام زیرمجموعه‌ها کپی شد.",
            reply_markup=get_keyboard(current_node_id, True)
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

    push_admin_history(context, db)  # 👈 اینجا
    db[current_node_id]["children"].append(new_id)
    save_db(db)

    #await update.message.reply_text(
    #    f"دکمه '{text}' ساخته شد.",
    #    reply_markup=get_keyboard(current_node_id, True)
    #)

    # تعداد دکمه اضافه شده هر ادمین
    userdata = load_userdata()
    if "sub_admins_buttons" not in userdata:
        userdata["sub_admins_buttons"] = {}
    
    user_id = update.effective_user.id
    current_count = userdata["sub_admins_buttons"].get(str(user_id), 0)
    userdata["sub_admins_buttons"][str(user_id)] = current_count + 1
    save_userdata(userdata)
    
    await update.message.reply_text(
        f"✅ دکمه '{text}' ساخته شد.",
        reply_markup=get_keyboard(current_node_id, True)
    )
    return CHOOSING


async def receive_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text
    
    if text == "❌ لغو":
        current = context.user_data.get('current_node', 'root')
        await update.message.reply_text("عملیات لغو شد.", reply_markup=get_keyboard(current, True))
        return CHOOSING

    if text == "✅ ثبت نهایی":
        # ذخیره محتوا در دیتابیس
        temp_content = context.user_data.get('temp_content', [])
        if temp_content:
            current_node_id = context.user_data.get('current_node', 'root')
            db = load_db()
            push_admin_history(context, db)

            if "contents" not in db[current_node_id]:
                db[current_node_id]["contents"] = []
            
            db[current_node_id]["contents"].extend(temp_content)
            save_db(db)
            await update.message.reply_text(f"{len(temp_content)} مورد ذخیره شد.", reply_markup=get_keyboard(current_node_id, True))
        else:
            current = context.user_data.get('current_node', 'root')
            await update.message.reply_text("موردی برای ذخیره وجود نداشت.", reply_markup=get_keyboard(current, True))
        
        return CHOOSING

    # پردازش فایل دریافتی
    content_data = None
    
    if msg.photo:
        content_data = {'type': 'photo', 'file_id': msg.photo[-1].file_id, 'caption': msg.caption_html or msg.caption, "format": "HTML"}
    elif msg.video:
        content_data = {'type': 'video', 'file_id': msg.video.file_id, 'caption': msg.caption_html or msg.caption, "format": "HTML"}
    elif msg.document:
        content_data = {'type': 'document', 'file_id': msg.document.file_id, 'caption': msg.caption_html or msg.caption, "format": "HTML"}
    elif msg.audio:
        content_data = {'type': 'audio', 'file_id': msg.audio.file_id, 'caption': msg.caption_html or msg.caption, "format": "HTML"}
    elif msg.voice:
        content_data = {'type': 'voice', 'file_id': msg.voice.file_id, 'caption': msg.caption_html or msg.caption, "format": "HTML"}
    elif msg.text and not msg.text.startswith('/'):
        content_data = {'type': 'text', 'text': msg.text_html, "format": "HTML"}

    if content_data:
        context.user_data['temp_content'].append(content_data)
        # یک ری اکشن یا پیام کوتاه برای اطمینان کاربر
        try:
            await update.message.set_reaction("👍") # فقط در نسخه های جدید تلگرام کار میکنه
        except:
            pass
    
    return WAITING_CONTENT

async def restore_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # لغو
    if update.message.text == "❌ لغو":
        current = context.user_data.get('current_node', 'root')
        await update.message.reply_text(
            "لغو شد.",
            reply_markup=get_keyboard(current, True)
        )
        return CHOOSING

    document = update.message.document
    if not document or not document.file_name.endswith(".zip"):
        await update.message.reply_text("لطفاً یک فایل ZIP ارسال کنید.")
        return WAITING_RESTORE_FILE

    file = await document.get_file()
    byte_array = await file.download_as_bytearray()

    try:
        with zipfile.ZipFile(iolib.BytesIO(byte_array)) as zf:
            # 🔍 پیدا کردن database.json بدون توجه به مسیر
            db_name = None
            for name in zf.namelist():
                if name.endswith("database.json"):
                    db_name = name
                    break

            if not db_name:
                await update.message.reply_text(
                    "❌ فایل database.json در بکاپ یافت نشد."
                )
                return WAITING_RESTORE_FILE

            # ✅ نوشتن دیتابیس
            with open(DB_FILE, "wb") as f:
                f.write(zf.read(db_name))
            upload_db_to_telegram()

        # 🔥 پاک کردن لاگ تغییرات ادمین
        context.user_data.pop("admin_history", None)
        context.user_data.pop("admin_future", None)

        context.user_data["current_node"] = "root"

        await update.message.reply_text(
            "✅ بکاپ با موفقیت وارد شد.\n"
            "🔄 تاریخچه تغییرات پاک شد.",
            reply_markup=get_keyboard("root", True)
        )
        return CHOOSING

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بازگردانی: {e}")
        return WAITING_RESTORE_FILE


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current = context.user_data.get('current_node', 'root')

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = update.effective_user.id in ADMIN_IDS or update.effective_user.id in sub_admins
    
    await update.message.reply_text(
        "لغو شد.",
        reply_markup=get_keyboard(current, is_admin)
    )
    
    return CHOOSING


# ======== BUILD APPLICATION ========  ======== BUILD APPLICATION ======== ======== BUILD APPLICATION ======== ======== BUILD APPLICATION ======== ======== BUILD APPLICATION ========
def build_application():

    # ساخت اپلیکیشن ربات
    application = ApplicationBuilder().token(TOKEN).build()

    # دستورات رنگی ادمین
    application.add_handler(CommandHandler("green", set_node_style), group=0)
    application.add_handler(CommandHandler("blue", set_node_style), group=0)
    application.add_handler(CommandHandler("red", set_node_style), group=0)
    application.add_handler(CommandHandler("none", set_node_style), group=0)

    # 🔔 پیام‌های بدون /start → not_started
    application.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            not_started
        ),
        group=0
    )

    # ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [
                CommandHandler("report", report_page),
                CommandHandler("chat", start_chat_with_admin),
                CallbackQueryHandler(inline_handler, pattern="^reply_to_admin$"),
                CallbackQueryHandler(inline_handler, pattern="^admin_"),
                MessageHandler(filters.TEXT & (~filters.COMMAND), handle_navigation)
            ],
            WAITING_BUTTON_NAME: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), add_button_name)
            ],
            WAITING_CONTENT: [
                MessageHandler(filters.ALL & (~filters.COMMAND), receive_content)
            ],
            WAITING_RESTORE_FILE: [
                MessageHandler(filters.Document.ALL, restore_backup),
                MessageHandler(filters.TEXT, restore_backup)
            ],
            WAITING_RENAME_BUTTON: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), rename_button)
            ],
            WAITING_ADMIN_PASSWORD_EDIT: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), set_admin_password)
            ],
            WAITING_USERDATA_UPLOAD: [
                MessageHandler(filters.Document.ALL, restore_userdata),
                MessageHandler(filters.TEXT & (~filters.COMMAND), restore_userdata)  # برای لغو یا متن اشتباه
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
            WAITING_CHAT_MESSAGE: [
                MessageHandler(filters.TEXT & (~filters.COMMAND), receive_chat_message),
                CommandHandler("cancel", cancel)
            ]
        },
        fallbacks=[CommandHandler('start', start)]
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
    await tg_app.bot.set_webhook(f"{WEBHOOK_URL}/{TOKEN}")

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
