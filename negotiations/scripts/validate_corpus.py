"""Validate acquired objects and provenance without altering originals."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "negotiation_sources.csv"
REQUIRED = ("SOURCE_ID", "TITLE", "URL", "ACCESS_DATE", "ACCESS_STATUS", "PROVENANCE")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    issues: list[dict[str, str]] = []
    hashes: dict[str, str] = {}
    if not MANIFEST.exists():
        raise SystemExit("Run merge_manifests.py first")
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        sid = row.get("SOURCE_ID", "")
        for field in REQUIRED:
            if not row.get(field):
                issues.append({"source_id": sid, "issue": f"missing {field}"})
        rel = row.get("FILE_PATH", "")
        if not rel:
            if row.get("ACCESS_STATUS") == "ACQUIRED":
                issues.append({"source_id": sid, "issue": "ACQUIRED record has no FILE_PATH"})
            continue
        # Batch producers may use either corpus-relative paths (PAPERS/...) or
        # repository-relative paths (negotiations/PAPERS/...). Accept both.
        path = (ROOT.parent / rel) if rel.replace("\\", "/").startswith("negotiations/") else (ROOT / rel)
        if not path.is_file():
            issues.append({"source_id": sid, "issue": f"missing file: {rel}"})
            continue
        if path.suffix.lower() == ".pdf" and path.read_bytes()[:5] != b"%PDF-":
            issues.append({"source_id": sid, "issue": "invalid PDF signature"})
        actual = digest(path)
        expected = row.get("SHA256", "").lower()
        if expected and actual != expected:
            issues.append({"source_id": sid, "issue": "SHA256 mismatch"})
        if actual in hashes and hashes[actual] != sid:
            issues.append({"source_id": sid, "issue": f"duplicate bytes of {hashes[actual]}"})
        hashes.setdefault(actual, sid)
    report = {"records": len(rows), "files": len(hashes), "issues": issues}
    (ROOT / "METADATA" / "validation_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"records": len(rows), "files": len(hashes), "issue_count": len(issues)}, indent=2))
    raise SystemExit(1 if issues else 0)


if __name__ == "__main__":
    main()
