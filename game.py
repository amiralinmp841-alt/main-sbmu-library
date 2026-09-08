# game.py
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
    """استخراج نام بازی و پارامتر data از لینک بازی"""
    match = re.search(r"https?://tbot\.xyz/([^/#?]+)/#(.*)", url.strip())
    if not match:
        return None, None

    game_name = match.group(1)
    fragment = match.group(2)
    data_raw = fragment.split("&")[0]
    data = (
        data_raw.replace("data=", "", 1)
        if data_raw.startswith("data=")
        else data_raw
    )
    return game_name, data


async def game_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع هدایت و دریافت لینک بازی (از /hack یا deeplink)"""
    guide_text = (
        "🎮 **راهنمای ثبت امتیاز بازی‌های تلگرام:**\n\n"
        "1️⃣ وارد بازی مدنظرتان شوید.\n"
        "2️⃣ روی سه‌نقطه (⋮) بزنید و گزینه Open in Chrome را انتخاب کنید.\n"
        "3️⃣ لینک کامل را کپی و اینجا ارسال کنید.\n\n"
        "🔗 **لینک بازی را ارسال کنید:**\n"
        "_(برای خروج از این حالت، دستور /start را بزنید.)_"
    )
    if update.message:
        await update.message.reply_text(guide_text, parse_mode="Markdown")
    return GET_URL


async def receive_game_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت لینک، استخراج نام بازی و درخواست امتیاز"""
    game_name, data = extract_game_data(update.message.text)

    if not game_name or not data:
        await update.message.reply_text(
            "⚠️ **فرمت لینک نامعتبر است.**\n"
            "لطفاً لینک `https://tbot.xyz/...` صحیح را بفرستید.\n"
            "برای خروج: /start"
        )
        return GET_URL

    context.user_data["hack_game_name"] = game_name
    context.user_data["hack_game_data"] = data

    await update.message.reply_text(
        f"✅ بازی **{game_name}** شناسایی شد.\n\n"
        "🔢 برای این بازی امتیاز دلخواه را به صورت عدد لاتین ارسال کنید:\n"
        "_(مثال: `1250`)_"
    )
    return GET_SCORE


async def receive_game_score(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت امتیاز، ارسال درخواست و نمایش نتیجه"""
    score_text = update.message.text.strip()

    if not score_text.isdigit():
        await update.message.reply_text("⚠️ لطفاً فقط عدد صحیح مثبت ارسال کنید:")
        return GET_SCORE

    score = int(score_text)
    game_name = context.user_data.get("hack_game_name")
    data = context.user_data.get("hack_game_data")

    payload = f"data={data}&score={score}"
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Referer": f"https://tbot.xyz/{game_name}/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    await update.message.reply_text("⏳ در حال ارسال امتیاز به سرور...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GAME_SET_SCORE_URL, data=payload, headers=headers, timeout=10
            ) as resp:
                resp_text = await resp.text()
                success = '"scores":true' in resp_text.replace(" ", "")  # "" هدایت

                if success:
                    result = (
                        f"🎉 **امتیاز {score} برای بازی {game_name} ثبت شد!**\n"
                        f"📝 پاسخ سرور: `{resp_text}`\n\n"
                        "🔄 برای بازی دیگری لینک جدید را بفرستید.\n"
                        "🏠 خروج: /start"
                    )
                else:
                    result = (
                        f"❌ ثبت امتیاز موفق نشد.\n"
                        f"📝 پاسخ سرور: `{resp_text}` (وضعیت {resp.status})\n"
                        "📌 احتمالاً توکن لینک منقضی شده.\n\n"
                        "🔄 لینک جدید بفرستید یا با /start خارج شوید."
                    )
    except Exception as exc:
        result = f"⚠️ خطای ارتباط: `{exc}`\n\n🏠 /start برای خروج"

    await update.message.reply_text(result, parse_mode="Markdown")

    # باقی ماندن در حالت GET_URL برای دریافت لینک بعدی (لوپ)
    return GET_URL


async def cancel_game_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """انصراف و بازگشت به منوی اصلی"""
    await update.message.reply_text("❌ عملیات هک لغو شد. به منوی اصلی بازگشتید.")
    return ConversationHandler.END


def build_game_handler():
    """ساخت ConversationHandler مخصوص بازی"""
    return ConversationHandler(
        entry_points=[
            CommandHandler("hack", game_start),
        ],
        states={
            GET_URL: [
                MessageHandler(
                    filters.TEXT & (~filters.COMMAND),
                    receive_game_url,
                )
            ],
            GET_SCORE: [
                MessageHandler(
                    filters.TEXT & (~filters.COMMAND),
                    receive_game_score,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_game_flow),
        ],
        allow_reentry=True,
    )
