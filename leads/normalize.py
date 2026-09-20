"""تنظيف وتوحيد بيانات المتاجر: أرقام الهاتف، المدن، العلامات التجارية.

كل الدوال هنا تعمل على نصوص عربية خام كما تظهر في صفحات الدليل.
"""
import json
import os
import re
import unicodedata

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# مشغلو الجوال في السعودية حسب البادئة
MOBILE_CARRIERS = {
    "50": "stc", "53": "stc", "55": "stc", "58": "stc",
    "54": "mobily", "56": "mobily",
    "51": "zain", "57": "zain", "59": "zain",
    "52": "virgin",
}
# بادئات الهاتف الثابت حسب المنطقة
LANDLINE_AREAS = {
    "11": "الرياض", "12": "مكة المكرمة", "13": "الشرقية",
    "14": "المدينة المنورة", "16": "القصيم", "17": "عسير",
}

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_TATWEEL = "ـ"
_DIACRITICS = re.compile(r"[ً-ْٰۖ-ۭ]")


def _load(name):
    with open(os.path.join(DATA_DIR, name), encoding="utf-8") as fh:
        data = json.load(fh)
    data.pop("_comment", None)
    return data


CITIES = _load("cities.json")
BRANDS = _load("brands.json")


def strip_arabic(text):
    """توحيد الحروف العربية لتسهيل المطابقة: أ/إ/آ -> ا، ى -> ي، ة -> ه."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS)
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    for src, dst in (("أإآٱ", "ا"), ("ى", "ي"), ("ة", "ه"), ("ؤ", "و"), ("ئ", "ي")):
        for ch in src:
            text = text.replace(ch, dst)
    return re.sub(r"\s+", " ", text).strip().lower()


# ---------------------------------------------------------------- الهواتف

PHONE_CANDIDATE = re.compile(r"(?:\+|00)?[\d٠-٩][\d٠-٩\s\-().]{6,30}")
# ما يفصل رقماً عن آخر: سطر جديد، فراغان، أو فاصل صريح
_PHONE_SEPARATOR = re.compile(r"[\n\r|،,;/]+|\s{2,}|(?<=\d)\s*(?=(?:\+|00966))")
MAX_PHONE_DIGITS = 13


def normalize_phone(raw):
    """يحوّل رقماً سعودياً إلى صيغة E.164 مع تصنيفه.

    يُرجع dict فيها e164 و kind (mobile/landline/unified/tollfree) أو None
    إذا لم يكن الرقم سعودياً صالحاً.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw).translate(_ARABIC_DIGITS))
    if not digits:
        return None
    # إزالة بادئات الاتصال الدولي
    if digits.startswith("00966"):
        digits = digits[5:]
    elif digits.startswith("966"):
        digits = digits[3:]
    digits = digits.lstrip("0") if digits.startswith("0") else digits

    if len(digits) == 9 and digits.startswith("5"):
        return {
            "e164": "+966" + digits,
            "kind": "mobile",
            "carrier": MOBILE_CARRIERS.get(digits[:2]),
            "local": "0" + digits,
        }
    if len(digits) == 9 and digits[:2] in LANDLINE_AREAS:
        return {
            "e164": "+966" + digits,
            "kind": "landline",
            "area": LANDLINE_AREAS[digits[:2]],
            "local": "0" + digits,
        }
    if len(digits) == 9 and digits.startswith("92"):
        return {"e164": "+966" + digits, "kind": "unified", "local": digits}
    if len(digits) == 9 and digits.startswith("80"):
        return {"e164": "+966" + digits, "kind": "tollfree", "local": digits}
    return None


def _split_digit_run(digits):
    """يفصل سلسلة أرقام ملتصقة إلى أرقام سعودية صالحة.

    مثال: «05011122230126543210» -> ‎+966501112223 و ‎+966126543210.
    """
    out, cursor = [], 0
    while cursor < len(digits):
        for width in (13, 12, 10, 9):
            info = normalize_phone(digits[cursor:cursor + width])
            if info:
                out.append(info)
                cursor += width
                break
        else:
            cursor += 1
    return out


def extract_phones(text):
    """يستخرج كل الأرقام السعودية الصالحة من نص، بدون تكرار وبالترتيب."""
    found, seen = [], set()
    for chunk in _PHONE_SEPARATOR.split(str(text or "")):
        for match in PHONE_CANDIDATE.finditer(chunk):
            raw = match.group(0)
            infos = [normalize_phone(raw)]
            if not infos[0]:
                digits = re.sub(r"\D", "", raw.translate(_ARABIC_DIGITS))
                infos = _split_digit_run(digits) if len(digits) > MAX_PHONE_DIGITS else []
            for info in infos:
                if info and info["e164"] not in seen:
                    seen.add(info["e164"])
                    found.append(info)
    return found


def split_phones(phones):
    """يفصل قائمة الأرقام إلى جوال / ثابت / موحد بحسب الأولوية للتواصل."""
    buckets = {"mobile": [], "landline": [], "unified": [], "tollfree": []}
    for p in phones:
        buckets[p["kind"]].append(p["e164"])
    return buckets


# ----------------------------------------------------------------- المدن

_CITY_INDEX = []
for _canonical, _meta in CITIES.items():
    for _alias in [_canonical, _meta["en"]] + _meta.get("aliases", []):
        _CITY_INDEX.append((strip_arabic(_alias), _canonical))
# الأطول أولاً حتى لا تبتلع "مكة" اسم "مكة المكرمة"
_CITY_INDEX.sort(key=lambda pair: len(pair[0]), reverse=True)


def detect_city(*texts):
    """يحدد المدينة من أي نص (عنوان، اسم المتجر، مسار الرابط)."""
    for text in texts:
        haystack = strip_arabic(text)
        if not haystack:
            continue
        for needle, canonical in _CITY_INDEX:
            if needle and needle in haystack:
                meta = CITIES[canonical]
                return {
                    "city": canonical,
                    "city_en": meta["en"],
                    "region": meta["region"],
                    "lat": meta["lat"],
                    "lng": meta["lng"],
                }
    return None


# ------------------------------------------------------- العلامات التجارية

_BRAND_INDEX = []
for _slug, _aliases in BRANDS.items():
    for _alias in _aliases:
        _BRAND_INDEX.append((strip_arabic(_alias), _slug))
_BRAND_INDEX.sort(key=lambda pair: len(pair[0]), reverse=True)

# أسماء قصيرة قد تُطابق داخل كلمة أخرى، فتحتاج مطابقة بحدود الكلمة
_SHORT_BRANDS = {"kia", "mg", "ram", "man", "مان", "رام", "جاك", "تانك", "ميني", "كيا", "جيب"}
# بادئات عربية تلتصق بالاسم: «وكيا»، «الكيا»، «بتويوتا»
_AR_PREFIX = r"(?:وال|بال|فال|لل|ال|[ولبفك])?"
_WORD_START = r"(?:^|[^\w؀-ۿ])"


def _matches_whole_word(needle, haystack):
    """مطابقة الاسم ككلمة كاملة مع السماح بالبادئات العربية الملتصقة."""
    pattern = _WORD_START + _AR_PREFIX + re.escape(needle) + r"(?![\w؀-ۿ])"
    return re.search(pattern, haystack) is not None


def detect_brands(*texts):
    """يُرجع قائمة مفاتيح العلامات التجارية المذكورة في النصوص."""
    slugs = []
    for text in texts:
        haystack = strip_arabic(text)
        if not haystack:
            continue
        for needle, slug in _BRAND_INDEX:
            if slug in slugs or not needle:
                continue
            if len(needle) <= 4 or needle in _SHORT_BRANDS:
                hit = _matches_whole_word(needle, haystack)
            else:
                hit = needle in haystack
            if hit:
                slugs.append(slug)
    return slugs


def brands_arabic(slugs):
    """أول تسمية عربية لكل علامة، للعرض في التقارير."""
    out = []
    for slug in slugs:
        for alias in BRANDS.get(slug, []):
            if re.search(r"[؀-ۿ]", alias):
                out.append(alias)
                break
        else:
            out.append(slug)
    return out


# ------------------------------------------------------------ نقاط الجدوى

def lead_score(store):
    """تقييم من 0 إلى 100 لجدوى المتجر كعميل محتمل.

    الجوال هو أهم عامل لأنه قناة التواصل العملية (واتساب/مكالمة).
    """
    score = 0
    if store.get("mobile"):
        score += 40
    if store.get("whatsapp"):
        score += 10
    if store.get("landline") or store.get("unified_number"):
        score += 10
    if store.get("lat") and store.get("geo_precision") == "exact":
        score += 15
    elif store.get("lat"):
        score += 5
    if store.get("city"):
        score += 10
    brands = store.get("brands") or []
    score += min(len(brands) * 3, 15)
    return min(score, 100)
