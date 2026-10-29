import json
import os
from aiohttp import web
import mimetypes
import aiohttp

DB_FILE = "/tmp/database.json"


def load_db():
    print("========== MINIAPP DB DEBUG ==========")
    print("DB_FILE:", DB_FILE)
    print("Current directory:", os.getcwd())
    print("File exists:", os.path.exists(DB_FILE))

    if os.path.exists(DB_FILE):
        try:
            print("File size:", os.path.getsize(DB_FILE))

            with open(DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            print("DB type:", type(data))
            print("DB keys:", len(data) if isinstance(data, dict) else "NOT DICT")

            if isinstance(data, dict):
                print("First keys:", list(data.keys())[:10])

            print("======================================")

            return data if isinstance(data, dict) else {}

        except Exception as e:
            print("❌ DB READ ERROR:", repr(e))
            print("======================================")
            return {}

    print("❌ DATABASE FILE DOES NOT EXIST")
    print("======================================")

    return {}

async def miniapp_file(request):
    """
    ارسال فایل Telegram به Mini App
    با پشتیبانی از HTTP Range.

    برای جلوگیری از مصرف شدید اینترنت:
    هر درخواست Range حداکثر PREVIEW_MAX_BYTES بایت
    از Telegram دریافت می‌کند.
    """

    file_id = request.query.get("file_id")

    if not file_id:
        return web.Response(
            text="file_id مشخص نشده",
            status=400
        )

    try:

        bot = request.app["bot"]

        # =====================================================
        # گرفتن فایل از Telegram
        # =====================================================

        telegram_file = await bot.get_file(file_id)

        file_url = telegram_file.file_path

        if not file_url:
            return web.Response(
                text="آدرس فایل از تلگرام دریافت نشد.",
                status=500
            )

        # =====================================================
        # مشخصات فایل
        # =====================================================

        filename = (
            request.query.get("filename")
            or "file"
        )

        mime_type = (
            request.query.get("mime")
            or mimetypes.guess_type(filename)[0]
            or "application/octet-stream"
        )

        # =====================================================
        # حداکثر حجم هر درخواست Preview
        # =====================================================

        PREVIEW_MAX_BYTES = 3 * 1024 * 1024
        # 8 MB

        # =====================================================
        # Header خروجی
        # =====================================================

        base_headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": mime_type,
            "Content-Disposition": (
                f'inline; filename="{filename}"'
            ),
            "Cache-Control": "no-cache",
        }

        # =====================================================
        # Range مرورگر
        # =====================================================

        range_header = request.headers.get("Range")

        # =====================================================
        # اگر Range وجود ندارد
        # =====================================================

        if not range_header:

            # برای جلوگیری از دانلود کامل فایل،
            # فقط ابتدای فایل را ارسال می‌کنیم.

            start = 0
            end = PREVIEW_MAX_BYTES - 1

            telegram_headers = {
                "Range": f"bytes={start}-{end}"
            }

            async with aiohttp.ClientSession() as session:

                async with session.get(
                    file_url,
                    headers=telegram_headers
                ) as response:

                    if response.status not in (200, 206):

                        return web.Response(
                            text="خطا در دریافت فایل از تلگرام.",
                            status=response.status
                        )

                    content_range = response.headers.get(
                        "Content-Range"
                    )

                    content_length = response.headers.get(
                        "Content-Length"
                    )

                    headers = dict(base_headers)

                    if content_range:
                        headers["Content-Range"] = content_range

                    if content_length:
                        headers["Content-Length"] = content_length

                    # اگر Telegram Range را قبول کرد
                    if response.status == 206:

                        resp = web.StreamResponse(
                            status=206,
                            headers=headers
                        )

                        await resp.prepare(request)

                        async for chunk in response.content.iter_chunked(
                            256 * 1024
                        ):

                            await resp.write(chunk)

                        await resp.write_eof()

                        return resp

                    # اگر Telegram کل فایل را برگرداند،
                    # بیشتر از Preview را به کاربر نمی‌دهیم.
                    if response.status == 200:

                        resp = web.StreamResponse(
                            status=200,
                            headers=headers
                        )

                        await resp.prepare(request)

                        remaining = PREVIEW_MAX_BYTES

                        while remaining > 0:

                            chunk = await response.content.read(
                                min(
                                    256 * 1024,
                                    remaining
                                )
                            )

                            if not chunk:
                                break

                            await resp.write(chunk)

                            remaining -= len(chunk)

                        await resp.write_eof()

                        return resp

        # =====================================================
        # Parse Range
        # =====================================================

        if not range_header.startswith("bytes="):

            return web.Response(
                text="Range نامعتبر است.",
                status=416
            )

        range_value = range_header[6:].strip()

        # Multiple Range
        if "," in range_value:

            return web.Response(
                text="Multiple ranges پشتیبانی نمی‌شود.",
                status=416
            )

        try:

            start_str, end_str = range_value.split(
                "-",
                1
            )

            start = int(start_str)

            if start < 0:
                raise ValueError()

            if end_str:
                requested_end = int(end_str)
            else:
                requested_end = start + PREVIEW_MAX_BYTES - 1

        except Exception:

            return web.Response(
                text="Range نامعتبر است.",
                status=416
            )

        # =====================================================
        # محدود کردن Range
        # =====================================================

        requested_end = min(
            requested_end,
            start + PREVIEW_MAX_BYTES - 1
        )

        end = requested_end

        print(
            f"🎬 MINIAPP RANGE: {start}-{end}"
        )

        # =====================================================
        # درخواست محدود به Telegram
        # =====================================================

        telegram_headers = {
            "Range": f"bytes={start}-{end}"
        }

        async with aiohttp.ClientSession() as session:

            async with session.get(
                file_url,
                headers=telegram_headers
            ) as response:

                # =================================================
                # Telegram Range
                # =================================================

                if response.status == 206:

                    headers = dict(base_headers)

                    content_range = response.headers.get(
                        "Content-Range"
                    )

                    content_length = response.headers.get(
                        "Content-Length"
                    )

                    if content_range:
                        headers["Content-Range"] = content_range

                    if content_length:
                        headers["Content-Length"] = content_length

                    resp = web.StreamResponse(
                        status=206,
                        headers=headers
                    )

                    await resp.prepare(request)

                    async for chunk in response.content.iter_chunked(
                        256 * 1024
                    ):

                        await resp.write(chunk)

                    await resp.write_eof()

                    return resp

                # =================================================
                # Telegram Range را قبول نکرد
                # =================================================

                if response.status == 200:

                    print(
                        "⚠️ Telegram returned 200 instead of 206"
                    )

                    headers = dict(base_headers)

                    resp = web.StreamResponse(
                        status=200,
                        headers=headers
                    )

                    await resp.prepare(request)

                    remaining = PREVIEW_MAX_BYTES

                    while remaining > 0:

                        chunk = await response.content.read(
                            min(
                                256 * 1024,
                                remaining
                            )
                        )

                        if not chunk:
                            break

                        await resp.write(chunk)

                        remaining -= len(chunk)

                    await resp.write_eof()

                    return resp

                return web.Response(
                    text="خطا در دریافت فایل از تلگرام.",
                    status=response.status
                )

    except Exception as e:

        print(
            "❌ Mini App file error:",
            repr(e)
        )

        return web.Response(
            text="خطا در دریافت فایل",
            status=500
        )

def get_style(node):
    raw_style = (
        node.get("color")
        or node.get("style")
        or node.get("node_style")
        or "none"
    )

    return str(raw_style).strip().lower()


def get_rows(node):
    children_ids = [
        str(cid)
        for cid in node.get("children", [])
    ]

    layout = node.get("layout")

    if isinstance(layout, list) and layout:

        cleaned = []

        child_set = set(children_ids)

        for row in layout:

            if isinstance(row, list):

                new_row = [
                    str(cid)
                    for cid in row
                    if str(cid) in child_set
                ]

                if new_row:
                    cleaned.append(new_row)

        if cleaned:
            return cleaned

    try:
        n = int(node.get("row_count", 2))

        if n <= 0:
            n = 2

    except (TypeError, ValueError):
        n = 2

    return [
        children_ids[i:i + n]
        for i in range(0, len(children_ids), n)
    ]


def serialize_contents(contents):

    result = []

    for item in contents or []:

        if not isinstance(item, dict):
            continue

        # کل اطلاعات content حفظ شود
        content = dict(item)

        if not content.get("type"):
            content["type"] = "unknown"

        if "text" not in content:
            content["text"] = (
                content.get("content")
                or ""
            )

        if "caption" not in content:
            content["caption"] = ""

        if "file_name" not in content:

            content["file_name"] = (
                content.get("filename")
                or content.get("document_name")
                or content.get("title")
                or ""
            )

        result.append(content)

    return result


def get_breadcrumb(db, node_id):

    path = []
    seen = set()

    cur = str(node_id)

    while cur and cur not in seen:

        seen.add(cur)

        node = db.get(cur)

        if not node:
            break

        path.append({
            "id": cur,
            "name": node.get(
                "name",
                "بدون نام"
            )
        })

        parent = node.get("parent")

        if parent is not None:
            cur = str(parent)
        else:
            cur = None

    path.reverse()

    return path


def serialize_node(db, node_id):

    str_node_id = str(node_id)

    node = db.get(str_node_id)

    if not node:
        return None

    children_ids = [
        str(cid)
        for cid in node.get("children", [])
    ]

    rows_ids = get_rows(node)

    rows = []

    for row_ids in rows_ids:

        row = []

        for cid in row_ids:

            child = db.get(str(cid))

            if not child:
                continue

            child_contents = child.get(
                "contents",
                []
            )

            if not isinstance(
                child_contents,
                list
            ):
                child_contents = []

            row.append({

                "id": str(cid),

                "name": child.get(
                    "name",
                    "بدون نام"
                ),

                "style": get_style(child),

                "has_children": bool(
                    child.get("children")
                ),

                "content_count": len(
                    child_contents
                )

            })

        if row:
            rows.append(row)

    node_contents = node.get(
        "contents",
        []
    )

    if not isinstance(
        node_contents,
        list
    ):
        node_contents = []

    return {

        "id": str_node_id,

        "name": node.get(
            "name",
            "بدون نام"
        ),

        "parent": (
            str(node["parent"])
            if node.get("parent") is not None
            else None
        ),

        "style": get_style(node),

        "children_count": len(
            children_ids
        ),

        "contents": serialize_contents(
            node_contents
        ),

        "rows": rows,

        "breadcrumb": get_breadcrumb(
            db,
            str_node_id
        )

    }


async def miniapp_data(request):

    node_id = request.query.get(
        "node",
        "root"
    )

    try:
        # این همان load_db اصلی main.py است
        db = load_db()

    except Exception as e:

        print(
            "❌ Miniapp load_db error:",
            repr(e)
        )

        return web.json_response(
            {
                "ok": False,
                "error": (
                    "خطا در بارگذاری دیتابیس: "
                    + str(e)
                )
            },
            status=500
        )

    if not db:

        return web.json_response(
            {
                "ok": False,
                "error": (
                    "دیتابیس در دسترس نیست "
                    "یا خالی است."
                )
            },
            status=500
        )

    str_node_id = str(node_id)

    if str_node_id not in db:

        str_node_id = "root"

    payload = serialize_node(
        db,
        str_node_id
    )

    if not payload:

        return web.json_response(
            {
                "ok": False,
                "error": (
                    f"نود '{node_id}' یافت نشد."
                )
            },
            status=404
        )

    return web.json_response(
        {
            "ok": True,
            "data": payload
        }
    )
