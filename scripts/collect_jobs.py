#!/usr/bin/env python3
"""Conservatively check public employer recruitment pages and emit data/jobs.json.

The crawler visits only URLs listed in data/companies.csv. It does not search
the web, execute JavaScript, sign in, bypass blocks, or scrape job-board pages.
Unknown or ambiguous details stay empty and the recruitment status becomes
needs_confirmation.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.robotparser
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / "data" / "companies.csv"
INPUT_DIR = ROOT / "data" / "companies"
OUTPUT_FILE = ROOT / "data" / "jobs.json"
JST = ZoneInfo("Asia/Tokyo")
USER_AGENT = (
    "JIAHAOBIN-job-info-checker/1.0 "
    "(+https://haobinjiag0-star.github.io/JIAHAOBIN/; robots.txt aware)"
)
REQUEST_TIMEOUT_SECONDS = 12
ROBOTS_TIMEOUT_SECONDS = 8
MAX_PAGE_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 256_000
MIN_SECONDS_PER_HOST = 2.0

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
    "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
    "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p",
    "section", "table", "td", "th", "tr", "ul",
}
SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}


class FetchBlocked(Exception):
    """A page was skipped because access was disallowed or unsafe."""


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self.skip_stack: list[str] = []
        self.anchor: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "a":
            self.anchor = {"href": attrs_map.get("href") or "", "text": []}

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack:
            if tag == self.skip_stack[-1]:
                self.skip_stack.pop()
            return
        if tag == "a" and self.anchor:
            text = " ".join(self.anchor["text"]).strip()
            self.links.append({"href": str(self.anchor["href"]), "text": text})
            self.anchor = None
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        text = data.strip()
        if not text:
            return
        self.parts.append(text)
        if self.anchor is not None:
            self.anchor["text"].append(text)


def normalize_header(value: object) -> str:
    return re.sub(r"[\s\u3000_\-./()（）【】\[\]{}:：*・]", "", str(value or "")).lower()


def row_value(row: dict[str, str], *names: str) -> str:
    values = {normalize_header(k): (v or "").strip() for k, v in row.items()}
    for name in names:
        value = values.get(normalize_header(name), "")
        if value:
            return value
    return ""


def split_candidates(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;；|、]", value) if part.strip()]


def company_key(name: str, number: str) -> str:
    digits = re.sub(r"\D", "", number)
    if len(digits) == 13:
        return f"法人番号:{digits}"
    compact = re.sub(r"[\s\u3000株式会社有限会社合同会社（）、,，.．・]", "", name).lower()
    return f"企业名:{compact}"


def validate_public_http_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise FetchBlocked("网址不是公开的 HTTP/HTTPS 页面")
    if parsed.username or parsed.password:
        raise FetchBlocked("网址含有登录凭据，已跳过")
    hostname = parsed.hostname.rstrip(".")
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
        }
    except OSError as exc:
        raise FetchBlocked(f"无法解析网站地址：{exc.__class__.__name__}") from exc
    if not addresses:
        raise FetchBlocked("网站没有公开地址")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise FetchBlocked("网站地址无法验证") from exc
        if not ip.is_global:
            raise FetchBlocked("网址解析到非公开网络地址，已跳过")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(PublicRedirectHandler)
ROBOTS_CACHE: dict[str, tuple[urllib.robotparser.RobotFileParser | None, str | None]] = {}
LAST_REQUEST_BY_HOST: dict[str, float] = {}


def origin_for(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def robots_policy(url: str) -> tuple[bool, str]:
    origin = origin_for(url)
    cached = ROBOTS_CACHE.get(origin)
    if cached is None:
        robots_url = f"{origin}/robots.txt"
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            safe_robots_url = validate_public_http_url(robots_url)
            request = urllib.request.Request(
                safe_robots_url,
                headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.1"},
            )
            with OPENER.open(request, timeout=ROBOTS_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_ROBOTS_BYTES + 1)
                if len(raw) > MAX_ROBOTS_BYTES:
                    ROBOTS_CACHE[origin] = (None, "robots.txt 过大，按禁止抓取处理")
                    return False, "robots.txt 过大，按禁止抓取处理"
                parser.parse(raw.decode("utf-8", errors="replace").splitlines())
                ROBOTS_CACHE[origin] = (parser, None)
        except urllib.error.HTTPError as exc:
            if exc.code in {404, 410}:
                ROBOTS_CACHE[origin] = (None, None)
            else:
                reason = f"robots.txt 返回 HTTP {exc.code}，按禁止抓取处理"
                ROBOTS_CACHE[origin] = (None, reason)
        except Exception as exc:
            reason = f"无法读取 robots.txt（{exc.__class__.__name__}），按禁止抓取处理"
            ROBOTS_CACHE[origin] = (None, reason)
        cached = ROBOTS_CACHE[origin]

    parser, blocked_reason = cached
    if blocked_reason:
        return False, blocked_reason
    if parser is not None and not parser.can_fetch(USER_AGENT, url):
        return False, "robots.txt 不允许访问该页面"
    return True, "robots.txt 允许访问"


def wait_for_host(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc.lower()
    previous = LAST_REQUEST_BY_HOST.get(host)
    if previous is not None:
        remaining = MIN_SECONDS_PER_HOST - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)
    LAST_REQUEST_BY_HOST[host] = time.monotonic()


def fetch_html(url: str) -> tuple[str, str]:
    safe_url = validate_public_http_url(url)
    allowed, reason = robots_policy(safe_url)
    if not allowed:
        raise FetchBlocked(reason)
    wait_for_host(safe_url)
    request = urllib.request.Request(
        safe_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    with OPENER.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        content_type = response.headers.get_content_type().lower()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise FetchBlocked(f"页面类型为 {content_type}，只处理 HTML")
        raw = response.read(MAX_PAGE_BYTES + 1)
        if len(raw) > MAX_PAGE_BYTES:
            raise FetchBlocked(f"HTML 超过 {MAX_PAGE_BYTES} 字节，已跳过")
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            html_text = raw.decode(charset, errors="replace")
        except LookupError:
            html_text = raw.decode("utf-8", errors="replace")
        return html_text, response.geturl()


def extract_document(html_text: str) -> tuple[str, list[dict[str, str]]]:
    parser = PageParser()
    parser.feed(html_text)
    parser.close()
    lines = [
        re.sub(r"\s+", " ", part).strip()
        for part in "".join(parser.parts).splitlines()
    ]
    text = "\n".join(line for line in lines if line)
    return text[:2_000_000], parser.links


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_context(text: str, patterns: list[str], before: int = 70, after: int = 300) -> tuple[str, str]:
    content = flat(text)
    best: tuple[int, re.Match[str], str] | None = None
    for pattern in patterns:
        match = re.search(pattern, content, flags=re.IGNORECASE)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), match, pattern)
    if best is None:
        return "", ""
    start, match, pattern = best
    excerpt = content[max(0, start - before): min(len(content), match.end() + after)]
    return excerpt[:480], pattern


DATE_PATTERNS = [
    re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"),
    re.compile(r"(?P<year>20\d{2})\s*[./-]\s*(?P<month>\d{1,2})\s*[./-]\s*(?P<day>\d{1,2})"),
]


def explicit_dates(text: str) -> list[tuple[dt.date, int, int]]:
    dates: list[tuple[dt.date, int, int]] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                parsed = dt.date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError:
                continue
            dates.append((parsed, match.start(), match.end()))
    return sorted(set(dates), key=lambda item: item[1])


def iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None


def evidence(snippet: str, source_url: str, confidence: float) -> dict[str, object] | None:
    if not snippet:
        return None
    return {"url": source_url, "snippet": snippet[:360], "confidence": round(confidence, 2)}


ROLE_PATTERNS = [
    r"募集職種",
    r"募集コース",
    r"職種名",
    r"仕事内容",
    r"職種",
]
TARGET_PATTERNS = [
    r"応募資格",
    r"応募対象",
    r"募集対象",
    r"採用対象",
    r"応募条件",
    r"卒業見込み",
    r"新卒採用",
]
PERIOD_PATTERNS = [
    r"応募受付期間",
    r"エントリー受付期間",
    r"エントリー締切",
    r"応募締切",
    r"受付期間",
    r"受付開始",
    r"応募受付中",
    r"エントリー受付中",
    r"随時受付",
    r"随時募集",
    r"募集終了",
    r"受付終了",
]
SEMINAR_PATTERNS = [
    r"会社説明会",
    r"採用説明会",
    r"説明会",
    r"セミナー",
]
SELECTION_PATTERNS = [
    r"選考フロー",
    r"選考方法",
    r"選考スケジュール",
    r"選考",
]


def candidates_from(snippet: str) -> tuple[list[str], str | None]:
    values: list[str] = []
    if re.search(r"第二新卒", snippet) and not re.search(r"第二新卒.{0,8}(?:不可|対象外)", snippet):
        values.append("第二新卒")
    if re.search(r"既卒", snippet) and not re.search(r"既卒.{0,8}(?:不可|対象外)", snippet):
        values.append("既卒歓迎")
    if re.search(r"新卒", snippet) and not re.search(r"新卒.{0,8}(?:不可|対象外)", snippet):
        values.append("新卒")
    year = re.search(r"(20\d{2})\s*年?\s*卒", snippet)
    graduation_year = f"{year.group(1)}卒" if year else None
    return list(dict.fromkeys(values)), graduation_year


def extract_application_window(snippet: str, today: dt.date) -> dict[str, object]:
    dates = explicit_dates(snippet)
    start: dt.date | None = None
    end: dt.date | None = None
    start_words = r"開始|受付開始|応募開始|エントリー開始|から"
    end_words = r"締切|期限|終了|まで"
    if len(dates) >= 2 and re.search(r"～|〜|~|から.{0,100}まで", snippet):
        start, end = dates[0][0], dates[-1][0]
    elif len(dates) == 1:
        prefix = snippet[:dates[0][1] + 1]
        suffix = snippet[dates[0][2]:]
        if re.search(end_words, prefix + suffix):
            end = dates[0][0]
        elif re.search(start_words, prefix + suffix):
            start = dates[0][0]

    rolling_match = re.search(r"随時\s*(?:受付|応募|募集|エントリー)", snippet)
    open_words = re.search(
        r"募集中|応募受付中|エントリー受付中|受付中|応募可能|随時\s*(?:受付|応募|募集|エントリー)",
        snippet,
    )
    closed_words = re.search(r"募集終了|受付終了|応募受付終了|締切りました|締め切りました|受付を終了", snippet)

    status = "needs_confirmation"
    confidence = 0.25
    if start and end and start <= today <= end:
        status, confidence = "open", 0.95
    elif end and end < today:
        status, confidence = "closed", 0.95
    elif rolling_match and not closed_words:
        status, confidence = "open", 0.9
    elif open_words and not closed_words:
        status, confidence = "open", 0.82
    elif closed_words and not open_words:
        status, confidence = "closed", 0.82

    return {
        "applicationStart": iso(start),
        "applicationEnd": iso(end),
        "applicationRolling": rolling_match.group(0) if rolling_match else "",
        "applicationPeriodRaw": snippet,
        "recruitmentSourceStatus": status,
        "recruitmentConfidence": confidence,
    }


def resolve_http_link(page_url: str, href: str) -> tuple[str, urllib.parse.SplitResult] | None:
    """Resolve a page link, ignoring malformed URLs instead of aborting a shard."""
    try:
        absolute = urllib.parse.urljoin(page_url, href)
        parsed = urllib.parse.urlsplit(absolute)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return absolute, parsed
    except (TypeError, ValueError, UnicodeError):
        return None


def application_link(links: list[dict[str, str]], page_url: str) -> tuple[str | None, str]:
    choices: list[tuple[int, str, str]] = []
    pattern = re.compile(r"エントリー|応募する|応募はこちら|マイページ|entry|apply", re.IGNORECASE)
    excluded = re.compile(r"お問い合わせ|資料請求|プライバシー|個人情報|採用サイト一覧", re.IGNORECASE)
    for link in links:
        label = flat(link.get("text", ""))
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")) or excluded.search(label):
            continue
        if not pattern.search(label + " " + href):
            continue
        resolved = resolve_http_link(page_url, href)
        if resolved is None:
            continue
        absolute, parsed = resolved
        score = 3 if re.search(r"エントリー|応募する|応募はこちら|entry|apply", label, re.I) else 1
        if parsed.hostname.lower() == urllib.parse.urlsplit(page_url).hostname.lower():
            score += 1
        choices.append((score, absolute, label or href))
    if not choices:
        return None, ""
    choices.sort(key=lambda item: (-item[0], len(item[1])))
    return choices[0][1], choices[0][2]


def stable_id(seed: dict[str, object]) -> str:
    key = str(seed.get("companyMatchKey") or seed.get("company") or "")
    return "official-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def load_seeds() -> list[dict[str, object]]:
    files = sorted(INPUT_DIR.glob("companies-*.csv")) if INPUT_DIR.exists() else []
    if INPUT_FILE.exists():
        files.insert(0, INPUT_FILE)
    if not files:
        raise FileNotFoundError(f"Missing company CSV files under {INPUT_DIR}")

    seeds: list[dict[str, object]] = []
    seen: set[str] = set()
    for input_path in files:
        with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"{input_path.relative_to(ROOT)} has no header row")
            for row in reader:
                company = row_value(row, "company", "companyName", "企業名", "会社名")
                page_url = row_value(row, "companyRecruitmentUrl", "recruitmentUrl", "採用ページ")
                homepage = row_value(row, "companyHomepage", "homepage", "企業ホームページ")
                if not company:
                    continue
                key = company_key(company, row_value(row, "companyNumber", "法人番号"))
                if key in seen:
                    continue
                seen.add(key)
                crawl_url = page_url or homepage
                seeds.append({
                    "id": "",
                    "company": company,
                    "companyNumber": row_value(row, "companyNumber", "法人番号"),
                    "companyMatchKey": key,
                    "industry": row_value(row, "industry", "業界", "業種"),
                    "size": row_value(row, "size", "企業規模区分"),
                    "employeesRaw": row_value(row, "employees", "employeesRaw", "従業員数", "企業規模"),
                    "employees": number_value(row_value(row, "employees", "employeesRaw", "従業員数", "企業規模")),
                    "location": row_value(row, "location", "都道府県", "勤務地"),
                    "companyAddress": row_value(row, "companyAddress", "企業所在地", "所在地"),
                    "business": row_value(row, "business", "description", "企業概要", "事業概要"),
                    "companyRecruitmentUrl": page_url,
                    "companyHomepage": homepage,
                    "crawlUrl": crawl_url,
                    "helloWorkGradUrl": row_value(row, "helloWorkGradUrl", "大卒求人リンク"),
                    "candidate": split_candidates(row_value(row, "candidate", "招聘对象")),
                    "legacyRoles": row_value(row, "legacyRoles", "roles", "職種"),
                    "legacyGraduationYear": row_value(row, "legacyGraduationYear", "graduationYear", "卒業年度"),
                    "legacyNote": row_value(row, "legacyNote", "sourceNote", "核验备注"),
                    "sourceLabel": row_value(row, "sourceLabel", "来源"),
                })
    if not seeds:
        raise ValueError("company CSV files contain no company rows")
    return seeds


def number_value(value: str) -> int | None:
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group(0).replace(",", "")) if match else None


def recruitment_page_link(links: list[dict[str, str]], page_url: str) -> str | None:
    homepage_host = (urllib.parse.urlsplit(page_url).hostname or "").lower()
    pattern = re.compile(r"採用|新卒|求人|recruit|career", re.IGNORECASE)
    excluded = re.compile(r"問い合わせ|問合せ|個人情報|プライバシー|ログイン", re.IGNORECASE)
    choices: list[tuple[int, str]] = []
    for link in links:
        label = flat(link.get("text", ""))
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")) or excluded.search(label):
            continue
        resolved = resolve_http_link(page_url, href)
        if resolved is None:
            continue
        absolute, parsed = resolved
        if parsed.hostname.lower() != homepage_host:
            continue
        if not pattern.search(label + " " + parsed.path):
            continue
        score = 3 if re.search(r"採用|新卒", label, re.IGNORECASE) else 1
        if re.search(r"recruit|career", parsed.path, re.IGNORECASE):
            score += 1
        choices.append((score, absolute))
    choices.sort(key=lambda item: (-item[0], len(item[1])))
    return choices[0][1] if choices else None


def shard_for(seed: dict[str, object], shard_count: int) -> int:
    url = str(seed.get("crawlUrl") or seed.get("companyRecruitmentUrl") or seed.get("companyHomepage") or "")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    key = host or str(seed.get("companyMatchKey") or seed.get("company") or "")
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % shard_count


def scrape_one(seed: dict[str, object], checked_at: dt.datetime) -> dict[str, object]:
    source_url = str(seed.get("crawlUrl") or seed.get("companyRecruitmentUrl") or seed.get("companyHomepage") or "")
    direct_recruitment_url = str(seed.get("companyRecruitmentUrl") or "")
    homepage_fallback = not direct_recruitment_url and bool(seed.get("companyHomepage"))
    checked_date = checked_at.date().isoformat()
    base = {
        **seed,
        "id": stable_id(seed),
        "sourceUrl": source_url or None,
        "sourceKind": "厚生劳动省名单 + 企业官网自动检查",
        "sourceLabel": f"{seed.get('sourceLabel') or '企业招聘页'}；官网检查 {checked_date}",
        "recruitmentSourceCheckedAt": checked_date,
        "checkedAt": checked_at.isoformat(timespec="seconds"),
        "recruitmentSourcePageAccessible": False,
        "recruitmentSourceStatus": "needs_confirmation",
        "recruitmentConfidence": 0.0,
        "applicationUrl": None,
        "applicationStart": None,
        "applicationEnd": None,
        "applicationRolling": "",
        "applicationPeriodRaw": "",
        "seminar": "",
        "seminarTime": "",
        "selection": "",
        "selectionTime": "",
        "roles": str(seed.get("legacyRoles") or ""),
        "graduationYear": str(seed.get("legacyGraduationYear") or "年度未注明"),
        "fieldEvidence": {},
        "fieldConfidence": {},
        "openStatus": "",
    }
    if not source_url:
        base["recruitmentSourceNote"] = "没有企业官网或招聘页网址；需要人工补充官方来源链接。"
        return base

    try:
        html_text, final_url = fetch_html(source_url)
    except Exception as exc:
        base["recruitmentSourceNote"] = (
            f"本次未能读取官网页面：{exc}。没有猜测岗位、对象或日期；旧岗位记录需要人工确认。"
        )
        base["roles"] = str(seed.get("legacyRoles") or "")
        base["recruitmentErrorKind"] = exc.__class__.__name__
        return base

    text, links = extract_document(html_text)
    discovered_recruitment = ""
    discovery_note = ""
    if homepage_fallback:
        candidate_url = recruitment_page_link(links, final_url)
        if candidate_url:
            try:
                recruitment_html, recruitment_final_url = fetch_html(candidate_url)
                text, links = extract_document(recruitment_html)
                final_url = recruitment_final_url
                discovered_recruitment = recruitment_final_url
            except Exception as exc:
                discovery_note = f"官网可访问，但招聘入口未能读取：{exc.__class__.__name__}。"
        else:
            discovery_note = "官网可访问，但页面没有找到同域招聘入口；需确认。"

    actual_recruitment_url = direct_recruitment_url or discovered_recruitment
    base.update({
        "sourceUrl": final_url,
        "companyRecruitmentUrl": actual_recruitment_url or None,
        "recruitmentSourcePageAccessible": True,
    })
    evidence_map: dict[str, dict[str, object]] = {}
    confidence_map: dict[str, float] = {}

    role_snippet, role_pattern = find_context(text, ROLE_PATTERNS, before=20, after=260)
    if role_snippet:
        base["roles"] = role_snippet
        score = 0.84 if role_pattern in {r"募集職種", r"募集コース", r"職種名"} else 0.68
        evidence_map["roles"] = evidence(role_snippet, final_url, score) or {}
        confidence_map["roles"] = score

    target_snippet, target_pattern = find_context(text, TARGET_PATTERNS, before=20, after=300)
    found_candidates, graduation_year = candidates_from(target_snippet)
    if found_candidates:
        base["candidate"] = found_candidates
        evidence_map["candidate"] = evidence(target_snippet, final_url, 0.84) or {}
        confidence_map["candidate"] = 0.84
    else:
        base["candidate"] = list(seed.get("candidate") or [])
    if graduation_year:
        base["graduationYear"] = graduation_year
        evidence_map["graduationYear"] = evidence(target_snippet, final_url, 0.88) or {}
        confidence_map["graduationYear"] = 0.88

    period_snippet, period_pattern = find_context(text, PERIOD_PATTERNS, before=35, after=280)
    window = extract_application_window(period_snippet, checked_at.date())
    base.update(window)
    if period_snippet:
        evidence_map["applicationPeriod"] = evidence(period_snippet, final_url, 0.82) or {}
        confidence_map["applicationPeriod"] = 0.82

    seminar_snippet, _ = find_context(text, SEMINAR_PATTERNS, before=25, after=270)
    if seminar_snippet:
        base["seminar"] = seminar_snippet
        base["seminarTime"] = seminar_snippet
        evidence_map["seminar"] = evidence(seminar_snippet, final_url, 0.72) or {}
        confidence_map["seminar"] = 0.72

    selection_snippet, _ = find_context(text, SELECTION_PATTERNS, before=25, after=270)
    if selection_snippet:
        base["selection"] = selection_snippet
        base["selectionTime"] = selection_snippet
        evidence_map["selection"] = evidence(selection_snippet, final_url, 0.72) or {}
        confidence_map["selection"] = 0.72

    apply_url, apply_label = application_link(links, final_url)
    if apply_url:
        base["applicationUrl"] = apply_url
        evidence_map["applicationUrl"] = evidence(f"链接文字：{apply_label}", final_url, 0.9) or {}
        confidence_map["applicationUrl"] = 0.9

    base["fieldEvidence"] = evidence_map
    base["fieldConfidence"] = confidence_map
    if discovery_note:
        base["recruitmentSourceNote"] = discovery_note + " 页面只记录明确出现的内容；未找到的字段保持空白并需确认。"
    else:
        base["recruitmentSourceNote"] = (
            "官网页面可读取。系统只记录页面出现的原文；未找到明确内容的字段保持空白并需确认。"
            if base["recruitmentSourceStatus"] == "needs_confirmation"
            else f"官网页面明确出现受理状态文字：{period_snippet[:200]}"
        )
    if base["recruitmentSourceStatus"] == "closed":
        base["openStatus"] = "closed"
    elif base["recruitmentSourceStatus"] == "open":
        base["openStatus"] = "open"
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description="Check public official recruitment pages.")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--output", default=str(OUTPUT_FILE))
    args = parser.parse_args()
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("--shard-index must be between 0 and --shard-count - 1")

    checked_at = dt.datetime.now(JST).replace(microsecond=0)
    seeds = load_seeds()
    all_count = len(seeds)
    if args.shard_count > 1:
        seeds = [seed for seed in seeds if shard_for(seed, args.shard_count) == args.shard_index]
    records: list[dict[str, object]] = []
    print(f"Shard {args.shard_index + 1}/{args.shard_count}: {len(seeds)} of {all_count} companies")
    for index, seed in enumerate(seeds, start=1):
        print(f"[{index}/{len(seeds)}] checking {seed['company']}")
        records.append(scrape_one(seed, checked_at))

    output_file = Path(args.output)
    if not output_file.is_absolute():
        output_file = ROOT / output_file
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_file.with_suffix(output_file.suffix + ".tmp")
    temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output_file)
    accessible = sum(1 for item in records if item["recruitmentSourcePageAccessible"])
    open_count = sum(1 for item in records if item["recruitmentSourceStatus"] == "open")
    confirm_count = sum(1 for item in records if item["recruitmentSourceStatus"] == "needs_confirmation")
    print(f"Output: {output_file}")
    print(f"Companies: {len(records)}; pages read: {accessible}; explicit open: {open_count}; needs confirmation: {confirm_count}")


if __name__ == "__main__":
    main()

