import os
import json
import uuid
import asyncio
from io import BytesIO
from datetime import datetime
import re
import pandas as pd
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

SCORE_FILE = os.getenv("SCORE_FILE", "/tmp/score.json")

# اگر می‌خواهی بکاپ نمرات در همان گروه بکاپ دیتابیس باشد،
# همین متغیر را برابر DB_BACKUP_CHAT_ID قرار بده.
SCORE_BACKUP_CHAT_ID = int(
    os.getenv("SCORE_BACKUP_CHAT_ID", "0") or "0"
)


# =========================================================
# STATE
# این مقدار باید از main.py به configure_score_system داده شود
# =========================================================

SCORE_ROOT = None
SCORE_WAITING_EXCEL = None
SCORE_WAITING_DELETE = None
SCORE_WAITING_BACKUP = None
SCORE_WAITING_NATIONAL_ID = None


# =========================================================
# DEPENDENCIES FROM MAIN.PY
# =========================================================

_upload_file_to_telegram = None
_download_latest_file_from_telegram = None
_get_admin_ids = None


def configure_score_system(
    *,
    score_root_state,
    score_waiting_excel_state,
    score_waiting_delete_state,
    score_waiting_backup_state,
    score_waiting_national_id_state,
    upload_file_to_telegram,
    download_latest_file_from_telegram,
    get_admin_ids,
):
    """
    این تابع از main.py صدا زده می‌شود تا score.py
    بتواند از سیستم فعلی بکاپ و ادمین استفاده کند.
    """

    global SCORE_ROOT
    global SCORE_WAITING_EXCEL
    global SCORE_WAITING_DELETE
    global SCORE_WAITING_BACKUP
    global SCORE_WAITING_NATIONAL_ID

    global _upload_file_to_telegram
    global _download_latest_file_from_telegram
    global _get_admin_ids

    SCORE_ROOT = score_root_state
    SCORE_WAITING_EXCEL = score_waiting_excel_state
    SCORE_WAITING_DELETE = score_waiting_delete_state
    SCORE_WAITING_BACKUP = score_waiting_backup_state
    SCORE_WAITING_NATIONAL_ID = score_waiting_national_id_state

    _upload_file_to_telegram = upload_file_to_telegram
    _download_latest_file_from_telegram = download_latest_file_from_telegram
    _get_admin_ids = get_admin_ids


# =========================================================
# JSON
# =========================================================

def get_empty_score_data():
    return {
        "version": 1,
        "updated_at": None,
        "courses": {}
    }


def load_scores():
    """
    اگر فایل وجود نداشت،
    دیکشنری خالی برمی‌گرداند.
    """

    if not os.path.exists(SCORE_FILE):
        return get_empty_score_data()

    try:
        with open(SCORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return get_empty_score_data()

        data.setdefault("version", 1)
        data.setdefault("updated_at", None)
        data.setdefault("courses", {})

        return data

    except Exception as e:
        print(f"❌ خطا در خواندن score.json: {e}")
        return get_empty_score_data()


def save_scores(data, upload=True):
    """
    ذخیره score.json
    و در صورت نیاز آپلود بکاپ جدید.
    """

    try:
        os.makedirs(
            os.path.dirname(SCORE_FILE),
            exist_ok=True
        )

        data["updated_at"] = datetime.now().isoformat()

        with open(
            SCORE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        print("💾 score.json ذخیره شد.")

    except Exception as e:
        print(f"❌ خطا در ذخیره score.json: {e}")
        return False

    if upload:
        return upload_score_backup()

    return True


# =========================================================
# TELEGRAM BACKUP
# =========================================================

def upload_score_backup():
    """
    ارسال آخرین نسخه score.json به گروه بکاپ.
    """

    if not SCORE_BACKUP_CHAT_ID:
        print(
            "⚠️ SCORE_BACKUP_CHAT_ID تنظیم نشده است."
        )
        return False

    if not _upload_file_to_telegram:
        print(
            "❌ سیستم آپلود فایل برای score.py تنظیم نشده."
        )
        return False

    try:
        result = _upload_file_to_telegram(
            chat_id=SCORE_BACKUP_CHAT_ID,
            file_path=SCORE_FILE,
            caption="score.json"
        )

        if result:
            print("⬆️ score.json در گروه بکاپ آپلود شد.")
            return True

        return False

    except Exception as e:
        print(f"❌ خطا در آپلود score.json: {e}")
        return False


def download_score_backup():
    """
    دانلود آخرین score.json از گروه بکاپ.
    """

    if not SCORE_BACKUP_CHAT_ID:
        print(
            "⚠️ SCORE_BACKUP_CHAT_ID تنظیم نشده است."
        )
        return False

    if not _download_latest_file_from_telegram:
        print(
            "❌ سیستم دانلود فایل برای score.py تنظیم نشده."
        )
        return False

    try:
        result = _download_latest_file_from_telegram(
            chat_id=SCORE_BACKUP_CHAT_ID,
            filename="score.json",
            save_path=SCORE_FILE
        )

        if result:
            print(
                "⬇️ آخرین score.json از گروه بکاپ دانلود شد."
            )
            return True

        return False

    except Exception as e:
        print(f"❌ خطا در دانلود score.json: {e}")
        return False


def restore_scores_on_startup():
    """
    هنگام بالا آمدن ربات،
    آخرین بکاپ score.json را دانلود می‌کند.

    اگر بکاپی وجود نداشت،
    فایل جدید و خالی می‌سازد.
    """

    try:
        restored = download_score_backup()

        if restored:
            print("✅ score.json از بکاپ بازیابی شد.")
            return True

        print(
            "ℹ️ هیچ بکاپ score.json پیدا نشد. "
            "فایل جدید ساخته می‌شود."
        )

        if not os.path.exists(SCORE_FILE):
            save_scores(
                get_empty_score_data(),
                upload=False
            )

        return False

    except Exception as e:
        print(f"⚠️ خطا در بازیابی score.json: {e}")

        if not os.path.exists(SCORE_FILE):
            save_scores(
                get_empty_score_data(),
                upload=False
            )

        return False


# =========================================================
# ADMIN CHECK
# =========================================================

def is_score_admin(user_id):
    try:
        admin_ids = _get_admin_ids()

        return user_id in admin_ids

    except Exception as e:
        print(f"❌ Admin check error: {e}")
        return False


# =========================================================
# DEEP LINK
# =========================================================

def create_course_token():
    """
    توکن یکتا برای Deep Link هر درس.
    """

    return uuid.uuid4().hex[:12]


def get_course_by_token(token):
    data = load_scores()

    for course_id, course in data["courses"].items():

        if course.get("token") == token:
            return course_id, course

    return None, None


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def get_score_admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ وارد کردن فایل اکسل جدید",
                callback_data="score_add_excel"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 حذف یک درس",
                callback_data="score_delete_course"
            )
        ],
        [
            InlineKeyboardButton(
                "📚 درس‌های ثبت‌شده",
                callback_data="score_list_courses"
            )
        ],
        [
            InlineKeyboardButton(
                "⚠️ حذف همه درس‌ها",
                callback_data="score_delete_all_confirm"
            )
        ],
        [
            InlineKeyboardButton(
                "📥 دریافت بکاپ score.json",
                callback_data="score_get_backup"
            )
        ],
        [
            InlineKeyboardButton(
                "📤 وارد کردن بکاپ score.json",
                callback_data="score_restore_backup"
            )
        ],
        [
            InlineKeyboardButton(
                "❌ بستن پنل",
                callback_data="score_close"
            )
        ]
    ])


def get_back_to_score_panel_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 بازگشت به پنل نمرات",
                callback_data="score_back"
            )
        ]
    ])


# =========================================================
# /score
# =========================================================

async def score_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    ورود ادمین به پنل نمرات.
    """

    user_id = update.effective_user.id

    if not is_score_admin(user_id):

        await update.effective_message.reply_text(
            "⛔ شما اجازه دسترسی به پنل نمرات را ندارید."
        )

        return SCORE_ROOT

    await update.effective_message.reply_text(
        "📊 <b>پنل مدیریت نمرات</b>\n\n"
        "از گزینه‌های زیر استفاده کنید:",
        parse_mode="HTML",
        reply_markup=get_score_admin_keyboard()
    )

    return SCORE_ROOT


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def score_callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id

    if not is_score_admin(user_id):

        await query.answer(
            "⛔ دسترسی ندارید.",
            show_alert=True
        )

        return SCORE_ROOT

    action = query.data

    # -----------------------------------------------------
    # برگشت
    # -----------------------------------------------------

    if action == "score_back":

        await query.edit_message_text(
            "📊 <b>پنل مدیریت نمرات</b>\n\n"
            "از گزینه‌های زیر استفاده کنید:",
            parse_mode="HTML",
            reply_markup=get_score_admin_keyboard()
        )

        return SCORE_ROOT

    # -----------------------------------------------------
    # بستن
    # -----------------------------------------------------

    if action == "score_close":

        await query.edit_message_text(
            "پنل مدیریت نمرات بسته شد."
        )

        return SCORE_ROOT

    # -----------------------------------------------------
    # افزودن اکسل
    # -----------------------------------------------------

    if action == "score_add_excel":

        context.user_data["score_mode"] = "add_excel"

        await query.edit_message_text(
            "📊 <b>افزودن آزمون جدید</b>\n\n"
            "لطفاً فایل Excel را ارسال کنید.\n\n"
            "فرمت‌های قابل قبول:\n"
            "• <code>.xlsx</code>\n"
            "• <code>.xls</code>\n\n"
            "در فایل باید یک ستون مربوط به کد ملی "
            "و یک ستون مربوط به نمره وجود داشته باشد.\n\n"
            "مثلاً:\n"
            "<code>کد ملی | نمره</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ لغو",
                        callback_data="score_back"
                    )
                ]
            ])
        )

        return SCORE_WAITING_EXCEL

    # -----------------------------------------------------
    # حذف درس
    # -----------------------------------------------------

    if action == "score_delete_course":

        data = load_scores()
        courses = data["courses"]

        if not courses:

            await query.edit_message_text(
                "📭 هنوز هیچ درسی ثبت نشده است.",
                reply_markup=get_back_to_score_panel_keyboard()
            )

            return SCORE_ROOT

        buttons = []

        for course_id, course in courses.items():

            buttons.append([
                InlineKeyboardButton(
                    f"🗑 {course.get('name', 'بدون نام')}",
                    callback_data=f"score_del_{course_id}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "🔙 بازگشت",
                callback_data="score_back"
            )
        ])

        await query.edit_message_text(
            "🗑 <b>حذف یک درس</b>\n\n"
            "درس موردنظر را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return SCORE_WAITING_DELETE

    # -----------------------------------------------------
    # حذف یک درس
    # -----------------------------------------------------

    if action.startswith("score_del_"):

        course_id = action.replace(
            "score_del_",
            "",
            1
        )

        data = load_scores()

        if course_id not in data["courses"]:

            await query.answer(
                "این درس دیگر وجود ندارد.",
                show_alert=True
            )

            return SCORE_ROOT

        course_name = data["courses"][course_id].get(
            "name",
            "بدون نام"
        )

        del data["courses"][course_id]

        save_scores(data)

        await query.edit_message_text(
            f"✅ درس <b>{course_name}</b> حذف شد.\n\n"
            "نسخه جدید score.json نیز بکاپ شد.",
            parse_mode="HTML",
            reply_markup=get_back_to_score_panel_keyboard()
        )

        return SCORE_ROOT

    # -----------------------------------------------------
    # لیست درس‌ها
    # -----------------------------------------------------

    if action == "score_list_courses":

        data = load_scores()
        courses = data["courses"]

        if not courses:

            await query.edit_message_text(
                "📭 هنوز هیچ درسی ثبت نشده است.",
                reply_markup=get_back_to_score_panel_keyboard()
            )

            return SCORE_ROOT

        text = (
            "📚 <b>درس‌های ثبت‌شده</b>\n\n"
        )

        for number, course in enumerate(
            courses.values(),
            start=1
        ):

            course_name = course.get(
                "name",
                "بدون نام"
            )

            student_count = len(
                course.get("scores", {})
            )

            token = course.get("token")
            bot_username = context.bot.username or (await context.bot.get_me()).username
            deep_link = f"https://t.me/{bot_username}?start=score_{token}"

            text += (
                f"{number}. <b>{course_name}</b>\n"
                f"👥 تعداد رکورد: "
                f"<code>{student_count}</code>\n"
                f"🔗 دیپ‌لینک:\n"
                f"<code>{deep_link}</code>\n\n"
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=get_back_to_score_panel_keyboard()
        )

        return SCORE_ROOT

    # -----------------------------------------------------
    # حذف همه
    # -----------------------------------------------------

    if action == "score_delete_all_confirm":

        await query.edit_message_text(
            "⚠️ <b>هشدار</b>\n\n"
            "آیا مطمئن هستید که می‌خواهید "
            "تمام درس‌ها و تمام نمرات را حذف کنید؟",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ بله، همه را حذف کن",
                        callback_data="score_delete_all_yes"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ انصراف",
                        callback_data="score_back"
                    )
                ]
            ])
        )

        return SCORE_ROOT

    if action == "score_delete_all_yes":

        data = get_empty_score_data()

        save_scores(data)

        await query.edit_message_text(
            "🗑 تمام درس‌ها و نمرات حذف شدند.\n\n"
            "نسخه جدید score.json بکاپ شد.",
            reply_markup=get_back_to_score_panel_keyboard()
        )

        return SCORE_ROOT


    # -----------------------------------------------------
    # دریافت بکاپ
    # -----------------------------------------------------

    if action == "score_get_backup":

        if not os.path.exists(SCORE_FILE):
            save_scores(
                get_empty_score_data(),
                upload=False
            )

        with open(SCORE_FILE, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename="score.json",
                caption="📥 بکاپ فعلی score.json"
            )

        await query.answer("بکاپ ارسال شد.")
        return SCORE_ROOT


    # -----------------------------------------------------
    # وارد کردن بکاپ
    # -----------------------------------------------------

    if action == "score_restore_backup":

        context.user_data["score_mode"] = "restore_backup"

        await query.edit_message_text(
            "📤 <b>وارد کردن بکاپ score.json</b>\n\n"
            "فایل JSON را ارسال کنید.\n\n"
            "⚠️ با وارد کردن فایل جدید، "
            "تمام اطلاعات فعلی نمرات جایگزین می‌شود.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ لغو",
                        callback_data="score_back"
                    )
                ]
            ])
        )

        return SCORE_WAITING_BACKUP

    return SCORE_ROOT

# =========================================================
# EXCEL HELPERS
# =========================================================

def normalize_national_id(value):
    """
    پیدا کردن و استانداردسازی کد ملی.

    کد ملی باید دقیقاً 10 رقم باشد.
    اگر داخل مقدار متن دیگری هم وجود داشته باشد،
    سعی می‌کنیم عدد 10 رقمی را از آن استخراج کنیم.
    """
    if pd.isna(value):
        return None

    value = str(value).strip()

    # تبدیل اعداد فارسی و عربی به انگلیسی
    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        )
    )

    # حذف .0 احتمالی اکسل
    if value.endswith(".0"):
        value = value[:-2]

    # حذف فاصله و خط تیره
    cleaned = (
        value
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
    )

    # پیدا کردن عدد دقیقاً 10 رقمی
    match = re.search(
        r"(?<!\d)(\d{10})(?!\d)",
        cleaned
    )

    if not match:
        return None

    national_id = match.group(1)

    # کد ملی معتبر باید دقیقاً 10 رقم باشد
    #if len(national_id) != 10:
    #    return None

    return national_id


def normalize_score(value):
    """
    تبدیل نمره به مقدار استاندارد.
    """
    if pd.isna(value):
        return None

    value = str(value).strip()

    # تبدیل اعداد فارسی و عربی به انگلیسی
    value = value.translate(
        str.maketrans(
            "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
            "01234567890123456789"
        )
    )

    if not value:
        return None

    # حذف فاصله‌های اضافی
    value = value.replace(" ", "")

    # اگر اکسل عدد را مثلاً 15.0 خوانده باشد
    if value.endswith(".0"):
        value = value[:-2]

    try:
        score_number = float(value)

        # جلوگیری از اینکه هر عددی به عنوان نمره شناخته شود
        # محدوده نمره 0 تا 20
        if 0 <= score_number <= 20:

            # اگر عدد صحیح بود
            if score_number.is_integer():
                return str(int(score_number))

            return str(score_number)

    except (ValueError, TypeError):
        pass

    return None


def extract_scores_from_dataframe(df):
    """
    استخراج هوشمند کد ملی و نمره از فایل Excel.

    منطق:
    1. در هر ردیف دنبال یک عدد 10 رقمی می‌گردیم.
    2. وقتی کد ملی پیدا شد، ستون‌های بعد از آن بررسی می‌شوند.
    3. اولین مقدار معتبر بین 0 تا 20 به عنوان نمره در نظر گرفته می‌شود.

    بنابراین نیازی به نام ستون «کد ملی» یا «نمره» نیست.
    """
    scores = {}

    # بدون وابستگی به عنوان ستون‌ها
    for _, row in df.iterrows():

        row_values = list(row.values)

        national_id = None
        national_id_index = None

        # ---------------------------------------------
        # پیدا کردن کد ملی در هر جای ردیف
        # ---------------------------------------------

        for index, value in enumerate(row_values):

            found_national_id = normalize_national_id(
                value
            )

            if found_national_id:
                national_id = found_national_id
                national_id_index = index
                break

        # اگر کد ملی پیدا نشد
        if not national_id:
            continue

        score = None

        # ---------------------------------------------
        # پیدا کردن اولین نمره بعد از کد ملی
        # ---------------------------------------------

        for value in row_values[national_id_index + 1:]:

            found_score = normalize_score(
                value
            )

            if found_score is not None:
                score = found_score
                break

        # اگر نمره پیدا نشد
        if score is None:
            continue

        scores[national_id] = score

    return scores


# =========================================================
# RECEIVE EXCEL
# =========================================================

async def receive_score_excel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    document = update.message.document

    if not document:
        await update.message.reply_text(
            "❌ لطفاً فایل Excel ارسال کنید."
        )
        return SCORE_WAITING_EXCEL

    filename = (
        document.file_name
        or ""
    ).lower()

    if not (
        filename.endswith(".xlsx")
        or filename.endswith(".xls")
    ):
        await update.message.reply_text(
            "❌ فقط فایل Excel با فرمت "
            ".xlsx یا .xls قابل قبول است."
        )
        return SCORE_WAITING_EXCEL

    try:
        await update.message.reply_text(
            "⏳ در حال بررسی و استخراج نمرات از فایل..."
        )

        file = await document.get_file()

        file_bytes = await file.download_as_bytearray()

        excel_file = BytesIO(file_bytes)

        # ---------------------------------------------
        # فایل را بدون وابستگی به Header بخوان
        # ---------------------------------------------

        df = pd.read_excel(
            excel_file,
            header=None,
            dtype=object
        )

        if df.empty:
            await update.message.reply_text(
                "❌ فایل اکسل خالی است."
            )
            return SCORE_WAITING_EXCEL

        # ---------------------------------------------
        # استخراج هوشمند نمرات
        # ---------------------------------------------

        scores = extract_scores_from_dataframe(df)

        if not scores:
            await update.message.reply_text(
                "❌ هیچ کد ملی و نمره معتبری "
                "در فایل پیدا نشد.\n\n"
                "ربات به صورت خودکار دنبال "
                "یک عدد ۱۰ رقمی به عنوان کد ملی "
                "و اولین نمره بعد از آن می‌گردد."
            )
            return SCORE_WAITING_EXCEL

        # ---------------------------------------------
        # ذخیره موقت تا گرفتن نام درس
        # ---------------------------------------------

        context.user_data["score_pending_excel"] = {
            "scores": scores,
            "file_name": document.file_name,
        }

        await update.message.reply_text(
            "✅ فایل اکسل با موفقیت خوانده شد.\n\n"
            f"👥 تعداد کد ملی و نمره پیدا شده: "
            f"<b>{len(scores)}</b>\n\n"
            "📚 حالا نام درس یا آزمون را ارسال کنید:",
            parse_mode="HTML"
        )

        context.user_data["score_mode"] = "waiting_course_name"

        return SCORE_WAITING_EXCEL

    except Exception as e:
        logging.exception("Excel read error")

        await update.message.reply_text(
            "❌ هنگام خواندن فایل اکسل خطایی رخ داد.\n\n"
            f"<code>{e}</code>",
            parse_mode="HTML"
        )

        return SCORE_WAITING_EXCEL

# =========================================================
# RECEIVE COURSE NAME
# =========================================================

async def receive_score_course_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    course_name = (
        update.message.text
        or ""
    ).strip()

    if not course_name:

        await update.message.reply_text(
            "❌ نام درس نمی‌تواند خالی باشد."
        )

        return SCORE_WAITING_EXCEL

    pending = context.user_data.get(
        "score_pending_excel"
    )

    if not pending:

        await update.message.reply_text(
            "❌ اطلاعات فایل اکسل پیدا نشد.\n"
            "لطفاً دوباره فایل اکسل را ارسال کنید."
        )

        context.user_data.pop(
            "score_mode",
            None
        )

        return SCORE_ROOT

    data = load_scores()

    # بررسی تکراری بودن نام
    for course in data["courses"].values():

        if (
            course.get("name", "").strip()
            == course_name
        ):

            await update.message.reply_text(
                "❌ درسی با این نام قبلاً ثبت شده است.\n\n"
                "یک نام دیگر ارسال کنید."
            )

            return SCORE_WAITING_EXCEL

    course_id = uuid.uuid4().hex

    token = create_course_token()

    data["courses"][course_id] = {
        "id": course_id,
        "name": course_name,
        "token": token,
        "created_at": datetime.now().isoformat(),
        "scores": pending["scores"]
    }

    save_scores(data)

    context.user_data.pop(
        "score_pending_excel",
        None
    )

    context.user_data.pop(
        "score_mode",
        None
    )

    await update.message.reply_text(
        "✅ <b>درس با موفقیت ثبت شد.</b>\n\n"
        f"📚 نام درس: <b>{course_name}</b>\n"
        f"👥 تعداد دانشجو: "
        f"<b>{len(pending['scores'])}</b>\n\n"
        "🔗 <b>دیپ‌لینک آزمون:</b>\n\n"
        f"<code>https://t.me/"
        f"{context.bot.username}"
        f"?start=score_{token}</code>\n\n"
        "هر کاربر از طریق این لینک وارد "
        "بخش دریافت نمره همین آزمون می‌شود.",
        parse_mode="HTML"
    )

    return SCORE_ROOT


# =========================================================
# WAITING EXCEL STATE
# =========================================================

async def handle_score_excel_state(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    mode = context.user_data.get(
        "score_mode"
    )

    if mode == "waiting_course_name":

        return await receive_score_course_name(
            update,
            context
        )

    return await receive_score_excel(
        update,
        context
    )


# =========================================================
# RESTORE BACKUP
# =========================================================

async def receive_score_backup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    document = update.message.document

    if not document:

        await update.message.reply_text(
            "❌ لطفاً فایل score.json را ارسال کنید."
        )

        return SCORE_WAITING_BACKUP

    filename = (
        document.file_name
        or ""
    ).lower()

    if not filename.endswith(".json"):

        await update.message.reply_text(
            "❌ فقط فایل JSON قابل قبول است."
        )

        return SCORE_WAITING_BACKUP

    try:

        file = await document.get_file()

        file_bytes = await file.download_as_bytearray()

        restored_data = json.loads(
            bytes(file_bytes).decode("utf-8")
        )

        if not isinstance(
            restored_data,
            dict
        ):

            raise ValueError(
                "ساختار فایل معتبر نیست."
            )

        restored_data.setdefault(
            "version",
            1
        )

        restored_data.setdefault(
            "updated_at",
            None
        )

        restored_data.setdefault(
            "courses",
            {}
        )

        if not isinstance(
            restored_data["courses"],
            dict
        ):

            raise ValueError(
                "ساختار courses معتبر نیست."
            )

        save_scores(restored_data)

        context.user_data.pop(
            "score_mode",
            None
        )

        await update.message.reply_text(
            "✅ بکاپ score.json با موفقیت وارد شد.\n\n"
            f"📚 تعداد درس‌های فعلی: "
            f"{len(restored_data['courses'])}\n\n"
            "نسخه جدید نیز در گروه بکاپ ذخیره شد.",
        )

        return SCORE_ROOT

    except Exception as e:

        print(
            f"❌ Score backup restore error: {e}"
        )

        await update.message.reply_text(
            f"❌ فایل JSON معتبر نیست:\n"
            f"<code>{e}</code>",
            parse_mode="HTML"
        )

        return SCORE_WAITING_BACKUP


# =========================================================
# USER DEEP LINK
# =========================================================

async def handle_score_deeplink(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    token: str
):
    """
    این تابع از داخل start اصلی صدا زده می‌شود.
    """

    course_id, course = get_course_by_token(token)

    if not course:

        await update.effective_message.reply_text(
            "❌ این لینک دریافت نمره معتبر نیست "
            "یا آزمون موردنظر حذف شده است."
        )

        return None

    context.user_data["score_course_id"] = course_id
    context.user_data["score_mode"] = "user_waiting_national_id"

    await update.effective_message.reply_text(
        "📊 <b>دریافت نمره امتحان</b>\n\n"
        f"📚 آزمون / درس: <b>{course.get('name')}</b>\n\n"
        "لطفاً <b>کد ملی</b> خود را ارسال کنید.\n\n"
        "برای برگشت به حالت عادی ربات، "
        "دستور /start را بزنید.",
        parse_mode="HTML"
    )

    return SCORE_WAITING_NATIONAL_ID


# =========================================================
# USER SENDS NATIONAL ID
# =========================================================

async def receive_national_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = (
        update.message.text
        or ""
    ).strip()

    national_id = normalize_national_id(text)

    #if not national_id:
#
    #    await update.message.reply_text(
    #        "❌ لطفاً یک کد ملی معتبر ارسال کنید."
    #    )
#
    #    return SCORE_WAITING_NATIONAL_ID

    course_id = context.user_data.get(
        "score_course_id"
    )

    if not course_id:

        await update.message.reply_text(
            "❌ اطلاعات آزمون پیدا نشد.\n\n"
            "لطفاً دوباره از لینک آزمون وارد شوید."
        )

        return SCORE_ROOT

    data = load_scores()

    course = data["courses"].get(
        course_id
    )

    if not course:

        await update.message.reply_text(
            "❌ این آزمون دیگر وجود ندارد."
        )

        context.user_data.pop(
            "score_course_id",
            None
        )

        context.user_data.pop(
            "score_mode",
            None
        )

        return SCORE_ROOT

    score = course.get(
        "scores",
        {}
    ).get(national_id)

    if score is None:

        await update.message.reply_text(
            "❌ نمره‌ای با این کد ملی پیدا نشد.\n\n"
            "لطفاً کد ملی را دوباره بررسی کنید."
        )

        return SCORE_WAITING_NATIONAL_ID

    await update.message.reply_text(
        "🎉 <b>نمره شما پیدا شد</b>\n\n"
        f"📚 درس: <b>{course.get('name')}</b>\n"
        f"📝 نمره: <b>{score}</b>\n\n"
        "برای دریافت نمره دوباره، کد ملی دیگری بفرستید.\n\n"
        "برای برگشت به حالت عادی ربات، "
        "دستور /start را بزنید.",
        parse_mode="HTML"
    )

    return SCORE_WAITING_NATIONAL_ID
