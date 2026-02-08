import os
import time
import threading
import hashlib
import requests
import base64

SUPABASE_URL = "https://ndpaqjuuznlyqzsemroq.supabase.co"
SUPABASE_KEY = "sb_publishable_S5i3TwYKqMhxO90VrKnPGA_n6fd-R2g"

TABLE_URL = f"{SUPABASE_URL}/rest/v1/bot_files"

HEADERS_JSON = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

HEADERS_GET = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}

ZIP_PATH = "/tmp/database.zip"

MIN_VALID_SIZE = 1024  # 1KB

last_hash = None
last_local_write = 0


# ---------- utils ----------

def file_size(path):
    if not os.path.exists(path):
        return 0
    return os.path.getsize(path)


def file_hash(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ---------- push ----------

def push_zip_to_supabase():
    global last_local_write

    size = file_size(ZIP_PATH)
    if size <= MIN_VALID_SIZE:
        return

    with open(ZIP_PATH, "rb") as f:
        binary = f.read()

    encoded = base64.b64encode(binary).decode()

    payload = {
        "name": "database_zip",
        "data": encoded
    }

    r = requests.post(TABLE_URL, headers=HEADERS_JSON, json=payload)

    if r.status_code in (200, 201, 204):
        last_local_write = time.time()
        print("[SYNC] pushed zip to Supabase")


# ---------- restore ----------

def restore_zip_from_supabase():
    global last_local_write

    # جلوگیری از لوپ بعد از push
    if time.time() - last_local_write < 10:
        return

    r = requests.get(
        f"{TABLE_URL}?name=eq.database_zip&select=data",
        headers=HEADERS_GET
    )

    if r.status_code != 200:
        return

    rows = r.json()
    if not rows:
        return

    encoded = rows[0]["data"]
    if not encoded:
        return

    binary = base64.b64decode(encoded)

    if len(binary) <= MIN_VALID_SIZE:
        return

    with open(ZIP_PATH, "wb") as f:
        f.write(binary)

    print("[SYNC] restored zip from Supabase")


# ---------- logic ----------

def render_is_reset():
    return file_size(ZIP_PATH) <= MIN_VALID_SIZE


# ---------- watcher ----------

def watcher():
    global last_hash

    while True:
        size = file_size(ZIP_PATH)

        # 1️⃣ اگر Render ریست شده → restore
        if render_is_reset():
            restore_zip_from_supabase()
            last_hash = file_hash(ZIP_PATH)
            time.sleep(5)
            continue

        # 2️⃣ اگر تغییر کرده → push
        current_hash = file_hash(ZIP_PATH)

        if current_hash != last_hash:
            push_zip_to_supabase()
            last_hash = current_hash

        time.sleep(5)


# ---------- initial restore ----------

def initial_restore():
    print("[SYNC] Initial restore check")

    if render_is_reset():
        restore_zip_from_supabase()


# ---------- start ----------

def start_sync_thread():
    print("[SYNC] Watcher thread started")
    t = threading.Thread(target=watcher, daemon=True)
    t.start()
