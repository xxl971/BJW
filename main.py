#!/usr/bin/env python3
"""Small, dependency-free job watcher for an initial live-site smoke test."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


USER_AGENT = "PersonalJobWatcher/0.1 (+manual personal job search)"
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
WXPUSHER_SPT_RE = re.compile(r"^SPT_[A-Za-z0-9_-]+$")
JOB_LINK_RE = re.compile(r"/(?:en/)?job(?:s)?/", re.I)
SPACE_RE = re.compile(r"\s+")
INTERNSHIP_MARKERS = (
    " intern", "internship", "placement", "co-op", "coop", "student worker", "实习"
)

SCORE_RULES: list[tuple[str, int, tuple[str, ...]]] = [
    ("biostatistics", 35, ("biostatistic", "biometric")),
    ("statistician", 25, ("statistician", "statistical scientist", "statistical science", " statistics")),
    ("statistical programming", 25, ("statistical programmer", "statistical programming")),
    ("RWE", 20, ("real world evidence", "real-world evidence", "rwe")),
    ("HEOR", 20, ("health economics", "outcomes research", "heor")),
    ("clinical data science", 15, ("clinical data scientist", "clinical data science")),
    ("clinical data", 15, ("clinical data manager", "clinical data management")),
    ("data science", 15, ("data scientist", "data science")),
    ("epidemiology", 18, ("epidemiologist", "epidemiology", "pharmacoepidemiology")),
    ("quantitative sciences", 15, ("quantitative science", "quantitative scientist")),
    ("clinical trials", 10, ("clinical trial", "clinical development")),
    ("survival analysis", 10, ("survival", "time-to-event")),
    ("oncology", 8, ("oncology", "hematology")),
    ("SAS", 6, (" sas ", "sas/", "/sas")),
    ("R", 4, (" r ", "r programming")),
    ("Python", 4, ("python",)),
    ("remote", 5, ("remote", "work from home")),
]

CHINA_CITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Chengdu", ("chengdu", "成都")),
    ("Beijing", ("beijing", "北京")),
    ("Shanghai", ("shanghai", "上海")),
    ("Guangzhou", ("guangzhou", "广州")),
    ("Shenzhen", ("shenzhen", "深圳")),
    ("Hangzhou", ("hangzhou", "杭州")),
    ("Nanjing", ("nanjing", "南京")),
    ("Suzhou", ("suzhou", "苏州")),
    ("Wuhan", ("wuhan", "武汉")),
    ("Tianjin", ("tianjin", "天津")),
    ("Xi'an", ("xi'an", "xian", "西安")),
    ("Chongqing", ("chongqing", "重庆")),
    ("Qingdao", ("qingdao", "青岛")),
    ("Dalian", ("dalian", "大连")),
    ("Shenyang", ("shenyang", "沈阳")),
    ("Changsha", ("changsha", "长沙")),
    ("Zhengzhou", ("zhengzhou", "郑州")),
    ("Hefei", ("hefei", "合肥")),
    ("Jinan", ("jinan", "济南")),
    ("Xiamen", ("xiamen", "厦门")),
    ("Kunming", ("kunming", "昆明")),
)


@dataclass(frozen=True)
class Job:
    job_id: str
    company: str
    title: str
    location: str
    posted_date: str
    url: str
    source_url: str
    score: int
    matched_rules: str
    collected_at_utc: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_item_key(item_type: str, url: str) -> str:
    return hashlib.sha256(f"{item_type}|{url}".encode("utf-8")).hexdigest()


def connect_history(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS seen_items (
            item_key TEXT PRIMARY KEY,
            item_type TEXT NOT NULL,
            url TEXT NOT NULL,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS push_queue (
            item_key TEXT PRIMARY KEY,
            item_type TEXT NOT NULL,
            queued_at_utc TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            sent_at_utc TEXT,
            last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_push_queue_status
            ON push_queue(status, queued_at_utc);
        """
    )
    return connection


def job_from_mapping(item: dict[str, Any]) -> Job:
    values = {name: str(item.get(name, "")) for name in Job.__dataclass_fields__}
    try:
        values["score"] = int(values["score"] or 0)
    except ValueError:
        values["score"] = 0
    return Job(**values)


def insert_seen_item(
    connection: sqlite3.Connection,
    item_type: str,
    url: str,
    payload: dict[str, Any],
    seen_at: str,
) -> str:
    key = stable_item_key(item_type, url)
    connection.execute(
        """
        INSERT OR IGNORE INTO seen_items
            (item_key, item_type, url, first_seen_utc, last_seen_utc, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (key, item_type, url, seen_at, seen_at, json.dumps(payload, ensure_ascii=False)),
    )
    return key


def bootstrap_history(
    connection: sqlite3.Connection,
    output_dir: Path,
    internships: dict[str, list[dict[str, Any]]],
) -> int:
    """Seed the first database from the previous report so old roles stay old."""
    existing = connection.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
    if existing:
        return 0
    seeded = 0
    candidates = sorted(output_dir.glob("jobs_all_*.csv"), reverse=True)
    if candidates:
        with candidates[0].open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                job = job_from_mapping(row)
                if not is_china_job(job):
                    continue
                insert_seen_item(connection, "job", job.url, asdict(job), job.collected_at_utc or utc_now())
                seeded += 1
    for item in internships.get("current", []):
        if item.get("region") != "中国" or not item.get("url"):
            continue
        insert_seen_item(connection, "internship", item["url"], item, utc_now())
        seeded += 1
    connection.commit()
    return seeded


def sync_push_pool(
    connection: sqlite3.Connection,
    jobs: list[Job],
    internships: dict[str, list[dict[str, Any]]],
) -> tuple[set[str], set[str]]:
    """Update history and queue newly discovered China jobs/internships."""
    now = utc_now()
    new_job_urls: set[str] = set()
    new_internship_urls: set[str] = set()
    items: list[tuple[str, str, dict[str, Any]]] = [
        ("job", job.url, asdict(job)) for job in jobs if is_china_job(job)
    ]
    items.extend(
        ("internship", item["url"], item)
        for item in internships.get("current", [])
        if item.get("region") == "中国" and item.get("url")
    )
    for item_type, url, payload in items:
        key = stable_item_key(item_type, url)
        encoded = json.dumps(payload, ensure_ascii=False)
        known = connection.execute(
            "SELECT 1 FROM seen_items WHERE item_key = ?", (key,)
        ).fetchone()
        if known:
            connection.execute(
                "UPDATE seen_items SET last_seen_utc = ?, payload_json = ? WHERE item_key = ?",
                (now, encoded, key),
            )
            continue
        connection.execute(
            """
            INSERT INTO seen_items
                (item_key, item_type, url, first_seen_utc, last_seen_utc, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (key, item_type, url, now, now, encoded),
        )
        connection.execute(
            """
            INSERT INTO push_queue
                (item_key, item_type, queued_at_utc, payload_json, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (key, item_type, now, encoded),
        )
        if item_type == "job":
            new_job_urls.add(url)
        else:
            new_internship_urls.add(url)
    connection.commit()
    return new_job_urls, new_internship_urls


def pending_push_items(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM push_queue WHERE status = 'pending' ORDER BY queued_at_utc, item_key"
    ).fetchall()


def load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def send_wxpusher_spt(spt: str, summary: str, content: str) -> dict[str, Any]:
    if not WXPUSHER_SPT_RE.fullmatch(spt):
        raise ValueError("WXPUSHER_SPT 格式无效，应以 SPT_ 开头。")
    payload = json.dumps(
        {"content": content, "summary": summary[:100], "contentType": 3, "spt": spt},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        "https://wxpusher.zjiecode.com/api/send/message/simple-push",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code") != 1000:
        raise RuntimeError(f"WxPusher 返回失败：{result.get('msg') or result}")
    return result


def build_push_markdown(items: list[dict[str, Any]], preview: bool = False) -> str:
    jobs = [item for item in items if item["item_type"] == "job"]
    internships = [item for item in items if item["item_type"] == "internship"]
    heading = "推送预览" if preview else "今日新增"
    lines = [
        f"# 生物统计岗位{heading}",
        "",
        f"正式岗位 **{len(jobs)}** 个，实习岗位 **{len(internships)}** 个。",
    ]
    for label, group in (("正式岗位", jobs), ("中国实习", internships)):
        if not group:
            continue
        lines.extend(["", f"## {label}"])
        for entry in group[:20]:
            payload = entry["payload"]
            title = payload.get("title", "未命名岗位")
            company = payload.get("company", "")
            location = payload.get("location", "")
            lines.append(f"- [{title}]({payload.get('url', '')}) — {company} · {location}")
    lines.extend(["", "以上链接均指向原始招聘页面；投递前请再次确认截止日期和岗位状态。"])
    return "\n".join(lines)


def preview_push_items(
    jobs: list[Job], internships: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    china_jobs = sorted(
        (job for job in jobs if is_china_job(job)),
        key=lambda item: (not is_chengdu_job(item), -item.score, item.company),
    )[:10]
    china_internships = [
        item for item in internships.get("current", []) if item.get("region") == "中国"
    ][:10]
    return [
        *({"item_type": "job", "payload": asdict(job)} for job in china_jobs),
        *({"item_type": "internship", "payload": item} for item in china_internships),
    ]


def send_pending_push(connection: sqlite3.Connection, spt: str) -> int:
    rows = pending_push_items(connection)
    if not rows:
        return 0
    items = [
        {"item_type": row["item_type"], "payload": json.loads(row["payload_json"])}
        for row in rows
    ]
    try:
        send_wxpusher_spt(spt, f"今日新增 {len(items)} 个岗位机会", build_push_markdown(items))
    except Exception as exc:
        connection.executemany(
            "UPDATE push_queue SET attempts = attempts + 1, last_error = ? WHERE item_key = ?",
            [(str(exc)[:500], row["item_key"]) for row in rows],
        )
        connection.commit()
        raise
    sent_at = utc_now()
    connection.executemany(
        """
        UPDATE push_queue
        SET status = 'sent', attempts = attempts + 1, sent_at_utc = ?, last_error = NULL
        WHERE item_key = ?
        """,
        [(sent_at, row["item_key"]) for row in rows],
    )
    connection.commit()
    return len(rows)


class AnchorParser(HTMLParser):
    """Collect href + visible text while tolerating malformed career-site HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._rel = ""
        self._parts: list[str] = []
        self.anchors: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        values = dict(attrs)
        href = values.get("href")
        if href:
            self._href = href
            self._rel = values.get("rel") or ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = clean_text(" ".join(self._parts))
            self.anchors.append((self._href, text, self._rel))
            self._href = None
            self._rel = ""
            self._parts = []


def clean_text(value: str) -> str:
    return SPACE_RE.sub(" ", html.unescape(value)).strip()


def score_text(value: str) -> tuple[int, list[str]]:
    padded = f" {value.lower()} "
    score = 0
    matches: list[str] = []
    for label, points, needles in SCORE_RULES:
        if any(needle in padded for needle in needles):
            score += points
            matches.append(label)
    return min(score, 100), matches


def infer_china_location(title: str, url: str) -> str:
    text = unquote(f" {title} {url} ").lower()
    for city, markers in CHINA_CITIES:
        if any(marker in text for marker in markers):
            return f"{city}, China"
    if " china" in text or "中国" in text:
        return "China"
    return ""


def is_china_job(job: Job) -> bool:
    return bool(infer_china_location(job.title, job.url))


def is_chengdu_job(job: Job) -> bool:
    return infer_china_location(job.title, job.url).startswith("Chengdu")


def is_internship_text(value: str) -> bool:
    """Return True for common English and Chinese internship labels."""
    padded = f" {value.lower()} "
    return any(marker in padded for marker in INTERNSHIP_MARKERS)


def portal_report(source: dict[str, Any]) -> dict[str, Any]:
    """Represent an official portal that is monitored but not generically scraped."""
    return {
        "company": source["company"],
        "category": source.get("category", "未分类"),
        "source_url": source["url"],
        "status": "portal",
        "pages_fetched": 0,
        "jobs_found": 0,
        "china_jobs_found": 0,
        "chengdu_jobs_found": 0,
        "message": "已纳入公司池；当前使用官方招聘入口人工复核，待增加专用适配器。",
    }


def parse_jobs(page_html: str, company: str, source_url: str, min_score: int) -> list[Job]:
    parser = AnchorParser()
    parser.feed(page_html)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    seen: set[str] = set()
    jobs: list[Job] = []

    for href, label, _rel in parser.anchors:
        absolute = urljoin(source_url, href)
        if not JOB_LINK_RE.search(urlparse(absolute).path) or not label:
            continue
        normalized_url = absolute.split("#", 1)[0]
        if normalized_url in seen:
            continue
        score, matched = score_text(label)
        if score < min_score:
            continue
        seen.add(normalized_url)
        job_id = hashlib.sha256(f"{company}|{normalized_url}".encode()).hexdigest()[:16]
        location = infer_china_location(label, normalized_url)
        jobs.append(
            Job(
                job_id=job_id,
                company=company,
                title=label,
                location=location,
                posted_date="",
                url=normalized_url,
                source_url=source_url,
                score=score,
                matched_rules="; ".join(matched),
                collected_at_utc=now,
            )
        )
    return jobs


def find_next_url(page_html: str, current_url: str) -> str | None:
    parser = AnchorParser()
    parser.feed(page_html)
    for href, label, rel in parser.anchors:
        normalized = clean_text(label).lower()
        rel_tokens = {item.lower() for item in rel.split()}
        if "next" in rel_tokens or normalized in {"next", "next page", "下一页", "›", "»"}:
            candidate = urljoin(current_url, href)
            # Several TalentBrew sites emit `/path&p=2`; urllib treats this as
            # part of the path even though the server expects `/path?p=2`.
            if "?" not in candidate and re.search(r"&p=\d+(?:$|#)", candidate):
                candidate = candidate.replace("&p=", "?p=", 1)
            return candidate
    return None


def fetch_html(url: str, timeout: float, retries: int = 1) -> tuple[str, dict[str, Any]]:
    last_error: Exception | None = None
    started = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.8",
                },
            )
            context = ssl.create_default_context()
            with urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                body = raw.decode(charset, errors="replace")
                return body, {
                    "http_status": response.status,
                    "bytes": len(raw),
                    "final_url": response.geturl(),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                }
        except (HTTPError, URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def collect_one(
    source: dict[str, Any], timeout: float, max_pages_override: int | None = None
) -> tuple[list[Job], dict[str, Any]]:
    company = source["company"]
    url = source["url"]
    report: dict[str, Any] = {
        "company": company,
        "category": source.get("category", "未分类"),
        "source_url": url,
        "status": "failed",
    }
    max_pages = max_pages_override or int(source.get("max_pages", 10))
    entry_urls = [url, *source.get("target_urls", [])]
    visited_pages: set[str] = set()
    jobs_by_url: dict[str, Job] = {}
    page_metadata: list[dict[str, Any]] = []
    channel_errors: list[dict[str, str]] = []
    try:
        for channel_index, entry_url in enumerate(entry_urls):
            current_url: str | None = entry_url
            channel_pages = 0
            channel_limit = max_pages if channel_index == 0 else int(source.get("target_max_pages", 3))
            while current_url and channel_pages < channel_limit and current_url not in visited_pages:
                visited_pages.add(current_url)
                channel_pages += 1
                try:
                    page, metadata = fetch_html(current_url, timeout=timeout)
                except Exception as exc:
                    channel_errors.append(
                        {"url": current_url, "error_type": type(exc).__name__, "message": clean_text(str(exc))[:300]}
                    )
                    break
                page_jobs = parse_jobs(page, company, current_url, int(source.get("min_score", 20)))
                before = len(jobs_by_url)
                for job in page_jobs:
                    jobs_by_url[job.url] = job
                page_metadata.append(
                    {
                        "channel": "primary" if channel_index == 0 else "targeted",
                        "page": channel_pages,
                        "url": current_url,
                        "http_status": metadata["http_status"],
                        "bytes": metadata["bytes"],
                        "elapsed_seconds": metadata["elapsed_seconds"],
                        "matched_jobs_on_page": len(page_jobs),
                        "unique_jobs_added": len(jobs_by_url) - before,
                    }
                )
                next_url = find_next_url(page, current_url)
                if (
                    not next_url
                    and channel_index == 0
                    and source.get("page_template")
                    and channel_pages < channel_limit
                ):
                    next_page = channel_pages + 1
                    page_size = int(source.get("page_size", 1))
                    next_url = str(source["page_template"]).format(
                        page=next_page, offset=(next_page - 1) * page_size
                    )
                current_url = next_url

        jobs = list(jobs_by_url.values())
        report["pages_fetched"] = len(visited_pages)
        report["page_details"] = page_metadata
        report["channel_errors"] = channel_errors
        report["jobs_found"] = len(jobs)
        report["china_jobs_found"] = sum(is_china_job(job) for job in jobs)
        report["chengdu_jobs_found"] = sum(is_chengdu_job(job) for job in jobs)
        report["status"] = "ok" if jobs and not channel_errors else "warning"
        if not jobs:
            report["message"] = (
                "Page loaded, but no matching job links were parsed. "
                "The site may have changed markup, require JavaScript, or currently have no matches."
            )
        return jobs, report
    except Exception as exc:  # one source must not abort the other sources
        report["error_type"] = type(exc).__name__
        report["message"] = clean_text(str(exc))[:500]
        return [], report


def write_csv(path: Path, jobs: list[Job]) -> None:
    fieldnames = list(Job.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in sorted(jobs, key=lambda item: (-item.score, item.company, item.title)):
            writer.writerow(asdict(job))


def job_table(jobs: list[Job], empty_message: str, new_urls: set[str] | None = None) -> str:
    if not jobs:
        return f'<div class="empty">{html.escape(empty_message)}</div>'
    new_urls = new_urls or set()
    rows = []
    for job in sorted(jobs, key=lambda item: (-item.score, item.company, item.title)):
        location = job.location or "China（城市未识别）"
        is_new = job.url in new_urls
        row_class = ' class="new-row"' if is_new else ""
        badge = '<span class="new-badge">NEW</span> ' if is_new else ""
        rows.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(job.company)}</td>"
            f"<td>{badge}<a href=\"{html.escape(job.url, quote=True)}\" target=\"_blank\" rel=\"noopener\">{html.escape(job.title)}</a></td>"
            f"<td>{html.escape(location)}</td>"
            f"<td><span class=\"score\">{job.score}</span></td>"
            f"<td>{html.escape(job.matched_rules)}</td>"
            "</tr>"
        )
    return (
        "<div class=\"table-wrap\"><table><thead><tr>"
        "<th>公司</th><th>岗位（点击打开）</th><th>地点</th><th>分数</th><th>匹配项</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def internship_table(
    items: list[dict[str, Any]], historical: bool = False, new_urls: set[str] | None = None
) -> str:
    if not items:
        return '<div class="empty">当前没有已核实的记录。建议直接打开下方公司官网入口复核。</div>'
    rows = []
    new_urls = new_urls or set()
    sort_key = (lambda item: (item.get("deadline", "9999"), item.get("company", "")))
    for item in sorted(items, key=sort_key):
        status_or_year = str(item.get("year", "")) if historical else item.get("status", "待核实")
        is_new = not historical and item.get("url", "") in new_urls
        row_class = ' class="new-row"' if is_new else ""
        badge = '<span class="new-badge">NEW</span> ' if is_new else ""
        rows.append(
            f"<tr{row_class}>"
            f"<td>{html.escape(item.get('company', ''))}</td>"
            f"<td>{badge}<a href=\"{html.escape(item.get('url', ''), quote=True)}\" target=\"_blank\" rel=\"noopener\">{html.escape(item.get('title', ''))}</a></td>"
            f"<td>{html.escape(item.get('location', ''))}</td>"
            f"<td>{html.escape(status_or_year)}</td>"
            f"<td><strong>{html.escape(item.get('deadline', '未公开'))}</strong></td>"
            f"<td>{html.escape(item.get('eligibility', ''))}</td>"
            f"<td>{html.escape(item.get('suitable_reason', ''))}</td>"
            f"<td>{html.escape(item.get('source_tier', ''))}</td>"
            "</tr>"
        )
    label = "年份" if historical else "状态"
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f'<th>公司</th><th>实习（点击打开）</th><th>地点</th><th>{label}</th><th>截止日期</th>'
        '<th>申请条件</th><th>适合原因</th><th>来源</th>'
        '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    )


def render_html_report(
    jobs: list[Job],
    reports: list[dict[str, Any]],
    internships: dict[str, list[dict[str, Any]]],
    report_path: Path,
    new_job_urls: set[str] | None = None,
    new_internship_urls: set[str] | None = None,
) -> None:
    new_job_urls = new_job_urls or set()
    new_internship_urls = new_internship_urls or set()
    china_jobs = [job for job in jobs if is_china_job(job)]
    chengdu_jobs = [job for job in china_jobs if is_chengdu_job(job)]
    new_china_jobs = [job for job in china_jobs if job.url in new_job_urls]
    old_chengdu_jobs = [job for job in chengdu_jobs if job.url not in new_job_urls]
    old_china_jobs = [job for job in china_jobs if job.url not in new_job_urls]
    generated = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M 北京时间")
    source_rows = []
    for item in sorted(reports, key=lambda value: value["company"]):
        status_label = {"ok":"自动抓取成功", "warning":"自动抓取需复核", "failed":"抓取失败", "portal":"官网入口"}.get(item["status"], item["status"])
        source_rows.append(
            "<tr>"
            f"<td>{html.escape(item['company'])}</td>"
            f"<td>{html.escape(item.get('category', '未分类'))}</td>"
            f"<td>{item.get('pages_fetched', 0)}</td>"
            f"<td>{item.get('jobs_found', 0)}</td>"
            f"<td>{item.get('china_jobs_found', 0)}</td>"
            f"<td class=\"status {html.escape(item['status'])}\">{html.escape(status_label)}</td>"
            f"<td><a href=\"{html.escape(item['source_url'], quote=True)}\" target=\"_blank\" rel=\"noopener\">招聘页</a></td>"
            "</tr>"
        )
    current_internships = internships.get("current", [])
    historical_internships = internships.get("historical", [])
    current_china = [item for item in current_internships if item.get("region") == "中国"]
    current_overseas = [item for item in current_internships if item.get("region") != "中国"]
    new_china_internships = [item for item in current_china if item.get("url") in new_internship_urls]
    old_china_internships = [item for item in current_china if item.get("url") not in new_internship_urls]
    today_new_count = len(new_china_jobs) + len(new_china_internships)
    category_counts = {
        category: sum(item.get("category") == category for item in reports)
        for category in ("跨国药企", "CRO", "国内药企")
    }
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>生物统计岗位监控报告</title>
<style>
:root{{--ink:#14213d;--muted:#64748b;--blue:#2563eb;--bg:#f4f7fb;--card:#fff;--line:#e2e8f0}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.55}}
.wrap{{max-width:1180px;margin:auto;padding:36px 22px 64px}} h1{{font-size:30px;margin:0 0 6px}} h2{{margin-top:38px;font-size:22px}} .sub{{color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:24px 0}} .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:0 4px 18px rgba(15,23,42,.04)}}
.num{{font-size:30px;font-weight:750;color:var(--blue)}} .label{{color:var(--muted);font-size:14px}}
.table-wrap{{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:14px}} table{{width:100%;border-collapse:collapse;min-width:820px}} th,td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{background:#eef4ff;font-size:14px}} tr:last-child td{{border-bottom:0}} a{{color:var(--blue);text-decoration:none}} a:hover{{text-decoration:underline}}
.score{{display:inline-block;min-width:38px;text-align:center;padding:3px 8px;background:#dbeafe;color:#1d4ed8;border-radius:999px;font-weight:700}} .empty{{background:#fff;border:1px dashed #94a3b8;border-radius:14px;padding:24px;color:var(--muted)}}
.note{{background:#fffbeb;border-left:4px solid #f59e0b;padding:13px 16px;border-radius:8px}} .info{{background:#eff6ff;border-left-color:#2563eb}} .status.ok{{color:#15803d}} .status.warning{{color:#b45309}} .status.failed{{color:#b91c1c}} .status.portal{{color:#1d4ed8}}
.today-update{{background:#fff1f2;border:1px solid #fecdd3;border-left:6px solid #dc2626;border-radius:12px;padding:18px 20px;margin:22px 0;color:#991b1b;font-size:18px}} .today-update strong{{font-size:28px;color:#dc2626}}
.new-row{{background:#fff1f2}} .new-row td{{color:#991b1b}} .new-row a{{color:#dc2626;font-weight:750}} .new-badge{{display:inline-block;background:#dc2626;color:#fff;border-radius:999px;padding:2px 7px;font-size:11px;font-weight:800;vertical-align:1px}}
@media(max-width:760px){{.cards{{grid-template-columns:repeat(2,1fr)}} h1{{font-size:25px}}}}
</style></head><body><main class="wrap">
<h1>生物统计岗位与实习机会报告</h1><div class="sub">生成时间：{generated} · 成都优先 · 中国岗位 · 实习日历</div>
<div class="today-update">今天更新了 <strong>{today_new_count}</strong> 个岗位机会：正式岗位 {len(new_china_jobs)} 个，中国实习 {len(new_china_internships)} 个。新岗位已在下方标红。</div>
<section class="cards">
<div class="card"><div class="num">{len(new_china_jobs)}</div><div class="label">今日新增正式岗位</div></div>
<div class="card"><div class="num">{len(china_jobs)}</div><div class="label">中国岗位（含成都）</div></div>
<div class="card"><div class="num">{len(chengdu_jobs)}</div><div class="label">成都岗位</div></div>
<div class="card"><div class="num">{len(new_china_internships)}</div><div class="label">今日新增中国实习</div></div>
</section>
<p class="note">正式岗位来自多页自动抓取；实习来自公司官网和权威历史汇总。中国岗位表包含成都岗位。海外实习务必先核对在读身份与当地工作许可。</p>
<h2>1. 今日新增正式岗位（标红）</h2>
{job_table(new_china_jobs, '今天暂未发现新的中国正式岗位。', new_job_urls)}
<h2>2. 历史在库：成都岗位</h2>
{job_table(old_chengdu_jobs, '当前没有历史在库的成都匹配岗位。')}
<h2>3. 历史在库：中国全部岗位（含成都）</h2>
{job_table(old_china_jobs, '当前没有历史在库的中国匹配岗位。')}
<h2>4. 今日新增实习：中国（标红）</h2>
{internship_table(new_china_internships, new_urls=new_internship_urls)}
<h2>5. 历史在库实习：中国</h2>
{internship_table(old_china_internships)}
<p class="note info"><strong>中国岗位核验规则：</strong>“公司官网”可直接优先投递；“近期职位/招聘平台”表示页面在近一个月出现或仍提供申请入口，但平台状态可能滞后，投递前请再检查是否仍可提交。未公布截止日期的岗位按滚动招聘处理，建议尽快申请。</p>
<h2>6. 当前开放实习：海外</h2>
{internship_table(current_overseas)}
<p class="note info">“开放”依据抓取日的公司官网状态；未公布截止日期的岗位可能随时关闭。滚动招聘应尽早投递。</p>
<h2>7. 2025 历史实习与截止日期</h2>
{internship_table(historical_internships, historical=True)}
<p class="note info"><strong>准备节奏（基于 2025 样本推断）：</strong>海外药企统计实习多在 1–3 月截止，建议前一年 10–12 月完成英文简历、项目材料和工作许可判断；国内暑期实习通常在春季集中出现，应从 2 月起每周检查官网和官方公众号。历史日期用于规划，不代表下一年度一定相同。</p>
<h2>8. 30 家公司监控池</h2>
<p class="sub">跨国药企 {category_counts['跨国药企']} 家 · CRO {category_counts['CRO']} 家 · 国内药企 {category_counts['国内药企']} 家。自动抓取站点会翻多页；“官网入口”表示已纳入监控，但需要人工复核或后续专用适配器。</p>
<div class="table-wrap"><table><thead><tr><th>公司</th><th>类型</th><th>抓取页数</th><th>全球初筛</th><th>中国岗位</th><th>状态</th><th>来源</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></div>
</main></body></html>"""
    report_path.write_text(document, encoding="utf-8")


def write_outputs(
    jobs: list[Job],
    reports: list[dict[str, Any]],
    internships: dict[str, list[dict[str, Any]]],
    output_dir: Path,
    new_job_urls: set[str] | None = None,
    new_internship_urls: set[str] | None = None,
) -> tuple[Path, Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"jobs_all_{stamp}.csv"
    china_csv_path = output_dir / f"jobs_china_{stamp}.csv"
    report_path = output_dir / f"run_report_{stamp}.json"
    html_path = output_dir / f"job_report_{stamp}.html"

    write_csv(csv_path, jobs)
    write_csv(china_csv_path, [job for job in jobs if is_china_job(job)])
    render_html_report(
        jobs,
        reports,
        internships,
        html_path,
        new_job_urls=new_job_urls,
        new_internship_urls=new_internship_urls,
    )
    shutil.copyfile(html_path, output_dir / "latest_report.html")

    payload = {
        "run_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "total_jobs": len(jobs),
        "sources_ok": sum(item["status"] == "ok" for item in reports),
        "sources_warning": sum(item["status"] == "warning" for item in reports),
        "sources_failed": sum(item["status"] == "failed" for item in reports),
        "sources_portal": sum(item["status"] == "portal" for item in reports),
        "current_internships": len(internships.get("current", [])),
        "historical_internships": len(internships.get("historical", [])),
        "new_china_jobs": len(new_job_urls or set()),
        "new_china_internships": len(new_internship_urls or set()),
        "sources": sorted(reports, key=lambda item: item["company"]),
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, china_csv_path, report_path, html_path


def load_config(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Config must contain a non-empty 'sources' list")
    return [item for item in sources if item.get("enabled", True)]


def demo_html() -> str:
    return """
    <html><body>
      <a href="/job/boston/associate-director-biostatistics/1/111">
        Associate Director, Biostatistics — Boston, Massachusetts
      </a>
      <a href="/job/remote/senior-statistical-programmer/1/222">
        Senior Statistical Programmer — United States Remote
      </a>
      <a href="/about-us">About us</a>
    </body></html>
    """


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    load_local_env(project_dir / ".env.local")
    parser = argparse.ArgumentParser(description="Collect and rank biostatistics-related jobs.")
    parser.add_argument("--config", type=Path, default=project_dir / "companies.json")
    parser.add_argument("--internships", type=Path, default=project_dir / "internships.json")
    parser.add_argument("--output", type=Path, default=project_dir / "output")
    parser.add_argument("--database", type=Path, default=project_dir / "data" / "job_history.sqlite3")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--max-pages", type=int, default=None, help="Override per-source page limit")
    parser.add_argument("--offline-demo", action="store_true")
    push_group = parser.add_mutually_exclusive_group()
    push_group.add_argument("--push", action="store_true", help="Send newly queued items through WxPusher")
    push_group.add_argument("--test-push", action="store_true", help="Send a preview without changing queue status")
    args = parser.parse_args()

    internships = json.loads(args.internships.read_text(encoding="utf-8"))
    connection = connect_history(args.database)
    seeded = bootstrap_history(connection, args.output, internships)

    if args.offline_demo:
        jobs = parse_jobs(demo_html(), "Demo Pharma", "https://example.com/jobs", 20)
        reports = [{"company": "Demo Pharma", "category": "演示", "source_url": "offline fixture", "status": "ok", "jobs_found": len(jobs)}]
    else:
        sources = load_config(args.config)
        jobs = []
        portal_sources = [source for source in sources if source.get("monitor_mode", "html") == "portal"]
        html_sources = [source for source in sources if source.get("monitor_mode", "html") == "html"]
        reports = [portal_report(source) for source in portal_sources]
        with ThreadPoolExecutor(max_workers=min(4, len(html_sources))) as pool:
            futures = {
                pool.submit(collect_one, source, args.timeout, args.max_pages): source for source in html_sources
            }
            for future in as_completed(futures):
                source_jobs, source_report = future.result()
                jobs.extend(source_jobs)
                reports.append(source_report)

    # URL is the natural stable key for this first version.
    jobs = list({job.url: job for job in jobs}.values())
    new_job_urls, new_internship_urls = sync_push_pool(connection, jobs, internships)
    csv_path, china_csv_path, report_path, html_path = write_outputs(
        jobs,
        reports,
        internships,
        args.output,
        new_job_urls=new_job_urls,
        new_internship_urls=new_internship_urls,
    )

    pushed = 0
    if args.test_push:
        spt = os.environ.get("WXPUSHER_SPT", "")
        if not spt:
            parser.error("首次测试推送需要在 .env.local 中配置 WXPUSHER_SPT=SPT_xxx")
        preview_items = preview_push_items(jobs, internships)
        send_wxpusher_spt(
            spt,
            "生物统计岗位推送测试",
            build_push_markdown(preview_items, preview=True),
        )
        pushed = len(preview_items)
    elif args.push:
        pending = pending_push_items(connection)
        if pending:
            spt = os.environ.get("WXPUSHER_SPT", "")
            if not spt:
                parser.error("存在待推送岗位，请在 .env.local 中配置 WXPUSHER_SPT=SPT_xxx")
            pushed = send_pending_push(connection, spt)

    print(f"Collected jobs: {len(jobs)}")
    print(f"History bootstrap items: {seeded}")
    print(f"New China jobs: {len(new_job_urls)}")
    print(f"New China internships: {len(new_internship_urls)}")
    if args.test_push:
        print(f"WxPusher preview sent: {pushed} items")
    elif args.push:
        print(f"WxPusher queue sent: {pushed} items")
    for report in sorted(reports, key=lambda item: item["company"]):
        count = report.get("jobs_found", 0)
        detail = report.get("message", "")
        pages = report.get("pages_fetched", 0)
        china = report.get("china_jobs_found", 0)
        print(f"- {report['company']}: {report['status']} ({pages} pages, {count} jobs, {china} China){' — ' + detail if detail else ''}")
    print(f"All jobs CSV: {csv_path}")
    print(f"China jobs CSV: {china_csv_path}")
    print(f"Machine report: {report_path}")
    print(f"HTML report: {html_path}")
    connection.close()
    return 0 if jobs or args.offline_demo or internships.get("current") else 2


if __name__ == "__main__":
    sys.exit(main())
