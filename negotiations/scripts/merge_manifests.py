"""Merge independently produced acquisition manifests into deterministic masters."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "METADATA"
FIELDS = (META / "manifest_fields.txt").read_text(encoding="utf-8").splitlines()


def load_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in batch_paths():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                clean = {field: (row.get(field) or "").strip() for field in FIELDS}
                clean["_BATCH"] = path.name
                rows.append(clean)
    return rows


def batch_paths() -> list[Path]:
    """Return acquisition manifests, excluding retry/failure ledgers."""
    return [path for path in sorted((META / "batches").glob("*.csv")) if not path.stem.endswith("_failures")]


def main() -> None:
    rows = load_rows()
    seen: dict[str, str] = {}
    for row in rows:
        digest = row.get("SHA256", "")
        if digest:
            row["DUPLICATE_STATUS"] = f"DUPLICATE_OF:{seen[digest]}" if digest in seen else row["DUPLICATE_STATUS"]
            seen.setdefault(digest, row["SOURCE_ID"])
    rows.sort(key=lambda r: (r["SOURCE_ID"], r["TITLE"]))
    with (ROOT / "negotiation_sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    try:
        import pandas as pd
        pd.DataFrame(rows, columns=FIELDS).to_parquet(ROOT / "negotiation_sources.parquet", index=False)
    except (ImportError, ValueError, OSError) as exc:
        (META / "parquet_status.json").write_text(json.dumps({"created": False, "reason": str(exc)}, indent=2), encoding="utf-8")
    print(f"Merged {len(rows)} records from {len(batch_paths())} batches")


if __name__ == "__main__":
    main()
