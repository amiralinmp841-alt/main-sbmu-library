import logging
import json
import os
import io
import uuid
import zipfile
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InputFile
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
)
import copy
from flask import Flask
import threading

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

# --- CONFIGURATION ---

# --- admin pannel
ADMIN_ACCESSIBILITY_NAME = os.getenv("ADMIN_ACCESSIBILITY_NAME")
# --- webhook_url مخصوص رندر
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
# توکن و آیدی عددی ادمین از متغیرهای محیطی خوانده می‌شود

TOKEN = os.getenv("TOKEN")
import os

# خواندن لیست ادمین‌ها از متغیر محیطی
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
    WAITING_REMOVE_ADMIN
) = range(9)


# --- DATABASE HANDLERS ---
def load_db():
    if not os.path.exists(DB_FILE):
        # ساختار اولیه: روت (خانه)
        initial_db = {
            "root": {
                "name": "خانه",
                "parent": None,
                "children": [], # لیست دکمه‌های زیرمجموعه (ID نودهای فرزند)
                "contents": []  # لیست محتواهای این صفحه (File_IDs)
            }
        }
        save_db(initial_db)
        return initial_db
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_db(data):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




# فایل بکاپ روزانه
BACKUP_FILE = "/tmp/backup_database.zip"
# --- user data --- --- --- --- --- --- --- --- --- -----------------------------------------------
def load_userdata():
    if not os.path.exists("userdata.json"):
        return {}
    with open("userdata.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_userdata(data):
    with open("userdata.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

userdata = load_userdata()

# --- KEYBOARD BUILDERS --- ------------------------------------------------------------------------
def get_keyboard(node_id, is_admin):
    db = load_db()
    node = db.get(node_id)
    
    if not node:
        return ReplyKeyboardMarkup([["/start"]], resize_keyboard=True)

    keyboard = []
    
    # دکمه‌های موجود (پوشه‌ها) را اضافه کن
    children_ids = node.get("children", [])
    row = []
    for child_id in children_ids:
        child_node = db.get(child_id)
        if child_node:
            row.append(KeyboardButton(child_node["name"]))
            if len(row) == 2: # چینش دو تایی
                keyboard.append(row)
                row = []
    if row:
        keyboard.append(row)

    # دکمه‌های کنترلی ادمین
    if is_admin:
        keyboard.append(["➕ افزودن دکمه", "➕ افزودن محتوا"])
        keyboard.append(["🗑 حذف دکمه", "🧹 حذف محتوای صفحه"])
        keyboard.append(["✏️ ویرایش نام دکمه", "🔑 دریافت هش و لینک دکمه", "🔀 جابه‌جایی چیدمان"])
        keyboard.append(["📥 دریافت بکاپ", "📤 وارد کردن بکاپ"])
        keyboard.append(["↩️", "↪️"])
        #keyboard.append([os.getenv("ADMIN_ACCESSIBILITY_NAME")])


    # دکمه‌های بازگشت
    nav_row = []
    if node.get("parent"):
        nav_row.append("🔙 بازگشت")
    
    nav_row.append("🏠 صفحه اصلی")
    keyboard.append(nav_row)

    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- HELPER FUNCTIONS ---
async def send_node_contents(update: Update, context: ContextTypes.DEFAULT_TYPE, node_id: str):
    """محتواهای موجود در نود فعلی را ارسال می‌کند"""
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


# --- HANDLERS ---
async def not_started(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user_id = update.effective_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    # پاک‌سازی کامل وضعیت قبلی
    context.user_data.clear()

    load_db()
    db = load_db()

    args = context.args  # 👈 payload اینجاست

    # 🔗 اگر start با هش اومده
    if args:
        target_id = args[0]

        if target_id in db:
            context.user_data["current_node"] = target_id

            await update.message.reply_text(
                f"📂 {db[target_id]['name']}",
                reply_markup=get_keyboard(target_id, is_admin)
            )

            await send_node_contents(update, context, target_id)
            return CHOOSING

    # 🏠 start عادی
    context.user_data["current_node"] = "root"

    await update.message.reply_text(
        "🕊️ به ربات دانشگاه خوش آمدید. (V_4.1.5🔥)",
        reply_markup=get_keyboard("root", is_admin)
    )

    return CHOOSING


async def handle_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    is_admin = (user_id in ADMIN_IDS) or (user_id in sub_admins)

    # --- Check Admin Password --- ----------------------------------------------------------
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


    # 1. هندل کردن بازگشت و خانه
    if text == "🏠 صفحه اصلی":
        context.user_data['current_node'] = 'root'
        await update.message.reply_text("به صفحه اصلی بازگشتید.", reply_markup=get_keyboard('root', is_admin))
        return CHOOSING
    
    if text == "🔙 بازگشت":
        parent = db[current_node_id].get('parent')
        if parent:
            context.user_data['current_node'] = parent
            await update.message.reply_text("بازگشت به عقب.", reply_markup=get_keyboard(parent, is_admin))
        else:
            context.user_data['current_node'] = 'root'
            await update.message.reply_text("شما در صفحه اصلی هستید.", reply_markup=get_keyboard('root', is_admin))
        return CHOOSING

    # --- 2. هندل کردن دستورات ادمین ---
    # --- Admin panel back handling -----------------------------------------
    if text == "🔙 بازگشت" and context.user_data.get("admin_panel"):
        panel = context.user_data["admin_panel"]
    
        if panel == "admin_mgmt":
            context.user_data["admin_panel"] = "access"
            await update.message.reply_text(
                "🔐 پنل مدیریت:",
                reply_markup=ReplyKeyboardMarkup([
                    ["👑 مدیریت ادمین‌ها"],
                    ["📤 دریافت userdata"],
                    ["📥 وارد کردن userdata"],
                    ["🔙 بازگشت"]
                ], resize_keyboard=True)
            )
            return CHOOSING
    
        if panel == "access":
            context.user_data.pop("admin_panel")
            await update.message.reply_text(
                "بازگشت به صفحه اصلی",
                reply_markup=get_keyboard("root", is_admin)
            )
            return CHOOSING
    
    # --- Admin Accessibility ---==============================================================
    if is_admin and text == os.getenv("ADMIN_ACCESSIBILITY_NAME"):
        context.user_data["admin_panel"] = "access"
        await update.message.reply_text(
            "🔐 پنل مدیریت:",
            reply_markup=ReplyKeyboardMarkup([
                ["👑 مدیریت ادمین‌ها"],
                ["📤 دریافت userdata"],
                ["📥 وارد کردن userdata"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )
        return CHOOSING

    # --- Admin Management ---
    if is_admin and text == "👑 مدیریت ادمین‌ها":
        context.user_data["admin_panel"] = "admin_mgmt"
        await update.message.reply_text(
            "👑 مدیریت ادمین‌ها:",
            reply_markup=ReplyKeyboardMarkup([
                ["🔑 تنظیم رمز ادمینی"],
                #["➕ افزودن ادمین", "➖ حذف ادمین"],
                #["📋 لیست ادمین‌ها"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )
        return CHOOSING

    if is_admin and text == "🔑 تنظیم رمز ادمینی":
        admin_pass = userdata.get("admin_password", "تعریف نشده")
        await update.message.reply_text(
            f"🔐 رمز ادمینی فعلی:\n\n<code>{admin_pass}</code>",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup([
                ["✏️ ویرایش رمز"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )
        return CHOOSING

    if is_admin and text == "✏️ ویرایش رمز":
        await update.message.reply_text(
            "✏️ رمز جدید ادمینی را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
        return WAITING_ADMIN_PASSWORD_EDIT
    if is_admin and text == "📤 دریافت userdata":
    
        userdata = load_userdata()
    
        json_bytes = json.dumps(userdata, ensure_ascii=False, indent=2).encode("utf-8")
    
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.writestr("userdata.json", json_bytes)
    
        zip_buffer.seek(0)
    
        await update.message.reply_document(
            document=zip_buffer,
            filename=".userdata.zip",
            caption="📦 بکاپ userdata"
        )
    
        return CHOOSING

    if is_admin and text == "📥 وارد کردن userdata":
        await update.message.reply_text(
            "📥 فایل .userdata.zip را ارسال کنید",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
        return WAITING_USERDATA_UPLOAD

    if is_admin and text == "➕ افزودن ادمین":
        await update.message.reply_text(
            "📝 آیدی عددی یا نام کاربری فرد مورد نظر را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
        return WAITING_ADD_ADMIN
    
    if is_admin and text == "➖ حذف ادمین":
        await update.message.reply_text(
            "📝 آیدی عددی یا نام کاربری ادمینی که میخواید حذف کنید را ارسال کنید:",
            reply_markup=ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)
        )
        return WAITING_REMOVE_ADMIN
    
    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=get_keyboard("admin_mgmt", True)
        )
        return CHOOSING
    
    
    #==============================================================================
            
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
            mem_zip = io.BytesIO()
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



# --- ADMIN ACTIONS HANDLERS --- ==========================================================================================================
async def set_admin_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # ❌ اگر کاربر منصرف شد
    if text in ["🔙 بازگشت", "❌ لغو"]:
        await update.message.reply_text(
            "لغو شد.",
            reply_markup=ReplyKeyboardMarkup([
                ["👑 مدیریت ادمین‌ها"],
                ["📤 دریافت userdata"],
                ["📥 وارد کردن userdata"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )
        return CHOOSING

    if len(text) < 4:
        await update.message.reply_text("❌ رمز خیلی کوتاه است.")
        return WAITING_ADMIN_PASSWORD_EDIT

    userdata = load_userdata()   # 👈 پایین توضیح دادم
    userdata["admin_password"] = text
    save_userdata(userdata)

    await update.message.reply_text(
        "✅ رمز ادمینی با موفقیت تغییر کرد.",
        reply_markup=ReplyKeyboardMarkup([
            ["👑 مدیریت ادمین‌ها"],
            ["📤 دریافت userdata"],
            ["📥 وارد کردن userdata"],
            ["🔙 بازگشت"]
        ], resize_keyboard=True)
    )
    return CHOOSING

async def restore_userdata(update: Update, context: ContextTypes.DEFAULT_TYPE):


    text = update.message.text

    if text in ["❌ لغو", "🔙 بازگشت"]:
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=ReplyKeyboardMarkup([
                ["👑 مدیریت ادمین‌ها"],
                ["📤 دریافت userdata"],
                ["📥 وارد کردن userdata"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )
        return CHOOSING

    # بقیه کد ریستور userdata که قبلاً نوشته بودیم

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".zip"):
        await update.message.reply_text("❌ فایل ZIP معتبر نیست")
        return WAITING_USERDATA_UPLOAD

    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zipf:
            if "userdata.json" not in zipf.namelist():
                await update.message.reply_text("❌ userdata.json داخل فایل نیست")
                return WAITING_USERDATA_UPLOAD

            userdata = json.loads(zipf.read("userdata.json").decode("utf-8"))

        save_userdata(userdata)

        await update.message.reply_text(
            "✅ userdata با موفقیت بازیابی شد",
            reply_markup=ReplyKeyboardMarkup([
                ["👑 مدیریت ادمین‌ها"],
                ["📤 دریافت userdata"],
                ["📥 وارد کردن userdata"],
                ["🔙 بازگشت"]
            ], resize_keyboard=True)
        )

        context.user_data["current_node"] = "admin_mgmt"
        return CHOOSING

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بازیابی:\n{e}")
        return WAITING_USERDATA_UPLOAD

async def add_sub_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # لغو
    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=get_keyboard("admin_mgmt", True)
        )
        return CHOOSING

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])

    # تشخیص عددی یا الفبایی
    try:
        new_admin_id = int(text)
    except ValueError:
        new_admin_id = text  # username

    # اگر ادمین اصلی باشد (ADMIN_IDS) نمی‌توان اضافه کرد
    if new_admin_id in ADMIN_IDS:
        await update.message.reply_text("❌ این فرد قبلاً ادمین اصلی است.")
        return WAITING_ADD_ADMIN

    # اضافه کردن
    if new_admin_id not in sub_admins:
        sub_admins.append(new_admin_id)
        userdata["sub_admins"] = sub_admins

        # شمارنده دکمه‌ها برای این ادمین
        if "sub_admins_buttons" not in userdata:
            userdata["sub_admins_buttons"] = {}
        if str(new_admin_id) not in userdata["sub_admins_buttons"]:
            userdata["sub_admins_buttons"][str(new_admin_id)] = 0

        save_userdata(userdata)

        await update.message.reply_text(
            f"✅ ادمین {new_admin_id} با موفقیت اضافه شد.",
            reply_markup=get_keyboard("admin_mgmt", True)
        )
        return CHOOSING
    else:
        await update.message.reply_text("❌ این فرد قبلاً ادمین فرعی است.")
        return WAITING_ADD_ADMIN

async def remove_sub_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # لغو
    if text == "❌ لغو":
        await update.message.reply_text(
            "❌ عملیات لغو شد.",
            reply_markup=get_keyboard("admin_mgmt", True)
        )
        return CHOOSING

    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])

    try:
        admin_id = int(text)
    except ValueError:
        admin_id = text

    # نمی‌توان ادمین اصلی را حذف کرد
    if admin_id in ADMIN_IDS:
        await update.message.reply_text("❌ نمی‌توان ادمین اصلی را حذف کرد.")
        return WAITING_REMOVE_ADMIN

    if admin_id in sub_admins:
        sub_admins.remove(admin_id)
        userdata["sub_admins"] = sub_admins
        # اختیاری: حذف شمارنده دکمه
        if "sub_admins_buttons" in userdata and str(admin_id) in userdata["sub_admins_buttons"]:
            userdata["sub_admins_buttons"].pop(str(admin_id))
        save_userdata(userdata)
        await update.message.reply_text(
            f"✅ ادمین {admin_id} حذف شد.",
            reply_markup=get_keyboard("admin_mgmt", True)
        )
        return CHOOSING
    else:
        await update.message.reply_text("❌ این فرد ادمین نیست.")
        return WAITING_REMOVE_ADMIN

async def list_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    userdata = load_userdata()
    sub_admins = userdata.get("sub_admins", [])
    buttons_count = userdata.get("sub_admins_buttons", {})

    msg = "👑 ادمین‌های اصلی:\n"
    for aid in ADMIN_IDS:
        count = buttons_count.get(str(aid), 0)
        msg += f"- {aid} | تعداد دکمه: {count}\n"

    msg += "\n👤 ادمین‌های فرعی:\n"
    # مرتب‌سازی فرعی‌ها بر اساس تعداد دکمه اضافه شده (زیاد به کم)
    sorted_sub_admins = sorted(sub_admins, key=lambda x: buttons_count.get(str(x),0), reverse=True)
    for aid in sorted_sub_admins:
        count = buttons_count.get(str(aid), 0)
        msg += f"- {aid} | تعداد دکمه: {count}\n"

    await update.message.reply_text(msg, reply_markup=get_keyboard("admin_mgmt", True))
    return CHOOSING

#=============================================================================================================================================
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

            db[new_id] = {
                "name": old["name"],
                "parent": new_parent,
                "children": [],
                "contents": old.get("contents", []).copy()
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

    await update.message.reply_text(
        f"دکمه '{text}' ساخته شد.",
        reply_markup=get_keyboard(current_node_id, True)
    )

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
        with zipfile.ZipFile(io.BytesIO(byte_array)) as zf:
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
    await update.message.reply_text("لغو شد.", reply_markup=get_keyboard(current, update.effective_user.id == ADMIN_IDS))
    return CHOOSING


async def send_daily_backup(context: ContextTypes.DEFAULT_TYPE):
    backup_id = os.getenv("BACKUP_ID")
    if not backup_id:
        return

    backup_id = int(backup_id)

    if not os.path.exists(DB_FILE):
        return

    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(DB_FILE)

    mem_zip.seek(0)

    await context.bot.send_document(
        chat_id=backup_id,
        document=InputFile(
            mem_zip,
            filename=f"backup_{datetime.now().strftime('%Y%m%d')}.zip"
        ),
        caption="📦 بکاپ اتوماتیک دیتابیس"
    )




#---health check--------------------------------------------------------
#from flask import Flask
#import threading
#
#app = Flask(__name__)
#
#@app.route("/")
#def home():
#    return "OK", 200
#
#def run_flask():
#    app.run(host="0.0.0.0", port=10000)
#
#threading.Thread(target=run_flask).start()
# --- MAIN -------------------------------------------------------------
# --- MAIN --- مخصوص رندر
# --- MAIN -------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        print("Error: TOKEN not found in environment variables.")
        exit(1)

    # ساخت اپلیکیشن ربات
    application = ApplicationBuilder().token(TOKEN).build()

    # 🔔 پیام‌های بدون /start → not_started
    application.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            not_started
        ),
        group=0
    )

    # ⏱ بکاپ اتوماتیک
    application.job_queue.run_repeating(
        send_daily_backup,
        interval=8 * 60 * 60,
        first=10
    )

    # ConversationHandler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            CHOOSING: [
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
            ]
            
        },
        fallbacks=[CommandHandler('start', start)]
    )

    application.add_handler(conv_handler, group=1)
    
    # --- Health check جداگانه برای uptime.com ---
    from flask import Flask
    import threading

    app = Flask(__name__)

    @app.route("/")
    def home():
        return "OK", 200

    def run_flask():
        app.run(host="0.0.0.0", port=8080)

    threading.Thread(target=run_flask, daemon=True).start()

    # --- حالا ربات webhook رو اجرا کن ---
    # ❗ خیلی مهم
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
