"""استخراج بيانات المتاجر من صفحة HTML.

لا يعتمد على أسماء CSS في الموقع (فهي تتغير)، بل على إشارات ثابتة:
1) بيانات JSON-LD المنظمة (LocalBusiness/Store) إن وُجدت — أدق مصدر.
2) روابط tel: و wa.me و روابط خرائط جوجل والإحداثيات في أي src.
3) النص الظاهر كحل أخير، مع مطابقة أرقام سعودية صالحة فقط.
"""
import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import normalize as nz

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}


class PageParser(HTMLParser):
    """يجمع من الصفحة: النص، الروابط، الوسوم الوصفية، وكتل JSON-LD."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts = []
        self.links = []          # (href, anchor_text)
        self.meta = {}
        self.jsonld = []
        self.iframes = []
        self.coord_attrs = []    # أي قيمة سمة تحتمل إحداثيات
        self.title = ""
        self._stack = []
        self._ld_buffer = None
        self._in_title = False
        self._current_link = None

    # -- وسوم البداية
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self._stack.append(tag)
        if tag == "title":
            self._in_title = True
        elif tag == "script" and attrs.get("type", "").strip().lower() == "application/ld+json":
            self._ld_buffer = []
        elif tag == "a" and attrs.get("href"):
            self._current_link = [attrs["href"], []]
        elif tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key and attrs.get("content"):
                self.meta[key.strip().lower()] = attrs["content"].strip()
        elif tag == "iframe" and attrs.get("src"):
            self.iframes.append(attrs["src"])
        for name, value in attrs.items():
            if not value:
                continue
            if name in ("data-lat", "data-lng", "data-latitude", "data-longitude",
                        "data-coords", "data-location", "content", "value", "href", "src"):
                self.coord_attrs.append(value)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._ld_buffer is not None:
            raw = "".join(self._ld_buffer).strip()
            self._ld_buffer = None
            if raw:
                try:
                    self.jsonld.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        elif tag == "a" and self._current_link:
            href, parts = self._current_link
            self.links.append((href, re.sub(r"\s+", " ", "".join(parts)).strip()))
            self._current_link = None
        while self._stack:
            if self._stack.pop() == tag:
                break

    def handle_data(self, data):
        if self._ld_buffer is not None:
            self._ld_buffer.append(data)
            return
        if self._in_title:
            self.title += data
            return
        if self._stack and self._stack[-1] in _SKIP_TAGS:
            return
        if data.strip():
            self.text_parts.append(data)
            if self._current_link:
                self._current_link[1].append(data)

    @property
    def text(self):
        return re.sub(r"[ \t]+", " ", "\n".join(p.strip() for p in self.text_parts if p.strip()))


# ------------------------------------------------------------ الإحداثيات

_LAT = r"(-?\d{1,2}\.\d{3,})"
_LNG = r"(-?\d{1,3}\.\d{3,})"
# (نمط، هل يأتي خط الطول أولاً)
_COORD_PATTERNS = [
    (re.compile(r"[?&](?:q|query|ll|center|destination)=%s,\s*%s" % (_LAT, _LNG)), False),
    (re.compile(r"/@%s,%s" % (_LAT, _LNG)), False),
    (re.compile(r"!3d%s!4d%s" % (_LAT, _LNG)), False),
    (re.compile(r"!3d%s!2d%s" % (_LAT, _LNG)), False),   # صيغة embed: lat ثم lng
    (re.compile(r"!2d%s!3d%s" % (_LNG, _LAT)), True),    # صيغة embed: lng ثم lat
]
# حدود السعودية التقريبية، لرفض إحداثيات غير منطقية
KSA_BOUNDS = (16.0, 32.5, 34.0, 56.0)  # lat_min, lat_max, lng_min, lng_max


def in_ksa(lat, lng):
    lo_lat, hi_lat, lo_lng, hi_lng = KSA_BOUNDS
    return lo_lat <= lat <= hi_lat and lo_lng <= lng <= hi_lng


def find_coords(*texts):
    """يبحث عن أول إحداثيات داخل حدود السعودية في أي نص/رابط."""
    for text in texts:
        if not text:
            continue
        candidate = unquote(unescape(str(text)))
        for pattern, lng_first in _COORD_PATTERNS:
            for match in pattern.finditer(candidate):
                first, second = float(match.group(1)), float(match.group(2))
                lat, lng = (second, first) if lng_first else (first, second)
                if in_ksa(lat, lng):
                    return lat, lng
    return None


def maps_url(lat, lng):
    return "https://www.google.com/maps/search/?api=1&query=%s,%s" % (lat, lng)


# --------------------------------------------------------------- JSON-LD

def _walk_jsonld(node, out):
    if isinstance(node, list):
        for item in node:
            _walk_jsonld(item, out)
    elif isinstance(node, dict):
        types = node.get("@type") or ""
        types = [types] if isinstance(types, str) else list(types)
        if any(t in ("LocalBusiness", "Store", "AutoPartsStore", "AutomotiveBusiness",
                     "AutoRepair", "Organization", "Place") for t in types):
            out.append(node)
        for value in node.values():
            _walk_jsonld(value, out)


def business_nodes(parser):
    out = []
    for block in parser.jsonld:
        _walk_jsonld(block, out)
    return out


def _flatten(value):
    """يحوّل حقل JSON-LD (نص أو قائمة أو كائن) إلى نص واحد."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(filter(None, (_flatten(v) for v in value)))
    if isinstance(value, dict):
        for key in ("name", "@id", "url", "value", "text"):
            if key in value:
                return _flatten(value[key])
        return ", ".join(filter(None, (_flatten(v) for k, v in value.items()
                                       if not k.startswith("@"))))
    return str(value)


def from_jsonld(node, page_url):
    """يبني سجل متجر من عقدة JSON-LD."""
    address = node.get("address") or {}
    if isinstance(address, list):
        address = address[0] if address else {}
    if isinstance(address, str):
        address_text, locality, street = address, "", ""
    else:
        locality = _flatten(address.get("addressLocality"))
        street = _flatten(address.get("streetAddress"))
        address_text = "، ".join(filter(None, [
            street, _flatten(address.get("addressRegion")), locality,
            _flatten(address.get("postalCode")),
        ]))

    geo = node.get("geo") or {}
    if isinstance(geo, list):
        geo = geo[0] if geo else {}
    lat = lng = None
    if isinstance(geo, dict):
        try:
            lat = float(geo.get("latitude"))
            lng = float(geo.get("longitude"))
        except (TypeError, ValueError):
            lat = lng = None
        if lat is not None and not in_ksa(lat, lng):
            lat = lng = None

    rating = reviews = None
    aggregate = node.get("aggregateRating") or {}
    if isinstance(aggregate, dict):
        try:
            rating = float(aggregate.get("ratingValue"))
        except (TypeError, ValueError):
            rating = None
        try:
            reviews = int(float(aggregate.get("reviewCount") or aggregate.get("ratingCount")))
        except (TypeError, ValueError):
            reviews = None

    return {
        "name": _flatten(node.get("name")),
        "address": address_text,
        "district": street,
        "city_hint": locality,
        "phones_raw": [_flatten(node.get("telephone")), _flatten(node.get("faxNumber"))],
        "website": _flatten(node.get("url")) or page_url,
        "email": _flatten(node.get("email")),
        "lat": lat,
        "lng": lng,
        "working_hours": _flatten(node.get("openingHours") or node.get("openingHoursSpecification")),
        "rating": rating,
        "reviews_count": reviews,
        "brands_text": _flatten(node.get("brand")) + " " + _flatten(node.get("description")),
        "source": "jsonld",
    }


# ------------------------------------------------------- استخراج من الروابط

def contact_links(parser, page_url):
    """أرقام الهاتف وواتساب والموقع الإلكتروني من الروابط."""
    phones, whatsapp, website, email = [], None, None, None
    for href, _label in parser.links:
        low = href.lower()
        if low.startswith("tel:"):
            phones.append(unquote(href[4:]))
        elif low.startswith("mailto:") and not email:
            email = unquote(href[7:]).strip()
        elif "wa.me/" in low or "api.whatsapp.com" in low or "web.whatsapp.com" in low:
            digits = re.sub(r"\D", "", parse_qs(urlparse(href).query).get("phone", [""])[0] or href)
            info = nz.normalize_phone(digits)
            if info and not whatsapp:
                whatsapp = info["e164"]
                phones.append(info["e164"])
        elif not website and re.search(r"https?://(?!(?:www\.)?%s)" % re.escape(urlparse(page_url).netloc), low):
            if not re.search(r"facebook|instagram|twitter|x\.com|tiktok|youtube|snapchat|linkedin|google|maps|wa\.me", low):
                website = href
    return phones, whatsapp, website, email


def clean_name(title):
    """ينظف اسم المتجر من لواحق عنوان الصفحة: «| دليل موتري»، «- الرياض»."""
    name = re.split(r"\s*[|»]\s*", unescape(title or ""))[0]
    name = re.sub(r"\s*[-–]\s*(?:%s)\s*$" % "|".join(map(re.escape, nz.CITIES)), "", name)
    return re.sub(r"\s+", " ", name).strip()


def extract_store(html, page_url):
    """يستخرج سجل متجر واحد من صفحة تفاصيل. يُرجع None إن لم يجد اسماً أو رقماً."""
    parser = PageParser()
    parser.feed(html)

    nodes = business_nodes(parser)
    record = from_jsonld(nodes[0], page_url) if nodes else {
        "name": "", "address": "", "district": "", "city_hint": "", "phones_raw": [],
        "website": page_url, "email": "", "lat": None, "lng": None, "working_hours": "",
        "rating": None, "reviews_count": None, "brands_text": "", "source": "html",
    }

    if not record["name"]:
        record["name"] = clean_name(parser.meta.get("og:title") or parser.title or "")

    link_phones, whatsapp, website, email = contact_links(parser, page_url)
    text = parser.text
    # المصادر المنظمة أولاً (أدق)، ثم النص الظاهر لاستكمال ما فات كالرقم الموحد.
    # الأرقام العامة للموقع نفسه تُنقّى لاحقاً في prune_shared_phones.
    phones = nz.extract_phones("\n".join(filter(None, record["phones_raw"] + link_phones)))
    seen = {p["e164"] for p in phones}
    for info in nz.extract_phones(text):
        if info["e164"] not in seen:
            seen.add(info["e164"])
            phones.append(info)
    record["email"] = record["email"] or email or ""
    record["website"] = website or record["website"]

    if record["lat"] is None:
        coords = find_coords(*(parser.iframes + parser.coord_attrs))
        if coords:
            record["lat"], record["lng"] = coords
            record["geo_precision"] = "exact"
    else:
        record["geo_precision"] = "exact"

    city = nz.detect_city(record["city_hint"], record["address"], record["name"],
                          unquote(page_url), text[:4000])
    if record["lat"] is None and city:
        record["lat"], record["lng"] = city["lat"], city["lng"]
        record["geo_precision"] = "city"

    brand_text = " ".join([record["brands_text"], record["name"], unquote(page_url), text[:4000]])
    brands = nz.detect_brands(brand_text)

    if not record["name"] or not phones:
        return None

    buckets = nz.split_phones(phones)
    store = {
        "source_url": page_url,
        "name": record["name"][:200],
        "address": (record["address"] or "")[:400],
        "district": (record["district"] or "")[:200],
        "city": city["city"] if city else "",
        "city_en": city["city_en"] if city else "",
        "region": city["region"] if city else "",
        "lat": record["lat"],
        "lng": record["lng"],
        "geo_precision": record.get("geo_precision", ""),
        "maps_url": maps_url(record["lat"], record["lng"]) if record["lat"] else "",
        "phones": [p["e164"] for p in phones],
        "mobile": buckets["mobile"][0] if buckets["mobile"] else "",
        "all_mobiles": buckets["mobile"],
        "landline": buckets["landline"][0] if buckets["landline"] else "",
        "unified_number": (buckets["unified"] + buckets["tollfree"] or [""])[0],
        "whatsapp": whatsapp or (buckets["mobile"][0] if buckets["mobile"] else ""),
        "website": record["website"] or "",
        "email": record["email"],
        "brands": brands,
        "working_hours": (record["working_hours"] or "")[:400],
        "rating": record["rating"],
        "reviews_count": record["reviews_count"],
        "extracted_by": record["source"],
    }
    store["lead_score"] = nz.lead_score(store)
    return store


def extract_listing_links(html, page_url, path_prefix):
    """روابط المتاجر وصفحات الترقيم داخل نفس قسم الدليل."""
    parser = PageParser()
    parser.feed(html)
    stores, pages = [], []
    for href, _label in parser.links:
        absolute = urljoin(page_url, href).split("#")[0]
        parsed = urlparse(absolute)
        if parsed.netloc != urlparse(page_url).netloc:
            continue
        path = unquote(parsed.path)
        if path_prefix not in path:
            continue
        if re.search(r"[?&]page=\d+", absolute) or re.search(r"/page/\d+", path):
            pages.append(absolute)
        elif absolute.rstrip("/") != page_url.rstrip("/"):
            stores.append(absolute)
    return list(dict.fromkeys(stores)), list(dict.fromkeys(pages))
