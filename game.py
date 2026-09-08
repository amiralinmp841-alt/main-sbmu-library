import re
import aiohttp
from telegram import Update
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

# مراحل گفتگو (States)
GET_URL, GET_SCORE = range(2)

GAME_SET_SCORE_URL = "https://tbot.xyz/api/setScore"


def extract_game_data(url: str):
    """
    استخراج نام بازی و پارامتر data از لینک دریافتی
    """
    match = re.search(r"https?://tbot\.xyz/([^/#?]+)/#(.*)", url.strip())
    if not match:
        return None, None
    
    game_name = match.group(1)
    fragment = match.group(2)
    
    # تفکیک بخش اول تا قبل از & بعدی
    data_raw = fragment.split("&")[0]
    # حذف پیشوند data= در صورت وجود
    data = data_raw.replace("data=", "", 1) if data_raw.startswith("data=") else data_raw
    return game_name, data


async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    شروع فرآیند ثبت امتیاز (با دستور /hack یا دیپ‌لینک start=hack)
    """
    guide_text = (
        "🎮 **راهنمای ثبت امتیاز بازی‌های تلگرام:**\n\n"
        "1️⃣ وارد بازی مدنظرتان شوید (مثل Corsairs، Math Battle یا Lumberjack).\n"
        "2️⃣ روی علامت **سه نقطه (⋮)** بالای صفحه بزنید و گزینه **Open in Chrome** (یا Open in Browser) را انتخاب کنید.\n"
        "3️⃣ لینک کامل بالای مرورگر را کپی کرده و اینجا ارسال کنید.\n\n"
        "🔗 **لطفاً لینک بازی را ارسال کنید:**\n"
        "_(برای انصراف و بازگشت به منوی کتابخانه، دستور /start را ارسال کنید.)_"
    )
    if update.message:
        await update.message.reply_text(guide_text, parse_mode="Markdown")
    return GET_URL


async def receive_game_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    دریافت و اعتبارسنجی لینک بازی
    """
    user_text = update.message.text
    game_name, data = extract_game_data(user_text)

    if not game_name or not data:
        await update.message.reply_text(
            "⚠️ **فرمت لینک نامعتبر است!**\n\n"
            "لینک باید با `https://tbot.xyz/` شروع شود و شامل هش بازی باشد.\n"
            "لطفاً مجدداً لینک صحیح را بفرستید یا با ارسال /start خارج شوید.",
            parse_mode="Markdown"
        )
        return GET_URL

    # ذخیره مشخصات در context برای مرحله بعد
    context.user_data["hack_game_name"] = game_name
    context.user_data["hack_game_data"] = data

    await update.message.reply_text(
        f"✅ بازی شناسایی شد: **{game_name}**\n\n"
        "🔢 حالا **امتیاز دلخواه** خود را به صورت عدد لاتین بفرستید (مثلاً: `1250`):",
        parse_mode="Markdown"
    )
    return GET_SCORE


async def receive_game_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    دریافت امتیاز، ارسال درخواست به سرور و اعلام نتیجه
    """
    score_text = update.message.text.strip()

    if not score_text.isdigit():
        await update.message.reply_text(
            "⚠️ لطفاً فقط یک **عدد صحیح مثبت** به عنوان امتیاز ارسال کنید:"
        )
        return GET_SCORE

    score = int(score_text)
    game_name = context.user_data.get("hack_game_name")
    data = context.user_data.get("hack_game_data")

    # ساخت پی‌لود با هدرهای ضروری
    payload = f"data={data}&score={score}"
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": f"https://tbot.xyz/{game_name}/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    status_msg = await update.message.reply_text("⏳ در حال ارسال امتیاز به سرور...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GAME_SET_SCORE_URL, data=payload, headers=headers, timeout=10) as resp:
                resp_text = await resp.text()

                if resp.status == 200 and '"scores":true' in resp_text.replace(" ", ""):
                    result_msg = (
                        f"🎉 **امتیاز {score} برای بازی {game_name} با موفقیت ثبت شد!**\n\n"
                        f"📝 پاسخ سرور: `{resp_text}`\n\n"
                        "------------------------------------\n"
                        "🔄 اگر می‌خواهید برای بازی دیگری امتیاز‌لود با هدرهای ضروری
    payload = f"data={data}&score={score}"
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": f"https://tbot.xyz/{game_name}/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    status_msg = await update.message.reply_text("⏳ در حال ارسال امتیاز به سرور...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(GAME_SET_SCORE_URL, data=payload, headers=headers, timeout=10) as resp:
                resp_text = await resp.text()

                if resp.status == 200 and '"scores":true' in resp_text.replace(" ", ""):
                    result_msg = (
                        f"🎉 **امتیاز {score} برای بازی {game_name} با موفقیت ثبت شد!**\n\n"
                        f"📝 پاسخ سرور: `{resp_text}`\n\n"
                        "------------------------------------\n"
                        "🔄 اگر می‌خواهید برای بازی دیگری امتیاز ثبت کنید، **لینک جدید را بفرستید**.\n"
                        "🏠 برای بازگشت به منوی کتابخانه، دستور /start را ارسال کنید."
                    )
                else:
                    result_msg = (
                        f"❌ **ثبت امتیاز با خطا مواجه شد.**\n\n"
                        f"📝 پاسخ سرور: `{resp_text}` (کد وضعیت: {resp.status})\n"
                        "📌 _دلیل احتمالی: منقضی شدن توکن لینک بازی یا عدم پشتیبانی سرور._\n\n"
                        "------------------------------------\n"
                        "🔄 برای تلاش مجدد، **لینک جدید بازی را بفرستید**.\n"
                        "🏠 برای خ: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_game_score)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_game_flow),
        ],
        allow_reentry=True,
    )
