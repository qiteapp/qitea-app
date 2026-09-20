"""استيراد ملف CSV/TSV خام (تصدير سابق أو كشط يدوي) إلى قاعدة العملاء.

المشكلة العملية: ملفات CSV الجاهزة تأتي برؤوس مختلفة، وأرقام أفسدها إكسل
(صيغة علمية أو صفر بادئ محذوف)، وأعمدة مدمجة، ومدن ناقصة. هذا الملف يعالج
ذلك ثم يمرّر الصفوف على نفس مسار التنظيف والتصدير.
"""
import csv
import io
import json
import os
import re

import extract
import normalize as nz
import store as db

# ---------------------------------------------------- كشف الأعمدة بالاسم

HEADER_HINTS = {
    "name": ["اسم المتجر", "الاسم", "اسم", "المتجر", "المنشأة", "المحل", "العنوان التجاري",
             "name", "store", "business", "title", "company", "shop"],
    "phone": ["الجوال", "جوال", "رقم الجوال", "الهاتف", "هاتف", "رقم", "الأرقام", "تلفون",
              "phone", "mobile", "tel", "telephone", "contact", "number"],
    "whatsapp": ["واتساب", "واتس", "whatsapp", "wa"],
    "city": ["المدينة", "مدينة", "البلد", "city", "town", "location_city"],
    "district": ["الحي", "حي", "المنطقة الفرعية", "district", "neighborhood"],
    "region": ["المنطقة", "منطقة", "region", "province"],
    "address": ["العنوان", "عنوان", "الموقع", "address", "street", "full_address"],
    "brand": ["العلامة", "العلامات", "الماركة", "الماركات", "الوكالة", "النوع",
              "brand", "brands", "make", "category", "type"],
    "lat": ["خط العرض", "العرض", "lat", "latitude", "y"],
    "lng": ["خط الطول", "الطول", "lng", "lon", "long", "longitude", "x"],
    "maps_url": ["الخريطة", "رابط الخريطة", "الموقع على الخريطة", "خرائط",
                 "map", "maps", "map_url", "google_maps", "gmaps", "coordinates", "الإحداثيات"],
    "website": ["الموقع الإلكتروني", "الموقع الالكتروني", "website", "site", "web", "url_site"],
    "email": ["البريد", "الايميل", "الإيميل", "email", "mail"],
    "working_hours": ["اوقات العمل", "أوقات العمل", "الدوام", "hours", "opening", "schedule"],
    "rating": ["التقييم", "تقييم", "rating", "stars", "score"],
    "reviews_count": ["عدد التقييمات", "المراجعات", "reviews", "review_count", "votes"],
    "source_url": ["رابط المصدر", "الرابط", "المصدر", "url", "link", "source", "page"],
}


def _norm_header(text):
    return nz.strip_arabic(re.sub(r"[_\-]+", " ", str(text or ""))).strip()


def match_headers(headers):
    """يربط رؤوس الملف بالحقول المعروفة. يُرجع {الرأس: الحقل}."""
    mapping = {}
    for header in headers:
        clean = _norm_header(header)
        if not clean:
            continue
        best, best_len = None, 0
        for field, hints in HEADER_HINTS.items():
            for hint in hints:
                hint_clean = _norm_header(hint)
                if clean == hint_clean:
                    best, best_len = field, 99
                    break
                if hint_clean and hint_clean in clean and len(hint_clean) > best_len:
                    best, best_len = field, len(hint_clean)
            if best_len == 99:
                break
        if best:
            mapping[header] = best
    return mapping


# --------------------------------------------- كشف الأعمدة من المحتوى

def sniff_columns(rows, headers, known):
    """يكتشف دور الأعمدة المجهولة من محتواها (أرقام، إحداثيات، مدن، روابط)."""
    guessed = {}
    sample = rows[:200]
    for header in headers:
        if header in known:
            continue
        values = [str(row.get(header) or "").strip() for row in sample]
        values = [v for v in values if v]
        if not values:
            continue
        hits = {
            "phone": sum(1 for v in values if nz.extract_phones(v)),
            "maps_url": sum(1 for v in values if extract.find_coords(v)),
            "city": sum(1 for v in values if nz.detect_city(v)),
            "brand": sum(1 for v in values if nz.detect_brands(v)),
        }
        url_like = sum(1 for v in values if v.startswith("http"))
        field, count = max(hits.items(), key=lambda kv: kv[1])
        if count >= max(3, len(values) * 0.5):
            # عمود المدينة يجب أن يكون قيمه قصيرة، وإلا فهو عنوان
            if field == "city" and sum(len(v) for v in values) / len(values) > 25:
                field = "address"
            guessed[header] = field
        elif url_like >= len(values) * 0.8:
            guessed[header] = "source_url"
    return guessed


# ------------------------------------------------- إصلاح ما أفسده إكسل

def repair_number(value):
    """يعيد بناء رقم أتلفه إكسل: صيغة علمية، أو صفر بادئ محذوف.

    9.66501E+11 -> 966501000000 ، و 501234567 -> 0501234567
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[\d.]+[eE]\+?\d+", text):
        try:
            text = "%.0f" % float(text)
        except ValueError:
            return text
    digits = re.sub(r"\D", "", text.translate(nz._ARABIC_DIGITS))
    # تسعة أرقام تبدأ بـ 5 أو 1: فقد الصفر البادئ عند الحفظ
    if len(digits) == 9 and digits[0] in "51":
        return "0" + digits
    return text


PHONE_SPLIT = re.compile(r"[\n\r;,/|]+|\s{2,}|\sو\s")


def row_phones(row, phone_fields):
    """يجمع كل الأرقام من كل الأعمدة المرشّحة، بعد إصلاحها."""
    found, seen = [], set()
    for field in phone_fields:
        raw = str(row.get(field) or "")
        for chunk in PHONE_SPLIT.split(raw):
            for info in nz.extract_phones(repair_number(chunk)):
                if info["e164"] not in seen:
                    seen.add(info["e164"])
                    found.append(info)
    return found


def read_table(path):
    """يقرأ CSV/TSV مع كشف الترميز والفاصل. يُرجع (الصفوف، الرؤوس)."""
    raw = open(path, "rb").read()
    for encoding in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    sample = text[:8000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = [row for row in reader]
    headers = reader.fieldnames or []
    return rows, [h for h in headers if h is not None]


def build_record(row, mapping, source_name, index):
    """يبني سجل متجر من صف واحد، مع استكمال الناقص من بقية الأعمدة."""
    fields = {}
    for header, field in mapping.items():
        value = str(row.get(header) or "").strip()
        if value:
            fields.setdefault(field, []).append(value)

    def first(field):
        return fields.get(field, [""])[0]

    def joined(field):
        return " ".join(fields.get(field, []))

    all_text = " \n ".join(str(v or "") for v in row.values())
    phone_headers = [h for h, f in mapping.items() if f in ("phone", "whatsapp")]
    phones = row_phones(row, phone_headers)
    if not phones:   # أرقام مخبأة في أعمدة غير موسومة
        phones = nz.extract_phones(repair_number(all_text) if len(all_text) < 40 else all_text)

    name = first("name") or ""
    if not name:
        # أطول قيمة نصية بلا أرقام هي الاسم غالباً
        candidates = [str(v).strip() for v in row.values()
                      if v and not re.search(r"\d{5,}", str(v)) and not str(v).startswith("http")]
        name = max(candidates, key=len) if candidates else ""

    city_info = nz.detect_city(first("city"), first("address"), joined("district"),
                               name, first("source_url"), all_text[:2000])
    lat = lng = None
    precision = ""
    try:
        lat, lng = float(first("lat")), float(first("lng"))
        if not extract.in_ksa(lat, lng):
            lat = lng = None
    except ValueError:
        lat = lng = None
    if lat is None:
        coords = extract.find_coords(joined("maps_url"), joined("source_url"), all_text)
        if not coords:   # خلية فيها «lat,lng» فقط بلا رابط
            for value in fields.get("maps_url", []) + fields.get("address", []):
                coords = extract.parse_coord_cell(value)
                if coords:
                    break
        if coords:
            lat, lng = coords
    if lat is not None:
        precision = "exact"
    elif city_info:
        lat, lng, precision = city_info["lat"], city_info["lng"], "city"

    brands = nz.detect_brands(joined("brand"), name, first("source_url"), all_text[:2000])
    buckets = nz.split_phones(phones)
    whatsapp = ""
    for header in [h for h, f in mapping.items() if f == "whatsapp"]:
        info = nz.extract_phones(repair_number(str(row.get(header) or "")))
        if info:
            whatsapp = info[0]["e164"]
            break

    rating = reviews = None
    try:
        rating = float(re.sub(r"[^\d.]", "", first("rating")))
    except ValueError:
        rating = None
    try:
        reviews = int(re.sub(r"\D", "", first("reviews_count")))
    except ValueError:
        reviews = None

    record = {
        "source_url": first("source_url") or "csv://%s#%d" % (source_name, index),
        "name": (name or "بدون اسم")[:200],
        "address": first("address")[:400],
        "district": first("district")[:200],
        "city": city_info["city"] if city_info else "",
        "city_en": city_info["city_en"] if city_info else "",
        "region": city_info["region"] if city_info else (first("region") or ""),
        "lat": lat, "lng": lng, "geo_precision": precision,
        "maps_url": extract.maps_url(lat, lng) if lat is not None else first("maps_url"),
        "phones": [p["e164"] for p in phones],
        "mobile": buckets["mobile"][0] if buckets["mobile"] else "",
        "all_mobiles": buckets["mobile"],
        "landline": buckets["landline"][0] if buckets["landline"] else "",
        "unified_number": (buckets["unified"] + buckets["tollfree"] or [""])[0],
        "whatsapp": whatsapp or (buckets["mobile"][0] if buckets["mobile"] else ""),
        "website": first("website"), "email": first("email"),
        "brands": brands,
        "working_hours": first("working_hours")[:400],
        "rating": rating, "reviews_count": reviews,
        "extracted_by": "csv",
    }
    record["lead_score"] = nz.lead_score(record)
    return record


def import_csv(conn, path, overrides=None, log=print):
    """يستورد ملفاً كاملاً. يُرجع (الملخص، خريطة الأعمدة)."""
    rows, headers = read_table(path)
    if not rows:
        return {"صفوف": 0}, {}

    mapping = match_headers(headers)
    mapping.update(sniff_columns(rows, headers, mapping))
    mapping.update(overrides or {})

    source_name = os.path.basename(path)
    summary = {"صفوف": len(rows), "متاجر جديدة": 0, "متاجر محدثة": 0,
               "صفوف بلا اسم أو رقم": 0}
    for index, row in enumerate(rows, 1):
        record = build_record(row, mapping, source_name, index)
        if record["name"] == "بدون اسم" and not record["phones"]:
            summary["صفوف بلا اسم أو رقم"] += 1
            continue
        action = db.upsert_store(conn, record)
        summary["متاجر جديدة" if action == "inserted" else "متاجر محدثة"] += 1
    conn.commit()
    return summary, mapping


# ----------------------------------------------------------- تقرير الجودة

def _far_from_city(row, limit_km=60):
    """هل الإحداثيات بعيدة عن المدينة المذكورة؟ تعارض يعني خطأ في أحدهما."""
    if row["lat"] is None or not row["city"] or row["geo_precision"] != "exact":
        return False
    centre = nz.CITIES.get(row["city"])
    if not centre:
        return False
    # تقريب كافٍ للفرز: درجة عرض ≈ 111 كم، ودرجة طول ≈ 101 كم عند خط عرض 24
    delta_km = (((row["lat"] - centre["lat"]) * 111) ** 2
                + ((row["lng"] - centre["lng"]) * 101) ** 2) ** 0.5
    return delta_km > limit_km


def audit(conn):
    """يكشف ما يحتاج تدخلاً بشرياً قبل حملة التواصل."""
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM stores WHERE duplicate_of IS NULL")]
    total = len(rows) or 1
    issues = {
        "الإحداثيات بعيدة عن المدينة المذكورة": [r for r in rows if _far_from_city(r)],
        "بلا جوال (لا يمكن التواصل)": [r for r in rows if not r["mobile"]],
        "بلا مدينة": [r for r in rows if not r["city"]],
        "موقع تقريبي (مركز المدينة)": [r for r in rows if r["geo_precision"] == "city"],
        "بلا إحداثيات إطلاقاً": [r for r in rows if r["lat"] is None],
        "بلا علامة تجارية": [r for r in rows if json.loads(r["brands"] or "[]") == []],
        "اسم مشبوه (قصير جداً)": [r for r in rows if len(r["name"].strip()) < 4],
    }
    report = {}
    for label, matched in issues.items():
        report[label] = "%d (%.0f%%)" % (len(matched), 100 * len(matched) / total)
    report["الإجمالي (بعد إزالة المكرر)"] = len(rows)
    report["جاهز للتواصل (جوال + مدينة)"] = sum(
        1 for r in rows if r["mobile"] and r["city"])
    return report, issues
