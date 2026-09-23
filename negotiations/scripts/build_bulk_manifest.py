"""Consolidate heterogeneous acquisition batches into one verified PDF manifest."""
from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
META = ROOT / "METADATA" / "batches_bulk"
FIELDS = ["SOURCE_ID", "TITLE", "ORGANIZATION", "URL", "FILE_PATH", "SHA256",
          "FILE_SIZE_BYTES", "STREAM", "ACCESS_DATE", "ACCESS_STATUS", "LICENSE", "PROVENANCE"]


def digest(path: Path) -> str:
    if re.fullmatch(r"[0-9a-fA-F]{64}", path.stem):
        return path.stem.lower()
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def load_records() -> dict[str, dict]:
    by_hash: dict[str, dict] = {}
    paths = [ROOT / "negotiation_sources.csv"]
    paths += sorted(META.glob("government_bulk_*.csv"))
    paths += [META / "international_bulk_manifest.csv"]
    paths += [p for p in META.glob("europepmc_*.csv") if "checkpoint" not in p.name]
    for path in paths:
        if not path.is_file() or "failures" in path.name:
            continue
        with path.open(encoding="utf-8-sig", newline="", errors="replace") as handle:
            for row in csv.DictReader(handle):
                sha = get(row, "SHA256", "sha256").lower()
                if sha:
                    by_hash.setdefault(sha, row)
    return by_hash


def main() -> None:
    records = load_records()
    rows = []
    seen = set()
    for path in sorted(ROOT.rglob("*.pdf")):
        if "release_shards" in path.parts or path.read_bytes()[:5] != b"%PDF-":
            continue
        sha = digest(path)
        if sha in seen:
            continue
        seen.add(sha)
        src = records.get(sha, {})
        rel = path.relative_to(REPO).as_posix()
        stream = next((part for part in path.parts if part.endswith("_BULK")), "SEED")
        rows.append({
            "SOURCE_ID": get(src, "SOURCE_ID", "source_id", "openalex_id", "pmcid") or f"SHA256-{sha[:16]}",
            "TITLE": get(src, "TITLE", "title"),
            "ORGANIZATION": get(src, "ORGANIZATION", "organization", "host_source", "SOURCE_COLLECTION"),
            "URL": get(src, "URL", "url", "pdf_url", "resolved_url", "landing_url"),
            "FILE_PATH": rel, "SHA256": sha, "FILE_SIZE_BYTES": path.stat().st_size,
            "STREAM": stream, "ACCESS_DATE": get(src, "ACCESS_DATE", "access_date"),
            "ACCESS_STATUS": "ACQUIRED", "LICENSE": get(src, "LICENSE", "license", "COPYRIGHT_STATUS"),
            "PROVENANCE": get(src, "PROVENANCE", "provenance", "listing_url", "host_source"),
        })
    out = ROOT / "negotiation_bulk_sources.csv"
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(rows)
    try:
        import pandas as pd
        pd.DataFrame(rows, columns=FIELDS).to_parquet(ROOT / "negotiation_bulk_sources.parquet", index=False)
    except (ImportError, ValueError, OSError):
        pass
    print(f"Wrote {len(rows)} globally unique verified PDF records")


if __name__ == "__main__":
    main()
