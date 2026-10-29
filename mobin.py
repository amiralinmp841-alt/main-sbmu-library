# mobin.py
import os
import json
import asyncio
import logging
import zipfile
import hashlib
import io as iolib
import html
import random
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from telethon import TelegramClient
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji

mobin_logger = logging.getLogger("mobin")
mobin_logger.setLevel(logging.INFO)

# ---------- CONSTANTS ----------
MOBIN_DATA_FILE = "/tmp/mobin.json"
MOBIN_SESSIONS_DIR = "/tmp/mobin_sessions"
MOBIN_BACKUP_ZIP = "/tmp/mobin_backup.zip"
MOBIN_RESTORE_ZIP = "/tmp/mobin_restore.zip"
DEFAULT_PASSWORD = "من مبینم"

# ---------- STATES ----------
(
    MOBIN_ROOT,
    MOBIN_WAITING_PASSWORD,
    MOBIN_WAITING_NEW_PASSWORD,
    MOBIN_REACTION_WAITING_LINK,
    MOBIN_REACTION_WAITING_EMOJI,
    MOBIN_REACTION_WAITING_ACCOUNTS,
    MOBIN_WAITING_IMPORT_ZIP,
    MOBIN_WAITING_ADD_SESSION_ZIP,
    MOBIN_SESSIONS_MANAGER,
) = range(100, 109)

# ---------- GLOBAL CONTEXT ----------
_mobin_ctx = {
    "admin_ids": [],
    "backup_chat_id": 0,
    "backup_session_getter": None,
    "backup_upload": None,
    "backup_download": None,
    "loop_getter": None,
}

def configure_mobin(
    admin_ids,
    backup_chat_id,
    backup_session_getter,
    backup_upload,
    backup_download,
    loop_getter,
):
    _mobin_ctx["admin_ids"] = admin_ids
    _mobin_ctx["backup_chat_id"] = backup_chat_id
    _mobin_ctx["backup_session_getter"] = backup_session_getter
    _mobin_ctx["backup_upload"] = backup_upload
    _mobin_ctx["backup_download"] = backup_download
    _mobin_ctx["loop_getter"] = loop_getter
    os.makedirs(MOBIN_SESSIONS_DIR, exist_ok=True)

# =========================================================
# DATA STORE
# =========================================================
def _default_data() -> dict:
    return {
        "password": DEFAULT_PASSWORD,
        "api_id": 2040,
        "api_hash": "b18441a1ff607e10a989891a5462e627",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def load_mobin_data() -> dict:
    if not os.path.exists(MOBIN_DATA_FILE):
        data = _default_data()
        save_mobin_data(data, upload=False)
        return data
    try:
        with open(MOBIN_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        mobin_logger.error(f"load_mobin_data failed: {e}")
        return _default_data()

def save_mobin_data(data: dict, upload: bool = True) -> bool:
    try:
        with open(MOBIN_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        mobin_logger.error(f"save_mobin_data failed: {e}")
        return False
    if upload:
        _schedule_backup_upload()
    return True

# =========================================================
# SESSION MANAGEMENT (local)
# =========================================================
def list_sessions() -> list[str]:
    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        return []
    return sorted(
        f[:-8] for f in os.listdir(MOBIN_SESSIONS_DIR)
        if f.endswith(".session") and not f.startswith(".")
    )

def delete_session(name: str) -> bool:
    path = os.path.join(MOBIN_SESSIONS_DIR, f"{name}.session")
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception as e:
            mobin_logger.error(f"delete_session failed: {e}")
    return False

def delete_all_sessions() -> int:
    count = 0
    for name in list_sessions():
        if delete_session(name):
            count += 1
    return count

# =========================================================
# ANTI-DUPLICATE SESSION IMPORT
# =========================================================
def _file_sha256(data: bytes) -> str:
    """هش SHA256 از محتوای بایت‌ها — برای تشخیص session یکسان."""
    return hashlib.sha256(data).hexdigest()

def _existing_session_hashes() -> dict[str, str]:
    """map از hash → session_name برای همه session های موجود روی دیسک."""
    result: dict[str, str] = {}
    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        return result
    for fname in os.listdir(MOBIN_SESSIONS_DIR):
        if not fname.endswith(".session") or fname.startswith("."):
            continue
        path = os.path.join(MOBIN_SESSIONS_DIR, fname)
        try:
            with open(path, "rb") as f:
                result[_file_sha256(f.read())] = fname[:-8]
        except Exception:
            continue
    return result

def import_sessions_zip(
    raw_bytes: bytes,
    on_conflict: str = "skip",   # "skip" | "rename"
) -> dict:
    """
    ZIP را باز می‌کند و session ها را با محافظت کامل در برابر تکرار وارد می‌کند.

    محافظت‌ها:
      - اسم تکراری با session های موجود       → skip (یا rename)
      - اسم تکراری داخل خود ZIP                → skip دوم + گزارش
      - محتوا (اکانت) تکراری با session موجود  → skip
      - محتوا (اکانت) تکراری داخل خود ZIP      → skip دوم + گزارش

    Returns:
      {
        "imported":      [names],
        "skipped_name":  [(name, reason)],
        "skipped_hash":  [(name, existing_name)],
        "renamed":       [(old, new)],
        "errors":        [str],
      }
    """
    imported: list[str] = []
    skipped_name: list[tuple[str, str]] = []
    skipped_hash: list[tuple[str, str]] = []
    renamed: list[tuple[str, str]] = []
    errors: list[str] = []

    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        os.makedirs(MOBIN_SESSIONS_DIR, exist_ok=True)

    existing_hashes = _existing_session_hashes()   # hash → name
    existing_names = set(list_sessions())

    try:
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            seen_in_zip_names: set[str] = set()
            seen_in_zip_hashes: dict[str, str] = {}   # hash → base

            for entry in zf.namelist():
                base = os.path.basename(entry)
                if not base.endswith(".session") or base.startswith("."):
                    continue
                sess_name = base[:-8]
                if not sess_name:
                    continue

                # --- ۱) اسم تکراری داخل خود ZIP ---
                if base in seen_in_zip_names:
                    errors.append(f"{base}: اسم تکراری داخل ZIP (skip)")
                    continue
                seen_in_zip_names.add(base)

                # خواندن محتوا
                try:
                    data = zf.read(entry)
                except Exception as e:
                    errors.append(f"{base}: خواندن خطا ({e})")
                    continue

                content_hash = _file_sha256(data)

                # --- ۲) محتوای تکراری داخل خود ZIP ---
                if content_hash in seen_in_zip_hashes:
                    errors.append(
                        f"{base}: محتوای یکسان با {seen_in_zip_hashes[content_hash]} در ZIP (skip)"
                    )
                    continue
                seen_in_zip_hashes[content_hash] = base

                # --- ۳) محتوای تکراری با session موجود (اکانت تکراری) ---
                if content_hash in existing_hashes:
                    skipped_hash.append((sess_name, existing_hashes[content_hash]))
                    continue

                # --- ۴) اسم تکراری با session موجود ---
                final_name = sess_name
                if sess_name in existing_names:
                    if on_conflict == "skip":
                        skipped_name.append((sess_name, "اسم تکراری"))
                        continue
                    elif on_conflict == "rename":
                        i = 1
                        while f"{sess_name}_copy{i}" in existing_names:
                            i += 1
                        final_name = f"{sess_name}_copy{i}"
                        renamed.append((sess_name, final_name))
                    else:
                        skipped_name.append((sess_name, f"on_conflict نامعتبر: {on_conflict}"))
                        continue

                # --- نوشتن فایل ---
                final_path = os.path.join(MOBIN_SESSIONS_DIR, f"{final_name}.session")
                try:
                    with open(final_path, "wb") as f:
                        f.write(data)
                    imported.append(final_name)
                    existing_names.add(final_name)
                    existing_hashes[content_hash] = final_name
                except Exception as e:
                    errors.append(f"{final_name}: نوشتن خطا ({e})")

    except zipfile.BadZipFile as e:
        return {
            "imported": [], "skipped_name": [], "skipped_hash": [],
            "renamed": [], "errors": [f"ZIP معیوب: {e}"],
        }
    except Exception as e:
        return {
            "imported": [], "skipped_name": [], "skipped_hash": [],
            "renamed": [], "errors": [f"خطای کلی: {e}"],
        }

    return {
        "imported": imported,
        "skipped_name": skipped_name,
        "skipped_hash": skipped_hash,
        "renamed": renamed,
        "errors": errors,
    }

def _format_import_report(result: dict) -> str:
    """ساخت متن گزارش import برای نمایش به کاربر."""
    imported = result["imported"]
    skipped_name = result["skipped_name"]
    skipped_hash = result["skipped_hash"]
    renamed = result["renamed"]
    errors = result["errors"]

    lines = [f"✅ وارد شد: {len(imported)}"]
    if imported:
        lines.append("  " + ", ".join(f"<code>{n}</code>" for n in imported[:8]))
        if len(imported) > 8:
            lines.append(f"  ... و {len(imported) - 8} تای دیگر")

    if renamed:
        lines.append(f"\n📝 تغییر نام: {len(renamed)}")
        for old, new in renamed[:5]:
            lines.append(f"  • {old} → {new}")

    if skipped_name:
        lines.append(f"\n🔁 رد شد (اسم تکراری): {len(skipped_name)}")
        for name, _ in skipped_name[:5]:
            lines.append(f"  • {name}")

    if skipped_hash:
        lines.append(f"\n🆔 رد شد (اکانت تکراری): {len(skipped_hash)}")
        for name, existing in skipped_hash[:5]:
            lines.append(f"  • {name} (همان {existing})")

    if errors:
        lines.append(f"\n⚠️ خطاها: {len(errors)}")
        for err in errors[:5]:
            lines.append(f"  • {err}")

    return "\n".join(lines)

# =========================================================
# BACKUP
# =========================================================
def _build_backup_zip() -> bytes:
    buf = iolib.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(MOBIN_DATA_FILE):
            zf.write(MOBIN_DATA_FILE, "mobin.json")
        if os.path.isdir(MOBIN_SESSIONS_DIR):
            for name in os.listdir(MOBIN_SESSIONS_DIR):
                if name.endswith(".session"):
                    zf.write(
                        os.path.join(MOBIN_SESSIONS_DIR, name),
                        f"sessions/{name}",
                    )
    return buf.getvalue()

def _upload_backup_sync():
    chat_id = _mobin_ctx["backup_chat_id"]
    uploader = _mobin_ctx["backup_upload"]
    if not chat_id or not uploader:
        mobin_logger.warning("mobin backup: not configured")
        return
    try:
        raw = _build_backup_zip()
        with open(MOBIN_BACKUP_ZIP, "wb") as f:
            f.write(raw)
        uploader(
            chat_id,
            MOBIN_BACKUP_ZIP,
            f"mobin_backup.zip ({len(list_sessions())} sessions)",
        )
        mobin_logger.info("✅ mobin backup uploaded")
    except Exception as e:
        mobin_logger.error(f"mobin backup upload failed: {e}")

def _schedule_backup_upload():
    import threading
    threading.Thread(target=_upload_backup_sync, daemon=True).start()

def restore_mobin_from_telegram() -> bool:
    """در استارت: دانلود و استخراج mobin_backup.zip از گروه."""
    chat_id = _mobin_ctx["backup_chat_id"]
    downloader = _mobin_ctx["backup_download"]
    if not chat_id or not downloader:
        return False
    try:
        ok = downloader(chat_id, "mobin_backup.zip", MOBIN_RESTORE_ZIP)
        if not ok or not os.path.exists(MOBIN_RESTORE_ZIP):
            return False
        with open(MOBIN_RESTORE_ZIP, "rb") as f:
            raw = f.read()

        # از تابع anti-duplicate استفاده کن
        result = import_sessions_zip(raw, on_conflict="skip")
        if result["errors"]:
            mobin_logger.warning(f"restore mobin errors: {result['errors'][:5]}")
        mobin_logger.info(
            f"✅ mobin restored — imported={len(result['imported'])}, "
            f"skipped_name={len(result['skipped_name'])}, "
            f"skipped_hash={len(result['skipped_hash'])}"
        )
        return True
    except Exception as e:
        mobin_logger.error(f"restore_mobin failed: {e}")
        return False

# =========================================================
# REACTION
# =========================================================
async def _react_one_session(session_name: str, target: str, emoji: str) -> dict:
    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{session_name}.session")
    if not os.path.exists(sess_path):
        return {"ok": False, "reason": "session file missing"}

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"ok": False, "reason": "unauthorized"}

        channel = target.strip()
        msg_id = 0
        if "/t.me/" in channel or "t.me/" in channel:
            parts = [p for p in channel.rstrip("/").split("/") if p]
            last = parts[-1]
            if last.isdigit():
                msg_id = int(last)
                channel = parts[-2]
            else:
                channel = last
        channel = channel.lstrip("@")

        entity = await client.get_entity(channel)
        msgs = (
            await client.get_messages(entity, ids=[msg_id])
            if msg_id
            else await client.get_messages(entity, limit=1)
        )
        if not isinstance(msgs, list):
            msgs = [msgs]

        for m in msgs:
            if m:
                try:
                    await client(SendReactionRequest(
                        peer=entity,
                        msg_id=m.id,
                        reaction=[ReactionEmoji(emoticon=emoji)],
                    ))
                    return {"ok": True, "reason": None}
                except Exception as e:
                    return {"ok": False, "reason": str(e)}
        return {"ok": False, "reason": "no message"}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass

# =========================================================
# KEYBOARDS
# =========================================================
def _main_kb() -> InlineKeyboardMarkup:
    n = len(list_sessions())
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ ری‌اکشن فیک", callback_data="mobin_reaction")],
        [InlineKeyboardButton(f"📥 اضافه کردن سشن (ZIP) — {n} فعال", callback_data="mobin_add_sessions")],
        [InlineKeyboardButton(f"🗂 سشن‌های فعال ({n})", callback_data="mobin_sessions_list")],
        [InlineKeyboardButton("📤 دریافت بکاپ", callback_data="mobin_backup_get")],
        [InlineKeyboardButton("📥 وارد کردن بکاپ", callback_data="mobin_backup_import")],
        [InlineKeyboardButton("🔑 تغییر رمز", callback_data="mobin_change_password")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="mobin_close")],
    ])

def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)

def _sessions_kb() -> InlineKeyboardMarkup:
    names = list_sessions()
    kb = []
    for name in names[:20]:
        kb.append([
            InlineKeyboardButton(
                f"🗑 {name}",
                callback_data=f"mobin_del_session_{name}",
            )
        ])
    if names:
        kb.append([InlineKeyboardButton("🗑 حذف همه سشن‌ها", callback_data="mobin_del_all_sessions")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="mobin_back")])
    return InlineKeyboardMarkup(kb)

# =========================================================
# HELPERS
# =========================================================
def _is_admin(user_id) -> bool:
    return user_id in _mobin_ctx["admin_ids"]

def _is_password_ok(text: str) -> bool:
    return text.strip() == load_mobin_data().get("password", DEFAULT_PASSWORD)

# =========================================================
# HANDLERS
# =========================================================
async def mobin_cmd(update: Update, context):
    user_id = update.effective_user.id
    if _is_admin(user_id):
        context.user_data["mobin_authenticated"] = True
        await update.message.reply_text(
            "🎛 <b>پنل مبین</b>\n\nیکی از گزینه‌ها را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
        return MOBIN_ROOT

    await update.message.reply_text(
        "🔐 این پنل خصوصی است. لطفاً رمز را وارد کنید:",
        reply_markup=ReplyKeyboardRemove(),
    )
    return MOBIN_WAITING_PASSWORD

async def mobin_password(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await update.message.reply_text("لغو شد.")
        return ConversationHandler.END
    if _is_password_ok(text):
        context.user_data["mobin_authenticated"] = True
        await update.message.reply_text(
            "✅ رمز صحیح است.\n\n🎛 <b>پنل مبین</b>",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
        return MOBIN_ROOT
    await update.message.reply_text("❌ رمز اشتباه است. دوباره تلاش کنید یا /cancel.")
    return MOBIN_WAITING_PASSWORD

async def mobin_cb(update: Update, context):
    q = update.callback_query
    await q.answer()
    data = q.data
    user_id = q.from_user.id

    if not (_is_admin(user_id) or context.user_data.get("mobin_authenticated")):
        await q.answer("⛔️ دسترسی ندارید.", show_alert=True)
        return MOBIN_ROOT

    # ---- بستن ----
    if data == "mobin_close":
        context.user_data.pop("mobin_authenticated", None)
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.reply_text("پنل بسته شد.")
        return ConversationHandler.END

    # ---- بازگشت ----
    if data == "mobin_back":
        await q.message.edit_text(
            "🎛 <b>پنل مبین</b>",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
        return MOBIN_ROOT

    # ---- ری‌اکشن ----
    if data == "mobin_reaction":
        sessions = list_sessions()
        if not sessions:
            await q.message.reply_text(
                "⚠️ هیچ سشن فعالی موجود نیست.\n"
                "ابتدا از طریق «📥 اضافه کردن سشن» یک ZIP آپلود کنید."
            )
            return MOBIN_ROOT
        await q.message.reply_text(
            f"⚡ <b>ری‌اکشن فیک</b>\n\n"
            f"🔋 {len(sessions)} سشن موجود\n\n"
            f"لینک پست/کانال را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_LINK

    # ---- اضافه کردن سشن (ZIP) ----
    if data == "mobin_add_sessions":
        await q.message.reply_text(
            "📥 ZIP حاوی فایل‌های <code>.session</code> را ارسال کنید.\n\n"
            "<b>ساختار ZIP:</b>\n"
            "<pre>sessions.zip\n├── acc1.session\n├── acc2.session\n└── acc3.session</pre>\n"
            "<b>محافظت‌ها:</b>\n"
            "• session با اسم تکراری وارد نمی‌شود\n"
            "• session با محتوای تکراری (همون اکانت) وارد نمی‌شود\n"
            "• session تکراری داخل خود ZIP نادیده گرفته می‌شود\n\n"
            "همه session ها باید با <b>API_ID و API_HASH</b> تنظیم‌شده در mobin.json ساخته شده باشند.\n"
            f"مقدار فعلی: <code>API_ID={load_mobin_data().get('api_id')}</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_ADD_SESSION_ZIP

    # ---- لیست سشن‌ها ----
    if data == "mobin_sessions_list":
        names = list_sessions()
        if not names:
            await q.message.edit_text(
                "🗂 هیچ سشنی موجود نیست.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="mobin_back")]
                ]),
            )
            return MOBIN_ROOT
        await q.message.edit_text(
            f"🗂 <b>سشن‌های فعال ({len(names)})</b>\n\n"
            "روی هر کدام بزنید تا حذف شود:",
            parse_mode="HTML",
            reply_markup=_sessions_kb(),
        )
        return MOBIN_SESSIONS_MANAGER

    # ---- حذف همه ----
    if data == "mobin_del_all_sessions":
        count = delete_all_sessions()
        _schedule_backup_upload()
        await q.message.edit_text(
            f"🗑 {count} سشن حذف شد.\nبکاپ آپلود شد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت", callback_data="mobin_back")]
            ]),
        )
        return MOBIN_ROOT

    # ---- حذف یک سشن ----
    if data.startswith("mobin_del_session_"):
        name = data[len("mobin_del_session_"):]
        if delete_session(name):
            _schedule_backup_upload()
            await q.answer(f"✅ {name} حذف شد.", show_alert=False)
        else:
            await q.answer(f"❌ خطا در حذف {name}", show_alert=True)
        names = list_sessions()
        if not names:
            await q.message.edit_text(
                "🗂 همه سشن‌ها حذف شدند.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 بازگشت", callback_data="mobin_back")]
                ]),
            )
            return MOBIN_ROOT
        await q.message.edit_text(
            f"🗂 <b>سشن‌های فعال ({len(names)})</b>",
            parse_mode="HTML",
            reply_markup=_sessions_kb(),
        )
        return MOBIN_SESSIONS_MANAGER

    # ---- دریافت بکاپ ----
    if data == "mobin_backup_get":
        raw = _build_backup_zip()
        await q.message.reply_document(
            document=InputFile(
                iolib.BytesIO(raw),
                filename=f"mobin_backup_{datetime.now():%Y%m%d_%H%M}.zip",
            ),
            caption="📦 بکاپ mobin.json + session ها",
        )
        return MOBIN_ROOT

    # ---- وارد کردن بکاپ ----
    if data == "mobin_backup_import":
        await q.message.reply_text(
            "📥 فایل ZIP بکاپ را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP

    # ---- تغییر رمز ----
    if data == "mobin_change_password":
        await q.message.reply_text(
            "🔑 رمز جدید (حداقل ۳ کاراکتر):",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_NEW_PASSWORD

    return MOBIN_ROOT

# ---------- ری‌اکشن: ۳ گام ----------
async def mobin_rx_link(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await update.message.reply_text("لغو شد.", reply_markup=_main_kb())
        return MOBIN_ROOT
    context.user_data["mobin_rx_target"] = text
    await update.message.reply_text("🎭 ایموجی ری‌اکشن را ارسال کنید (مثل ❤️):")
    return MOBIN_REACTION_WAITING_EMOJI

async def mobin_rx_emoji(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await update.message.reply_text("لغو شد.", reply_markup=_main_kb())
        return MOBIN_ROOT
    context.user_data["mobin_rx_emoji"] = text
    n = len(list_sessions())
    await update.message.reply_text(
        f"🔢 تعداد سشن‌ها (حداکثر {min(n, 10)}):\n"
        f"⚠️ توصیه: بیش از ۵ تا در هر بار نزنید."
    )
    return MOBIN_REACTION_WAITING_ACCOUNTS

async def mobin_rx_count(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await update.message.reply_text("لغو شد.", reply_markup=_main_kb())
        return MOBIN_ROOT
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return MOBIN_REACTION_WAITING_ACCOUNTS

    count = int(text)
    sessions = list_sessions()
    count = min(count, len(sessions), 10)
    target = context.user_data.get("mobin_rx_target")
    emoji = context.user_data.get("mobin_rx_emoji")

    status_msg = await update.message.reply_text(
        f"🚀 در حال اجرا روی {count} سشن...\n"
        f"🎯 {target}\n🎭 {emoji}"
    )

    ok_total = 0
    failed = []
    selected = sessions[:count]
    for i in range(0, len(selected), 3):
        batch = selected[i:i+3]
        results = await asyncio.gather(
            *[_react_one_session(s, target, emoji) for s in batch],
            return_exceptions=True,
        )
        for s, r in zip(batch, results):
            if isinstance(r, dict) and r.get("ok"):
                ok_total += 1
            else:
                reason = r.get("reason") if isinstance(r, dict) else str(r)
                failed.append(f"• <code>{s}</code>: {reason}")
        if i + 3 < len(selected):
            await asyncio.sleep(random.uniform(3, 7))

    summary = f"✅ موفق: {ok_total}/{count}"
    if failed:
        summary += "\n\n❌ ناموفق‌ها:\n" + "\n".join(failed[:10])
    try:
        await status_msg.edit_text(summary, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(summary, parse_mode="HTML")

    context.user_data.pop("mobin_rx_target", None)
    context.user_data.pop("mobin_rx_emoji", None)
    return MOBIN_ROOT

# ---------- تغییر رمز ----------
async def mobin_new_password(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await update.message.reply_text("لغو شد.", reply_markup=_main_kb())
        return MOBIN_ROOT
    if len(text) < 3:
        await update.message.reply_text("❌ خیلی کوتاه است.")
        return MOBIN_WAITING_NEW_PASSWORD
    data = load_mobin_data()
    data["password"] = text
    save_mobin_data(data, upload=True)
    await update.message.reply_text(
        "✅ رمز تغییر کرد و بکاپ آپلود شد.",
        reply_markup=_main_kb(),
    )
    return MOBIN_ROOT

# ---------- وارد کردن بکاپ ----------
async def mobin_import_zip(update: Update, context):
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("❌ فقط ZIP بفرستید یا /cancel.")
        return MOBIN_WAITING_IMPORT_ZIP

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    raw_bytes = bytes(raw)

    try:
        # ۱) mobin.json را اول import کن
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            if "mobin.json" in zf.namelist():
                with open(MOBIN_DATA_FILE, "wb") as f:
                    f.write(zf.read("mobin.json"))

        # ۲) session ها را با محافظت کامل import کن
        result = import_sessions_zip(raw_bytes, on_conflict="skip")
        _schedule_backup_upload()
        n = len(list_sessions())

        report = _format_import_report(result)
        await update.message.reply_text(
            f"✅ بکاپ پردازش شد — {n} سشن فعال.\n\n{report}",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")
        return MOBIN_WAITING_IMPORT_ZIP
    return MOBIN_ROOT

# ---------- اضافه کردن سشن‌ها با ZIP ----------
async def mobin_add_sessions_zip(update: Update, context):
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("❌ فقط ZIP بفرستید یا /cancel.")
        return MOBIN_WAITING_ADD_SESSION_ZIP

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    raw_bytes = bytes(raw)

    status = await update.message.reply_text("⏳ در حال پردازش ZIP...")

    # سیاست پیش‌فرض: skip تکراری‌ها
    result = import_sessions_zip(raw_bytes, on_conflict="skip")

    if not result["imported"] and not result["skipped_name"] and not result["skipped_hash"] and not result["errors"]:
        try:
            await status.edit_text(
                "❌ هیچ فایل <code>.session</code> در ZIP پیدا نشد.",
                parse_mode="HTML",
                reply_markup=_main_kb(),
            )
        except Exception:
            await update.message.reply_text(
                "❌ هیچ فایل .session در ZIP پیدا نشد.",
                reply_markup=_main_kb(),
            )
        return MOBIN_ROOT

    _schedule_backup_upload()
    total = len(list_sessions())
    report = _format_import_report(result)

    try:
        await status.edit_text(
            f"📊 مجموع: {total} سشن\n\n{report}",
            parse_mode="HTML",
        )
    except Exception:
        await update.message.reply_text(
            f"📊 مجموع: {total} سشن\n\n{report}",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
    return MOBIN_ROOT

async def mobin_cancel(update: Update, context):
    for k in list(context.user_data.keys()):
        if k.startswith("mobin_"):
            context.user_data.pop(k, None)
    await update.message.reply_text("لغو شد.")
    return ConversationHandler.END

# =========================================================
# CONVERSATION BUILDER
# =========================================================
def build_mobin_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("mobin", mobin_cmd)],
        states={
            MOBIN_WAITING_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_password),
            ],
            MOBIN_ROOT: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_SESSIONS_MANAGER: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_REACTION_WAITING_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_link),
            ],
            MOBIN_REACTION_WAITING_EMOJI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_emoji),
            ],
            MOBIN_REACTION_WAITING_ACCOUNTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_count),
            ],
            MOBIN_WAITING_NEW_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_new_password),
            ],
            MOBIN_WAITING_IMPORT_ZIP: [
                MessageHandler(filters.Document.ALL, mobin_import_zip),
            ],
            MOBIN_WAITING_ADD_SESSION_ZIP: [
                MessageHandler(filters.Document.ALL, mobin_add_sessions_zip),
            ],
        },
        fallbacks=[CommandHandler("cancel", mobin_cancel)],
        allow_reentry=True,
    )
