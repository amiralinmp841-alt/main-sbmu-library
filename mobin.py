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
import time
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)# mobin.py
import os
import json
import asyncio
import logging
import zipfile
import hashlib
import io as iolib
import html
import random
import time
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

from telethon import TelegramClient, functions
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

# کش برای ری‌اکشن‌های سراسری
_reactions_cache = {
    "list": None,
    "fetched_at": 0,
    "ttl": 12 * 60 * 60,
}

# لیست پیش‌فرض امن
_FALLBACK_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔",
    "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
]

# ---------- STATES ----------
(
    MOBIN_ROOT,
    MOBIN_WAITING_PASSWORD,
    MOBIN_WAITING_NEW_PASSWORD,
    MOBIN_REACTION_WAITING_TARGET,
    MOBIN_REACTION_WAITING_EMOJI,
    MOBIN_REACTION_WAITING_ACCOUNTS,
    MOBIN_WAITING_IMPORT_ZIP,
    MOBIN_WAITING_ADD_SESSION_ZIP,
    MOBIN_SESSIONS_MANAGER,
    MOBIN_REACTION_WAITING_REMOVE,
) = range(100, 110)

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
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _existing_session_hashes() -> set[str]:
    hashes: set[str] = set()
    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        return hashes
    for fname in os.listdir(MOBIN_SESSIONS_DIR):
        if not fname.endswith(".session") or fname.startswith("."):
            continue
        try:
            with open(os.path.join(MOBIN_SESSIONS_DIR, fname), "rb") as f:
                hashes.add(_sha256_bytes(f.read()))
        except Exception:
            continue
    return hashes

def _next_session_name() -> str:
    existing = set(list_sessions())
    i = 1
    while True:
        candidate = f"acc_{i:04d}"
        if candidate not in existing:
            return candidate
        i += 1

def import_sessions_zip(raw_bytes: bytes) -> dict:
    imported: list[tuple[str, str]] = []
    skipped_existing: list[tuple[str, str]] = []
    skipped_duplicate_in_zip: list[tuple[str, str]] = []
    errors: list[str] = []

    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        os.makedirs(MOBIN_SESSIONS_DIR, exist_ok=True)

    existing_hashes = _existing_session_hashes()
    seen_in_zip: dict[str, str] = {}

    try:
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            for entry in zf.namelist():
                base = os.path.basename(entry)
                if not base.endswith(".session") or base.startswith("."):
                    continue
                try:
                    data = zf.read(entry)
                except Exception as e:
                    errors.append(f"{base}: خواندن خطا ({e})")
                    continue

                content_hash = _sha256_bytes(data)

                if content_hash in existing_hashes:
                    skipped_existing.append((base, "session از قبل موجود است"))
                    continue
                if content_hash in seen_in_zip:
                    skipped_duplicate_in_zip.append((base, seen_in_zip[content_hash]))
                    continue
                seen_in_zip[content_hash] = base

                new_name = _next_session_name()
                final_path = os.path.join(MOBIN_SESSIONS_DIR, f"{new_name}.session")
                try:
                    with open(final_path, "wb") as f:
                        f.write(data)
                    imported.append((new_name, base))
                    existing_hashes.add(content_hash)
                except Exception as e:
                    errors.append(f"{base}: نوشتن خطا ({e})")
    except zipfile.BadZipFile as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"ZIP معیوب: {e}"]}
    except Exception as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"خطای کلی: {e}"]}

    return {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_duplicate_in_zip": skipped_duplicate_in_zip,
        "errors": errors,
    }

def _format_import_report(result: dict) -> str:
    imported = result["imported"]
    skipped_existing = result["skipped_existing"]
    skipped_dup = result["skipped_duplicate_in_zip"]
    errors = result["errors"]

    lines = [f"✅ وارد شد: {len(imported)}"]
    if imported:
        for new_name, old_name in imported[:8]:
            lines.append(f"  • <code>{new_name}</code> ← <code>{old_name}</code>")
        if len(imported) > 8:
            lines.append(f"  ... و {len(imported) - 8} تای دیگر")
    if skipped_existing:
        lines.append(f"\n🆔 رد شد (session از قبل موجود): {len(skipped_existing)}")
        for name, _ in skipped_existing[:5]:
            lines.append(f"  • <code>{name}</code>")
    if skipped_dup:
        lines.append(f"\n🔁 رد شد (تکراری داخل ZIP): {len(skipped_dup)}")
        for name, first in skipped_dup[:5]:
            lines.append(f"  • <code>{name}</code> (همان {first})")
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
        uploader(chat_id, MOBIN_BACKUP_ZIP,
                 f"mobin_backup.zip ({len(list_sessions())} sessions)")
        mobin_logger.info("✅ mobin backup uploaded")
    except Exception as e:
        mobin_logger.error(f"mobin backup upload failed: {e}")

def _schedule_backup_upload():
    import threading
    threading.Thread(target=_upload_backup_sync, daemon=True).start()

def restore_mobin_from_telegram() -> bool:
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
        result = import_sessions_zip(raw)
        mobin_logger.info(
            f"✅ mobin restored — imported={len(result['imported'])}, "
            f"skipped_existing={len(result['skipped_existing'])}, "
            f"skipped_dup={len(result['skipped_duplicate_in_zip'])}"
        )
        return True
    except Exception as e:
        mobin_logger.error(f"restore_mobin failed: {e}")
        return False

# =========================================================
# AVAILABLE REACTIONS (per-channel)
# =========================================================
async def _get_channel_reactions(
    peer_str: str | None = None,
    forward_info: dict | None = None,
) -> list[str]:
    """
    ری‌اکشن‌های مجاز مخصوص یک کانال/گروه را برمی‌گرداند.
    - اگر کانال تنظیمات ری‌اکشن نداشته باشه، لیست سراسری fallback می‌شه.
    - اگر `chatReactionsNone` باشه، لیست خالی برمی‌گرده.
    """
    sessions = list_sessions()
    if not sessions:
        return list(_FALLBACK_EMOJIS)

    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{sessions[0]}.session")

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return list(_FALLBACK_EMOJIS)

        # entity رو resolve کن
        entity = None
        if forward_info:
            try:
                entity = await client.get_entity(forward_info["chat_id"])
            except Exception:
                entity = None
        elif peer_str:
            try:
                entity = await client.get_entity(peer_str)
            except Exception:
                entity = None

        if entity is None:
            return list(_FALLBACK_EMOJIS)

        # گرفتن full info کانال/گروه
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            chat_reactions = getattr(full.full_chat, "available_reactions", None)
        except Exception:
            # شاید گروه معمولی باشه (نه کانال)
            try:
                full = await client(functions.messages.GetFullChatRequest(entity.id))
                chat_reactions = getattr(full.full_chat, "available_reactions", None)
            except Exception:
                chat_reactions = None

        if chat_reactions is None:
            # کانال تنظیمات خاصی نداره → لیست سراسری
            return await _get_global_reactions(client)

        # تشخیص نوع ChatReactions
        class_name = type(chat_reactions).__name__

        if "None" in class_name:
            # هیچ ری‌اکشنی مجاز نیست
            return []

        if "All" in class_name:
            # همه ری‌اکشن‌های سراسری مجازن
            return await _get_global_reactions(client)

        if "Some" in class_name:
            # فقط بعضی‌ها
            reactions = getattr(chat_reactions, "reactions", [])
            emojis = []
            for r in reactions:
                emoticon = getattr(r, "emoticon", None)
                if emoticon:
                    emojis.append(emoticon)
            if emojis:
                return emojis
            return []

        # پیش‌فرض
        return await _get_global_reactions(client)

    except Exception as e:
        mobin_logger.error(f"get_channel_reactions failed: {e}")
        return list(_FALLBACK_EMOJIS)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _get_global_reactions(client) -> list[str]:
    """لیست سراسری ری‌اکشن‌های مجاز تلگرام."""
    now = time.time()
    if (_reactions_cache["list"] is not None
            and now - _reactions_cache["fetched_at"] < _reactions_cache["ttl"]):
        return _reactions_cache["list"]

    try:
        result = await client(functions.messages.GetAvailableReactionsRequest(hash=0))
        reactions_attr = getattr(result, "reactions", None)
        if reactions_attr:
            emojis = []
            for r in reactions_attr:
                react = getattr(r, "reaction", None)
                if react is not None:
                    emoticon = getattr(react, "emoticon", None)
                    if emoticon:
                        emojis.append(emoticon)
            if emojis:
                _reactions_cache["list"] = emojis
                _reactions_cache["fetched_at"] = now
                return emojis
    except Exception as e:
        mobin_logger.error(f"get_global_reactions failed: {e}")

    return list(_FALLBACK_EMOJIS)

# =========================================================
# REACTION TARGET PARSING
# =========================================================
def _parse_target_from_text(text: str):
    t = text.strip()
    msg_id = 0
    if "/t.me/" in t or "t.me/" in t:
        parts = [p for p in t.rstrip("/").split("/") if p]
        last = parts[-1]
        if last.isdigit():
            msg_id = int(last)
            peer = parts[-2]
        else:
            peer = last
        return peer, msg_id
    if t.startswith("@"):
        return t[1:], 0
    if t.startswith("https://") or t.startswith("http://"):
        parts = [p for p in t.rstrip("/").split("/") if p]
        if parts:
            last = parts[-1]
            if last.isdigit():
                msg_id = int(last)
                peer = parts[-2]
            else:
                peer = last
            return peer, msg_id
    return None, None

def _extract_forward_info(message) -> dict | None:
    if not message:
        return None
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_msg_id = getattr(message, "forward_from_message_id", None)
    if fwd_chat is not None and fwd_msg_id:
        return {"chat_id": fwd_chat.id, "message_id": int(fwd_msg_id)}
    fwd_origin = getattr(message, "forward_origin", None)
    if fwd_origin is not None:
        origin_chat = getattr(fwd_origin, "chat", None)
        origin_msg_id = getattr(fwd_origin, "message_id", None)
        if origin_chat is not None and origin_msg_id:
            return {"chat_id": origin_chat.id, "message_id": int(origin_msg_id)}
    return None

# =========================================================
# REACTION (single session)
# =========================================================
async def _react_one_session(
    session_name: str,
    peer_str: str | None,
    msg_id: int,
    emoji: str | None,
    forward_info: dict | None = None,
) -> dict:
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

        if forward_info:
            try:
                entity = await client.get_entity(forward_info["chat_id"])
            except Exception as e:
                return {"ok": False, "reason": f"forward entity: {e}"}
            target_msg_id = forward_info["message_id"]
        else:
            if not peer_str:
                return {"ok": False, "reason": "no target"}
            entity = await client.get_entity(peer_str)
            if msg_id:
                target_msg_id = msg_id
            else:
                msgs = await client.get_messages(entity, limit=1)
                if not msgs:
                    return {"ok": False, "reason": "no message"}
                target_msg_id = msgs[0].id

        reaction_list = [] if not emoji else [ReactionEmoji(emoticon=emoji)]
        try:
            await client(SendReactionRequest(
                peer=entity,
                msg_id=target_msg_id,
                reaction=reaction_list,
            ))
            return {"ok": True, "reason": None}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
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
        [InlineKeyboardButton("🚫 برداشتن ری‌اکشن", callback_data="mobin_remove_reaction")],
        [InlineKeyboardButton(f"📥 اضافه کردن سشن (ZIP) — {n} فعال", callback_data="mobin_add_sessions")],
        [InlineKeyboardButton(f"🗂 سشن‌های فعال ({n})", callback_data="mobin_sessions_list")],
        [InlineKeyboardButton("📤 دریافت بکاپ", callback_data="mobin_backup_get")],
        [InlineKeyboardButton("📥 وارد کردن بکاپ", callback_data="mobin_backup_import")],
        [InlineKeyboardButton("🔑 تغییر رمز", callback_data="mobin_change_password")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="mobin_close")],
    ])

def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)

def _emoji_kb(emojis: list[str]) -> InlineKeyboardMarkup:
    """کیبورد اینلاین با ایموجی‌های مجاز (پویا)."""
    if not emojis:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
        ])
    kb, row = [], []
    for e in emojis[:24]:
        row.append(InlineKeyboardButton(e, callback_data=f"mobin_em_{e}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")])
    return InlineKeyboardMarkup(kb)

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

async def _send_main_menu(update: Update, context, text="🎛 <b>پنل مبین</b>"):
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                text, parse_mode="HTML", reply_markup=_main_kb(),
            )
            return
        except Exception:
            pass
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode="HTML", reply_markup=_main_kb(),
        )

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

    if data == "mobin_close":
        context.user_data.pop("mobin_authenticated", None)
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.reply_text("پنل بسته شد.")
        return ConversationHandler.END

    if data == "mobin_back":
        await _send_main_menu(update, context)
        return MOBIN_ROOT

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
            "روش ارسال هدف:\n"
            "• <b>لینک پست</b> (مثل <code>https://t.me/chan/123</code>)\n"
            "• <b>@username</b> کانال (آخرین پست)\n"
            "• <b>فوروارد/کپی</b> کردن پست از کانال به اینجا\n\n"
            "برای انصراف «❌ لغو» را بفرستید.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    if data == "mobin_remove_reaction":
        sessions = list_sessions()
        if not sessions:
            await q.message.reply_text("⚠️ هیچ سشن فعالی موجود نیست.")
            return MOBIN_ROOT
        await q.message.reply_text(
            f"🚫 <b>برداشتن ری‌اکشن</b>\n\n"
            f"🔋 {len(sessions)} سشن موجود\n\n"
            "لینک پست را بفرستید یا پست را فوروارد/کپی کنید.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_REMOVE

    if data.startswith("mobin_em_"):
        emoji = data[len("mobin_em_"):]
        context.user_data["mobin_rx_emoji"] = emoji
        n = len(list_sessions())
        await q.message.edit_text(
            f"🎭 ایموجی انتخابی: {emoji}\n\n"
            f"🔢 تعداد سشن‌ها (حداکثر {min(n, 10)}):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
            ]),
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    if data == "mobin_add_sessions":
        await q.message.reply_text(
            "📥 ZIP حاوی فایل‌های <code>.session</code> را ارسال کنید.\n\n"
            "<b>نحوه کار:</b>\n"
            "• اسم فایل‌های داخل ZIP مهم نیست\n"
            "• خودش اسم می‌ده: <code>acc_0001</code>، <code>acc_0002</code>، ...\n"
            "• اگه session تکراری (بر اساس محتوا) باشه، skip می‌شه\n"
            "• هم با session‌های موجود چک می‌شه، هم داخل خود ZIP\n\n"
            f"مقدار فعلی: <code>API_ID={load_mobin_data().get('api_id')}</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_ADD_SESSION_ZIP

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

    if data == "mobin_backup_import":
        await q.message.reply_text(
            "📥 فایل ZIP بکاپ را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP

    if data == "mobin_change_password":
        await q.message.reply_text(
            "🔑 رمز جدید (حداقل ۳ کاراکتر):",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_NEW_PASSWORD

    return MOBIN_ROOT

# ---------- هدف ری‌اکشن: لینک یا فوروارد ----------
async def mobin_rx_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        emojis = await _get_channel_reactions(forward_info=fwd)
        if not emojis:
            await msg.reply_text(
                "⚠️ این کانال هیچ ری‌اکشنی مجاز نکرده است.",
                reply_markup=_cancel_kb(),
            )
            return MOBIN_REACTION_WAITING_TARGET
        await msg.reply_text(
            f"🎭 ایموجی ری‌اکشن را انتخاب کنید ({len(emojis)} مورد مجاز):",
            reply_markup=_emoji_kb(emojis),
        )
        return MOBIN_REACTION_WAITING_EMOJI

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست (مثل <code>https://t.me/chan/123</code>)، "
            "یا @username، یا پست را فوروارد کن.",
            parse_mode="HTML",
        )
        return MOBIN_REACTION_WAITING_TARGET

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None

    emojis = await _get_channel_reactions(peer_str=peer)
    if not emojis:
        await msg.reply_text(
            "⚠️ این کانال هیچ ری‌اکشنی مجاز نکرده است.",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    await msg.reply_text(
        f"🎭 ایموجی ری‌اکشن را انتخاب کنید ({len(emojis)} مورد مجاز):",
        reply_markup=_emoji_kb(emojis),
    )
    return MOBIN_REACTION_WAITING_EMOJI

# ---------- برداشتن ری‌اکشن: هدف ----------
async def mobin_remove_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        await msg.reply_text(
            f"🔢 تعداد سشن‌ها (حداکثر {min(len(list_sessions()), 10)}):",
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست یا پست را فوروارد کن.",
        )
        return MOBIN_REACTION_WAITING_REMOVE

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None

    await msg.reply_text(
        f"🔢 تعداد سشن‌ها (حداکثر {min(len(list_sessions()), 10)}):",
    )
    return MOBIN_REACTION_WAITING_ACCOUNTS

# ---------- تعداد سشن‌ها -> اجرا ----------
async def mobin_rx_count(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return MOBIN_REACTION_WAITING_ACCOUNTS

    count = int(text)
    sessions = list_sessions()
    count = min(count, len(sessions), 10)

    peer = context.user_data.get("mobin_rx_peer")
    msg_id = context.user_data.get("mobin_rx_msg_id", 0)
    emoji = context.user_data.get("mobin_rx_emoji")
    fwd_info = context.user_data.get("mobin_forward_info")

    action_label = "برداشتن ری‌اکشن" if not emoji else f"ری‌اکشن {emoji}"
    status_msg = await update.message.reply_text(
        f"🚀 در حال اجرا ({action_label}) روی {count} سشن...\n"
    )

    ok_total = 0
    failed = []
    selected = sessions[:count]
    for i in range(0, len(selected), 3):
        batch = selected[i:i+3]
        results = await asyncio.gather(
            *[_react_one_session(s, peer, msg_id, emoji, fwd_info) for s in batch],
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

    for k in ["mobin_rx_peer", "mobin_rx_msg_id", "mobin_rx_emoji", "mobin_forward_info"]:
        context.user_data.pop(k, None)

    await _send_main_menu(update, context, "🎛 <b>پنل مبین</b>")
    return MOBIN_ROOT

# ---------- تغییر رمز ----------
async def mobin_new_password(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
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
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            if "mobin.json" in zf.namelist():
                with open(MOBIN_DATA_FILE, "wb") as f:
                    f.write(zf.read("mobin.json"))

        result = import_sessions_zip(raw_bytes)
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

    result = import_sessions_zip(raw_bytes)

    if (not result["imported"]
            and not result["skipped_existing"]
            and not result["skipped_duplicate_in_zip"]
            and not result["errors"]):
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
            reply_markup=_main_kb(),
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
            MOBIN_REACTION_WAITING_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_target),
                MessageHandler(filters.FORWARDED, mobin_rx_target),
            ],
            MOBIN_REACTION_WAITING_EMOJI: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_REACTION_WAITING_REMOVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_remove_target),
                MessageHandler(filters.FORWARDED, mobin_remove_target),
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
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from telethon import TelegramClient, functions
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

# کش برای ری‌اکشن‌های سراسری
_reactions_cache = {
    "list": None,
    "fetched_at": 0,
    "ttl": 12 * 60 * 60,
}

# لیست پیش‌فرض امن
_FALLBACK_EMOJIS = [
    "👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🤔",
    "🤯", "😱", "🤬", "😢", "🎉", "🤩", "🤮", "💩",
    "🙏", "👌", "🕊", "🤡", "🥱", "🥴", "😍", "🐳",
]

# ---------- STATES ----------
(
    MOBIN_ROOT,
    MOBIN_WAITING_PASSWORD,
    MOBIN_WAITING_NEW_PASSWORD,
    MOBIN_REACTION_WAITING_TARGET,
    MOBIN_REACTION_WAITING_EMOJI,
    MOBIN_REACTION_WAITING_ACCOUNTS,
    MOBIN_WAITING_IMPORT_ZIP,
    MOBIN_WAITING_ADD_SESSION_ZIP,
    MOBIN_SESSIONS_MANAGER,
    MOBIN_REACTION_WAITING_REMOVE,
) = range(100, 110)

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
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _existing_session_hashes() -> set[str]:
    hashes: set[str] = set()
    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        return hashes
    for fname in os.listdir(MOBIN_SESSIONS_DIR):
        if not fname.endswith(".session") or fname.startswith("."):
            continue
        try:
            with open(os.path.join(MOBIN_SESSIONS_DIR, fname), "rb") as f:
                hashes.add(_sha256_bytes(f.read()))
        except Exception:
            continue
    return hashes

def _next_session_name() -> str:
    existing = set(list_sessions())
    i = 1
    while True:
        candidate = f"acc_{i:04d}"
        if candidate not in existing:
            return candidate
        i += 1

def import_sessions_zip(raw_bytes: bytes) -> dict:
    imported: list[tuple[str, str]] = []
    skipped_existing: list[tuple[str, str]] = []
    skipped_duplicate_in_zip: list[tuple[str, str]] = []
    errors: list[str] = []

    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        os.makedirs(MOBIN_SESSIONS_DIR, exist_ok=True)

    existing_hashes = _existing_session_hashes()
    seen_in_zip: dict[str, str] = {}

    try:
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            for entry in zf.namelist():
                base = os.path.basename(entry)
                if not base.endswith(".session") or base.startswith("."):
                    continue
                try:
                    data = zf.read(entry)
                except Exception as e:
                    errors.append(f"{base}: خواندن خطا ({e})")
                    continue

                content_hash = _sha256_bytes(data)

                if content_hash in existing_hashes:
                    skipped_existing.append((base, "session از قبل موجود است"))
                    continue
                if content_hash in seen_in_zip:
                    skipped_duplicate_in_zip.append((base, seen_in_zip[content_hash]))
                    continue
                seen_in_zip[content_hash] = base

                new_name = _next_session_name()
                final_path = os.path.join(MOBIN_SESSIONS_DIR, f"{new_name}.session")
                try:
                    with open(final_path, "wb") as f:
                        f.write(data)
                    imported.append((new_name, base))
                    existing_hashes.add(content_hash)
                except Exception as e:
                    errors.append(f"{base}: نوشتن خطا ({e})")
    except zipfile.BadZipFile as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"ZIP معیوب: {e}"]}
    except Exception as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"خطای کلی: {e}"]}

    return {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_duplicate_in_zip": skipped_duplicate_in_zip,
        "errors": errors,
    }

def _format_import_report(result: dict) -> str:
    imported = result["imported"]
    skipped_existing = result["skipped_existing"]
    skipped_dup = result["skipped_duplicate_in_zip"]
    errors = result["errors"]

    lines = [f"✅ وارد شد: {len(imported)}"]
    if imported:
        for new_name, old_name in imported[:8]:
            lines.append(f"  • <code>{new_name}</code> ← <code>{old_name}</code>")
        if len(imported) > 8:
            lines.append(f"  ... و {len(imported) - 8} تای دیگر")
    if skipped_existing:
        lines.append(f"\n🆔 رد شد (session از قبل موجود): {len(skipped_existing)}")
        for name, _ in skipped_existing[:5]:
            lines.append(f"  • <code>{name}</code>")
    if skipped_dup:
        lines.append(f"\n🔁 رد شد (تکراری داخل ZIP): {len(skipped_dup)}")
        for name, first in skipped_dup[:5]:
            lines.append(f"  • <code>{name}</code> (همان {first})")
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
        uploader(chat_id, MOBIN_BACKUP_ZIP,
                 f"mobin_backup.zip ({len(list_sessions())} sessions)")
        mobin_logger.info("✅ mobin backup uploaded")
    except Exception as e:
        mobin_logger.error(f"mobin backup upload failed: {e}")

def _schedule_backup_upload():
    import threading
    threading.Thread(target=_upload_backup_sync, daemon=True).start()

def restore_mobin_from_telegram() -> bool:
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
        result = import_sessions_zip(raw)
        mobin_logger.info(
            f"✅ mobin restored — imported={len(result['imported'])}, "
            f"skipped_existing={len(result['skipped_existing'])}, "
            f"skipped_dup={len(result['skipped_duplicate_in_zip'])}"
        )
        return True
    except Exception as e:
        mobin_logger.error(f"restore_mobin failed: {e}")
        return False

# =========================================================
# AVAILABLE REACTIONS (per-channel)
# =========================================================
async def _get_channel_reactions(
    peer_str: str | None = None,
    forward_info: dict | None = None,
) -> list[str]:
    """
    ری‌اکشن‌های مجاز مخصوص یک کانال/گروه را برمی‌گرداند.
    - اگر کانال تنظیمات ری‌اکشن نداشته باشه، لیست سراسری fallback می‌شه.
    - اگر `chatReactionsNone` باشه، لیست خالی برمی‌گرده.
    """
    sessions = list_sessions()
    if not sessions:
        return list(_FALLBACK_EMOJIS)

    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{sessions[0]}.session")

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return list(_FALLBACK_EMOJIS)

        # entity رو resolve کن
        entity = None
        if forward_info:
            try:
                entity = await client.get_entity(forward_info["chat_id"])
            except Exception:
                entity = None
        elif peer_str:
            try:
                entity = await client.get_entity(peer_str)
            except Exception:
                entity = None

        if entity is None:
            return list(_FALLBACK_EMOJIS)

        # گرفتن full info کانال/گروه
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            chat_reactions = getattr(full.full_chat, "available_reactions", None)
        except Exception:
            # شاید گروه معمولی باشه (نه کانال)
            try:
                full = await client(functions.messages.GetFullChatRequest(entity.id))
                chat_reactions = getattr(full.full_chat, "available_reactions", None)
            except Exception:
                chat_reactions = None

        if chat_reactions is None:
            # کانال تنظیمات خاصی نداره → لیست سراسری
            return await _get_global_reactions(client)

        # تشخیص نوع ChatReactions
        class_name = type(chat_reactions).__name__

        if "None" in class_name:
            # هیچ ری‌اکشنی مجاز نیست
            return []

        if "All" in class_name:
            # همه ری‌اکشن‌های سراسری مجازن
            return await _get_global_reactions(client)

        if "Some" in class_name:
            # فقط بعضی‌ها
            reactions = getattr(chat_reactions, "reactions", [])
            emojis = []
            for r in reactions:
                emoticon = getattr(r, "emoticon", None)
                if emoticon:
                    emojis.append(emoticon)
            if emojis:
                return emojis
            return []

        # پیش‌فرض
        return await _get_global_reactions(client)

    except Exception as e:
        mobin_logger.error(f"get_channel_reactions failed: {e}")
        return list(_FALLBACK_EMOJIS)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _get_global_reactions(client) -> list[str]:
    """لیست سراسری ری‌اکشن‌های مجاز تلگرام."""
    now = time.time()
    if (_reactions_cache["list"] is not None
            and now - _reactions_cache["fetched_at"] < _reactions_cache["ttl"]):
        return _reactions_cache["list"]

    try:
        result = await client(functions.messages.GetAvailableReactionsRequest(hash=0))
        reactions_attr = getattr(result, "reactions", None)
        if reactions_attr:
            emojis = []
            for r in reactions_attr:
                react = getattr(r, "reaction", None)
                if react is not None:
                    emoticon = getattr(react, "emoticon", None)
                    if emoticon:
                        emojis.append(emoticon)
            if emojis:
                _reactions_cache["list"] = emojis
                _reactions_cache["fetched_at"] = now
                return emojis
    except Exception as e:
        mobin_logger.error(f"get_global_reactions failed: {e}")

    return list(_FALLBACK_EMOJIS)

# =========================================================
# REACTION TARGET PARSING
# =========================================================
def _parse_target_from_text(text: str):
    t = text.strip()
    msg_id = 0
    if "/t.me/" in t or "t.me/" in t:
        parts = [p for p in t.rstrip("/").split("/") if p]
        last = parts[-1]
        if last.isdigit():
            msg_id = int(last)
            peer = parts[-2]
        else:
            peer = last
        return peer, msg_id
    if t.startswith("@"):
        return t[1:], 0
    if t.startswith("https://") or t.startswith("http://"):
        parts = [p for p in t.rstrip("/").split("/") if p]
        if parts:
            last = parts[-1]
            if last.isdigit():
                msg_id = int(last)
                peer = parts[-2]
            else:
                peer = last
            return peer, msg_id
    return None, None

def _extract_forward_info(message) -> dict | None:
    if not message:
        return None
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_msg_id = getattr(message, "forward_from_message_id", None)
    if fwd_chat is not None and fwd_msg_id:
        return {"chat_id": fwd_chat.id, "message_id": int(fwd_msg_id)}
    fwd_origin = getattr(message, "forward_origin", None)
    if fwd_origin is not None:
        origin_chat = getattr(fwd_origin, "chat", None)
        origin_msg_id = getattr(fwd_origin, "message_id", None)
        if origin_chat is not None and origin_msg_id:
            return {"chat_id": origin_chat.id, "message_id": int(origin_msg_id)}
    return None

# =========================================================
# REACTION (single session)
# =========================================================
async def _react_one_session(
    session_name: str,
    peer_str: str | None,
    msg_id: int,
    emoji: str | None,
    forward_info: dict | None = None,
) -> dict:
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

        if forward_info:
            try:
                entity = await client.get_entity(forward_info["chat_id"])
            except Exception as e:
                return {"ok": False, "reason": f"forward entity: {e}"}
            target_msg_id = forward_info["message_id"]
        else:
            if not peer_str:
                return {"ok": False, "reason": "no target"}
            entity = await client.get_entity(peer_str)
            if msg_id:
                target_msg_id = msg_id
            else:
                msgs = await client.get_messages(entity, limit=1)
                if not msgs:
                    return {"ok": False, "reason": "no message"}
                target_msg_id = msgs[0].id

        reaction_list = [] if not emoji else [ReactionEmoji(emoticon=emoji)]
        try:
            await client(SendReactionRequest(
                peer=entity,
                msg_id=target_msg_id,
                reaction=reaction_list,
            ))
            return {"ok": True, "reason": None}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
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
        [InlineKeyboardButton("🚫 برداشتن ری‌اکشن", callback_data="mobin_remove_reaction")],
        [InlineKeyboardButton(f"📥 اضافه کردن سشن (ZIP) — {n} فعال", callback_data="mobin_add_sessions")],
        [InlineKeyboardButton(f"🗂 سشن‌های فعال ({n})", callback_data="mobin_sessions_list")],
        [InlineKeyboardButton("📤 دریافت بکاپ", callback_data="mobin_backup_get")],
        [InlineKeyboardButton("📥 وارد کردن بکاپ", callback_data="mobin_backup_import")],
        [InlineKeyboardButton("🔑 تغییر رمز", callback_data="mobin_change_password")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="mobin_close")],
    ])

def _cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["❌ لغو"]], resize_keyboard=True)

def _emoji_kb(emojis: list[str]) -> InlineKeyboardMarkup:
    """کیبورد اینلاین با ایموجی‌های مجاز (پویا)."""
    if not emojis:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
        ])
    kb, row = [], []
    for e in emojis[:24]:
        row.append(InlineKeyboardButton(e, callback_data=f"mobin_em_{e}"))
        if len(row) == 4:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")])
    return InlineKeyboardMarkup(kb)

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

async def _send_main_menu(update: Update, context, text="🎛 <b>پنل مبین</b>"):
    if update.callback_query and update.callback_query.message:
        try:
            await update.callback_query.message.edit_text(
                text, parse_mode="HTML", reply_markup=_main_kb(),
            )
            return
        except Exception:
            pass
    if update.effective_message:
        await update.effective_message.reply_text(
            text, parse_mode="HTML", reply_markup=_main_kb(),
        )

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

    if data == "mobin_close":
        context.user_data.pop("mobin_authenticated", None)
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.reply_text("پنل بسته شد.")
        return ConversationHandler.END

    if data == "mobin_back":
        await _send_main_menu(update, context)
        return MOBIN_ROOT

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
            "روش ارسال هدف:\n"
            "• <b>لینک پست</b> (مثل <code>https://t.me/chan/123</code>)\n"
            "• <b>@username</b> کانال (آخرین پست)\n"
            "• <b>فوروارد/کپی</b> کردن پست از کانال به اینجا\n\n"
            "برای انصراف «❌ لغو» را بفرستید.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    if data == "mobin_remove_reaction":
        sessions = list_sessions()
        if not sessions:
            await q.message.reply_text("⚠️ هیچ سشن فعالی موجود نیست.")
            return MOBIN_ROOT
        await q.message.reply_text(
            f"🚫 <b>برداشتن ری‌اکشن</b>\n\n"
            f"🔋 {len(sessions)} سشن موجود\n\n"
            "لینک پست را بفرستید یا پست را فوروارد/کپی کنید.",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_REMOVE

    if data.startswith("mobin_em_"):
        emoji = data[len("mobin_em_"):]
        context.user_data["mobin_rx_emoji"] = emoji
        n = len(list_sessions())
        await q.message.edit_text(
            f"🎭 ایموجی انتخابی: {emoji}\n\n"
            f"🔢 تعداد سشن‌ها (حداکثر {min(n, 10)}):",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
            ]),
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    if data == "mobin_add_sessions":
        await q.message.reply_text(
            "📥 ZIP حاوی فایل‌های <code>.session</code> را ارسال کنید.\n\n"
            "<b>نحوه کار:</b>\n"
            "• اسم فایل‌های داخل ZIP مهم نیست\n"
            "• خودش اسم می‌ده: <code>acc_0001</code>، <code>acc_0002</code>، ...\n"
            "• اگه session تکراری (بر اساس محتوا) باشه، skip می‌شه\n"
            "• هم با session‌های موجود چک می‌شه، هم داخل خود ZIP\n\n"
            f"مقدار فعلی: <code>API_ID={load_mobin_data().get('api_id')}</code>",
            parse_mode="HTML",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_ADD_SESSION_ZIP

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

    if data == "mobin_backup_import":
        await q.message.reply_text(
            "📥 فایل ZIP بکاپ را ارسال کنید:",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP

    if data == "mobin_change_password":
        await q.message.reply_text(
            "🔑 رمز جدید (حداقل ۳ کاراکتر):",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_WAITING_NEW_PASSWORD

    return MOBIN_ROOT

# ---------- هدف ری‌اکشن: لینک یا فوروارد ----------
async def mobin_rx_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        emojis = await _get_channel_reactions(forward_info=fwd)
        if not emojis:
            await msg.reply_text(
                "⚠️ این کانال هیچ ری‌اکشنی مجاز نکرده است.",
                reply_markup=_cancel_kb(),
            )
            return MOBIN_REACTION_WAITING_TARGET
        await msg.reply_text(
            f"🎭 ایموجی ری‌اکشن را انتخاب کنید ({len(emojis)} مورد مجاز):",
            reply_markup=_emoji_kb(emojis),
        )
        return MOBIN_REACTION_WAITING_EMOJI

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست (مثل <code>https://t.me/chan/123</code>)، "
            "یا @username، یا پست را فوروارد کن.",
            parse_mode="HTML",
        )
        return MOBIN_REACTION_WAITING_TARGET

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None

    emojis = await _get_channel_reactions(peer_str=peer)
    if not emojis:
        await msg.reply_text(
            "⚠️ این کانال هیچ ری‌اکشنی مجاز نکرده است.",
            reply_markup=_cancel_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    await msg.reply_text(
        f"🎭 ایموجی ری‌اکشن را انتخاب کنید ({len(emojis)} مورد مجاز):",
        reply_markup=_emoji_kb(emojis),
    )
    return MOBIN_REACTION_WAITING_EMOJI

# ---------- برداشتن ری‌اکشن: هدف ----------
async def mobin_remove_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        await msg.reply_text(
            f"🔢 تعداد سشن‌ها (حداکثر {min(len(list_sessions()), 10)}):",
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست یا پست را فوروارد کن.",
        )
        return MOBIN_REACTION_WAITING_REMOVE

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None

    await msg.reply_text(
        f"🔢 تعداد سشن‌ها (حداکثر {min(len(list_sessions()), 10)}):",
    )
    return MOBIN_REACTION_WAITING_ACCOUNTS

# ---------- تعداد سشن‌ها -> اجرا ----------
async def mobin_rx_count(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
        return MOBIN_ROOT
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return MOBIN_REACTION_WAITING_ACCOUNTS

    count = int(text)
    sessions = list_sessions()
    count = min(count, len(sessions), 10)

    peer = context.user_data.get("mobin_rx_peer")
    msg_id = context.user_data.get("mobin_rx_msg_id", 0)
    emoji = context.user_data.get("mobin_rx_emoji")
    fwd_info = context.user_data.get("mobin_forward_info")

    action_label = "برداشتن ری‌اکشن" if not emoji else f"ری‌اکشن {emoji}"
    status_msg = await update.message.reply_text(
        f"🚀 در حال اجرا ({action_label}) روی {count} سشن...\n"
    )

    ok_total = 0
    failed = []
    selected = sessions[:count]
    for i in range(0, len(selected), 3):
        batch = selected[i:i+3]
        results = await asyncio.gather(
            *[_react_one_session(s, peer, msg_id, emoji, fwd_info) for s in batch],
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

    for k in ["mobin_rx_peer", "mobin_rx_msg_id", "mobin_rx_emoji", "mobin_forward_info"]:
        context.user_data.pop(k, None)

    await _send_main_menu(update, context, "🎛 <b>پنل مبین</b>")
    return MOBIN_ROOT

# ---------- تغییر رمز ----------
async def mobin_new_password(update: Update, context):
    text = (update.message.text or "").strip()
    if text == "❌ لغو":
        await _send_main_menu(update, context, "لغو شد. به پنل برگشتید.")
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
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            if "mobin.json" in zf.namelist():
                with open(MOBIN_DATA_FILE, "wb") as f:
                    f.write(zf.read("mobin.json"))

        result = import_sessions_zip(raw_bytes)
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

    result = import_sessions_zip(raw_bytes)

    if (not result["imported"]
            and not result["skipped_existing"]
            and not result["skipped_duplicate_in_zip"]
            and not result["errors"]):
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
            reply_markup=_main_kb(),
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
            MOBIN_REACTION_WAITING_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_target),
                MessageHandler(filters.FORWARDED, mobin_rx_target),
            ],
            MOBIN_REACTION_WAITING_EMOJI: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_REACTION_WAITING_REMOVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_remove_target),
                MessageHandler(filters.FORWARDED, mobin_remove_target),
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
