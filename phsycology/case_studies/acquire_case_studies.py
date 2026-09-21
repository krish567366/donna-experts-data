#!/usr/bin/env python3
"""Acquire openly licensed psychology case-study metadata and a small PDF sample."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
QUERY = ('OPEN_ACCESS:Y AND FIRST_PDATE:[2000 TO 2024] AND '
         '(TITLE:"case report" OR TITLE:"case study" OR TITLE:"case series") AND '
         '(TITLE:psychology OR TITLE:psychological OR TITLE:psychotherapy OR TITLE:psychiatric OR '
         'TITLE:psychosis OR TITLE:depression OR TITLE:anxiety OR TITLE:trauma OR TITLE:behavioral OR '
         'TITLE:behavioural OR TITLE:negotiation OR TITLE:conflict OR TITLE:mediation)')
ALLOW = {"cc by", "cc-by", "cc by-sa", "cc-by-sa", "cc0", "public domain"}
FIELDS = [
    "SOURCE_ID","TITLE","AUTHORS","YEAR","JOURNAL","PUBLISHER","DOI","URL",
    "LANGUAGE","COUNTRY","SOURCE_TYPE","PSYCHOLOGY_DOMAIN","SUBDOMAIN","CONSTRUCTS",
    "POPULATION","SAMPLE_SIZE","METHOD","EXPERIMENTAL","OBSERVATIONAL","LONGITUDINAL",
    "FIELD_STUDY","REVIEW","META_ANALYSIS","PREREGISTERED","REPLICATION","ORIGINAL_STUDY",
    "EFFECT_SIZE_AVAILABLE","DATA_AVAILABLE","CODE_AVAILABLE","OPEN_ACCESS","LICENSE",
    "COPYRIGHT_STATUS","TRAINING_PERMISSION","FILE_PATH","PRIMARY_SOURCE","RELIABILITY_SCORE",
    "REPLICATION_STATUS","EVIDENCE_STRENGTH","NEGOTIATION_RELEVANCE","TRAINING_VALUE",
    "KNOWN_LIMITATIONS","RETRACTION_STATUS","ACCESS_STATUS","OA_PDF_URL","OPENALEX_ID",
    "FILE_SHA256","FILE_BYTES","DISCOVERED_AT","ACQUIRED_AT","NOTES"
]

def norm_license(value: str) -> str:
    s = re.sub(r"[_-]+", " ", (value or "").lower()).strip()
    if "cc0" in s: return "cc0"
    if "public domain" in s: return "public domain"
    if "cc by sa" in s or "creative commons attribution share alike" in s: return "cc-by-sa"
    if s == "cc by" or "creative commons attribution license" in s: return "cc-by"
    return s

def domain(title: str) -> tuple[str, str, str, int]:
    t = title.lower()
    if any(x in t for x in ("negotiat", "conflict", "mediat", "bargain")):
        return "negotiation_conflict", "CASE_EVIDENCE", "NEGOTIATION,CONFLICT", 5
    if any(x in t for x in ("psychotherap", "cognitive behavio", "cbt", "counsel")):
        return "clinical_psychology", "CASE_EVIDENCE", "PSYCHOTHERAPY", 2
    if any(x in t for x in ("psychiatr", "psychosis", "depress", "anxiety", "trauma")):
        return "clinical_psychology", "CASE_EVIDENCE", "MENTAL_HEALTH", 1
    return "psychology", "CASE_EVIDENCE", "CASE_STUDY", 1

def fetch_records(limit: int = 100) -> list[dict]:
    out, cursor = [], "*"
    session = requests.Session()
    while len(out) < limit:
        params = {"query": QUERY, "format":"json", "resultType":"core", "pageSize": min(25, limit-len(out)), "cursorMark":cursor}
        for attempt in range(3):
            try:
                resp = session.get(API, params=params, timeout=45); resp.raise_for_status(); break
            except requests.RequestException:
                if attempt == 2: return out
                time.sleep(2 ** attempt)
        data = resp.json(); batch = data.get("resultList",{}).get("result",[])
        if not batch: break
        out.extend(batch)
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor: break
        cursor = nxt; time.sleep(.15)
    return out[:limit]

def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    pdf_dir = ROOT / "pdfs"; pdf_dir.mkdir(exist_ok=True)
    raw = fetch_records()
    now = datetime.now(timezone.utc).isoformat()
    rows=[]
    for x in raw:
        pmcid=x.get("pmcid",""); lic=norm_license(x.get("license", ""))
        if lic not in ALLOW or not pmcid: continue
        title=x.get("title","") or ""; dom,sub,constructs,rel=domain(title)
        pdf=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
        sid=f"EPMC_{pmcid}"
        row={k:"" for k in FIELDS}
        row.update({"SOURCE_ID":sid,"TITLE":title,"AUTHORS":x.get("authorString","") or "",
          "YEAR":x.get("pubYear","") or "","JOURNAL":x.get("journalTitle","") or "",
          "PUBLISHER":x.get("publisher","") or "","DOI":x.get("doi","") or "",
          "URL":f"https://europepmc.org/article/MED/{x.get('pmid')}" if x.get("pmid") else f"https://europepmc.org/article/PMC/{pmcid}",
          "LANGUAGE":x.get("language","") or "","SOURCE_TYPE":"case study","PSYCHOLOGY_DOMAIN":dom,
          "SUBDOMAIN":sub,"CONSTRUCTS":constructs,"METHOD":"case report/case study","OBSERVATIONAL":"TRUE",
          "ORIGINAL_STUDY":"TRUE","OPEN_ACCESS":"TRUE","LICENSE":lic,"COPYRIGHT_STATUS":"LICENSED_OPEN_ACCESS",
          "TRAINING_PERMISSION":"LICENSE_ALLOWS_REUSE_WITH_ATTRIBUTION","FILE_PATH":f"pdfs/{sid}.pdf",
          "PRIMARY_SOURCE":"TRUE","RELIABILITY_SCORE":"3","EVIDENCE_STRENGTH":"CASE_EVIDENCE",
          "NEGOTIATION_RELEVANCE":str(rel),"TRAINING_VALUE":"CASE_EVIDENCE","KNOWN_LIMITATIONS":"Single-case/series evidence; verify clinical and contextual generalizability.",
          "RETRACTION_STATUS":"NOT_RETRACTED","ACCESS_STATUS":"DISCOVERED","OA_PDF_URL":pdf,"FILE_SHA256":"",
          "FILE_BYTES":"","DISCOVERED_AT":now,"ACQUIRED_AT":"","NOTES":"Europe PMC REST API; license supplied by Europe PMC core record"})
        rows.append(row)
    # stable de-duplication
    rows=list({r["SOURCE_ID"]:r for r in rows}.values())
    def download(row):
        try:
            article=requests.get(row["OA_PDF_URL"],timeout=30,headers={"User-Agent":"donna-experts-data/1.0 (research corpus)"})
            match=re.search(r'<meta\s+name="citation_pdf_url"\s+content="([^"]+)"',article.text,re.I)
            if not match: return False
            row["OA_PDF_URL"]=match.group(1).replace("&amp;","&")
            resp=requests.get(row["OA_PDF_URL"],timeout=45,headers={"User-Agent":"donna-experts-data/1.0 (research corpus)"})
            body=resp.content
            if resp.status_code==200 and body.startswith(b"%PDF-") and len(body)>5000:
                path=ROOT/row["FILE_PATH"]; path.write_bytes(body)
                row["FILE_SHA256"]=hashlib.sha256(body).hexdigest(); row["FILE_BYTES"]=str(len(body))
                row["ACQUIRED_AT"]=datetime.now(timezone.utc).isoformat(); row["ACCESS_STATUS"]="ACQUIRED"
                return True
        except requests.RequestException as exc:
            row["NOTES"] += f"; download error: {type(exc).__name__}"
        return False
    downloaded=0
    candidates=rows[:60]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures={pool.submit(download,row):row for row in candidates}
        for fut in as_completed(futures):
            if fut.result(): downloaded += 1
            if downloaded >= 20:
                for pending in futures: pending.cancel()
                break
    with (ROOT/"case_study_sources.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(rows)
    provenance={"generated_at":now,"source_api":API,"query":QUERY,"raw_records":len(raw),
                "license_allowlist":sorted(ALLOW),"eligible_records":len(rows),"pdfs_acquired":downloaded,
                "method":"Europe PMC core metadata; retained records with PMCID and explicit CC BY, CC BY-SA, CC0, or public-domain license; PDF magic and minimum-size checked; SHA-256 recorded."}
    (ROOT/"provenance.json").write_text(json.dumps(provenance,indent=2),encoding="utf-8")
    print(json.dumps(provenance,indent=2))

if __name__ == "__main__": main()
