#!/usr/bin/env python3
"""قاعدة بيانات متاجر قطع الغيار — عملاء محتملون لتطبيق قطعة.

أوامر:
  crawl      زحف دليل موتري وبناء القاعدة
  ingest     معالجة صفحات HTML محفوظة (بدون إنترنت)
  import-csv استيراد ملف CSV جاهز مع كشف الأعمدة تلقائياً
  audit      تقرير جودة: ما الناقص وما يحتاج مراجعة
  clean    تنقية الأرقام العامة ووسم المكرر
  export   تصدير CSV / JSON / GeoJSON / خريطة HTML
  stats    ملخص القاعدة
  contact  تسجيل محاولة تواصل مع متجر

مثال كامل:
  python3 cli.py crawl --max-pages 300 --save-html pages/
  python3 cli.py clean
  python3 cli.py export --all
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crawler
import export as ex
import importer
import store as db

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "leads.db")
OUT_DIR = os.path.join(HERE, "out")


def load_seeds():
    with open(os.path.join(HERE, "data", "seeds.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return [data["root"]] + data.get("brand_pages", [])


def print_table(title, mapping):
    print("\n%s" % title)
    print("-" * 46)
    for key, value in mapping.items():
        print("  %-22s %s" % (key, value))


def cmd_crawl(args):
    conn = db.connect(args.db)
    seeds = [args.url] if args.url else load_seeds()
    print("بدء الزحف من %d رابط…" % len(seeds))
    try:
        summary = crawler.crawl(
            conn, seeds, prefix=args.prefix, max_pages=args.max_pages,
            delay=args.delay, html_dir=args.save_html, resume=not args.restart)
    except PermissionError as err:
        print("\n⛔ %s" % err)
        return 2
    print_table("ملخص الزحف", summary)
    print_table("القاعدة الآن", db.stats(conn))
    print("\nالخطوة التالية: python3 cli.py clean ثم export")
    conn.close()
    return 0


def cmd_ingest(args):
    conn = db.connect(args.db)
    print("معالجة الصفحات المحفوظة في %s …" % args.dir)
    summary = crawler.ingest_dir(conn, args.dir)
    print_table("ملخص المعالجة", summary)
    print_table("القاعدة الآن", db.stats(conn))
    conn.close()
    return 0


def cmd_import_csv(args):
    conn = db.connect(args.db)
    if not os.path.exists(args.file):
        print("لا يوجد ملف بهذا المسار: %s" % args.file)
        return 1
    overrides = {}
    for pair in args.map or []:
        if "=" not in pair:
            print("صيغة --map يجب أن تكون «الرأس=الحقل»، وصلني: %s" % pair)
            return 1
        header, field = pair.split("=", 1)
        overrides[header.strip()] = field.strip()

    summary, mapping = importer.import_csv(conn, args.file, overrides)
    if mapping:
        print_table("الأعمدة كما فُهمت", {h: f for h, f in mapping.items()})
        unmapped = [h for h in importer.read_table(args.file)[1] if h not in mapping]
        if unmapped:
            print("\n  أعمدة لم تُستخدم: %s" % "، ".join(unmapped))
            print("  لتصحيح أي عمود: --map \"اسم العمود=phone\"")
    print_table("ملخص الاستيراد", summary)
    print_table("القاعدة الآن", db.stats(conn))
    print("\nالخطوة التالية: python3 cli.py clean ثم audit ثم export --all")
    conn.close()
    return 0


def cmd_audit(args):
    conn = db.connect(args.db)
    report, issues = importer.audit(conn)
    print_table("تقرير الجودة", report)
    if args.show:
        rows = issues.get(args.show)
        if rows is None:
            print("\nالفئات المتاحة: %s" % "، ".join(issues))
            return 1
        print("\n%s — أول %d سجل:" % (args.show, min(len(rows), args.limit)))
        for row in rows[:args.limit]:
            print("  #%-4s %-45s %s" % (row["id"], row["name"][:45],
                                        row["mobile"] or row["source_url"][:40]))
    conn.close()
    return 0


def cmd_clean(args):
    conn = db.connect(args.db)
    shared = db.prune_shared_phones(conn, args.phone_threshold)
    duplicates = db.mark_duplicates(conn)
    conn.commit()
    print_table("التنقية", {
        "أرقام عامة أُزيلت": len(shared) or "لا شيء",
        "الأرقام": "، ".join(sorted(shared)) or "—",
        "سجلات موسومة كمكرر": duplicates,
    })
    print_table("القاعدة الآن", db.stats(conn))
    conn.close()
    return 0


def cmd_export(args):
    conn = db.connect(args.db)
    rows = ex.fetch_rows(conn, include_duplicates=args.include_duplicates,
                         city=args.city, brand=args.brand, mobile_only=args.mobile_only)
    if not rows:
        print("لا توجد سجلات مطابقة. جرّب crawl أو ingest أولاً.")
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    made = []
    if args.all or args.csv:
        made.append(ex.to_csv(rows, os.path.join(OUT_DIR, "leads.csv")))
    if args.all or args.json:
        made.append(ex.to_json(rows, os.path.join(OUT_DIR, "leads.json")))
    if args.all or args.geojson:
        made.append(ex.to_geojson(rows, os.path.join(OUT_DIR, "leads.geojson")))
    if args.all or args.map:
        made.append(ex.to_map(rows, os.path.join(OUT_DIR, "map.html")))
    if not made:
        made.append(ex.to_csv(rows, os.path.join(OUT_DIR, "leads.csv")))
    print("صُدّر %d متجر إلى:" % len(rows))
    for path in made:
        print("  • %s" % path)
    conn.close()
    return 0


def cmd_stats(args):
    conn = db.connect(args.db)
    print_table("ملخص القاعدة", db.stats(conn))
    top_cities = conn.execute(
        "SELECT city, COUNT(*) n FROM stores WHERE duplicate_of IS NULL AND city <> ''"
        " GROUP BY city ORDER BY n DESC LIMIT 12").fetchall()
    if top_cities:
        print_table("أكثر المدن", {row["city"]: row["n"] for row in top_cities})
    brands = {}
    for row in conn.execute("SELECT brands_ar FROM stores WHERE duplicate_of IS NULL"):
        for brand in json.loads(row["brands_ar"] or "[]"):
            brands[brand] = brands.get(brand, 0) + 1
    if brands:
        top = dict(sorted(brands.items(), key=lambda kv: -kv[1])[:12])
        print_table("أكثر العلامات التجارية", top)
    conn.close()
    return 0


def cmd_contact(args):
    conn = db.connect(args.db)
    row = conn.execute("SELECT id, name FROM stores WHERE id = ?", (args.store_id,)).fetchone()
    if not row:
        print("لا يوجد متجر بالمعرّف %s" % args.store_id)
        return 1
    conn.execute(
        "INSERT INTO contact_log (store_id, contacted_at, channel, outcome, notes)"
        " VALUES (?,?,?,?,?)",
        (args.store_id, db.now(), args.channel, args.outcome, args.notes or ""))
    if args.status:
        conn.execute("UPDATE stores SET lead_status = ? WHERE id = ?", (args.status, args.store_id))
    conn.commit()
    print("سُجّل التواصل مع «%s»%s" % (row["name"], " وحالته: %s" % args.status if args.status else ""))
    conn.close()
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="بناء قاعدة بيانات متاجر قطع الغيار من دليل موتري",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB, help="مسار قاعدة البيانات")
    subs = parser.add_subparsers(dest="command", required=True)

    crawl = subs.add_parser("crawl", help="زحف الدليل عبر الإنترنت")
    crawl.add_argument("--url", help="رابط بداية واحد بدل قائمة seeds.json")
    crawl.add_argument("--prefix", default=crawler.DEFAULT_PREFIX,
                       help="جزء المسار الذي يحدد قسم الدليل")
    crawl.add_argument("--max-pages", type=int, default=400)
    crawl.add_argument("--delay", type=float, default=1.0, help="ثوانٍ بين الطلبات")
    crawl.add_argument("--save-html", metavar="DIR", help="حفظ نسخة من كل صفحة")
    crawl.add_argument("--restart", action="store_true", help="تجاهل سجل الزحف السابق")
    crawl.set_defaults(func=cmd_crawl)

    ingest = subs.add_parser("ingest", help="معالجة صفحات محفوظة بدون إنترنت")
    ingest.add_argument("dir", help="مجلد فيه ملفات HTML")
    ingest.set_defaults(func=cmd_ingest)

    imp = subs.add_parser("import-csv", help="استيراد ملف CSV جاهز")
    imp.add_argument("file", help="مسار ملف CSV أو TSV")
    imp.add_argument("--map", action="append", metavar="العمود=الحقل",
                     help="تصحيح ربط عمود يدوياً، يمكن تكراره")
    imp.set_defaults(func=cmd_import_csv)

    audit = subs.add_parser("audit", help="تقرير جودة البيانات")
    audit.add_argument("--show", metavar="الفئة", help="عرض سجلات فئة معينة")
    audit.add_argument("--limit", type=int, default=20)
    audit.set_defaults(func=cmd_audit)

    clean = subs.add_parser("clean", help="تنقية الأرقام ووسم المكرر")
    clean.add_argument("--phone-threshold", type=int, default=5,
                       help="رقم يظهر في أكثر من هذا العدد من المتاجر يُعدّ رقم الموقع")
    clean.set_defaults(func=cmd_clean)

    export = subs.add_parser("export", help="تصدير النتائج")
    export.add_argument("--all", action="store_true", help="كل الصيغ")
    export.add_argument("--csv", action="store_true")
    export.add_argument("--json", action="store_true")
    export.add_argument("--geojson", action="store_true")
    export.add_argument("--map", action="store_true", help="خريطة HTML تفاعلية")
    export.add_argument("--city", help="تصفية بمدينة")
    export.add_argument("--brand", help="تصفية بعلامة تجارية (المفتاح الإنجليزي)")
    export.add_argument("--mobile-only", action="store_true", help="من لديه جوال فقط")
    export.add_argument("--include-duplicates", action="store_true")
    export.set_defaults(func=cmd_export)

    stats = subs.add_parser("stats", help="ملخص القاعدة")
    stats.set_defaults(func=cmd_stats)

    contact = subs.add_parser("contact", help="تسجيل محاولة تواصل")
    contact.add_argument("store_id", type=int)
    contact.add_argument("--channel", default="whatsapp",
                         choices=["whatsapp", "call", "visit", "email"])
    contact.add_argument("--outcome", default="")
    contact.add_argument("--status", choices=["new", "contacted", "interested",
                                             "onboarded", "rejected"])
    contact.add_argument("--notes")
    contact.set_defaults(func=cmd_contact)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    sys.exit(args.func(args))
