import os
import json
import time
import threading
import hashlib
import requests

SUPABASE_URL = "https://ndpaqjuuznlyqzsemroq.supabase.co"
SUPABASE_KEY = "sb_publishable_S5i3TwYKqMhxO90VrKnPGA_n6fd-R2g"

TABLE_URL = f"{SUPABASE_URL}/rest/v1/bot_files"

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates"
}

FILES = {
    "database": "/tmp/database.json",
    "userdata": "userdata.json"
}

last_hash = {}
last_local_write = 0

DEFAULT_DATABASE = {
    "root": {
        "name": "خانه",
        "parent": None,
        "children": [],
        "contents": []
    }
}


# ---------- validity check ----------

def is_valid_userdata(path):
    data = read_json(path)
    return "admin_password" in data


def is_valid_database(path):
    data = read_json(path)

    if "root" not in data:
        return False

    root = data["root"]

    if "children" not in root:
        return False

    return isinstance(root["children"], list) and len(root["children"]) > 0

    def needs_restore(name, path):
    if not os.path.exists(path):
        return True

    if name == "userdata":
        return not is_valid_userdata(path)

    if name == "database":
        return not is_valid_database(path)

    return False


# ---------- utils ----------

def file_hash(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


# ---------- push ----------

def push_to_supabase(name, data):
    global last_local_write

    payload = {
        "name": name,
        "data": data
    }

    requests.post(TABLE_URL, headers=HEADERS, json=payload)
    last_local_write = time.time()
    print(f"[SYNC] pushed {name}")


# ---------- restore ----------

def restore_from_supabase(name, path):
    if time.time() - last_local_write < 20:
        return

    r = requests.get(
        f"{TABLE_URL}?name=eq.{name}",
        headers=HEADERS
    )

    if r.status_code != 200:
        return

    rows = r.json()
    if not rows:
        return

    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows[0]["data"], f, ensure_ascii=False, indent=2)

    print(f"[SYNC] restored {name}")


# ---------- watcher ----------

def watcher():
    while True:
        for name, path in FILES.items():

            # 1️⃣ اگر فایل خراب/ریست شده → فقط restore
            if needs_restore(name, path):
                restore_from_supabase(name, path)
                last_hash[name] = file_hash(path)
                continue

            # 2️⃣ اگر سالم بود → بررسی تغییر برای push
            h = file_hash(path)

            if h != last_hash.get(name):
                data = read_json(path)
                push_to_supabase(name, data)
                last_hash[name] = h

        time.sleep(5)


# ---------- initial_restore ----------

def initial_restore():
    print("[SYNC] Initial restore check")

    for name, path in FILES.items():
        if needs_restore(name, path):
            restore_from_supabase(name, path)


# ---------- start ----------

def start_sync_thread():
    print("[SYNC] Watcher thread started")
    t = threading.Thread(target=watcher, daemon=True)
    t.start()
