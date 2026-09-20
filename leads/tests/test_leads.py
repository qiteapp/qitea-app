"""اختبارات خط الأنابيب. التشغيل: python3 tests/test_leads.py"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import crawler
import export as ex
import extract
import normalize as nz
import store as db

FIXTURES = os.path.join(HERE, "fixtures")


def read_fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class TestPhones(unittest.TestCase):
    def test_mobile_formats(self):
        for raw in ["0554433221", "٠٥٥٤٤٣٣٢٢١", "+966554433221", "00966554433221",
                    "966 55 443 3221", "055-443-3221", "(055) 4433221"]:
            info = nz.normalize_phone(raw)
            self.assertIsNotNone(info, raw)
            self.assertEqual(info["e164"], "+966554433221", raw)
            self.assertEqual(info["kind"], "mobile")

    def test_carrier_and_kinds(self):
        self.assertEqual(nz.normalize_phone("0501234567")["carrier"], "stc")
        self.assertEqual(nz.normalize_phone("0561234567")["carrier"], "mobily")
        self.assertEqual(nz.normalize_phone("0112345678")["kind"], "landline")
        self.assertEqual(nz.normalize_phone("0112345678")["area"], "الرياض")
        self.assertEqual(nz.normalize_phone("920012345")["kind"], "unified")
        self.assertEqual(nz.normalize_phone("800123456")["kind"], "tollfree")

    def test_rejects_non_saudi_and_noise(self):
        for raw in ["+971501234567", "123", "2019", "1500", "+201234567890"]:
            self.assertIsNone(nz.normalize_phone(raw), raw)

    def test_adjacent_numbers_are_split(self):
        for text in ["0501112223  0126543210", "05011122230126543210",
                     "+966501112223+966126543210", "جوال 0501112223 | هاتف 0126543210"]:
            found = [p["e164"] for p in nz.extract_phones(text)]
            self.assertEqual(found, ["+966501112223", "+966126543210"], text)

    def test_ignores_prices_and_years(self):
        self.assertEqual(nz.extract_phones("موديل 2019 بسعر 1500 ريال"), [])


class TestCitiesAndBrands(unittest.TestCase):
    def test_city_from_any_text(self):
        self.assertEqual(nz.detect_city("طريق الملك فهد، جده")["city"], "جدة")
        self.assertEqual(nz.detect_city("Al Khobar, Eastern")["city"], "الخبر")
        self.assertEqual(nz.detect_city("حي العزيزية، مكة المكرمة")["city"], "مكة المكرمة")
        self.assertIsNone(nz.detect_city("عنوان غير معروف"))

    def test_brands_with_arabic_prefixes(self):
        self.assertEqual(sorted(nz.detect_brands("قطع غيار تويوتا ولكزس وكيا")),
                         ["kia", "lexus", "toyota"])
        self.assertEqual(sorted(nz.detect_brands("الكيا وبتويوتا")), ["kia", "toyota"])

    def test_brands_no_false_positives(self):
        self.assertEqual(nz.detect_brands("قطع غيار ألمانيا الأصلية"), [])
        self.assertEqual(nz.detect_brands("وكيل تركيا للقطع"), [])
        self.assertEqual(nz.detect_brands("مؤسسة النخبة لقطع غيار السيارات"), [])


class TestCoordinates(unittest.TestCase):
    def test_google_maps_forms(self):
        cases = [
            ("?pb=!1m18!3d24.6408!2d46.7728!5e0", (24.6408, 46.7728)),
            ("!2d46.7728!3d24.6408", (24.6408, 46.7728)),
            ("https://maps.google.com/?q=21.4858,39.1925", (21.4858, 39.1925)),
            ("/maps/place/x/@26.4207,50.0888,17z", (26.4207, 50.0888)),
        ]
        for text, expected in cases:
            self.assertEqual(extract.find_coords(text), expected, text)

    def test_rejects_outside_ksa(self):
        self.assertIsNone(extract.find_coords("?q=48.8566,2.3522"))


class TestExtraction(unittest.TestCase):
    def test_jsonld_page(self):
        record = extract.extract_store(read_fixture("store_jsonld.html"),
                                       "https://ksa.motory.com/ar/x/fahd-12345/")
        self.assertEqual(record["name"], "مركز الفهد لقطع غيار تويوتا")
        self.assertEqual(record["city"], "جدة")
        self.assertEqual(record["mobile"], "+966554433221")
        self.assertEqual(record["landline"], "+966126543210")
        self.assertEqual(record["unified_number"], "+966920001122")
        self.assertEqual(record["brands"], ["toyota", "lexus"])
        self.assertEqual(record["geo_precision"], "exact")
        self.assertNotIn("PostalAddress", record["address"])
        self.assertEqual(record["rating"], 4.6)

    def test_plain_html_page(self):
        record = extract.extract_store(read_fixture("store_plain.html"),
                                       "https://ksa.motory.com/ar/x/nokhba-777/")
        self.assertEqual(record["name"], "مؤسسة النخبة لقطع غيار هيونداي وكيا")
        self.assertEqual(record["city"], "الرياض")
        self.assertEqual(record["mobile"], "+966501112223")
        self.assertEqual(sorted(record["brands"]), ["hyundai", "kia"])
        self.assertEqual((record["lat"], record["lng"]), (24.6408, 46.7728))

    def test_page_without_business_data_is_skipped(self):
        self.assertIsNone(extract.extract_store("<html><body>لا شيء</body></html>",
                                                "https://ksa.motory.com/ar/x/"))

    def test_listing_links(self):
        stores, pages = extract.extract_listing_links(
            read_fixture("listing.html"),
            "https://ksa.motory.com/ar/الدليل/قطع-غيار-سيارات/تويوتا/",
            "/الدليل/قطع-غيار-سيارات")
        self.assertEqual(stores, ["https://ksa.motory.com/ar/الدليل/قطع-غيار-سيارات/تويوتا/مركز-الفهد-12345/"])
        self.assertEqual(pages, ["https://ksa.motory.com/ar/الدليل/قطع-غيار-سيارات/تويوتا/?page=2"])


class TestDatabase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def record(self, **overrides):
        base = {
            "source_url": "https://ksa.motory.com/s/1", "name": "متجر تجربة",
            "address": "حي الصناعية", "district": "", "city": "الرياض",
            "city_en": "Riyadh", "region": "الرياض", "lat": 24.7, "lng": 46.7,
            "geo_precision": "exact", "maps_url": "https://maps/x",
            "phones": ["+966501112223"], "mobile": "+966501112223",
            "all_mobiles": ["+966501112223"], "landline": "", "unified_number": "",
            "whatsapp": "+966501112223", "website": "", "email": "",
            "brands": ["toyota"], "working_hours": "", "rating": None,
            "reviews_count": None, "lead_score": 80, "extracted_by": "test",
        }
        base.update(overrides)
        return base

    def test_insert_then_merge_keeps_both_brands(self):
        self.assertEqual(db.upsert_store(self.conn, self.record()), "inserted")
        self.assertEqual(db.upsert_store(self.conn, self.record(brands=["kia"])), "merged")
        row = self.conn.execute("SELECT brands, brands_ar FROM stores").fetchone()
        self.assertEqual(json.loads(row["brands"]), ["toyota", "kia"])
        self.assertEqual(json.loads(row["brands_ar"]), ["تويوتا", "كيا"])

    def test_merge_does_not_lose_exact_coordinates(self):
        db.upsert_store(self.conn, self.record())
        db.upsert_store(self.conn, self.record(lat=24.0, lng=46.0, geo_precision="city"))
        row = self.conn.execute("SELECT lat, geo_precision FROM stores").fetchone()
        self.assertEqual((row["lat"], row["geo_precision"]), (24.7, "exact"))

    def test_shared_phone_is_pruned(self):
        support = "+966920011223"
        for index in range(7):
            db.upsert_store(self.conn, self.record(
                source_url="https://ksa.motory.com/s/%d" % index,
                name="متجر %d" % index,
                phones=["+96650111222%d" % index, support],
                mobile="+96650111222%d" % index, all_mobiles=["+96650111222%d" % index],
                unified_number=support))
        removed = db.prune_shared_phones(self.conn, threshold=5)
        self.assertIn(support, removed)
        for row in self.conn.execute("SELECT phones, unified_number FROM stores"):
            self.assertNotIn(support, json.loads(row["phones"]))
            self.assertEqual(row["unified_number"], "")

    def test_duplicates_marked_by_mobile_and_by_name(self):
        db.upsert_store(self.conn, self.record(lead_score=90))
        db.upsert_store(self.conn, self.record(source_url="https://ksa.motory.com/s/2",
                                              name="فرع آخر", lead_score=70))
        db.upsert_store(self.conn, self.record(source_url="https://ksa.motory.com/s/3",
                                              name="متجر تجربه", mobile="", phones=[],
                                              all_mobiles=[], whatsapp="", lead_score=50))
        self.assertEqual(db.mark_duplicates(self.conn), 2)
        primary = self.conn.execute(
            "SELECT id FROM stores WHERE duplicate_of IS NULL").fetchone()["id"]
        self.assertEqual(primary, 1)

    def test_exports_written(self):
        db.upsert_store(self.conn, self.record())
        rows = ex.fetch_rows(self.conn)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = ex.to_csv(rows, os.path.join(tmp, "a.csv"))
            geo_path = ex.to_geojson(rows, os.path.join(tmp, "a.geojson"))
            map_path = ex.to_map(rows, os.path.join(tmp, "a.html"))
            with open(csv_path, encoding="utf-8-sig") as fh:
                self.assertIn("اسم المتجر", fh.readline())
            with open(geo_path, encoding="utf-8") as fh:
                geo = json.load(fh)
            self.assertEqual(geo["features"][0]["geometry"]["coordinates"], [46.7, 24.7])
            with open(map_path, encoding="utf-8") as fh:
                self.assertIn("متجر تجربة", fh.read())

    def test_ingest_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "one.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("<!-- source_url: https://ksa.motory.com/ar/x/1/ -->\n"
                         + read_fixture("store_jsonld.html"))
            summary = crawler.ingest_dir(self.conn, tmp, log=lambda *a: None)
            self.assertEqual(summary["متاجر جديدة"], 1)
            row = self.conn.execute("SELECT source_url, city FROM stores").fetchone()
            self.assertEqual(row["source_url"], "https://ksa.motory.com/ar/x/1/")
            self.assertEqual(row["city"], "جدة")


if __name__ == "__main__":
    unittest.main(verbosity=2)
