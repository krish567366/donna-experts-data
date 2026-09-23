#!/usr/bin/env python3
"""Resumable acquisition of public-domain U.S. government negotiation PDFs.

The default FRUS source discovers every published volume from the Department of
State sitemap. Additional sitemap/page seeds may be supplied on the command line.
Only responses whose decoded body begins with the PDF magic signature are kept.
Run state lives in SQLite, CSV manifests are sharded, and objects are deduplicated
by SHA-256. The downloader deliberately does not evade robots, authentication,
rate limits, or other access controls.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "GOVERNMENT_BULK"
META = ROOT / "METADATA" / "batches_bulk"
STATE = META / "government_bulk_state.sqlite3"
UA = "donna-negotiation-corpus/1.0 (research archiver; contact via repository)"
PDF_RE = re.compile(r'''href=["']([^"']+\.pdf(?:\?[^"']*)?)["']''', re.I)
FRUS_PAGE_RE = re.compile(r"^https://history\.state\.gov/historicaldocuments/(frus[^/?#]+)$")
FIELDS = ["SOURCE_ID", "TITLE", "URL", "ACCESS_DATE", "ACCESS_STATUS", "PROVENANCE",
          "FILE_PATH", "SHA256", "BYTES", "CONTENT_TYPE", "HTTP_STATUS", "SOURCE_COLLECTION"]


def request(url: str, timeout: int = 90) -> tuple[bytes, dict, int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,text/html,application/xml;q=0.9,*/*;q=0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read(), dict(res.headers), res.status, res.geturl()


def xml_locs(blob: bytes) -> list[str]:
    root = ET.fromstring(blob)
    return [(node.text or "").strip() for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]


def discover_frus(db: sqlite3.Connection) -> int:
    # The ebook catalogue is a compact authoritative index. The site's sitemap
    # index currently contains >16,000 very small shards, so traversing it is an
    # unnecessarily expensive load on the public service.
    catalogue = "https://history.state.gov/historicaldocuments/ebooks"
    index, _, _, final = request(catalogue)
    page_re = re.compile(r'''href=["']([^"']*/historicaldocuments/frus[^/"'#?]+)["']''', re.I)
    pages = {urllib.parse.urljoin(final, html.unescape(x)) for x in page_re.findall(index.decode("utf-8", "replace"))}
    print(f"discovery: catalogue contains {len(pages)} volume pages")
    found = 0
    for page in sorted(pages):
        match = FRUS_PAGE_RE.match(page)
        if not match:
            continue
        volume = match.group(1)
        # This is the stable URL scheme used by the catalogue's own PDF buttons.
        # Validation during acquisition catches unavailable legacy volumes.
        url = f"https://static.history.state.gov/frus/{volume}/pdf/{volume}.pdf"
        db.execute("INSERT OR IGNORE INTO queue(url,title,collection,provenance,status) VALUES(?,?,?,?, 'pending')",
                   (url, f"Foreign Relations of the United States: {volume}", "FRUS", page))
        found += 1
    db.commit()
    return found


def discover_sitemap(db: sqlite3.Connection, seed: str, collection: str, keywords: list[str]) -> int:
    """Discover PDF URLs from a sitemap or sitemap index without crawling pages."""
    pending, seen, pdfs = [seed], set(), set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            body, _, _, _ = request(url)
            locs = xml_locs(body)
        except Exception as exc:
            print(f"sitemap failure {url}: {exc}", file=sys.stderr)
            continue
        for loc in locs:
            clean = loc.lower().split("?", 1)[0]
            if clean.endswith((".xml", ".xml.gz")) and len(seen) + len(pending) < 10000:
                pending.append(loc)
            elif clean.endswith(".pdf") and (not keywords or any(k in clean for k in keywords)):
                pdfs.add(loc)
    for url in sorted(pdfs):
        title = Path(urllib.parse.urlsplit(url).path).stem.replace("-", " ").replace("_", " ")
        db.execute("INSERT OR IGNORE INTO queue(url,title,collection,provenance,status) VALUES(?,?,?,?, 'pending')",
                   (url, title, collection, seed))
    db.commit()
    return len(pdfs)


def discover_page_sitemap(db: sqlite3.Connection, seed: str, collection: str, workers: int) -> int:
    """Fetch pages listed by a sitemap and retain only explicitly linked PDFs."""
    body, _, _, _ = request(seed)
    pages = xml_locs(body)
    found: set[tuple[str, str]] = set()
    def scan(page: str) -> list[str]:
        try:
            blob, _, _, final = request(page, 45)
            return [urllib.parse.urljoin(final, html.unescape(x)) for x in PDF_RE.findall(blob.decode("utf-8", "replace"))]
        except Exception:
            return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as pool:
        futures = {pool.submit(scan, page): page for page in pages}
        for n, future in enumerate(as_completed(futures), 1):
            page = futures[future]
            found.update((url, page) for url in future.result())
            if n % 100 == 0:
                print(f"page discovery: {n}/{len(pages)}, {len(found)} PDF links")
    for url, page in sorted(found):
        title = Path(urllib.parse.urlsplit(url).path).stem.replace("-", " ").replace("_", " ")
        db.execute("INSERT OR IGNORE INTO queue(url,title,collection,provenance,status) VALUES(?,?,?,?, 'pending')",
                   (url, title, collection, page))
    db.commit()
    return len(found)


def discover_wordpress_media(db: sqlite3.Connection, base: str, collection: str, workers: int) -> int:
    """Use a public WordPress media API to enumerate original PDF objects."""
    endpoint = base.rstrip("/") + "/wp-json/wp/v2/media"
    first, headers, _, _ = request(endpoint + "?per_page=100&page=1", 60)
    pages = int(headers.get("X-WP-TotalPages", "1"))
    payloads = [json.loads(first)]
    def page(n: int) -> list[dict]:
        blob, _, _, _ = request(endpoint + f"?per_page=100&page={n}", 60)
        return json.loads(blob)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12))) as pool:
        payloads.extend(f.result() for f in as_completed([pool.submit(page, n) for n in range(2, pages + 1)]))
    found = 0
    for media in (item for payload in payloads for item in payload):
        url = media.get("source_url", "")
        if not urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
            continue
        title = html.unescape(media.get("title", {}).get("rendered", "")) or Path(urllib.parse.urlsplit(url).path).stem
        provenance = media.get("link", endpoint)
        db.execute("INSERT OR IGNORE INTO queue(url,title,collection,provenance,status) VALUES(?,?,?,?, 'pending')",
                   (url, re.sub("<[^>]+>", "", title), collection, provenance))
        found += 1
    db.commit()
    return found


def init_db() -> sqlite3.Connection:
    META.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE)
    db.execute("""CREATE TABLE IF NOT EXISTS queue(
      url TEXT PRIMARY KEY, title TEXT, collection TEXT, provenance TEXT,
      status TEXT NOT NULL, attempts INTEGER DEFAULT 0, error TEXT,
      sha256 TEXT, bytes INTEGER, file_path TEXT, content_type TEXT,
      http_status INTEGER, accessed TEXT, updated TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS queue_status ON queue(status)")
    db.commit()
    return db


def reconcile_objects(db: sqlite3.Connection) -> int:
    """Recover verified files left by interruption between atomic write and DB commit."""
    by_name: dict[str, tuple[str, str]] = {}
    for url, status in db.execute("SELECT url,status FROM queue"):
        by_name[Path(urllib.parse.urlsplit(url).path).name.lower()] = (url, status)
    fixed = 0
    for path in OUT.glob("*/*.pdf"):
        original = path.name.split("__", 1)[-1].lower()
        match = by_name.get(original)
        if not match or match[1] == "acquired":
            continue
        body = path.read_bytes()
        if not body.startswith(b"%PDF-") or b"%%EOF" not in body[-4096:]:
            continue
        sha = hashlib.sha256(body).hexdigest()
        db.execute("""UPDATE queue SET status='acquired',error=NULL,sha256=?,bytes=?,file_path=?,
                     content_type='application/pdf',http_status=200,accessed=?,updated=? WHERE url=?""",
                   (sha, len(body), path.relative_to(ROOT).as_posix(), date.today().isoformat(),
                    datetime.now(timezone.utc).isoformat(), match[0]))
        fixed += 1
        if fixed % 100 == 0:
            db.commit()
            export(db)
    db.commit()
    return fixed


def safe_name(url: str, sha: str) -> str:
    base = Path(urllib.parse.urlsplit(url).path).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.unquote(base))
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return f"{sha[:12]}__{base}"[:220]


def fetch_one(row: tuple, timeout: int) -> dict:
    url, title, collection, provenance, attempts = row
    result = {"url": url, "title": title, "collection": collection, "provenance": provenance,
              "accessed": date.today().isoformat(), "attempts": attempts + 1}
    try:
        body, headers, status, final = request(url, timeout)
        ctype = headers.get("Content-Type", "").split(";", 1)[0].lower()
        result.update(http_status=status, content_type=ctype, final_url=final, bytes=len(body))
        if not body.startswith(b"%PDF-"):
            raise ValueError(f"not a PDF (content-type={ctype}, prefix={body[:16]!r})")
        # A truncated transfer often retains the header; require an EOF marker near the end.
        if b"%%EOF" not in body[-4096:]:
            raise ValueError("PDF missing terminal %%EOF marker")
        sha = hashlib.sha256(body).hexdigest()
        folder = OUT / collection
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe_name(final, sha)
        tmp = target.with_suffix(target.suffix + f".{os.getpid()}.part")
        tmp.write_bytes(body)
        os.replace(tmp, target)
        result.update(status="acquired", sha256=sha,
                      file_path=target.relative_to(ROOT).as_posix())
    except urllib.error.HTTPError as exc:
        result.update(status="failed", http_status=exc.code, error=f"HTTP {exc.code}: {exc.reason}")
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return result


def download(db: sqlite3.Connection, workers: int, limit: int, retries: int, timeout: int) -> None:
    rows = db.execute("""SELECT url,title,collection,provenance,attempts FROM queue
                         WHERE status='pending' OR (status='failed' AND attempts < ?)
                         ORDER BY collection,url DESC LIMIT ?""", (retries, limit)).fetchall()
    print(f"download queue: {len(rows)}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, row, timeout): row[0] for row in rows}
        for n, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            db.execute("""UPDATE queue SET status=?,attempts=?,error=?,sha256=?,bytes=?,file_path=?,
                         content_type=?,http_status=?,accessed=?,updated=? WHERE url=?""",
                       (r["status"], r["attempts"], r.get("error"), r.get("sha256"), r.get("bytes"),
                        r.get("file_path"), r.get("content_type"), r.get("http_status"), r["accessed"],
                        datetime.now(timezone.utc).isoformat(), r["url"]))
            if n % 10 == 0:
                db.commit()
                print(f"downloaded/attempted: {n}/{len(rows)}")
            if n % 100 == 0:
                checkpoint = export(db)
                print(f"checkpoint: {json.dumps(checkpoint, sort_keys=True)}")
    db.commit()


def export(db: sqlite3.Connection, shard_size: int = 250) -> dict:
    rows = db.execute("SELECT url,title,collection,provenance,status,error,sha256,bytes,file_path,content_type,http_status,accessed FROM queue ORDER BY collection,url").fetchall()
    records = []
    seen_hash: set[str] = set()
    duplicates = 0
    for url, title, coll, prov, status, error, sha, size, path, ctype, http, accessed in rows:
        if status == "acquired" and sha in seen_hash:
            duplicates += 1
            status = "duplicate"
        elif sha:
            seen_hash.add(sha)
        sid = "USGOV-" + hashlib.sha256(url.encode()).hexdigest()[:16].upper()
        records.append({"SOURCE_ID": sid, "TITLE": title, "URL": url, "ACCESS_DATE": accessed or "",
                        "ACCESS_STATUS": status.upper(), "PROVENANCE": prov, "FILE_PATH": path or "",
                        "SHA256": sha or "", "BYTES": size or "", "CONTENT_TYPE": ctype or "",
                        "HTTP_STATUS": http or "", "SOURCE_COLLECTION": coll})
    for old in META.glob("government_bulk_*.csv"):
        old.unlink()
    for i in range(0, len(records), shard_size):
        target = META / f"government_bulk_{i // shard_size:05d}.csv"
        with target.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader(); w.writerows(records[i:i + shard_size])
    counts = {k: db.execute("SELECT count(*) FROM queue WHERE status=?", (k,)).fetchone()[0]
              for k in ("pending", "acquired", "failed")}
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "queued": len(rows), **counts,
               "unique_acquired_sha256": len(seen_hash), "duplicate_rows": duplicates,
               "bytes": db.execute("SELECT coalesce(sum(bytes),0) FROM queue WHERE status='acquired'").fetchone()[0]}
    (META / "government_bulk_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--discover-frus", action="store_true", help="discover State Department FRUS volume PDFs")
    p.add_argument("--sitemap", action="append", default=[], metavar="COLLECTION=URL")
    p.add_argument("--page-sitemap", action="append", default=[], metavar="COLLECTION=URL",
                   help="crawl sitemap-listed pages and extract their explicit PDF links")
    p.add_argument("--wordpress-media", action="append", default=[], metavar="COLLECTION=BASE_URL",
                   help="enumerate PDFs through a site's public WordPress media API")
    p.add_argument("--keywords", default="negotiat,diploma,treaty,agreement,settlement,mediat,conflict,peace,contract,bargain")
    p.add_argument("--download", action="store_true")
    p.add_argument("--reconcile", action="store_true", help="recover locally written PDFs into state DB")
    p.add_argument("--limit", type=int, default=2500)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--retries", type=int, default=3)
    p.add_argument("--timeout", type=int, default=120)
    args = p.parse_args()
    db = init_db()
    if args.reconcile:
        print(f"reconciled objects: {reconcile_objects(db)}")
    if args.discover_frus:
        print(f"FRUS candidates: {discover_frus(db)}")
    for spec in args.sitemap:
        coll, sep, url = spec.partition("=")
        if not sep: p.error("--sitemap must be COLLECTION=URL")
        print(f"{coll} candidates: {discover_sitemap(db, url, coll, args.keywords.lower().split(','))}")
    for spec in args.page_sitemap:
        coll, sep, url = spec.partition("=")
        if not sep: p.error("--page-sitemap must be COLLECTION=URL")
        print(f"{coll} candidates: {discover_page_sitemap(db, url, coll, args.workers)}")
    for spec in args.wordpress_media:
        coll, sep, url = spec.partition("=")
        if not sep: p.error("--wordpress-media must be COLLECTION=BASE_URL")
        print(f"{coll} candidates: {discover_wordpress_media(db, url, coll, args.workers)}")
    if args.download:
        download(db, max(1, min(args.workers, 16)), args.limit, args.retries, args.timeout)
    print(json.dumps(export(db), indent=2))


if __name__ == "__main__":
    main()
