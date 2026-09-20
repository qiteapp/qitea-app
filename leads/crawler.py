"""زاحف قسم قطع الغيار في دليل موتري.

مبادئ:
- يحترم robots.txt ويتوقف إن كان المسار ممنوعاً.
- مهلة بين الطلبات (افتراضياً ثانية واحدة) وعدد صفحات محدود.
- يحفظ نسخة HTML لكل صفحة اختيارياً، فتُعاد المعالجة لاحقاً بدون إنترنت.
"""
import gzip
import os
import time
import urllib.error
import urllib.request
from urllib.parse import quote, unquote, urlparse
from urllib.robotparser import RobotFileParser

import extract
import store as db

# رؤوس HTTP تُرمّز بـ latin-1، فيجب أن يبقى الوسم إنجليزياً
USER_AGENT = "QiteaLeadsBot/1.0 (+https://qiteapp.com; public business directory data)"
DEFAULT_PREFIX = "/الدليل/قطع-غيار-سيارات"


def encode_url(url):
    """ترميز الأحرف العربية في المسار حتى يقبلها الخادم."""
    parts = urlparse(url)
    return parts._replace(path=quote(unquote(parts.path), safe="/-_.~")).geturl()


def fetch(url, timeout=30):
    """يجلب صفحة ويُرجع (html, status). لا يرفع استثناءً لأخطاء HTTP."""
    request = urllib.request.Request(encode_url(url), headers={
        "User-Agent": USER_AGENT,
        "Accept-Language": "ar,en;q=0.8",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            charset = response.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace"), response.status
    except urllib.error.HTTPError as err:
        return "", err.code
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return "", "خطأ شبكة: %s" % err


def robots_allows(url):
    """هل يسمح robots.txt بزحف هذا الرابط لوكيلنا؟"""
    parsed = urlparse(url)
    parser = RobotFileParser()
    parser.set_url("%s://%s/robots.txt" % (parsed.scheme, parsed.netloc))
    try:
        parser.read()
    except Exception:
        return True, "لم يمكن قراءة robots.txt — سنكمل بحذر"
    allowed = parser.can_fetch(USER_AGENT, encode_url(url))
    delay = parser.crawl_delay(USER_AGENT)
    return allowed, "crawl-delay=%s" % delay if delay else ""


def save_html(directory, url, html):
    os.makedirs(directory, exist_ok=True)
    name = unquote(urlparse(url).path).strip("/").replace("/", "__") or "index"
    path = os.path.join(directory, name[:150] + ".html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<!-- source_url: %s -->\n%s" % (url, html))
    return path


def crawl(conn, seeds, prefix=DEFAULT_PREFIX, max_pages=400, delay=1.0,
          html_dir=None, resume=True, log=print):
    """زحف بالعرض داخل قسم الدليل. يُرجع ملخص العملية."""
    allowed, note = robots_allows(seeds[0])
    if not allowed:
        raise PermissionError(
            "robots.txt يمنع زحف %s — أوقفنا العملية. راجع شروط الموقع أو اطلب إذناً." % seeds[0])
    if note:
        log("robots.txt: %s" % note)

    visited = db.seen_pages(conn) if resume else set()
    queue = [url for url in seeds if url not in visited]
    summary = {"صفحات": 0, "متاجر جديدة": 0, "متاجر محدثة": 0, "بلا بيانات": 0, "أخطاء": 0}

    while queue and summary["صفحات"] < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        html, status = fetch(url)
        summary["صفحات"] += 1
        if status != 200:
            summary["أخطاء"] += 1
            db.log_page(conn, url, 0, "error", str(status))
            log("  ✗ %s → %s" % (url, status))
            time.sleep(delay)
            continue
        if html_dir:
            save_html(html_dir, url, html)

        record = extract.extract_store(html, url)
        if record:
            action = db.upsert_store(conn, record)
            summary["متاجر جديدة" if action == "inserted" else "متاجر محدثة"] += 1
            db.log_page(conn, url, 200, "store")
            log("  ✓ %s | %s | %s" % (record["name"][:40], record["city"] or "—",
                                      record["mobile"] or "بلا جوال"))
        else:
            summary["بلا بيانات"] += 1
            db.log_page(conn, url, 200, "listing")

        stores, pages = extract.extract_listing_links(html, url, prefix)
        for link in pages + stores:
            if link not in visited and link not in queue:
                queue.append(link)
        conn.commit()
        time.sleep(delay)

    summary["في الانتظار"] = len(queue)
    return summary


def ingest_dir(conn, directory, log=print):
    """يعالج صفحات HTML محفوظة مسبقاً — بدون أي اتصال بالإنترنت."""
    summary = {"ملفات": 0, "متاجر جديدة": 0, "متاجر محدثة": 0, "بلا بيانات": 0}
    for root, _dirs, files in os.walk(directory):
        for name in sorted(files):
            if not name.lower().endswith((".html", ".htm")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                html = fh.read()
            summary["ملفات"] += 1
            # الرابط الأصلي من تعليق الحفظ إن وُجد، وإلا مسار الملف
            first_line = html[:300]
            url = path
            if "source_url:" in first_line:
                url = first_line.split("source_url:", 1)[1].split("-->")[0].strip()
            record = extract.extract_store(html, url)
            if record:
                action = db.upsert_store(conn, record)
                summary["متاجر جديدة" if action == "inserted" else "متاجر محدثة"] += 1
                log("  ✓ %s | %s" % (record["name"][:45], record["mobile"] or "بلا جوال"))
            else:
                summary["بلا بيانات"] += 1
    conn.commit()
    return summary
