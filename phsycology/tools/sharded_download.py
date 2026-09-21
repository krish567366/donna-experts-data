#!/usr/bin/env python3
"""Download one deterministic shard without rewriting the shared manifest."""
from __future__ import annotations

import argparse, csv, email.utils, hashlib, json, os, random, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ALLOW = {"cc-by", "cc-by-sa", "cc0", "public-domain"}
USER_AGENT = "DonnaPsychologyCorpus/1.1"
RESULT_FIELDS = ["SOURCE_ID", "STATUS", "FILE_PATH", "FILE_SHA256", "FILE_BYTES", "ACQUIRED_AT", "ERROR"]

def now(): return datetime.now(timezone.utc).isoformat()

def select_shard(rows, shard_index, shard_count):
    """Stable ID hashing makes shards disjoint despite manifest reordering."""
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("shard-index must be in [0, shard-count)")
    return [r for r in rows if r.get("SOURCE_ID", "").strip() and
            int(hashlib.sha256(r["SOURCE_ID"].strip().encode()).hexdigest(), 16) % shard_count == shard_index]

def content_url(row, api_key=""):
    if api_key:
        return f"https://content.openalex.org/works/{urllib.parse.quote(row['SOURCE_ID'])}.pdf?api_key={urllib.parse.quote(api_key)}"
    return row.get("OA_PDF_URL", "").strip()

def safe_retrieval_url(url):
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in {"api_key", "key", "token"}]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment))

def retry_delay(error, attempt):
    value = error.headers.get("Retry-After") if isinstance(error, urllib.error.HTTPError) else None
    if value:
        try: return max(0.0, float(value))
        except ValueError:
            try: return max(0.0, (email.utils.parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError): pass
    return min(60.0, 2 ** attempt) + random.random()

class RateLimiter:
    def __init__(self, requests_per_second):
        self.interval = 0 if requests_per_second <= 0 else 1 / requests_per_second
        self.next_at = 0.0
        self.lock = threading.Lock()
    def wait(self):
        with self.lock:
            delay = max(0.0, self.next_at - time.monotonic())
            if delay: time.sleep(delay)
            self.next_at = time.monotonic() + self.interval

def atomic_json(path, value):
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    part.replace(path)

def hash_pdf(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-": raise ValueError("existing file is not a PDF")
        handle.seek(0)
        while chunk := handle.read(1024 * 1024): digest.update(chunk); size += len(chunk)
    return digest.hexdigest(), size

def download_one(row, root, api_key, timeout, retries, limiter, shard_index):
    source_id = row.get("SOURCE_ID", "").strip()
    result = dict.fromkeys(RESULT_FIELDS, "") | {"SOURCE_ID": source_id, "FILE_PATH": row.get("FILE_PATH", "")}
    if row.get("LICENSE", "").lower() not in ALLOW:
        return result | {"STATUS": "SKIPPED_LICENSE", "ERROR": "license not allow-listed"}
    relative = Path(row.get("FILE_PATH") or f"corpus/{source_id}/original.pdf")
    if relative.is_absolute() or ".." in relative.parts:
        return result | {"STATUS": "SKIPPED_PATH", "ERROR": "unsafe FILE_PATH"}
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            digest, size = hash_pdf(destination)
            return result | {"STATUS": "EXISTS", "FILE_SHA256": digest, "FILE_BYTES": str(size)}
        except ValueError as exc: return result | {"STATUS": "INVALID_EXISTING", "ERROR": str(exc)}
    url = content_url(row, api_key)
    if not url: return result | {"STATUS": "NO_URL", "ERROR": "no acquisition URL"}
    part = destination.with_name(destination.name + f".shard-{shard_index}.part")
    try:
        for attempt in range(retries):
            try:
                limiter.wait()
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
                digest, size = hashlib.sha256(), 0
                with urllib.request.urlopen(request, timeout=timeout) as source, part.open("wb") as output:
                    head = source.read(5)
                    if head != b"%PDF-": raise ValueError("response is not a PDF")
                    output.write(head); digest.update(head); size += len(head)
                    while chunk := source.read(1024 * 1024): output.write(chunk); digest.update(chunk); size += len(chunk)
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                part.unlink(missing_ok=True)
                if isinstance(exc, urllib.error.HTTPError) and exc.code not in (429, 500, 502, 503, 504): raise
                if attempt + 1 == retries: raise
                time.sleep(retry_delay(exc, attempt))
        part.replace(destination)
        acquired_at = now()
        metadata = dict(row) | {"FILE_SHA256": digest.hexdigest(), "FILE_BYTES": str(size), "ACQUIRED_AT": acquired_at, "ACCESS_STATUS": "ACQUIRED"}
        atomic_json(destination.parent / "metadata.json", metadata)
        atomic_json(destination.parent / "provenance.json", {"retrieved_at": acquired_at,
            "retrieval_url": safe_retrieval_url(url), "sha256": digest.hexdigest(), "bytes": size,
            "license": row.get("LICENSE", ""), "openalex_id": row.get("OPENALEX_ID", ""),
            "source_manifest": str(root / "psychology_sources.csv"), "shard_index": shard_index})
        return result | {"STATUS": "ACQUIRED", "FILE_SHA256": digest.hexdigest(), "FILE_BYTES": str(size), "ACQUIRED_AT": acquired_at}
    except Exception as exc:
        part.unlink(missing_ok=True)
        return result | {"STATUS": "FAILED", "ERROR": f"{type(exc).__name__}: {exc}"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "psychology_sources.csv")
    parser.add_argument("--shard-index", type=int, required=True); parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--workers", type=int, default=4); parser.add_argument("--rate", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=0); parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--retries", type=int, default=8); parser.add_argument("--api-key", default=os.getenv("OPENALEX_API_KEY", ""))
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args()
    if args.workers < 1 or args.retries < 1: parser.error("workers and retries must be positive")
    manifest, results = args.manifest.resolve(), []
    with manifest.open(encoding="utf-8", newline="") as handle:
        rows = select_shard(list(csv.DictReader(handle)), args.shard_index, args.shard_count)
    if args.limit: rows = rows[:args.limit]
    limiter = RateLimiter(args.rate)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, row, manifest.parent, args.api_key, args.timeout,
                               args.retries, limiter, args.shard_index) for row in rows]
        for future in as_completed(futures): results.append(future.result())
    results.sort(key=lambda item: item["SOURCE_ID"])
    results_dir = (args.results_dir or manifest.parent / "logs" / "shards").resolve(); results_dir.mkdir(parents=True, exist_ok=True)
    target = results_dir / f"download-shard-{args.shard_index:04d}-of-{args.shard_count:04d}.csv"; part = target.with_suffix(".csv.part")
    with part.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS); writer.writeheader(); writer.writerows(results)
    part.replace(target)
    counts = {status: sum(r["STATUS"] == status for r in results) for status in sorted({r["STATUS"] for r in results})}
    print(json.dumps({"shard": args.shard_index, "of": args.shard_count, "selected": len(rows), "results": str(target), "counts": counts}, indent=2))

if __name__ == "__main__": main()
