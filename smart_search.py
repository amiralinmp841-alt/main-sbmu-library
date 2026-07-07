from rapidfuzz import fuzz
import re

# =========================================================
# ۱) مترادف‌های تخصصی پزشکی و آموزشی
# =========================================================
MEDICAL_SYNONYMS = {
    # علوم پایه و بالینی
    "اناتومی": ["کالبد شناسی", "کالبدشناسی", "تشریح", "علوم تشریح", "anatomy"],
    "هیستولوژی": ["بافت شناسی", "بافت‌شناسی", "histology"],
    "امبریولوژی": ["جنین شناسی", "جنین‌شناسی", "embryology"],
    "فیزیولوژی": ["عملکرد بدن", "physiology"],
    "بیوشیمی": ["biochemistry"],
    "ژنتیک": ["genetics"],
    "ایمونولوژی": ["ایمنی شناسی", "ایمنی‌شناسی", "immunology"],
    "میکروب شناسی": ["میکروب‌شناسی", "باکتریولوژی", "ویروس شناسی", "ویروس‌شناسی", "قارچ شناسی", "قارچ‌شناسی", "microbiology"],
    "پاتولوژی": ["آسیب شناسی", "آسیب‌شناسی", "pathology", "histopathology"],
    "فارماکولوژی": ["داروشناسی", "pharmacology"],
    "داخلی": ["internal medicine", "طب داخلی", "internal"],
    "جراحی": ["surgery", "surgical"],
    "اطفال": ["کودکان", "پدیاتری", "pediatrics"],
    "زنان": ["زنان و زایمان", "مامایی", "obgyn", "gynecology"],
    "روانپزشکی": ["سایک", "psychiatry"],
    "نورولوژی": ["مغز و اعصاب", "neurology"],
    "ارتوپدی": ["orthopedics", "ارتو"],
    "اورولوژی": ["urology"],
    "قلب": ["کاردیولوژی", "cardiology"],
    "ریه": ["pulmonology"],
    "غدد": ["اندوکرین", "endocrine", "endocrinology"],
    "عفونی": ["بیماری های عفونی", "infectious"],
    "پوست": ["درماتولوژی", "dermatology"],
    "چشم": ["افتالمولوژی", "ophthalmology"],
    "گوش": ["گوش و حلق و بینی", "ent"],
    "بیهوشی": ["anesthesia"],
    "اورژانس": ["emergency", "طب اورژانس"],
    "رادیولوژی": ["تصویربرداری", "radiology"],
    "انکولوژی": ["سرطان", "oncology"],

    # اصطلاحات آموزشی و مدیا
    "کلاس": ["جلسه", "lesson", "session"],
    "جلسه": ["کلاس", "lesson", "session"],
    "نظری": ["تئوری", "theory"],
    "تئوری": ["نظری", "theory"],
    "عملی": ["لاب", "آزمایشگاه", "practical", "lab"],
    "مرور": ["جمع بندی", "جمع‌بندی", "review"],
    "جزوه": ["نوت", "یادداشت", "note", "notes"],
    "نمونه سوال": ["سوالات", "questions", "exam", "امتحان"],
    "امتحان": ["آزمون", "exam", "test"],
    "آزمون": ["امتحان", "exam", "test"],
    "وویس": ["صدا", "صوت", "voice", "audio"],
    "ویس": ["وویس", "صدا", "صوت", "voice", "audio"],
    "صدا": ["وویس", "صوت", "voice", "audio"],
    "ویدیو": ["فیلم", "video", "media"],
    "فیلم": ["ویدیو", "video"],
    "پاور": ["پاورپوینت", "اسلاید", "presentation", "ppt", "pptx"],
    "پاورپوینت": ["پاور", "اسلاید", "presentation", "ppt", "pptx"],
    "اسلاید": ["پاور", "پاورپوینت", "presentation", "slide"],
    "pdf": ["پی دی اف", "جزوه pdf"],
    "کتاب": ["بوک", "book"],
    "مقاله": ["article", "paper"],
}

# =========================================================
# ۲) نرمال‌سازی متن (پاکسازی کاراکترهای عربی و علائم)
# =========================================================
def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = str(text)
    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "آ": "ا",
        "\u200c": " ",
        "_": " ",
        "-": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = text.lower()
    
    # حذف تگ‌های HTML در صورت وجود
    text = re.sub(r"<[^>]+>", " ", text)
    
    # حذف پسوند فایل‌ها
    text = re.sub(
        r"\.(pdf|doc|docx|ppt|pptx|xls|xlsx|zip|rar|mp3|mp4|mkv|avi|jpg|jpeg|png)$",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # حذف علائم نگارشی و کاراکترهای اضافه
    text = re.sub(r"[^\w\sآ-ی]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text

# =========================================================
# ۳) ساخت دیکشنری مترادف‌های دوطرفه و بسط کوئری
# =========================================================
def build_bidirectional_synonyms(syn_dict):
    bidirectional = {}
    for key, values in syn_dict.items():
        all_terms = set([key] + values)
        normalized_terms = {normalize_text(t) for t in all_terms if t}
        for term in normalized_terms:
            bidirectional.setdefault(term, set()).update(normalized_terms - {term})
    return bidirectional

BIDIRECTIONAL_SYNONYMS = build_bidirectional_synonyms(MEDICAL_SYNONYMS)

def expand_query_terms(query: str):
    query_norm = normalize_text(query)
    words = query_norm.split()
    expanded = set(words)

    # بررسی انطباق کل عبارت
    if query_norm in BIDIRECTIONAL_SYNONYMS:
        expanded.update(BIDIRECTIONAL_SYNONYMS[query_norm])

    # بررسی انطباق تک‌واژه‌ها
    for word in words:
        if word in BIDIRECTIONAL_SYNONYMS:
            expanded.update(BIDIRECTIONAL_SYNONYMS[word])
            
    return expanded

# =========================================================
# ۴) استخراج اطلاعات فایل‌های درون پوشه به صورت لیست‌های مجزا
# =========================================================
def get_contents_data(node):
    """
    استخراج جداگانه نام فایل‌ها، کپشن‌ها و بخش کوتاه متون متنی به صورت لیست خام
    تا بتوان تک‌به‌تک هر فایل را مستقل سنجید.
    """
    file_names = []
    captions = []
    short_texts = []

    for item in node.get("contents", []):
        item_type = item.get("type")

        # ۱. نام فایل (برای انواع مدیا و اسناد)
        file_name = item.get("file_name") or item.get("title")
        if file_name:
            file_names.append(file_name)

        # ۲. کپشن فایل‌ها
        caption = item.get("caption")
        if caption:
            captions.append(caption)

        # ۳. متون متنی (فقط ۵۰ کاراکتر اول)
        if item_type == "text":
            text_val = item.get("text", "")
            if text_val:
                short_texts.append(text_val[:50])

    return {
        "file_names": file_names,
        "captions": captions,
        "short_texts": short_texts
    }

# =========================================================
# ۵) تخت‌سازی دیتابیس با حفظ ساختار لیست‌های تفکیک شده و نرمال‌سازی
# =========================================================
def flatten_db_for_search(db):
    results = []
    start_nodes = []

    for node_id, node in db.items():
        parent = node.get("parent")
        if not parent or parent not in db:
            start_nodes.append(node_id)

    def walk(node_id, path_parts):
        node = db.get(node_id)
        if not node:
            return

        node_name = node.get("name", "")
        new_path_parts = path_parts.copy()

        if node_id != "root":
            new_path_parts.append(node_name)

        path_text = " ".join(new_path_parts)
        contents = get_contents_data(node)

        # آماده‌سازی متون نرمال‌شده
        node_name_norm = normalize_text(node_name)
        path_norm = normalize_text(path_text)
        
        # نرمال‌سازی تک‌تک عناصر لیست‌ها به صورت جداگانه
        file_names_norm = [normalize_text(f) for f in contents["file_names"] if normalize_text(f)]
        captions_norm = [normalize_text(c) for c in contents["captions"] if normalize_text(c)]
        short_texts_norm = [normalize_text(t) for t in contents["short_texts"] if normalize_text(t)]

        if node_id != "root":
            results.append({
                "node_id": node_id,
                "title": node_name,
                "path": " ⬅️ ".join(new_path_parts),
                "node_name_norm": node_name_norm,
                "path_norm": path_norm,
                "file_names_norm": file_names_norm,
                "captions_norm": captions_norm,
                "short_texts_norm": short_texts_norm
            })

        for child_id in node.get("children", []):
            walk(child_id, new_path_parts)

    for start in start_nodes:
        walk(start, [])

    return results

# =========================================================
# ۶) تابع اصلی سرچ هوشمند با منطق بهترین انطباق (Best Match) و اولویت شدید نام فایل
# =========================================================
def smart_search(db, query, limit=5, min_score=45):
    query_norm = normalize_text(query)
    if not query_norm:
        return []

    expanded_terms = expand_query_terms(query)
    items = flatten_db_for_search(db)
    results = []

    for item in items:
        n_norm = item["node_name_norm"]
        p_norm = item["path_norm"]
        f_norm_list = item["file_names_norm"]
        c_norm_list = item["captions_norm"]
        t_norm_list = item["short_texts_norm"]

        # ===== ۱) امتیاز نام پوشه =====
        score_name = max(
            fuzz.token_set_ratio(query_norm, n_norm),
            fuzz.partial_ratio(query_norm, n_norm) * 0.92,
            fuzz.WRatio(query_norm, n_norm) * 0.95,
        ) if n_norm else 0
        
        # ===== ۲) امتیاز مسیر پوشه =====
        score_path = max(
            fuzz.token_set_ratio(query_norm, p_norm),
            fuzz.partial_ratio(query_norm, p_norm) * 0.85,
            fuzz.WRatio(query_norm, p_norm) * 0.88,
        ) if p_norm else 0

        # ===== ۳) امتیاز اسم فایل (محاسبه بهترین انطباق تک‌به‌تک فایل‌ها) =====
        score_file_raw = 0
        best_file_name_matched = ""
        for f_name in f_norm_list:
            current_score = max(
                fuzz.token_set_ratio(query_norm, f_name),
                fuzz.partial_ratio(query_norm, f_name),
                fuzz.WRatio(query_norm, f_name),
            )
            if current_score > score_file_raw:
                score_file_raw = current_score
                best_file_name_matched = f_name

        # اعمال ضریب افزایش (Boost) قوی برای انطباق نام فایل
        score_file = min(100, score_file_raw * 1.25)

        # ===== ۴) امتیاز کپشن (محاسبه بهترین انطباق بین کپشن‌ها) =====
        score_caption_raw = 0
        best_caption_matched = ""
        for caption in c_norm_list:
            current_score = max(
                fuzz.token_set_ratio(query_norm, caption),
                fuzz.partial_ratio(query_norm, caption) * 0.9,
                fuzz.WRatio(query_norm, caption) * 0.9,
            )
            if current_score > score_caption_raw:
                score_caption_raw = current_score
                best_caption_matched = caption
        score_caption = score_caption_raw * 0.72

        # ===== ۵) امتیاز متون کوتاه (محاسبه بهترین انطباق) =====
        score_text_raw = 0
        best_text_matched = ""
        for txt in t_norm_list:
            current_score = max(
                fuzz.token_set_ratio(query_norm, txt),
                fuzz.partial_ratio(query_norm, txt) * 0.88,
                fuzz.WRatio(query_norm, txt) * 0.85,
            )
            if current_score > score_text_raw:
                score_text_raw = current_score
                best_text_matched = txt
        score_text = score_text_raw * 0.55

        # ===== ۶) بررسی تطابق مستقیم قوی در اسم بهترین فایل تطابق یافته =====
        exact_file_bonus = 0
        if best_file_name_matched and query_norm:
            if query_norm in best_file_name_matched:
                exact_file_bonus += 15  # افزایش بونوس مستقیم

            # بررسی تعداد کلمات هم‌پوشان با بهترین فایل منطبق شده
            query_words = [w for w in query_norm.split() if len(w) >= 2]
            matched_words_in_file = sum(1 for w in query_words if w in best_file_name_matched)

            if query_words:
                word_match_ratio = matched_words_in_file / len(query_words)
                exact_file_bonus += int(word_match_ratio * 12)

        exact_file_bonus = min(exact_file_bonus, 20)

        # ===== ۷) ترکیب وزن‌دار نهایی با اولویت شدید نام فایل =====
        weighted_score = (
            score_name * 0.95 +
            score_path * 0.70 +
            score_file * 1.35 +  # افزایش وزن ضریب نام فایل به ۱.۳۵
            score_caption * 0.50 +
            score_text * 0.30
        ) / (0.95 + 0.70 + 1.35 + 0.50 + 0.30)

        # ===== ۸) مشخص کردن بیس اصلی امتیاز بدون فدا کردن مقادیر ماکسیمم =====
        base_score = max(
            score_name,
            score_path * 0.92,
            score_file,
            score_caption,
            score_text
        )

        # ===== ۹) اعمال بونوس مترادف‌ها روی بهترین موارد انطباق یافته =====
        synonym_bonus = 0
        for term in expanded_terms:
            if not term or term == query_norm:
                continue

            if best_file_name_matched and term in best_file_name_matched:
                synonym_bonus += 8  # بالاترین بونوس برای وجود مترادف در نام فایل
            elif term in n_norm:
                synonym_bonus += 6
            elif term in p_norm:
                synonym_bonus += 4
            elif best_caption_matched and term in best_caption_matched:
                synonym_bonus += 3
            elif best_text_matched and term in best_text_matched:
                synonym_bonus += 2

        synonym_bonus = min(synonym_bonus, 18)

        # ===== ۱۰) محاسبه نهایی امتیاز کل =====
        final_score = max(base_score * 0.65 + weighted_score * 0.35, weighted_score)
        final_score += exact_file_bonus
        final_score += synonym_bonus

        # جلوگیری منطقی از نمره ۱۰۰ برای نتایجی که ارتباط نام ضعیفی دارند
        if score_file_raw < 85 and final_score > 95:
            final_score = 95

        final_score = min(100, int(final_score))

        if final_score >= min_score:
            results.append({
                "node_id": item["node_id"],
                "title": item["title"],
                "path": item["path"],
                "score": final_score
            })

    # مرتب‌سازی نتایج بر اساس بالاترین امتیاز
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
