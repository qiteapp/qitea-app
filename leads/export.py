"""تصدير قاعدة العملاء المحتملين: CSV للإكسل، JSON، GeoJSON، وخريطة HTML."""
import csv
import json

CSV_HEADERS = [
    ("name", "اسم المتجر"), ("city", "المدينة"), ("region", "المنطقة"),
    ("district", "الحي"), ("mobile", "الجوال"), ("whatsapp", "واتساب"),
    ("landline", "هاتف ثابت"), ("unified_number", "الرقم الموحد"),
    ("brands_ar", "العلامات التجارية"), ("maps_url", "الموقع على الخريطة"),
    ("lat", "خط العرض"), ("lng", "خط الطول"), ("geo_precision", "دقة الموقع"),
    ("address", "العنوان"), ("website", "الموقع الإلكتروني"), ("email", "البريد"),
    ("working_hours", "أوقات العمل"), ("rating", "التقييم"),
    ("lead_score", "نقاط الجدوى"), ("lead_status", "حالة التواصل"),
    ("source_url", "رابط المصدر"),
]


def fetch_rows(conn, include_duplicates=False, city=None, brand=None, mobile_only=False):
    sql = "SELECT * FROM stores WHERE 1=1"
    params = []
    if not include_duplicates:
        sql += " AND duplicate_of IS NULL"
    if city:
        sql += " AND city = ?"
        params.append(city)
    if brand:
        sql += " AND brands LIKE ?"
        params.append("%%%s%%" % brand)
    if mobile_only:
        sql += " AND mobile <> ''"
    sql += " ORDER BY lead_score DESC, city, name"
    return [dict(row) for row in conn.execute(sql, params)]


def _display(row, key):
    value = row.get(key)
    if key in ("brands_ar", "phones", "all_mobiles", "brands"):
        try:
            return "، ".join(json.loads(value or "[]"))
        except (TypeError, json.JSONDecodeError):
            return value or ""
    return "" if value is None else value


def to_csv(rows, path):
    # utf-8-sig حتى يفتح إكسل العربية بشكل صحيح
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _key, label in CSV_HEADERS])
        for row in rows:
            writer.writerow([_display(row, key) for key, _label in CSV_HEADERS])
    return path


def to_json(rows, path):
    clean = []
    for row in rows:
        item = dict(row)
        for key in ("phones", "all_mobiles", "brands", "brands_ar"):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except (TypeError, json.JSONDecodeError):
                item[key] = []
        clean.append(item)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=2)
    return path


def to_geojson(rows, path):
    features = []
    for row in rows:
        if row.get("lat") is None or row.get("lng") is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lng"], row["lat"]]},
            "properties": {
                "name": row["name"], "city": row["city"],
                "mobile": row["mobile"], "whatsapp": row["whatsapp"],
                "brands": _display(row, "brands_ar"),
                "precision": row["geo_precision"], "score": row["lead_score"],
                "source": row["source_url"],
            },
        })
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, ensure_ascii=False)
    return path


MAP_TEMPLATE = """<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>خريطة متاجر قطع الغيار</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  :root { --bg:#f6f7f9; --fg:#14171a; --card:#fff; --line:#e3e6ea; --accent:#0f766e; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#11151a; --fg:#e8eaed; --card:#1a1f26; --line:#2b323b; --accent:#5eead4; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:system-ui,"Segoe UI",Tahoma,sans-serif; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); background:var(--card);
           display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
  h1 { font-size:1rem; margin:0; font-weight:600; }
  .stat { font-size:.8rem; color:var(--accent); }
  select { padding:6px 8px; border:1px solid var(--line); border-radius:8px;
           background:var(--bg); color:var(--fg); }
  #map { height:calc(100vh - 58px); width:100%; }
  .pop { font-size:.85rem; line-height:1.6; }
  .pop b { font-size:.95rem; }
  .pop a { color:var(--accent); }
</style></head><body>
<header>
  <h1>متاجر قطع الغيار — عملاء محتملون</h1>
  <span class="stat" id="count"></span>
  <select id="cityFilter"><option value="">كل المدن</option></select>
</header>
<div id="map"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const DATA = __DATA__;
const map = L.map('map').setView([23.8859, 45.0792], 6);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  { attribution: '© OpenStreetMap', maxZoom: 19 }).addTo(map);
let layer = L.layerGroup().addTo(map);
const cities = [...new Set(DATA.map(d => d.city).filter(Boolean))].sort();
const select = document.getElementById('cityFilter');
cities.forEach(c => select.add(new Option(c, c)));
function render(city) {
  layer.clearLayers();
  const rows = city ? DATA.filter(d => d.city === city) : DATA;
  rows.forEach(d => {
    const wa = d.whatsapp ? `<a href="https://wa.me/${d.whatsapp.replace('+','')}">واتساب</a>` : '';
    const tel = d.mobile ? `<a href="tel:${d.mobile}">${d.mobile}</a>` : 'بلا جوال';
    L.circleMarker([d.lat, d.lng], {
      radius: 6, color: d.precision === 'exact' ? '#0f766e' : '#b45309',
      fillOpacity: .75, weight: 2,
    }).bindPopup(`<div class="pop"><b>${d.name}</b><br>${d.city || ''} ${d.brands || ''}<br>
      ${tel} ${wa}<br><a href="${d.source}" target="_blank" rel="noopener">صفحة المصدر</a></div>`)
     .addTo(layer);
  });
  document.getElementById('count').textContent = `${rows.length} متجر معروض`;
  if (rows.length) map.fitBounds(rows.map(d => [d.lat, d.lng]), { padding: [30, 30] });
}
select.addEventListener('change', e => render(e.target.value));
render('');
</script></body></html>
"""


def to_map(rows, path):
    """خريطة تفاعلية بملف واحد: نقطة لكل متجر مع زر واتساب ومكالمة."""
    points = [{
        "name": row["name"], "city": row["city"], "mobile": row["mobile"],
        "whatsapp": row["whatsapp"], "brands": _display(row, "brands_ar"),
        "precision": row["geo_precision"], "source": row["source_url"],
        "lat": row["lat"], "lng": row["lng"],
    } for row in rows if row.get("lat") is not None]
    html = MAP_TEMPLATE.replace("__DATA__", json.dumps(points, ensure_ascii=False))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path
