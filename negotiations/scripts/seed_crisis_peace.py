"""Acquire a small, reproducible seed set of official crisis/peace PDFs."""
from __future__ import annotations

import csv
import hashlib
import json
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (ROOT / "METADATA" / "manifest_fields.txt").read_text(encoding="utf-8").splitlines()
SOURCES = [
    ("CRISIS-FBI-1990-GUIDELINES", "Guidelines for Negotiation", "FBI", "1990", "CRISIS/FBI/CRISIS-FBI-1990-GUIDELINES", "https://leb.fbi.gov/file-repository/archives/july-1990.pdf", "hostage negotiation;terrorism;demands;deadlines"),
    ("CRISIS-FBI-1995-GUIDE", "A Guide to Crisis Negotiations", "FBI", "1995", "CRISIS/FBI/CRISIS-FBI-1995-GUIDE", "https://leb.fbi.gov/file-repository/archives/october-1995.pdf", "crisis negotiation;hostage;field command"),
    ("CRISIS-FBI-1996-COMMUNICATION", "Therapeutic Communication and Crisis Negotiation", "FBI", "1996", "CRISIS/FBI/CRISIS-FBI-1996-COMMUNICATION", "https://leb.fbi.gov/file-repository/archives/may-1996.pdf", "active listening;empathy;role play;crisis negotiation"),
    ("CRISIS-FBI-1999-COMMANDERS", "Critical Incident Management: Bringing Subjects to the Table", "FBI", "1999", "CRISIS/FBI/CRISIS-FBI-1999-COMMANDERS", "https://leb.fbi.gov/file-repository/archives/jan99leb.pdf", "crisis negotiation;tactical coordination;impasse"),
    ("CRISIS-FBI-2002-TEAMS", "Crisis Negotiation Teams: Selection and Training", "FBI", "2002", "CRISIS/FBI/CRISIS-FBI-2002-TEAMS", "https://leb.fbi.gov/file-repository/archives/nov02leb.pdf", "crisis negotiation;team selection;training"),
    ("CRISIS-FBI-2003-POSITION", "Negotiation Position Papers: A Tool for Crisis Negotiators", "FBI", "2003", "CRISIS/FBI/CRISIS-FBI-2003-POSITION", "https://leb.fbi.gov/file-repository/archives/oct03leb.pdf", "crisis negotiation;position paper;risk assessment"),
    ("PEACE-BELFAST-1998-AGREEMENT", "The Belfast Agreement", "Northern Ireland Office", "1998", "PEACE/NORTHERN_IRELAND/PEACE-BELFAST-1998-AGREEMENT", "https://assets.publishing.service.gov.uk/media/619500728fa8f5037d67b678/The_Belfast_Agreement_An_Agreement_Reached_at_the_Multi-Party_Talks_on_Northern_Ireland.pdf", "peace negotiation;power sharing;Good Friday Agreement"),
]


def main() -> None:
    rows = []
    failures = []
    for sid, title, org, year, folder, url, topics in SOURCES:
        target_dir = ROOT / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "original.pdf"
        request = urllib.request.Request(url, headers={"User-Agent": "NegotiationCorpus/1.0 (+research acquisition)"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            failures.append({"source_id": sid, "url": url, "status": "RETRY", "error": str(exc)})
            continue
        if not content.startswith(b"%PDF-"):
            raise RuntimeError(f"Not a PDF: {url}")
        target.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        row = {field: "" for field in FIELDS}
        row.update({
            "SOURCE_ID": sid, "TITLE": title, "ORGANIZATION": org, "YEAR": year,
            "LANGUAGE": "English", "COUNTRY": "United States" if org == "FBI" else "United Kingdom",
            "SOURCE_TYPE": "government publication" if org == "FBI" else "agreement",
            "DOMAIN": "crisis/hostage" if org == "FBI" else "peace/diplomatic",
            "TOPICS": topics, "CASE_NAME": "" if org == "FBI" else "Northern Ireland peace process",
            "URL": url, "FILE_PATH": str(target.relative_to(ROOT)).replace("\\", "/"), "FILE_FORMAT": "PDF",
            "PRIMARY_SOURCE": "false" if org == "FBI" else "true", "SECONDARY_SOURCE": "true" if org == "FBI" else "false",
            "ORIGINAL_SOURCE": org, "HOST_SOURCE": org, "ACCESS_DATE": date.today().isoformat(),
            "ACCESS_STATUS": "ACQUIRED", "COPYRIGHT_STATUS": "official public web publication",
            "TRAINING_PERMISSION": "REVIEW_REQUIRED", "PROVENANCE": f"Downloaded directly from official {org} host; original bytes preserved",
            "RELIABILITY_SCORE": "5", "TRAINING_VALUE": "4", "UNIQUENESS_SCORE": "3", "CASE_VALUE": "3" if org == "FBI" else "5",
            "TRANSCRIPT_VALUE": "0", "SOURCE_QUALITY": "5", "OCR_REQUIRED": "false", "DUPLICATE_STATUS": "UNIQUE",
            "SHA256": sha, "FILE_SIZE_BYTES": str(len(content)), "AUTHORITY": "5", "PRIMARY_SOURCE_VALUE": "2" if org == "FBI" else "5",
            "RELIABILITY": "5", "NEGOTIATION_RELEVANCE": "5", "INFORMATION_DENSITY": "4", "CASE_DETAIL": "2" if org == "FBI" else "5",
            "TACTICAL_DETAIL": "5" if org == "FBI" else "3", "STRATEGIC_DETAIL": "3" if org == "FBI" else "5",
            "OUTCOME_VISIBILITY": "2" if org == "FBI" else "5", "COUNTERPARTY_VISIBILITY": "2" if org == "FBI" else "5", "UNIQUENESS": "3",
        })
        (target_dir / "metadata.json").write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
        rows.append(row)
    batch = ROOT / "METADATA" / "batches" / "crisis_peace.csv"
    batch.parent.mkdir(parents=True, exist_ok=True)
    with batch.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (ROOT / "METADATA" / "failed_acquisitions_crisis_peace.json").write_text(
        json.dumps(failures, indent=2), encoding="utf-8"
    )
    print(f"Acquired {len(rows)} official PDFs; {len(failures)} queued for retry")


if __name__ == "__main__":
    main()
