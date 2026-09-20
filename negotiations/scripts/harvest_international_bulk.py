#!/usr/bin/env python3
"""Resumable acquisition of negotiation-relevant PDFs from official repositories.

The default provider is the World Bank Documents & Reports API.  It exposes
stable metadata and direct official PDF URLs at sufficient scale to bootstrap
the international/public-law/procurement portion of the corpus.  Additional
official repositories are represented in ``international_sources.json`` and
can be supplied as URL-list exports with ``--url-list``.

Only native PDFs with a valid PDF signature and EOF marker are accepted.
Content is deduplicated by SHA-256.  Checkpoints are append-only JSONL so a
stopped run can resume without re-downloading successful URLs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "INTERNATIONAL_BULK"
DEFAULT_META = ROOT / "METADATA" / "batches_bulk"
API = "https://search.worldbank.org/api/v2/wds"
UA = "donna-negotiation-corpus/1.0 (research dataset; respectful bulk retrieval)"
QUERIES = (
    "negotiation", "bargaining", "mediation", "collective bargaining",
    "peace agreement", "dispute settlement", "trade negotiation",
    "contract negotiation", "procurement negotiation", "social dialogue",
)


def clean(value: Any) -> str:
    if isinstance(value, dict):
        value = next(iter(value.values()), "")
        if isinstance(value, dict):
            value = next(iter(value.values()), "")
    return re.sub(r"\s+", " ", str(value or "")).strip()


def discover_world_bank(limit: int) -> list[dict[str, Any]]:
    """Return distinct public records from the official WDS search API."""
    found: dict[str, dict[str, Any]] = {}
    session = requests.Session()
    session.headers["User-Agent"] = UA
    per_query = max(500, min(5000, limit))
    for query in QUERIES:
        if len(found) >= limit * 2:
            break
        response = session.get(API, params={"format": "json", "qterm": query,
                               "rows": per_query, "os": 0}, timeout=90)
        response.raise_for_status()
        for record in response.json().get("documents", {}).values():
            url = clean(record.get("pdfurl")).replace("http://", "https://", 1)
            if not url or clean(record.get("seccl")).lower() not in ("", "public"):
                continue
            found.setdefault(url, {
                "provider": "World Bank Documents & Reports",
                "organization": "World Bank",
                "query": query,
                "url": url,
                "record_id": clean(record.get("id")),
                "title": clean(record.get("display_title") or record.get("docna") or record.get("repnme")),
                "date": clean(record.get("docdt"))[:10],
                "language": clean(record.get("lang")) or "Unknown",
                "country": clean(record.get("count")) or "International",
                "source_type": clean(record.get("docty")) or "Official publication",
                "document_number": clean(record.get("repnb")),
                "topics": clean(record.get("subtopic") or record.get("teratopic")),
                "listing_url": clean(record.get("url") or record.get("url_friendly_title")),
            })
    return list(found.values())[: limit * 2]


def load_url_list(path: Path) -> list[dict[str, Any]]:
    """Read CSV/JSONL exports from any official repository.

    Required column: url. Recommended: provider, organization, title, date,
    language, country, source_type, document_number, topics, listing_url.
    """
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def prior_state(checkpoint: Path) -> tuple[set[str], set[str]]:
    urls, hashes = set(), set()
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == "ACQUIRED":
                urls.add(row.get("url", "")); hashes.add(row.get("sha256", ""))
    return urls, hashes


def validate_pdf(data: bytes) -> tuple[bool, str]:
    if not data.startswith(b"%PDF-"):
        return False, "bad_signature"
    if b"%%EOF" not in data[-65536:]:
        return False, "missing_eof"
    if len(data) < 512:
        return False, "too_small"
    return True, ""


def fetch_one(item: dict[str, Any], temp_dir: Path, max_bytes: int) -> dict[str, Any]:
    url = item["url"]
    result = dict(item)
    try:
        with requests.get(url, headers={"User-Agent": UA, "Accept": "application/pdf"},
                          timeout=(20, 120), stream=True, allow_redirects=True) as response:
            response.raise_for_status()
            length = int(response.headers.get("content-length", "0") or 0)
            if length > max_bytes:
                raise ValueError(f"content_length_exceeds_limit:{length}")
            digest = hashlib.sha256(); size = 0
            tmp = temp_dir / (hashlib.sha1(url.encode()).hexdigest() + ".part")
            with tmp.open("wb") as handle:
                for chunk in response.iter_content(256 * 1024):
                    if not chunk: continue
                    size += len(chunk)
                    if size > max_bytes: raise ValueError(f"stream_exceeds_limit:{size}")
                    digest.update(chunk); handle.write(chunk)
            data = tmp.read_bytes()
            valid, error = validate_pdf(data)
            if not valid: raise ValueError(error)
            result.update(status="DOWNLOADED", sha256=digest.hexdigest(), bytes=size,
                          temp_path=str(tmp), resolved_url=response.url,
                          content_type=response.headers.get("content-type", ""))
    except Exception as exc:
        try:
            if "tmp" in locals(): tmp.unlink(missing_ok=True)
        except OSError: pass
        result.update(status="FAILED", error=f"{type(exc).__name__}:{exc}")
    return result


def manifest_row(item: dict[str, Any], target: Path) -> dict[str, str]:
    fields = (ROOT / "METADATA" / "manifest_fields.txt").read_text(encoding="utf-8").splitlines()
    row = {key: "" for key in fields}
    dt = clean(item.get("date"))
    source_id = "INT-WB-" + clean(item.get("record_id") or item["sha256"][:16])
    row.update(SOURCE_ID=source_id, TITLE=clean(item.get("title")),
               ORGANIZATION=clean(item.get("organization")), DATE=dt,
               YEAR=dt[:4], LANGUAGE=clean(item.get("language")),
               COUNTRY=clean(item.get("country")), SOURCE_TYPE=clean(item.get("source_type")),
               DOMAIN="INTERNATIONAL", SUBDOMAIN="official negotiation-related publication",
               TOPICS=clean(item.get("topics")), URL=item["url"],
               DOCUMENT_NUMBER=clean(item.get("document_number")),
               FILE_PATH=target.relative_to(ROOT.parent).as_posix(), FILE_FORMAT="PDF",
               PRIMARY_SOURCE="TRUE", SECONDARY_SOURCE="FALSE", ORIGINAL_SOURCE="TRUE",
               HOST_SOURCE=clean(item.get("provider")), ACCESS_DATE=str(date.today()),
               ACCESS_STATUS="ACQUIRED", COPYRIGHT_STATUS="Official public-access document; rights retained by issuer",
               LICENSE="No license inference", TRAINING_PERMISSION="REVIEW_REQUIRED",
               PROVENANCE=f"Downloaded from official URL; API/listing: {clean(item.get('listing_url'))}",
               RELIABILITY_SCORE="5", TRAINING_VALUE="4", UNIQUENESS_SCORE="4",
               SOURCE_QUALITY="5", DUPLICATE_STATUS="UNIQUE_BY_SHA256",
               NOTES=f"Discovery query: {clean(item.get('query'))}", SHA256=item["sha256"],
               FILE_SIZE_BYTES=str(item["bytes"]), AUTHORITY="5", PRIMARY_SOURCE_VALUE="4",
               RELIABILITY="5", NEGOTIATION_RELEVANCE="4", INFORMATION_DENSITY="4",
               UNIQUENESS="4")
    return row


def write_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    fields = (ROOT / "METADATA" / "manifest_fields.txt").read_text(encoding="utf-8").splitlines()
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=2500)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--max-mb", type=int, default=60)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_META)
    parser.add_argument("--url-list", action="append", type=Path, default=[])
    parser.add_argument("--discover-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True); args.metadata.mkdir(parents=True, exist_ok=True)
    temp_dir = args.output / ".partial"; temp_dir.mkdir(exist_ok=True)
    checkpoint = args.metadata / "international_bulk_checkpoint.jsonl"
    manifest = args.metadata / "international_bulk_manifest.csv"
    failures = args.metadata / "international_bulk_failures.csv"

    candidates = discover_world_bank(args.target)
    for source in args.url_list: candidates.extend(load_url_list(source))
    dedup = {clean(row.get("url")): row for row in candidates if clean(row.get("url"))}
    candidates = list(dedup.values())
    discovery = args.metadata / "international_bulk_discovery.jsonl"
    discovery.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in candidates), encoding="utf-8")
    if args.discover_only:
        print(json.dumps({"discovered": len(candidates), "file": str(discovery)})); return 0

    done_urls, known_hashes = prior_state(checkpoint)
    rows: list[dict[str, str]] = []
    failures_out: list[dict[str, Any]] = []
    # Rehydrate existing accepted checkpoint entries for deterministic manifest rebuilding.
    for line in checkpoint.read_text(encoding="utf-8").splitlines() if checkpoint.exists() else []:
        item = json.loads(line)
        if item.get("status") == "ACQUIRED" and Path(item.get("file_path", "")).exists():
            rows.append(manifest_row(item, Path(item["file_path"])))
    remaining = [x for x in candidates if x["url"] not in done_urls]
    accepted = len(rows)
    with checkpoint.open("a", encoding="utf-8", buffering=1) as log:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            # Bound in-flight work.  This avoids thousands of orphan .part files
            # and prevents one slow URL from blocking completed results behind it.
            cursor = 0
            pending: dict[concurrent.futures.Future, dict[str, Any]] = {}
            while accepted < args.target and (cursor < len(remaining) or pending):
                while cursor < len(remaining) and len(pending) < args.workers * 2:
                    candidate = remaining[cursor]; cursor += 1
                    future = pool.submit(fetch_one, candidate, temp_dir, args.max_mb * 1024 * 1024)
                    pending[future] = candidate
                done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    pending.pop(future, None); item = future.result()
                    if item["status"] == "DOWNLOADED":
                        if item["sha256"] in known_hashes:
                            Path(item["temp_path"]).unlink(missing_ok=True)
                            item.update(status="DUPLICATE", error="duplicate_sha256")
                        else:
                            host = re.sub(r"[^a-z0-9]+", "_", clean(item.get("organization")).lower()).strip("_") or "official"
                            folder = args.output / host; folder.mkdir(exist_ok=True)
                            target = folder / f"{item['sha256']}.pdf"
                            os.replace(item.pop("temp_path"), target)
                            item.update(status="ACQUIRED", file_path=str(target.resolve()))
                            known_hashes.add(item["sha256"]); rows.append(manifest_row(item, target)); accepted += 1
                    if item["status"] not in ("ACQUIRED",): failures_out.append(item)
                    log.write(json.dumps(item, ensure_ascii=False) + "\n")
                    if accepted and accepted % 100 == 0:
                        write_manifest(manifest, rows[:args.target])
                        print(f"accepted={accepted}", flush=True)
                    if accepted >= args.target: break

    write_manifest(manifest, rows[:args.target])
    failure_fields = sorted({key for row in failures_out for key in row})
    with failures.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=failure_fields, extrasaction="ignore"); writer.writeheader(); writer.writerows(failures_out)
    print(json.dumps({"discovered": len(candidates), "verified_unique_pdfs": min(len(rows), args.target),
                      "failures_or_duplicates": len(failures_out), "manifest": str(manifest)}))
    return 0 if len(rows) >= args.target else 2


if __name__ == "__main__":
    raise SystemExit(main())
