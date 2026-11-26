"""Crawler multi-sumber yang patuh robots.txt dan sopan (politeness).

Contoh:
  python scripts/crawl.py --source rskemenkes_surabaya --limit 3     # uji dulu
  python scripts/crawl.py --source rskemenkes_surabaya               # penuh
  python scripts/crawl.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger("crawler")

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5, "jun": 6,
          "jul": 7, "agu": 8, "agt": 8, "aug": 8, "sep": 9, "okt": 10, "oct": 10,
          "nov": 11, "des": 12, "dec": 12}
DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")


# ---------------------------------------------------------------- fetching
class PoliteFetcher:
    def __init__(self, user_agent: str, delay: float, timeout: int, retries: int):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.user_agent, self.delay = user_agent, delay
        self.timeout, self.retries = timeout, retries
        self._robots: dict[str, RobotFileParser] = {}
        self._last: dict[str, float] = {}

    def _robots_for(self, url: str) -> RobotFileParser:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp = RobotFileParser()
            try:
                r = self.session.get(f"{origin}/robots.txt", timeout=self.timeout)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                elif r.status_code in (401, 403):
                    rp.disallow_all = True
                else:                      # 404 dll: tidak ada aturan
                    rp.allow_all = True
            except requests.RequestException:
                rp.disallow_all = True     # konservatif: jika tidak bisa cek, jangan crawl
            self._robots[origin] = rp
            log.info("robots.txt %s dimuat", origin)
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        return self._robots_for(url).can_fetch(self.user_agent, url)

    def _wait(self, url: str) -> None:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        rp = self._robots_for(url)
        delay = max(self.delay, float(rp.crawl_delay(self.user_agent) or 0))
        gap = time.time() - self._last.get(origin, 0)
        if gap < delay:
            time.sleep(delay - gap)
        self._last[origin] = time.time()

    def get(self, url: str) -> str | None:
        if not self.allowed(url):
            log.warning("DILARANG robots.txt, dilewati: %s", url)
            return None
        for attempt in range(1, self.retries + 1):
            self._wait(url)
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 200:
                    r.encoding = r.encoding or r.apparent_encoding
                    return r.text
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(5 * attempt)   # backoff
                    continue
                log.warning("HTTP %s: %s", r.status_code, url)
                return None
            except requests.RequestException as e:
                log.warning("Gagal (%s) percobaan %d: %s", e, attempt, url)
                time.sleep(3 * attempt)
        return None


# --------------------------------------------------------------- discovery
def discover(fetcher: PoliteFetcher, src: dict):
    """Telusuri halaman daftar, hasilkan URL artikel unik secara berurutan."""
    pattern = re.compile(src["article_url_regex"])
    seen: set[str] = set()
    start = src.get("start_page", 1)
    for page in range(start, start + src.get("max_pages", 50)):
        list_url = src["list_url_template"].format(page=page)
        html = fetcher.get(list_url)
        if html is None:
            break
        soup = BeautifulSoup(html, "lxml")
        new = []
        for a in soup.find_all("a", href=True):
            u = urldefrag(urljoin(list_url, a["href"]))[0]
            if pattern.match(u) and u not in seen:
                seen.add(u)
                new.append(u)
        log.info("halaman %d: %d URL baru", page, len(new))
        if not new:          # halaman kosong/berulang -> selesai
            break
        yield from new


# ------------------------------------------------------------- extraction
def parse_date(strings) -> str | None:
    for s in strings:
        m = DATE_RE.search(s)
        if m and m.group(2)[:3].lower() in MONTHS:
            d, mon, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
            return f"{y:04d}-{mon:02d}-{d:02d}"
    return None


def labeled_value(soup: BeautifulSoup, label: str) -> str | None:
    """Ambil teks setelah node yang memuat label (mis. 'Penulis Medis')."""
    node = soup.find(string=re.compile(re.escape(label)))
    if not node:
        return None
    inline = node.strip()
    if ":" in inline and inline.split(":", 1)[1].strip():
        return inline.split(":", 1)[1].strip()
    for s in node.find_all_next(string=True, limit=6):
        s = s.strip(" :\n\t")
        if s and label not in s:
            return s
    return None


def meta(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", attrs={"property": key})
    return tag["content"].strip() if tag and tag.get("content") else None


def extract(html: str, url: str, src: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one(src.get("title_selector", "h1"))
    title = h1.get_text(" ", strip=True) if h1 else meta(soup, "og:title")

    date = parse_date(s.strip() for s in h1.find_all_next(string=True, limit=15)) if h1 else None
    labels = src.get("labels", {})
    author = labeled_value(soup, labels["author"]) if "author" in labels else None
    reviewed_raw = labeled_value(soup, labels["reviewed"]) if "reviewed" in labels else None

    node = None
    for sel in src.get("content_selectors", []):
        node = soup.select_one(sel)
        if node:
            break
    node = node or soup.body
    for sel in src.get("remove_selectors", []):
        for t in node.select(sel):
            t.decompose()

    lines = [re.sub(r"\s+", " ", l).strip() for l in node.get_text("\n").splitlines()]
    lines = [l for l in lines if l]

    refs: list[str] = []
    ref_re = re.compile(src["references_heading"]) if src.get("references_heading") else None
    if ref_re:
        for i, l in enumerate(lines):
            if ref_re.match(l):
                refs, lines = lines[i + 1:], lines[:i]
                break

    drops = [re.compile(p) for p in src.get("drop_line_patterns", [])]
    lines = [l for l in lines if not any(p.search(l) for p in drops)]
    if title and lines and lines[0] == title:
        lines = lines[1:]

    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:12],
        "url": url,
        "source": src["_name"],
        "title": title,
        "date_published": date,
        "author": author,
        "date_reviewed": parse_date([reviewed_raw]) if reviewed_raw else None,
        "meta_description": meta(soup, "description"),
        "keywords": meta(soup, "keywords"),
        "text": "\n".join(lines),
        "references": refs,
        "license_note": src.get("license_note", ""),
        "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# -------------------------------------------------------------------- main
def crawl_source(name: str, cfg: dict, limit: int | None) -> None:
    d = cfg["defaults"]
    src = {**cfg["sources"][name], "_name": name}
    fetcher = PoliteFetcher(d["user_agent"], d["delay_seconds"], d["timeout"], d["retries"])

    out = Path(d["out_dir"]) / f"{name}.jsonl"
    cache = Path(d["html_cache_dir"]) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    done = set()
    if out.exists():
        done = {json.loads(l)["url"] for l in out.open(encoding="utf-8")}
    log.info("[%s] sudah ada %d artikel (resume)", name, len(done))

    n_new = 0
    with out.open("a", encoding="utf-8") as f:
        for url in discover(fetcher, src):
            if url in done:
                continue
            if limit is not None and n_new >= limit:
                break
            cache_file = cache / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.html"
            if cache_file.exists():
                html = cache_file.read_text(encoding="utf-8")
            else:
                html = fetcher.get(url)
                if html is None:
                    continue
                cache_file.write_text(html, encoding="utf-8")
            rec = extract(html, url, src)
            if len(rec["text"]) < d["min_chars"]:
                log.warning("teks terlalu pendek (%d), dilewati: %s", len(rec["text"]), url)
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_new += 1
            log.info("[%s] %d. %s (%d karakter)", name, n_new, rec["title"], len(rec["text"]))
    log.info("[%s] selesai, %d artikel baru -> %s", name, n_new, out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sources.yaml")
    ap.add_argument("--source", help="nama sumber di sources.yaml")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, help="maks artikel baru per sumber (untuk uji)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    names = list(cfg["sources"]) if args.all else [args.source]
    if not names or names == [None]:
        ap.error("pilih --source NAMA atau --all")
    for n in names:
        crawl_source(n, cfg, args.limit)


if __name__ == "__main__":
    main()
