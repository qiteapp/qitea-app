"""قاعدة بيانات العملاء المحتملين (SQLite) مع الدمج وإزالة التكرار."""
import json
import sqlite3
from datetime import datetime, timezone

import normalize as nz

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id              INTEGER PRIMARY KEY,
    source          TEXT    NOT NULL DEFAULT 'motory',
    source_url      TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    address         TEXT,
    district        TEXT,
    city            TEXT,
    city_en         TEXT,
    region          TEXT,
    lat             REAL,
    lng             REAL,
    geo_precision   TEXT,               -- exact = من الموقع، city = مركز المدينة
    maps_url        TEXT,
    phones          TEXT,               -- JSON: كل الأرقام بصيغة E.164
    mobile          TEXT,               -- الجوال الأساسي للتواصل
    all_mobiles     TEXT,               -- JSON
    landline        TEXT,
    unified_number  TEXT,
    whatsapp        TEXT,
    website         TEXT,
    email           TEXT,
    brands          TEXT,               -- JSON: مفاتيح إنجليزية
    brands_ar       TEXT,
    working_hours   TEXT,
    rating          REAL,
    reviews_count   INTEGER,
    lead_score      INTEGER DEFAULT 0,
    lead_status     TEXT    DEFAULT 'new',   -- new|contacted|interested|onboarded|rejected
    assigned_to     TEXT,
    notes           TEXT,
    duplicate_of    INTEGER REFERENCES stores(id),
    extracted_by    TEXT,
    first_seen_at   TEXT,
    last_seen_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_stores_city   ON stores(city);
CREATE INDEX IF NOT EXISTS idx_stores_mobile ON stores(mobile);
CREATE INDEX IF NOT EXISTS idx_stores_score  ON stores(lead_score DESC);
CREATE INDEX IF NOT EXISTS idx_stores_status ON stores(lead_status);

-- سجل التواصل مع كل متجر
CREATE TABLE IF NOT EXISTS contact_log (
    id          INTEGER PRIMARY KEY,
    store_id    INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    contacted_at TEXT NOT NULL,
    channel     TEXT,                   -- whatsapp|call|visit|email
    outcome     TEXT,
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_store ON contact_log(store_id);

-- صفحات تمت زيارتها، لتجنب إعادة الزحف
CREATE TABLE IF NOT EXISTS crawl_log (
    url         TEXT PRIMARY KEY,
    fetched_at  TEXT,
    status      INTEGER,
    kind        TEXT,                   -- listing|store
    note        TEXT
);

-- العملاء الجاهزون للتواصل: جوال موجود وغير مكرر
CREATE VIEW IF NOT EXISTS leads_ready AS
SELECT id, name, city, mobile, whatsapp, brands_ar, maps_url, lead_score, lead_status
FROM stores
WHERE duplicate_of IS NULL AND mobile <> ''
ORDER BY lead_score DESC, city;
"""

JSON_FIELDS = ("phones", "all_mobiles", "brands", "brands_ar")
COLUMNS = (
    "source_url", "name", "address", "district", "city", "city_en", "region",
    "lat", "lng", "geo_precision", "maps_url", "phones", "mobile", "all_mobiles",
    "landline", "unified_number", "whatsapp", "website", "email", "brands",
    "brands_ar", "working_hours", "rating", "reviews_count", "lead_score",
    "extracted_by",
)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def _encode(store):
    row = dict(store)
    row["brands_ar"] = nz.brands_arabic(store.get("brands") or [])
    for field in JSON_FIELDS:
        row[field] = json.dumps(row.get(field) or [], ensure_ascii=False)
    return row


def upsert_store(conn, store):
    """يضيف متجراً أو يدمجه مع سجل موجود لنفس الرابط.

    الدمج يوحّد الأرقام والعلامات، لأن المتجر نفسه يظهر تحت عدة علامات.
    يُرجع 'inserted' أو 'merged'.
    """
    row = _encode(store)
    existing = conn.execute(
        "SELECT * FROM stores WHERE source_url = ?", (store["source_url"],)
    ).fetchone()
    stamp = now()

    if existing is None:
        fields = list(COLUMNS) + ["first_seen_at", "last_seen_at"]
        values = [row.get(col) for col in COLUMNS] + [stamp, stamp]
        conn.execute(
            "INSERT INTO stores (%s) VALUES (%s)" % (",".join(fields), ",".join("?" * len(fields))),
            values,
        )
        return "inserted"

    merged = dict(row)
    for field in ("phones", "all_mobiles", "brands"):
        old = json.loads(existing[field] or "[]")
        new = json.loads(row[field] or "[]")
        merged[field] = json.dumps(list(dict.fromkeys(old + new)), ensure_ascii=False)
    merged["brands_ar"] = json.dumps(
        nz.brands_arabic(json.loads(merged["brands"])), ensure_ascii=False)
    # لا نفقد قيمة موجودة إذا جاءت الصفحة الجديدة ناقصة
    for field in ("address", "district", "city", "city_en", "region", "mobile",
                  "landline", "unified_number", "whatsapp", "website", "email",
                  "working_hours", "maps_url"):
        if not merged.get(field) and existing[field]:
            merged[field] = existing[field]
    if merged.get("geo_precision") != "exact" and existing["geo_precision"] == "exact":
        merged["lat"], merged["lng"] = existing["lat"], existing["lng"]
        merged["geo_precision"], merged["maps_url"] = "exact", existing["maps_url"]
    for field in ("rating", "reviews_count"):
        if merged.get(field) is None:
            merged[field] = existing[field]

    merged["lead_score"] = nz.lead_score({
        "mobile": merged.get("mobile"), "whatsapp": merged.get("whatsapp"),
        "landline": merged.get("landline"), "unified_number": merged.get("unified_number"),
        "lat": merged.get("lat"), "geo_precision": merged.get("geo_precision"),
        "city": merged.get("city"), "brands": json.loads(merged["brands"]),
    })
    assignments = ",".join("%s = ?" % col for col in COLUMNS if col != "source_url")
    conn.execute(
        "UPDATE stores SET %s, last_seen_at = ? WHERE source_url = ?" % assignments,
        [merged.get(col) for col in COLUMNS if col != "source_url"] + [stamp, store["source_url"]],
    )
    return "merged"


def log_page(conn, url, status, kind, note=""):
    conn.execute(
        "INSERT OR REPLACE INTO crawl_log (url, fetched_at, status, kind, note) VALUES (?,?,?,?,?)",
        (url, now(), status, kind, note),
    )


def seen_pages(conn):
    return {row["url"] for row in conn.execute("SELECT url FROM crawl_log WHERE status = 200")}


def prune_shared_phones(conn, threshold=5):
    """يحذف الأرقام التي تتكرر عبر متاجر كثيرة (أرقام الموقع نفسه لا المتجر).

    رقم واحد يظهر في أكثر من `threshold` متجراً هو غالباً رقم دعم الدليل،
    لا رقم المتجر، فوجوده يفسد قائمة التواصل.
    """
    counts = {}
    rows = conn.execute("SELECT id, phones FROM stores").fetchall()
    for row in rows:
        for phone in json.loads(row["phones"] or "[]"):
            counts.setdefault(phone, set()).add(row["id"])
    shared = {phone for phone, ids in counts.items() if len(ids) > threshold}
    if not shared:
        return shared

    for row in rows:
        phones = [p for p in json.loads(row["phones"] or "[]") if p not in shared]
        buckets = nz.split_phones([nz.normalize_phone(p) for p in phones if nz.normalize_phone(p)])
        current = conn.execute("SELECT * FROM stores WHERE id = ?", (row["id"],)).fetchone()
        mobile = buckets["mobile"][0] if buckets["mobile"] else ""
        whatsapp = current["whatsapp"] if current["whatsapp"] not in shared else ""
        updated = {
            "phones": json.dumps(phones, ensure_ascii=False),
            "all_mobiles": json.dumps(buckets["mobile"], ensure_ascii=False),
            "mobile": mobile,
            "landline": buckets["landline"][0] if buckets["landline"] else "",
            "unified_number": (buckets["unified"] + buckets["tollfree"] or [""])[0],
            "whatsapp": whatsapp or mobile,
        }
        updated["lead_score"] = nz.lead_score({
            "mobile": updated["mobile"], "whatsapp": updated["whatsapp"],
            "landline": updated["landline"], "unified_number": updated["unified_number"],
            "lat": current["lat"], "geo_precision": current["geo_precision"],
            "city": current["city"], "brands": json.loads(current["brands"] or "[]"),
        })
        conn.execute(
            "UPDATE stores SET %s WHERE id = ?" % ",".join("%s = ?" % k for k in updated),
            list(updated.values()) + [row["id"]],
        )
    return shared


def mark_duplicates(conn):
    """يوسم المتاجر المكرّرة: نفس الجوال، أو نفس الاسم في نفس المدينة.

    يُبقي الأعلى تقييماً كسجل أصلي ويشير الباقي إليه.
    """
    conn.execute("UPDATE stores SET duplicate_of = NULL")
    groups = {}
    rows = conn.execute(
        "SELECT id, name, city, mobile, lead_score FROM stores ORDER BY lead_score DESC, id"
    ).fetchall()
    for row in rows:
        keys = []
        if row["mobile"]:
            keys.append(("mobile", row["mobile"]))
        keys.append(("name", nz.strip_arabic(row["name"]), row["city"]))
        primary = next((groups[k] for k in keys if k in groups), None)
        if primary is None:
            for key in keys:
                groups[key] = row["id"]
        else:
            for key in keys:
                groups.setdefault(key, primary)
            conn.execute("UPDATE stores SET duplicate_of = ? WHERE id = ?", (primary, row["id"]))
    return conn.execute(
        "SELECT COUNT(*) AS n FROM stores WHERE duplicate_of IS NOT NULL").fetchone()["n"]


def stats(conn):
    def one(sql):
        return conn.execute(sql).fetchone()[0]

    return {
        "المتاجر": one("SELECT COUNT(*) FROM stores"),
        "مكررة": one("SELECT COUNT(*) FROM stores WHERE duplicate_of IS NOT NULL"),
        "فيها جوال": one("SELECT COUNT(*) FROM stores WHERE mobile <> '' AND duplicate_of IS NULL"),
        "فيها واتساب": one("SELECT COUNT(*) FROM stores WHERE whatsapp <> '' AND duplicate_of IS NULL"),
        "إحداثيات دقيقة": one("SELECT COUNT(*) FROM stores WHERE geo_precision = 'exact' AND duplicate_of IS NULL"),
        "بدون مدينة": one("SELECT COUNT(*) FROM stores WHERE city = '' AND duplicate_of IS NULL"),
        "مدن": one("SELECT COUNT(DISTINCT city) FROM stores WHERE city <> ''"),
    }
