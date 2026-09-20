#!/usr/bin/env python3
"""Validate the psychology corpus manifest and publish coverage reports."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "psychology_sources.csv"
DEFAULT_TAXONOMY = ROOT / "config" / "constructs.csv"
DEFAULT_JSON = ROOT / "reports" / "coverage.json"
DEFAULT_MARKDOWN = ROOT / "reports" / "coverage.md"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(handle)]


def normalized_doi(value: str) -> str:
    value = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.rstrip("/ ")


def split_constructs(value: str) -> list[str]:
    # Semicolon and pipe are preferred because commas can occur in labels.
    delimiter = ";" if ";" in value else "|" if "|" in value else None
    values = value.split(delimiter) if delimiter else [value]
    return [item.strip().upper() for item in values if item.strip()]


def counts(values: Iterable[str], missing: str = "(missing)") -> dict[str, int]:
    result = Counter(value.strip() or missing for value in values)
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def duplicate_groups(rows: list[dict[str, str]], field: str, normalize=lambda x: x) -> list[dict]:
    groups: dict[str, list[int]] = defaultdict(list)
    originals: dict[str, str] = {}
    for row_number, row in enumerate(rows, start=2):
        raw = row.get(field, "").strip()
        key = normalize(raw) if raw else ""
        if key:
            groups[key].append(row_number)
            originals.setdefault(key, raw)
    return [
        {"value": originals[key], "normalized_value": key, "count": len(row_numbers),
         "csv_rows": row_numbers}
        for key, row_numbers in sorted(groups.items()) if len(row_numbers) > 1
    ]


def year_summary(rows: list[dict[str, str]]) -> dict:
    valid: list[int] = []
    invalid: list[dict] = []
    for row_number, row in enumerate(rows, start=2):
        value = row.get("YEAR", "").strip()
        if not value:
            continue
        try:
            year = int(value)
            if not 1000 <= year <= datetime.now(timezone.utc).year + 1:
                raise ValueError
            valid.append(year)
        except ValueError:
            invalid.append({"csv_row": row_number, "value": value})
    return {
        "by_year": counts(map(str, valid)),
        "by_decade": counts(f"{year // 10 * 10}s" for year in valid),
        "minimum": min(valid) if valid else None,
        "maximum": max(valid) if valid else None,
        "missing": sum(not row.get("YEAR", "").strip() for row in rows),
        "invalid": invalid,
    }


def file_audit(rows: list[dict[str, str]], corpus_root: Path, hash_files: bool) -> dict:
    missing_files: list[dict] = []
    hash_mismatches: list[dict] = []
    verified = 0
    for row_number, row in enumerate(rows, start=2):
        if row.get("ACCESS_STATUS", "").upper() != "ACQUIRED":
            continue
        relative = row.get("FILE_PATH", "").strip()
        path = corpus_root / relative if relative else None
        if not path or not path.is_file():
            missing_files.append({"csv_row": row_number, "source_id": row.get("SOURCE_ID", ""),
                                  "file_path": relative})
            continue
        expected = row.get("FILE_SHA256", "").strip().lower()
        if hash_files and expected:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            if actual != expected:
                hash_mismatches.append({"csv_row": row_number, "source_id": row.get("SOURCE_ID", ""),
                                        "expected": expected, "actual": actual})
                continue
        verified += 1
    return {"acquired_files_verified": verified, "missing_files": missing_files,
            "hash_mismatches": hash_mismatches, "content_hashing_enabled": hash_files}


def build_report(manifest: Path, taxonomy: Path, *, hash_files: bool = False) -> dict:
    rows = read_csv(manifest)
    taxonomy_rows = read_csv(taxonomy)
    taxonomy_domains = {row.get("domain", "").strip() for row in taxonomy_rows if row.get("domain", "").strip()}
    taxonomy_constructs = {row.get("construct", "").strip().upper() for row in taxonomy_rows
                           if row.get("construct", "").strip()}
    manifest_domains = {row.get("PSYCHOLOGY_DOMAIN", "").strip() for row in rows
                        if row.get("PSYCHOLOGY_DOMAIN", "").strip()}
    row_constructs = [construct for row in rows for construct in split_constructs(row.get("CONSTRUCTS", ""))]
    manifest_constructs = set(row_constructs)
    duplicates = {
        "source_ids": duplicate_groups(rows, "SOURCE_ID", lambda value: value.casefold()),
        "dois": duplicate_groups(rows, "DOI", normalized_doi),
        "file_sha256": duplicate_groups(rows, "FILE_SHA256", lambda value: value.lower()),
    }
    acquired = sum(row.get("ACCESS_STATUS", "").upper() == "ACQUIRED" for row in rows)
    audit = file_audit(rows, manifest.parent, hash_files)
    issues = sum(len(group) for group in duplicates.values()) + len(audit["missing_files"]) + len(audit["hash_mismatches"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest.resolve()),
        "taxonomy": str(taxonomy.resolve()),
        "summary": {
            "manifest_rows": len(rows),
            "acquired_documents": acquired,
            "acquisition_percent": round(acquired * 100 / len(rows), 2) if rows else 0.0,
            "taxonomy_domains": len(taxonomy_domains),
            "taxonomy_constructs": len(taxonomy_constructs),
            "issue_groups": issues,
        },
        "coverage": {
            "by_domain": counts(row.get("PSYCHOLOGY_DOMAIN", "") for row in rows),
            "by_construct": counts(row_constructs),
            "by_license": counts(row.get("LICENSE", "").lower() for row in rows),
            "by_access_status": counts(row.get("ACCESS_STATUS", "").upper() for row in rows),
            "by_retraction_status": counts(row.get("RETRACTION_STATUS", "").upper() for row in rows),
            "by_source_type": counts(row.get("SOURCE_TYPE", "") for row in rows),
            "years": year_summary(rows),
        },
        "taxonomy_coverage": {
            "domains_without_documents": sorted(taxonomy_domains - manifest_domains),
            "constructs_without_documents": sorted(taxonomy_constructs - manifest_constructs),
            "domains_not_in_taxonomy": sorted(manifest_domains - taxonomy_domains),
            "constructs_not_in_taxonomy": sorted(manifest_constructs - taxonomy_constructs),
        },
        "duplicates": duplicates,
        "files": audit,
    }
    return report


def markdown_table(mapping: dict[str, int]) -> str:
    lines = ["| Value | Count |", "|---|---:|"]
    lines.extend(f"| {key.replace('|', '\\|')} | {value} |" for key, value in mapping.items())
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Psychology corpus coverage report", "",
        f"Generated: `{report['generated_at']}`", "",
        "## Summary", "",
        "| Metric | Value |", "|---|---:|",
        f"| Manifest rows | {summary['manifest_rows']} |",
        f"| Acquired documents | {summary['acquired_documents']} ({summary['acquisition_percent']}%) |",
        f"| Taxonomy domains | {summary['taxonomy_domains']} |",
        f"| Taxonomy constructs | {summary['taxonomy_constructs']} |",
        f"| Duplicate/integrity issue groups | {summary['issue_groups']} |", "",
    ]
    labels = (("by_domain", "Domain"), ("by_construct", "Construct"),
              ("by_license", "License"), ("by_access_status", "Access status"),
              ("by_retraction_status", "Retraction status"), ("by_source_type", "Source type"))
    for key, label in labels:
        lines.extend((f"## Coverage by {label.lower()}", "", markdown_table(report["coverage"][key]), ""))
    years = report["coverage"]["years"]
    lines.extend(("## Publication years", "",
                  f"Range: `{years['minimum']}`–`{years['maximum']}`; missing: `{years['missing']}`; invalid: `{len(years['invalid'])}`.", "",
                  markdown_table(years["by_decade"]), ""))
    lines.extend(("## Taxonomy gaps", ""))
    for key, title in (("domains_without_documents", "Domains without documents"),
                       ("constructs_without_documents", "Constructs without documents"),
                       ("domains_not_in_taxonomy", "Manifest domains absent from taxonomy"),
                       ("constructs_not_in_taxonomy", "Manifest constructs absent from taxonomy")):
        values = report["taxonomy_coverage"][key]
        lines.append(f"- {title}: {', '.join(f'`{value}`' for value in values) if values else 'none'}")
    lines.extend(("", "## Duplicate groups", ""))
    for key, title in (("source_ids", "Source IDs"), ("dois", "DOIs"), ("file_sha256", "File hashes")):
        groups = report["duplicates"][key]
        lines.append(f"### {title}")
        lines.append("")
        if groups:
            lines.extend(("| Value | Count | CSV rows |", "|---|---:|---|"))
            lines.extend(f"| {item['value'].replace('|', '\\|')} | {item['count']} | {', '.join(map(str, item['csv_rows']))} |" for item in groups)
        else:
            lines.append("None.")
        lines.append("")
    files = report["files"]
    lines.extend(("## File integrity", "",
                  f"- Acquired files verified: `{files['acquired_files_verified']}`",
                  f"- Missing acquired files: `{len(files['missing_files'])}`",
                  f"- SHA-256 mismatches: `{len(files['hash_mismatches'])}`",
                  f"- Content hashing enabled: `{str(files['content_hashing_enabled']).lower()}`", ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="JSON output path")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN, help="Markdown output path")
    parser.add_argument("--hash-files", action="store_true", help="Recompute hashes of acquired files")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero for duplicate or integrity issues")
    args = parser.parse_args()
    report = build_report(args.manifest, args.taxonomy, hash_files=args.hash_files)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    return 1 if args.strict and report["summary"]["issue_groups"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
