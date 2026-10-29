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
import concurrent.futures
from collections import Counter
from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
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
from telethon.tl.functions.messages import GetMessageReactionsListRequest

mobin_logger = logging.getLogger("mobin")
mobin_logger.setLevel(logging.INFO)

# ---------- CONSTANTS ----------
MOBIN_DATA_FILE = "/tmp/mobin.json"
MOBIN_SESSIONS_DIR = "/tmp/mobin_sessions"
MOBIN_BACKUP_ZIP = "/tmp/mobin_backup.zip"
MOBIN_RESTORE_ZIP = "/tmp/mobin_restore.zip"
DEFAULT_PASSWORD = "من مبینم"
MAX_PARALLEL_CHECKS = 5  # تعداد سشن‌های همزمان برای چک اولیه

_reactions_cache = {
    "list": None,
    "fetched_at": 0,
    "ttl": 12 * 60 * 60,
}

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
    MOBIN_REACTION_WAITING_EMOJI_CHOICE,   # جدید: انتخاب ایموجی برای برداشتن
) = range(100, 111)

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
# SESSION user_id EXTRACTION
# =========================================================
async def _session_user_id(session_name: str) -> int | None:
    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{session_name}.session")
    if not os.path.exists(sess_path):
        return None

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        me = await client.get_me()
        if me is None:
            return None
        return int(me.id)
    except Exception as e:
        mobin_logger.debug(f"_session_user_id({session_name}) failed: {e}")
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _existing_session_user_ids() -> dict[int, str]:
    mapping: dict[int, str] = {}
    for name in list_sessions():
        uid = await _session_user_id(name)
        if uid is not None:
            mapping[uid] = name
    return mapping

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

async def import_sessions_zip(raw_bytes: bytes) -> dict:
    imported: list[tuple[str, str]] = []
    skipped_existing: list[tuple[str, str]] = []
    skipped_duplicate_in_zip: list[tuple[str, str]] = []
    errors: list[str] = []

    if not os.path.isdir(MOBIN_SESSIONS_DIR):
        os.makedirs(MOBIN_SESSIONS_DIR, exist_ok=True)

    existing_hashes = _existing_session_hashes()

    entries: list[tuple[str, bytes, str]] = []
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
                entries.append((base, data, _sha256_bytes(data)))
    except zipfile.BadZipFile as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"ZIP معیوب: {e}"]}
    except Exception as e:
        return {"imported": [], "skipped_existing": [],
                "skipped_duplicate_in_zip": [], "errors": [f"خطای کلی: {e}"]}

    existing_user_ids = await _existing_session_user_ids()

    seen_in_zip_hash: dict[str, str] = {}
    seen_in_zip_uid: dict[int, str] = {}

    for base, data, content_hash in entries:
        if content_hash in existing_hashes:
            skipped_existing.append((base, "session از قبل موجود است (hash یکسان)"))
            continue
        if content_hash in seen_in_zip_hash:
            skipped_duplicate_in_zip.append((base, seen_in_zip_hash[content_hash]))
            continue

        new_name = _next_session_name()
        final_path = os.path.join(MOBIN_SESSIONS_DIR, f"{new_name}.session")
        try:
            with open(final_path, "wb") as f:
                f.write(data)
        except Exception as e:
            errors.append(f"{base}: نوشتن خطا ({e})")
            continue

        uid = await _session_user_id(new_name)

        if uid is not None:
            if uid in existing_user_ids:
                try:
                    os.remove(final_path)
                except Exception:
                    pass
                skipped_existing.append(
                    (base, f"همون اکانت (user_id={uid}) قبلاً هست: {existing_user_ids[uid]}")
                )
                continue
            if uid in seen_in_zip_uid:
                try:
                    os.remove(final_path)
                except Exception:
                    pass
                skipped_duplicate_in_zip.append(
                    (base, f"همون اکانت داخل ZIP: {seen_in_zip_uid[uid]} (user_id={uid})")
                )
                continue
            seen_in_zip_uid[uid] = base
            existing_user_ids[uid] = new_name
        else:
            mobin_logger.warning(f"import: user_id استخراج نشد برای {base} (session شاید نامعتبر)")

        seen_in_zip_hash[content_hash] = base
        existing_hashes.add(content_hash)
        imported.append((new_name, base))

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
        lines.append(f"\n🆔 رد شد (از قبل موجود): {len(skipped_existing)}")
        for name, reason in skipped_existing[:5]:
            lines.append(f"  • <code>{name}</code> — {reason}")
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
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    result = ex.submit(lambda: asyncio.run(import_sessions_zip(raw))).result()
            else:
                result = loop.run_until_complete(import_sessions_zip(raw))
        except RuntimeError:
            result = asyncio.run(import_sessions_zip(raw))
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
    sessions = list_sessions()
    if not sessions:
        return []

    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{sessions[0]}.session")

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return []

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
            return []

        chat_reactions = None
        try:
            full = await client(functions.channels.GetFullChannelRequest(entity))
            chat_reactions = getattr(full.full_chat, "available_reactions", None)
        except Exception:
            try:
                full = await client(functions.messages.GetFullChatRequest(entity.id))
                chat_reactions = getattr(full.full_chat, "available_reactions", None)
            except Exception:
                chat_reactions = None

        if chat_reactions is None:
            return await _get_global_reactions(client)

        class_name = type(chat_reactions).__name__

        if "None" in class_name:
            return []

        if "All" in class_name:
            return await _get_global_reactions(client)

        if "Some" in class_name:
            reactions = getattr(chat_reactions, "reactions", []) or []
            emojis = []
            for r in reactions:
                emoticon = getattr(r, "emoticon", None)
                if emoticon:
                    emojis.append(emoticon)
            return emojis

        return []

    except Exception as e:
        mobin_logger.error(f"get_channel_reactions failed: {e}")
        return []
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _get_global_reactions(client) -> list[str]:
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
# SCAN ALL SESSIONS FOR REACTION STATUS
# =========================================================
async def _get_my_reaction_status(
    session_name: str,
    peer_str: str | None,
    msg_id: int,
    forward_info: dict | None = None,
) -> dict:
    """
    بررسی وضعیت ری‌اکشن یک سشن روی پیام هدف — دقیق.
    از GetMessageReactionsListRequest استفاده می‌کنه و فقط ری‌اکشنی که
    خود این سشن زده رو برمی‌گردونه (نه aggregate).
    """
    data = load_mobin_data()
    api_id = data.get("api_id", 2040)
    api_hash = data.get("api_hash", "b18441a1ff607e10a989891a5462e627")
    sess_path = os.path.join(MOBIN_SESSIONS_DIR, f"{session_name}.session")
    if not os.path.exists(sess_path):
        return {"ok": False, "has_reaction": False, "emojis": [], "reason": "session file missing"}

    client = TelegramClient(sess_path, api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {"ok": False, "has_reaction": False, "emojis": [], "reason": "unauthorized"}

        me = await client.get_me()
        my_uid = int(me.id) if me else None
        if my_uid is None:
            return {"ok": False, "has_reaction": False, "emojis": [], "reason": "no me"}

        if forward_info:
            try:
                entity = await client.get_entity(forward_info["chat_id"])
            except Exception as e:
                return {"ok": False, "has_reaction": False, "emojis": [], "reason": f"forward entity: {e}"}
            target_msg_id = forward_info["message_id"]
        else:
            if not peer_str:
                return {"ok": False, "has_reaction": False, "emojis": [], "reason": "no target"}
            try:
                entity = await client.get_entity(peer_str)
            except Exception as e:
                return {"ok": False, "has_reaction": False, "emojis": [], "reason": f"entity: {e}"}
            if msg_id:
                target_msg_id = msg_id
            else:
                msgs = await client.get_messages(entity, limit=1)
                if not msgs:
                    return {"ok": False, "has_reaction": False, "emojis": [], "reason": "no message"}
                target_msg_id = msgs[0].id

        # لیست کسانی که روی این پیام ری‌اکشن زدن
        my_emojis: list[str] = []
        offset = ""
        try:
            while True:
                res = await client(GetMessageReactionsListRequest(
                    peer=entity,
                    id=target_msg_id,
                    limit=100,
                    offset=offset,
                ))
                reactions_list = getattr(res, "reactions", None) or []
                for item in reactions_list:
                    peer_id = getattr(item, "peer_id", None)
                    uid = getattr(peer_id, "user_id", None) if peer_id else None
                    if uid != my_uid:
                        continue
                    reaction = getattr(item, "reaction", None)
                    if reaction is None:
                        continue
                    emoticon = getattr(reaction, "emoticon", None)
                    if emoticon:
                        my_emojis.append(emoticon)
                    else:
                        document_id = getattr(reaction, "document_id", None)
                        if document_id:
                            my_emojis.append(f"custom:{document_id}")
                # ادامه صفحه‌بندی
                next_offset = getattr(res, "next_offset", None)
                if not next_offset:
                    break
                offset = next_offset
                # محافظت: حداکثر ۵ صفحه
                if len(my_emojis) > 0 and len(reactions_list) < 100:
                    break
        except Exception as e:
            # برخی کانال‌ها (private یا خیلی بزرگ) ممکنه اجازه ندن
            # در این حالت fallback به روش قبلی (aggregate) — ولی فقط برای تشخیص has_reaction
            mobin_logger.debug(f"GetMessageReactionsList failed for {session_name}: {e}")
            try:
                msgs = await client.get_messages(entity, ids=[target_msg_id])
                if not msgs or not msgs[0]:
                    return {"ok": False, "has_reaction": False, "emojis": [], "reason": "message not found"}
                reactions = getattr(msgs[0], "reactions", None)
                if not reactions:
                    return {"ok": True, "has_reaction": False, "emojis": [], "reason": None}
                # نمی‌تونیم مطمئن باشیم کدوم مال این سشنه — فقط aggregate
                return {"ok": True, "has_reaction": False, "emojis": [], "reason": None}
            except Exception as e2:
                return {"ok": False, "has_reaction": False, "emojis": [], "reason": f"list: {e} / fallback: {e2}"}

        return {
            "ok": True,
            "has_reaction": bool(my_emojis),
            "emojis": my_emojis,
            "reason": None,
        }

    except Exception as e:
        return {"ok": False, "has_reaction": False, "emojis": [], "reason": str(e)}
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _scan_all_sessions_reactions(
    peer_str: str | None,
    msg_id: int,
    forward_info: dict | None = None,
    progress_cb=None,
) -> dict:
    """
    وضعیت ری‌اکشن همه سشن‌ها را جمع‌آوری می‌کند.
    خروجی:
      {
        "total": int,
        "scanned_ok": int,
        "scanned_failed": int,
        "no_reaction": [session_names...],
        "with_reaction": [(session_name, [emojis])...],
        "emoji_counts": {emoji: count},
        "custom_emoji_counts": {doc_id: count},
        "failed": [(session_name, reason)...],
      }
    """
    sessions = list_sessions()
    no_reaction: list[str] = []
    with_reaction: list[tuple[str, list[str]]] = []
    failed: list[tuple[str, str]] = []
    emoji_counts: Counter = Counter()
    custom_emoji_counts: Counter = Counter()

    total = len(sessions)
    if total == 0:
        return {
            "total": 0, "scanned_ok": 0, "scanned_failed": 0,
            "no_reaction": [], "with_reaction": [], "failed": [],
            "emoji_counts": {}, "custom_emoji_counts": {},
        }

    done = 0
    for i in range(0, total, MAX_PARALLEL_CHECKS):
        batch = sessions[i:i + MAX_PARALLEL_CHECKS]
        results = await asyncio.gather(
            *[_get_my_reaction_status(s, peer_str, msg_id, forward_info) for s in batch],
            return_exceptions=True,
        )
        for s, r in zip(batch, results):
            done += 1
            if not isinstance(r, dict) or not r.get("ok"):
                reason = r.get("reason") if isinstance(r, dict) else str(r)
                failed.append((s, reason or "unknown"))
                continue
            if r["has_reaction"]:
                emojis = r["emojis"]
                with_reaction.append((s, emojis))
                for e in emojis:
                    if e.startswith("custom:"):
                        custom_emoji_counts[e] += 1
                    else:
                        emoji_counts[e] += 1
            else:
                no_reaction.append(s)
        if progress_cb:
            try:
                await progress_cb(done, total)
            except Exception:
                pass
        if i + MAX_PARALLEL_CHECKS < total:
            await asyncio.sleep(random.uniform(0.8, 1.8))

    return {
        "total": total,
        "scanned_ok": len(no_reaction) + len(with_reaction),
        "scanned_failed": len(failed),
        "no_reaction": no_reaction,
        "with_reaction": with_reaction,
        "failed": failed,
        "emoji_counts": dict(emoji_counts),
        "custom_emoji_counts": dict(custom_emoji_counts),
    }


def _format_scan_report(scan: dict, is_remove: bool) -> str:
    lines: list[str] = []
    total = scan["total"]
    no_react = len(scan["no_reaction"])
    with_react = len(scan["with_reaction"])
    failed = len(scan["failed"])

    if is_remove:
        lines.append(f"📊 <b>وضعیت ری‌اکشن روی این پست:</b>")
        lines.append(f"• 🔴 {with_react} سشن ری‌اکشن دارند")
        lines.append(f"• 🟢 {no_react} سشن هیچ ری‌اکشنی ندارند")
    else:
        lines.append(f"📊 <b>وضعیت ری‌اکشن روی این پست:</b>")
        lines.append(f"• 🔴 {with_react} سشن ری‌اکشن دارند")
        lines.append(f"• 🟢 {no_react} سشن آزاد (بدون ری‌اکشن)")
        lines.append(f"\n<i>از سشن‌های آزاد برای زدن ری‌اکشن جدید استفاده می‌شود.</i>")

    if scan["emoji_counts"]:
        lines.append("\n🎭 <b>ایموجی‌هایی که این سشن‌ها زده‌اند:</b>")
        # مرتب‌سازی نزولی بر اساس تعداد
        sorted_emojis = sorted(scan["emoji_counts"].items(), key=lambda x: -x[1])
        for emoji, cnt in sorted_emojis[:12]:
            lines.append(f"  {emoji} × <b>{cnt}</b> سشن")
        if len(sorted_emojis) > 12:
            lines.append(f"  ... و {len(sorted_emojis) - 12} ایموجی دیگر")

    if scan["custom_emoji_counts"]:
        total_custom = sum(scan["custom_emoji_counts"].values())
        lines.append(f"\n🎨 {total_custom} ری‌اکشن از نوع custom emoji (سند)")

    if failed:
        lines.append(f"\n⚠️ {failed} سشن قابل چک نبودند (غیرمجاز/خطا)")

    return "\n".join(lines)


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

def _cancel_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو", callback_data="mobin_cancel_action")],
    ])

def _emoji_kb(emojis: list[str]) -> InlineKeyboardMarkup:
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
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="mobin_cancel_action")])
    return InlineKeyboardMarkup(kb)

def _remove_emoji_kb(emoji_counts: dict[str, int]) -> InlineKeyboardMarkup:
    """
    کیبورد انتخاب ایموجی برای حذف — فقط ایموجی‌هایی که واقعاً روی پست زده شده‌اند.
    callback_data شامل ایموجی هست.
    """
    if not emoji_counts:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
        ])
    kb, row = [], []
    sorted_emojis = sorted(emoji_counts.items(), key=lambda x: -x[1])
    for emoji, cnt in sorted_emojis[:24]:
        label = f"{emoji} ({cnt})"
        row.append(InlineKeyboardButton(label, callback_data=f"mobin_rm_em_{emoji}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🗑 حذف همه ری‌اکشن‌ها (همه ایموجی‌ها)", callback_data="mobin_rm_em_ALL")])
    kb.append([InlineKeyboardButton("❌ لغو", callback_data="mobin_cancel_action")])
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

def _clear_mobin_user_data(context):
    for k in list(context.user_data.keys()):
        if k.startswith("mobin_"):
            context.user_data.pop(k, None)

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

    # لغو سراسری (اینلاین)
    if data == "mobin_cancel_action":
        _clear_mobin_user_data(context)
        try:
            await q.message.delete()
        except Exception:
            pass
        await q.message.reply_text(
            "لغو شد. به پنل برگشتید.",
            reply_markup=_main_kb(),
        )
        return MOBIN_ROOT

    if data == "mobin_close":
        context.user_data.pop("mobin_authenticated", None)
        _clear_mobin_user_data(context)
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
            "بعد از ارسال هدف، ربات همه سشن‌ها را چک می‌کند و گزارش می‌دهد چه ری‌اکشن‌هایی روی پست هست و چند سشن آزاد است.\n"
            "سپس فقط از سشن‌های آزاد برای زدن ری‌اکشن جدید استفاده می‌شود.\n\n"
            "برای انصراف دکمه «❌ لغو» را بزنید.",
            parse_mode="HTML",
            reply_markup=_cancel_inline_kb(),
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
            "لینک پست را بفرستید یا پست را فوروارد/کپی کنید.\n"
            "بعد از ارسال هدف، ربات همه سشن‌ها را چک می‌کند و می‌گوید هر ایموجی توسط چند سشن زده شده.\n"
            "سپس می‌توانید انتخاب کنید کدام ایموجی برداشته شود.\n\n"
            "برای انصراف دکمه «❌ لغو» را بزنید.",
            parse_mode="HTML",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_REACTION_WAITING_REMOVE

    if data.startswith("mobin_em_"):
        emoji = data[len("mobin_em_"):]
        context.user_data["mobin_rx_emoji"] = emoji
        n = len(list_sessions())
        await q.message.edit_text(
            f"🎭 ایموجی انتخابی: {emoji}\n\n"
            f"🔢 تعداد سشن‌ها (حداکثر {min(n, 10)}):\n"
            f"<i>اعداد را همینجا تایپ کنید.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ لغو", callback_data="mobin_cancel_action")]
            ]),
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    if data.startswith("mobin_rm_em_"):
        # انتخاب ایموجی برای حذف
        emoji = data[len("mobin_rm_em_"):]
        if emoji == "ALL":
            context.user_data["mobin_remove_emoji"] = None  # یعنی همه
        else:
            context.user_data["mobin_remove_emoji"] = emoji
        n = len(list_sessions())
        await q.message.edit_text(
            f"🚫 حذف ری‌اکشن {emoji if emoji != 'ALL' else 'همه'}\n\n"
            f"🔢 تعداد سشن‌ها برای اجرا (حداکثر {min(n, 10)}):\n"
            f"<i>اعداد را همینجا تایپ کنید.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ لغو", callback_data="mobin_cancel_action")]
            ]),
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    if data == "mobin_add_sessions":
        await q.message.reply_text(
            "📥 ZIP حاوی فایل‌های <code>.session</code> را ارسال کنید.\n\n"
            "<b>نحوه کار:</b>\n"
            "• اسم فایل‌های داخل ZIP مهم نیست\n"
            "• خودش اسم می‌ده: <code>acc_0001</code>، <code>acc_0002</code>، ...\n"
            "• اگه session تکراری باشه skip می‌شه — دو لایه:\n"
            "  1️⃣ hash محتوای فایل\n"
            "  2️⃣ user_id عددی داخل session (حتی اگه hash فرق کنه)\n"
            "• هم با سشن‌های موجود چک می‌شه، هم داخل خود ZIP\n\n"
            f"مقدار فعلی: <code>API_ID={load_mobin_data().get('api_id')}</code>",
            parse_mode="HTML",
            reply_markup=_cancel_inline_kb(),
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
            f"⏳ در حال خواندن user_id سشن‌ها ({len(names)})...",
            parse_mode="HTML",
        )
        lines = [f"🗂 <b>سشن‌های فعال ({len(names)})</b>\n"]
        for name in names[:30]:
            uid = await _session_user_id(name)
            uid_str = str(uid) if uid else "نامعتبر/غیرمجاز"
            lines.append(f"• <code>{name}</code> — user_id: <code>{uid_str}</code>")
        if len(names) > 30:
            lines.append(f"\n... و {len(names) - 30} تای دیگر")

        await q.message.edit_text(
            "\n".join(lines),
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
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP

    if data == "mobin_change_password":
        await q.message.reply_text(
            "🔑 رمز جدید (حداقل ۳ کاراکتر):",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_WAITING_NEW_PASSWORD

    return MOBIN_ROOT

# ---------- هدف ری‌اکشن ----------
async def mobin_rx_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        return await _start_reaction_scan(update, context, peer=None, msg_id=0, forward_info=fwd)

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست (مثل <code>https://t.me/chan/123</code>)، "
            "یا @username، یا پست را فوروارد کن.",
            parse_mode="HTML",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None
    return await _start_reaction_scan(update, context, peer=peer, msg_id=msg_id, forward_info=None)


async def _start_reaction_scan(update, context, peer, msg_id, forward_info):
    """اسکن همه سشن‌ها و گزارش وضعیت + نمایش ایموجی‌های مجاز."""
    msg = update.message
    sessions = list_sessions()
    if not sessions:
        await msg.reply_text("⚠️ هیچ سشن فعالی موجود نیست.", reply_markup=_cancel_inline_kb())
        return MOBIN_REACTION_WAITING_TARGET

    # 1) گرفتن ایموجی‌های مجاز کانال
    emojis = await _get_channel_reactions(peer_str=peer, forward_info=forward_info)
    if not emojis:
        await msg.reply_text(
            "⚠️ این کانال هیچ ری‌اکشنی مجاز نکرده است.\n"
            "یا نتونستم به تنظیمات ری‌اکشن کانال دسترسی پیدا کنم.\n"
            "برای انصراف دکمه «❌ لغو» را بزنید.",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_REACTION_WAITING_TARGET

    # 2) اسکن همه سشن‌ها
    progress_msg = await msg.reply_text(
        f"🔍 در حال اسکن {len(sessions)} سشن برای بررسی ری‌اکشن‌های فعلی..."
    )

    async def progress_cb(done, total):
        if done == total or done % 5 == 0:
            try:
                await progress_msg.edit_text(
                    f"🔍 در حال اسکن سشن‌ها... ({done}/{total})"
                )
            except Exception:
                pass

    scan = await _scan_all_sessions_reactions(
        peer, msg_id, forward_info, progress_cb=progress_cb
    )

    # ذخیره در context برای استفاده در مرحله بعد
    context.user_data["mobin_scan_result"] = scan
    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = forward_info
    context.user_data["mobin_rx_emojis_available"] = emojis

    # 3) ساخت گزارش
    report = _format_scan_report(scan, is_remove=False)
    no_react_count = len(scan["no_reaction"])

    await progress_msg.edit_text(
        f"{report}\n\n"
        f"🎭 حالا یک ایموجی مجاز انتخاب کنید "
        f"({len(emojis)} مورد مجاز):\n"
        f"<i>توجه: ری‌اکشن جدید فقط روی سشن‌های آزاد زده می‌شود"
        f" (الان {no_react_count} سشن آزاد دارید).</i>",
        parse_mode="HTML",
        reply_markup=_emoji_kb(emojis),
    )
    return MOBIN_REACTION_WAITING_EMOJI


# ---------- هدف برداشتن ری‌اکشن ----------
async def mobin_remove_target(update: Update, context):
    msg = update.message
    text = (msg.text or "").strip()

    fwd = _extract_forward_info(msg)
    if fwd:
        context.user_data["mobin_forward_info"] = fwd
        context.user_data["mobin_rx_peer"] = None
        context.user_data["mobin_rx_msg_id"] = 0
        return await _start_remove_scan(update, context, peer=None, msg_id=0, forward_info=fwd)

    peer, msg_id = _parse_target_from_text(text)
    if not peer:
        await msg.reply_text(
            "❌ نتوانستم هدف را تشخیص بدم.\n"
            "لینک بفرست یا پست را فوروارد کن.",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_REACTION_WAITING_REMOVE

    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = None
    return await _start_remove_scan(update, context, peer=peer, msg_id=msg_id, forward_info=None)


async def _start_remove_scan(update, context, peer, msg_id, forward_info):
    """اسکن همه سشن‌ها برای حالت حذف + نمایش ایموجی‌های موجود روی پست."""
    msg = update.message
    sessions = list_sessions()
    if not sessions:
        await msg.reply_text("⚠️ هیچ سشن فعالی موجود نیست.", reply_markup=_cancel_inline_kb())
        return MOBIN_REACTION_WAITING_REMOVE

    progress_msg = await msg.reply_text(
        f"🔍 در حال اسکن {len(sessions)} سشن برای بررسی ری‌اکشن‌های فعلی..."
    )

    async def progress_cb(done, total):
        if done == total or done % 5 == 0:
            try:
                await progress_msg.edit_text(
                    f"🔍 در حال اسکن سشن‌ها... ({done}/{total})"
                )
            except Exception:
                pass

    scan = await _scan_all_sessions_reactions(
        peer, msg_id, forward_info, progress_cb=progress_cb
    )

    context.user_data["mobin_scan_result"] = scan
    context.user_data["mobin_rx_peer"] = peer
    context.user_data["mobin_rx_msg_id"] = msg_id
    context.user_data["mobin_forward_info"] = forward_info

    report = _format_scan_report(scan, is_remove=True)

    if not scan["with_reaction"]:
        # هیچ سشنی ری‌اکشن ندارد
        await progress_msg.edit_text(
            f"{report}\n\n"
            f"ℹ️ هیچ سشنی روی این پست ری‌اکشن ندارد — کاری برای حذف وجود ندارد.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="mobin_back")]
            ]),
        )
        return MOBIN_ROOT

    emoji_counts = scan["emoji_counts"]
    await progress_msg.edit_text(
        f"{report}\n\n"
        f"🎭 کدام ایموجی برداشته شود؟ (روی هر ایموجی تعداد سشن‌هایی که آن را زده‌اند نوشته شده)",
        parse_mode="HTML",
        reply_markup=_remove_emoji_kb(emoji_counts),
    )
    return MOBIN_REACTION_WAITING_EMOJI_CHOICE


# ---------- تعداد سشن‌ها -> اجرا ----------
async def mobin_rx_count(update: Update, context):
    text = (update.message.text or "").strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text(
            "❌ عدد معتبر وارد کنید.",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_REACTION_WAITING_ACCOUNTS

    requested = int(text)
    sessions = list_sessions()
    requested = min(requested, len(sessions), 10)

    peer = context.user_data.get("mobin_rx_peer")
    msg_id = context.user_data.get("mobin_rx_msg_id", 0)
    emoji = context.user_data.get("mobin_rx_emoji")
    fwd_info = context.user_data.get("mobin_forward_info")
    scan = context.user_data.get("mobin_scan_result") or {}
    remove_emoji = context.user_data.get("mobin_remove_emoji", "__NOT_SET__")

    is_remove = emoji is None

    if is_remove:
        # ===== حالت حذف =====
        # سشن‌هایی که واقعاً ری‌اکشن دارند (از اسکن)
        sessions_with_reaction = [s for s, _ in scan.get("with_reaction", [])]
        if not sessions_with_reaction:
            await update.message.reply_text(
                "ℹ️ هیچ سشنی روی این پست ری‌اکشن ندارد — کاری برای حذف نیست.",
                reply_markup=_main_kb(),
            )
            _clear_mobin_user_data(context)
            return MOBIN_ROOT

        # اگر کاربر ایموجی خاصی انتخاب کرده، فقط سشن‌هایی که همان ایموجی را زده‌اند
        if remove_emoji != "__NOT_SET__" and remove_emoji is not None:
            target_emoji = remove_emoji
            filtered = []
            for s, emojis in scan.get("with_reaction", []):
                if target_emoji in emojis:
                    filtered.append(s)
            if not filtered:
                await update.message.reply_text(
                    f"ℹ️ هیچ سشنی ری‌اکشن {target_emoji} روی این پست ندارد.",
                    reply_markup=_main_kb(),
                )
                _clear_mobin_user_data(context)
                return MOBIN_ROOT
            candidates = filtered
            # برای حذف یک ایموجی خاص، به هر سشن باید reaction=[] بفرستیم
            # (یعنی همه ری‌اکشن‌هایش برداشته شود) — یا می‌توان از API مخصوصش استفاده کرد
            # ساده‌ترین راه: خالی کردن همه ری‌اکشن‌های همان سشن
            action_label = f"حذف ری‌اکشن {target_emoji}"
            selected = candidates[:requested]
            emoji_to_send = None  # خالی کردن همه
        else:
            # حذف همه ایموجی‌ها از سشن‌های دارای ری‌اکشن
            selected = sessions_with_reaction[:requested]
            action_label = "حذف همه ری‌اکشن‌ها"
            emoji_to_send = None

    else:
        # ===== حالت زدن ری‌اکشن =====
        no_reaction_sessions = list(scan.get("no_reaction", []))
        with_reaction_sessions = [s for s, _ in scan.get("with_reaction", [])]

        # اول از سشن‌های آزاد
        selected = no_reaction_sessions[:requested]
        used_fallback = False
        if len(selected) < requested:
            # کمبود سشن آزاد → از سشن‌های ری‌اکشن‌دار هم استفاده کن (با هشدار)
            remaining = requested - len(selected)
            selected += with_reaction_sessions[:remaining]
            used_fallback = True
        emoji_to_send = emoji
        action_label = f"ری‌اکشن {emoji}"

    if not selected:
        await update.message.reply_text(
            "❌ سشنی برای اجرا پیدا نشد.",
            reply_markup=_main_kb(),
        )
        _clear_mobin_user_data(context)
        return MOBIN_ROOT

    actual_count = len(selected)
    status_msg = await update.message.reply_text(
        f"🚀 در حال اجرا ({action_label}) روی {actual_count} سشن..."
    )

    ok_total = 0
    failed = []

    for i in range(0, len(selected), 3):
        batch = selected[i:i+3]
        results = await asyncio.gather(
            *[_react_one_session(s, peer, msg_id, emoji_to_send, fwd_info) for s in batch],
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

    # ===== ساخت گزارش نهایی =====
    lines = [f"✅ موفق: {ok_total}/{actual_count}"]

    if is_remove:
        lines.append(f"\n🗑 عملیات: {action_label}")
    else:
        no_count = len(scan.get("no_reaction", []))
        with_count = len(scan.get("with_reaction", []))
        lines.append(f"\n🔋 از سشن‌های آزاد استفاده شد: {min(actual_count, no_count)}")
        if used_fallback and actual_count > no_count:
            extra = actual_count - no_count
            lines.append(f"⚠️ {extra} سشن دیگر از بین سشن‌های ری‌اکشن‌دار انتخاب شد (کمبود سشن آزاد)")

    if failed:
        lines.append(f"\n❌ ناموفق‌ها ({len(failed)}):")
        lines.extend(failed[:10])

    summary = "\n".join(lines)
    try:
        await status_msg.edit_text(summary, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(summary, parse_mode="HTML")

    _clear_mobin_user_data(context)
    await _send_main_menu(update, context, "🎛 <b>پنل مبین</b>")
    return MOBIN_ROOT

# ---------- تغییر رمز ----------
async def mobin_new_password(update: Update, context):
    text = (update.message.text or "").strip()
    if len(text) < 3:
        await update.message.reply_text(
            "❌ خیلی کوتاه است. حداقل ۳ کاراکتر:",
            reply_markup=_cancel_inline_kb(),
        )
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
        await update.message.reply_text(
            "❌ فقط ZIP بفرستید یا از دکمه «❌ لغو» استفاده کنید.",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    raw_bytes = bytes(raw)

    try:
        with zipfile.ZipFile(iolib.BytesIO(raw_bytes)) as zf:
            if "mobin.json" in zf.namelist():
                with open(MOBIN_DATA_FILE, "wb") as f:
                    f.write(zf.read("mobin.json"))

        result = await import_sessions_zip(raw_bytes)
        _schedule_backup_upload()
        n = len(list_sessions())
        report = _format_import_report(result)
        await update.message.reply_text(
            f"✅ بکاپ پردازش شد — {n} سشن فعال.\n\n{report}",
            parse_mode="HTML",
            reply_markup=_main_kb(),
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا: {e}",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_WAITING_IMPORT_ZIP
    return MOBIN_ROOT

# ---------- اضافه کردن سشن‌ها با ZIP ----------
async def mobin_add_sessions_zip(update: Update, context):
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text(
            "❌ فقط ZIP بفرستید یا از دکمه «❌ لغو» استفاده کنید.",
            reply_markup=_cancel_inline_kb(),
        )
        return MOBIN_WAITING_ADD_SESSION_ZIP

    file = await doc.get_file()
    raw = await file.download_as_bytearray()
    raw_bytes = bytes(raw)

    status = await update.message.reply_text("⏳ در حال پردازش ZIP...")

    result = await import_sessions_zip(raw_bytes)

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
    _clear_mobin_user_data(context)
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
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
            MOBIN_REACTION_WAITING_EMOJI: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_REACTION_WAITING_REMOVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_remove_target),
                MessageHandler(filters.FORWARDED, mobin_remove_target),
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
            MOBIN_REACTION_WAITING_EMOJI_CHOICE: [
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_"),
            ],
            MOBIN_REACTION_WAITING_ACCOUNTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_rx_count),
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
            MOBIN_WAITING_NEW_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, mobin_new_password),
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
            MOBIN_WAITING_IMPORT_ZIP: [
                MessageHandler(filters.Document.ALL, mobin_import_zip),
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
            MOBIN_WAITING_ADD_SESSION_ZIP: [
                MessageHandler(filters.Document.ALL, mobin_add_sessions_zip),
                CallbackQueryHandler(mobin_cb, pattern=r"^mobin_cancel_action$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", mobin_cancel)],
        allow_reentry=True,
    )
