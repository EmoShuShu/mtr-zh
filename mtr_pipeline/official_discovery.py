"""WPN structured document discovery; adapted from the paired IPG checker."""
import io
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
import pdfplumber

def english_date(value: str) -> str:
    months = "January February March April May June July August September October November December".split()
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", value.strip())
    if match:
        names = {name: i for i, name in enumerate(months, 1)}
        names.update({name[:3]: i for i, name in enumerate(months, 1)})
        try:
            return date(int(match[3]), names[match[1]], int(match[2])).isoformat()
        except (ValueError, KeyError):
            pass
    raise ValueError(f"Unrecognized English date: {value!r}")


RULES_PAGE = "https://wpn.wizards.com/en/rules-documents"
EXPECTED_TITLE = "Magic: The Gathering Tournament Rules"
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 30 * 1024 * 1024


def validate_url(url: str, kind: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = "wpn.wizards.com" if kind == "page" else "media.wizards.com"
    try:
        valid = (parsed.scheme == "https" and parsed.hostname == host and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    path = urllib.parse.unquote(parsed.path)
    if (not valid or "\\" in path or ".." in path.split("/") or any(ord(c) < 32 or ord(c) == 127 for c in urllib.parse.unquote(url))
            or kind == "page" and path not in {"/en/rules-documents", "/en/document/magic-the-gathering-tournament-rules"}
            or kind == "pdf" and not path.lower().endswith(".pdf")):
        raise ValueError(f"非法官方 {kind} URL：{url}")


class RulesPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.stack, self.capture = [], None, [], None
        self.payloads, self.payload = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("id") == "__NUXT_DATA__":
            if self.payload is not None or attrs.get("type") != "application/json":
                raise ValueError("官方页面数据脚本结构异常")
            self.payload = ""
        classes = attrs.get("class", "") or ""
        if re.search(r"downloadableDocument(?![a-zA-Z])", classes, re.I):
            if self.row is not None:
                raise ValueError("官方文档条目异常嵌套")
            self.row = {"title": "", "date": "", "links": [], "fields": []}
            self.stack = []
        if self.row is None:
            return
        if tag not in {"img", "br", "input", "hr", "meta", "link", "source"}:
            self.stack.append(tag)
        field = "title" if "titletext" in classes.lower() else "date" if "updated" in classes.lower() else None
        if field:
            if field in self.row["fields"] or self.capture is not None:
                raise ValueError("官方文档标题或日期字段重复")
            self.row["fields"].append(field)
            self.capture = (field, len(self.stack))
        if tag == "a" and attrs.get("href"):
            self.row["links"].append(attrs["href"])

    def handle_data(self, data):
        if self.payload is not None:
            self.payload += data
        if self.row is not None and self.capture:
            self.row[self.capture[0]] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.payload is not None:
            self.payloads.append(self.payload)
            self.payload = None
        if self.row is None or tag in {"img", "br", "input", "hr", "meta", "link", "source"}:
            return
        if not self.stack or self.stack[-1] != tag:
            raise ValueError("官方文档 HTML 结构不匹配")
        if self.capture and self.capture[1] == len(self.stack):
            self.capture = None
        self.stack.pop()
        if not self.stack:
            self.rows.append(self.row)
            self.row = None


def _nuxt_documents(payload: str) -> list[dict]:
    """Read only the known rules-page document collection, not arbitrary strings."""
    def unique_keys(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("官方页面数据含重复字段")
            value[key] = item
        return value
    pool = json.loads(payload, object_pairs_hook=unique_keys)
    if not isinstance(pool, list) or len(pool) > 50000:
        raise ValueError("官方页面引用池结构异常")
    def ref(index):
        if type(index) is not int or not 0 <= index < len(pool):
            raise ValueError("官方页面引用越界")
        value = pool[index]
        for _ in range(3):
            if isinstance(value, list) and len(value) == 2 and value[0] in ("Reactive", "ShallowReactive"):
                index = value[1]
                if type(index) is not int or not 0 <= index < len(pool):
                    raise ValueError("官方页面包装引用越界")
                value = pool[index]
            else:
                return value
        raise ValueError("官方页面引用包装异常")
    try:
        root = ref(0)
        if ref(root["path"]) != "/en/rules-documents":
            raise ValueError("官方页面数据不是规则文档集合")
        collection = ref(ref(root["data"])["downloadableDocuments"])
        entries = ref(collection["entries"])
        if not isinstance(entries, list) or len(entries) != len(set(entries)):
            raise ValueError("官方文档集合异常或重复引用")
        rows = []
        for index in entries:
            entry = ref(index)
            fields = ref(entry["fields"])
            title = ref(fields["title"])
            if title != EXPECTED_TITLE:
                continue
            system = ref(entry["sys"])
            if ref(system["locale"]) != "en" or ref(ref(ref(system["contentType"])["sys"])["id"]) != "downloadableDocument":
                raise ValueError("MTR 条目的类型或语言不合法")
            updated = ref(fields["updated"])
            if not isinstance(updated, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", updated):
                raise ValueError("MTR 网页更新时间不合法")
            date.fromisoformat(updated)
            url = ref(ref(ref(fields["cta"])["fields"])["link"])
            validate_url(url, "pdf")
            rows.append({"title": title, "pageUpdatedDate": updated, "url": url})
        return rows
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("官方文档数据结构无法识别") from error


def parse_rules_page(html: str, page_url: str = RULES_PAGE) -> dict:
    validate_url(page_url, "page")
    parser = RulesPageParser()
    parser.feed(html)
    parser.close()
    if parser.row is not None or parser.payload is not None or len(parser.payloads) > 1:
        raise ValueError("官方文档条目未闭合")
    matches = [r for r in parser.rows if " ".join(r["title"].split()) == EXPECTED_TITLE]
    rows = _nuxt_documents(parser.payloads[0]) if parser.payloads else []
    if len(matches) > 1 or parser.payloads and len(rows) != 1 or not parser.payloads and len(matches) != 1:
        raise ValueError(f"必须精确找到一个 MTR 条目，实际 {len(rows) if parser.payloads else len(matches)}")
    visible = None
    if matches:
        row = matches[0]
        links = {urllib.parse.urljoin(page_url, link) for link in row["links"]}
        if len(links) != 1 or set(row["fields"]) != {"title", "date"}:
            raise ValueError("MTR 条目的日期或 PDF 链接不唯一")
        url = next(iter(links))
        validate_url(url, "pdf")
        visible = {"title": EXPECTED_TITLE, "pageUpdatedDate": english_date(row["date"].strip()), "url": url}
    if rows and visible and rows[0] != visible:
        raise ValueError("网页 MTR 卡片与完整文档集合不一致")
    return {"pageUrl": page_url, **(rows[0] if rows else visible)}


def pdf_effective_date(pdf: bytes) -> str:
    """The page's `updated` is publication metadata, not the PDF Effective date."""
    with pdfplumber.open(io.BytesIO(pdf)) as document:
        if not 1 <= len(document.pages) <= 100:
            raise ValueError("官方 PDF 页数超出支持范围")
        dates = re.findall(r"^Effective (.+)$", document.pages[0].extract_text() or "", re.M)
    if len(dates) != 1:
        raise ValueError("官方 PDF 必须包含唯一的有效日期")
    return english_date(dates[0])


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, kind):
        self.kind, self.hops = kind, []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.kind)  # Validate BEFORE following any redirect.
        self.hops.append(newurl)
        if len(self.hops) > 5:
            raise ValueError("官方下载重定向过多")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, kind: str) -> tuple[bytes, str, list[str]]:
    validate_url(url, kind)
    maximum = MAX_PAGE_BYTES if kind == "page" else MAX_PDF_BYTES
    handler = SafeRedirect(kind)
    opener = urllib.request.build_opener(handler)
    request = urllib.request.Request(url, headers={"User-Agent": "mtr-zh-local-update/1", "Accept-Encoding": "identity"})
    with opener.open(request, timeout=30) as response:
        final = response.geturl()
        validate_url(final, kind)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("不接受压缩的官方下载响应")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > maximum):
            raise ValueError("官方下载长度异常或超限")
        chunks, size = [], 0
        while True:
            chunk = response.read(min(65536, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise ValueError("官方下载超限")
        if length is not None and size != int(length):
            raise ValueError("官方下载截断或长度不一致")
    payload = b"".join(chunks)
    if kind == "pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("官方下载不是 PDF")
    return payload, final, handler.hops


