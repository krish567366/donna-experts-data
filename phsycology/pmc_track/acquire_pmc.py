#!/usr/bin/env python3
"""Acquire negotiation-relevant articles from the official PMC OA subset."""
from __future__ import annotations

import argparse, csv, hashlib, json, time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
UA = "donna-experts-data/1.0 (lawful research corpus; contact via repository)"
QUERY = ('(negotiation[Title/Abstract] OR bargaining[Title/Abstract] OR '
         'persuasion[Title/Abstract] OR "conflict resolution"[Title/Abstract]) AND '
         '(psychology[Title/Abstract] OR psychological[Title/Abstract] OR '
         'behavioral[Title/Abstract] OR behavioural[Title/Abstract]) AND '
         'open_access[filter] AND has_pdf[filter]')

def get(url: str, attempts: int = 2) -> bytes:
    for i in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=20) as r:
                return r.read()
        except Exception:
            if i == attempts - 1: raise
            time.sleep(2 ** i)

def text(node, path):
    x = node.find(path)
    return "" if x is None else " ".join("".join(x.itertext()).split())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=100)
    ap.add_argument("--download", type=int, default=20)
    args = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "pdfs").mkdir(exist_ok=True)
    api = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    search = api + "esearch.fcgi?" + urlencode({"db":"pmc","term":QUERY,"retmax":args.candidates,"sort":"relevance"})
    ids = [x.text for x in ET.fromstring(get(search)).findall(".//Id")]
    rows=[]
    if ids:
        fetch = api + "esummary.fcgi?" + urlencode({"db":"pmc","id":",".join(ids),"retmode":"xml"})
        root=ET.fromstring(get(fetch))
        def item(a, name):
            x=a.find(f".//Item[@Name='{name}']")
            return "" if x is None else (x.text or "")
        for a in root.findall(".//DocSum"):
            pmcid=item(a,"pmcid") or text(a,"Id")
            if pmcid and not pmcid.startswith("PMC"): pmcid="PMC"+pmcid
            pubdate=item(a,"PubDate")
            rows.append({"pmcid":pmcid,"pmid":item(a,"pmid"),
                         "doi":item(a,"doi") or item(a,"DOI"),"title":item(a,"Title"),
                         "journal":item(a,"FullJournalName"),"year":pubdate[:4],
                         "license_text":"See OA package/article license file","oa_status":"unchecked","package_url":"",
                         "download_status":"not_attempted","sha256":"","pdf_file":"","failure":""})
    downloaded=0
    for n,row in enumerate(rows):
        try:
            s3="https://pmc-oa-opendata.s3.amazonaws.com/"
            listing=ET.fromstring(get(s3+"?"+urlencode({"list-type":"2","prefix":row["pmcid"]+".","delimiter":"/"})))
            prefixes=[x.text for x in listing.findall(".//{*}CommonPrefixes/{*}Prefix") if x.text]
            if not prefixes:
                row["oa_status"]="not_in_oa_subset"; continue
            prefix=sorted(prefixes)[-1].rstrip("/")
            meta_url=s3+"metadata/"+prefix+".json"
            meta=json.loads(get(meta_url))
            if str(meta.get("is_pmc_openaccess","")).lower() not in ("yes","true","1"):
                row["oa_status"]="not_in_oa_subset"; continue
            row["oa_status"]="oa_subset"; row["license_code"]=meta.get("license_code","")
            href=meta.get("pdf_url","") or ""
            if href.startswith("s3://pmc-oa-opendata/"):
                href=s3+href[len("s3://pmc-oa-opendata/"):]
            row["package_url"]=href
            row["license_text"]="PMC metadata license_code="+row["license_code"]
            if downloaded >= args.download: continue
            if not href: row["download_status"]="no_pdf"; continue
            pdf_data=get(href)
            if not pdf_data.startswith(b"%PDF-"): raise ValueError("extracted file lacks PDF signature")
            out=ROOT/"pdfs"/(row["pmcid"]+".pdf"); out.write_bytes(pdf_data)
            row["sha256"]=hashlib.sha256(pdf_data).hexdigest(); row["pdf_file"]=str(out.relative_to(ROOT))
            row["download_status"]="verified"; downloaded+=1
        except Exception as e:
            row["download_status"]="failed"; row["failure"]=f"{type(e).__name__}: {e}"
    fields=["pmcid","pmid","doi","title","journal","year","license_code","license_text","oa_status","package_url","download_status","sha256","pdf_file","failure"]
    with (ROOT/"candidates.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    licenses={}
    for r in rows: licenses[r.get("license_code","")]=licenses.get(r.get("license_code",""),0)+1
    report={"query":QUERY,"candidate_count":len(rows),"oa_subset_count":sum(r["oa_status"]=="oa_subset" for r in rows),
            "verified_pdf_count":sum(r["download_status"]=="verified" for r in rows),
            "failed_download_count":sum(r["download_status"]=="failed" for r in rows),"licenses":licenses,
            "source_apis":[search,"https://pmc-oa-opendata.s3.amazonaws.com/"],
            "generated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
    (ROOT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__ == "__main__": main()
