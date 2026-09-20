"""Build lightweight case, people, organization, and concept indexes from the manifest."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def split(value: str) -> list[str]:
    return [item.strip() for item in re.split(r";|\|", value or "") if item.strip()]


def write(name: str, rows: list[dict], fields: list[str]) -> None:
    path = ROOT / name
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    try:
        import pandas as pd
        pd.DataFrame(rows, columns=fields).to_parquet(path.with_suffix(".parquet"), index=False)
    except (ImportError, ValueError, OSError):
        pass


def main() -> None:
    with (ROOT / "negotiation_sources.csv").open(encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))
    cases: dict[str, list[dict]] = defaultdict(list)
    people: dict[str, set[str]] = defaultdict(set)
    orgs: dict[str, set[str]] = defaultdict(set)
    concepts: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        sid = source["SOURCE_ID"]
        if source.get("CASE_NAME"): cases[source["CASE_NAME"]].append(source)
        for person in split(source.get("PARTICIPANTS", "")): people[person].add(sid)
        for org in split(source.get("ORGANIZATION", "")): orgs[org].add(sid)
        for concept in split(source.get("TOPICS", "")): concepts[concept].add(sid)
    case_rows = []
    for i, (name, docs) in enumerate(sorted(cases.items()), 1):
        case_rows.append({"CASE_ID": f"CASE-{i:05d}", "CASE_NAME": name, "DOMAIN": docs[0].get("DOMAIN", ""),
                          "DATES": "; ".join(sorted({d.get("DATE", "") for d in docs if d.get("DATE")})),
                          "PARTIES": "; ".join(sorted({p for d in docs for p in split(d.get("PARTICIPANTS", ""))})),
                          "COUNTRIES": "; ".join(sorted({d.get("COUNTRY", "") for d in docs if d.get("COUNTRY")})),
                          "INDUSTRIES": "", "SOURCE_COUNT": len(docs),
                          "PRIMARY_SOURCE_COUNT": sum(d.get("PRIMARY_SOURCE", "").lower() == "true" for d in docs),
                          "TRANSCRIPT_AVAILABLE": any(float(d.get("TRANSCRIPT_VALUE") or 0) > 0 for d in docs),
                          "AGREEMENT_AVAILABLE": any(d.get("SOURCE_TYPE", "").lower() == "agreement" for d in docs),
                          "SUCCESS_FAILURE_STATUS": "UNASSESSED", "RELATED_CASES": ""})
    case_fields = ["CASE_ID","CASE_NAME","DOMAIN","DATES","PARTIES","COUNTRIES","INDUSTRIES","SOURCE_COUNT","PRIMARY_SOURCE_COUNT","TRANSCRIPT_AVAILABLE","AGREEMENT_AVAILABLE","SUCCESS_FAILURE_STATUS","RELATED_CASES"]
    write("negotiation_cases.csv", case_rows, case_fields)
    write("negotiators.csv", [{"PERSON": k, "SOURCE_COUNT": len(v), "SOURCE_IDS": "; ".join(sorted(v))} for k,v in sorted(people.items())], ["PERSON","SOURCE_COUNT","SOURCE_IDS"])
    write("organizations.csv", [{"ORGANIZATION": k, "SOURCE_COUNT": len(v), "SOURCE_IDS": "; ".join(sorted(v))} for k,v in sorted(orgs.items())], ["ORGANIZATION","SOURCE_COUNT","SOURCE_IDS"])
    write("negotiation_concepts.csv", [{"CONCEPT": k, "SOURCE_COUNT": len(v), "SOURCE_IDS": "; ".join(sorted(v))} for k,v in sorted(concepts.items())], ["CONCEPT","SOURCE_COUNT","SOURCE_IDS"])
    print(f"Built {len(case_rows)} cases, {len(people)} people, {len(orgs)} organizations, {len(concepts)} concepts")


if __name__ == "__main__":
    main()
