#!/usr/bin/env python3
"""Discover and acquire a licensed psychology corpus from OpenAlex."""
from __future__ import annotations
import argparse, csv, email.utils, hashlib, json, os, pathlib, random, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
MANIFEST = ROOT / "psychology_sources.csv"
FIELDS = ["SOURCE_ID","TITLE","AUTHORS","YEAR","JOURNAL","PUBLISHER","DOI","URL","LANGUAGE","COUNTRY","SOURCE_TYPE","PSYCHOLOGY_DOMAIN","SUBDOMAIN","CONSTRUCTS","POPULATION","SAMPLE_SIZE","METHOD","EXPERIMENTAL","OBSERVATIONAL","LONGITUDINAL","FIELD_STUDY","REVIEW","META_ANALYSIS","PREREGISTERED","REPLICATION","ORIGINAL_STUDY","EFFECT_SIZE_AVAILABLE","DATA_AVAILABLE","CODE_AVAILABLE","OPEN_ACCESS","LICENSE","COPYRIGHT_STATUS","TRAINING_PERMISSION","FILE_PATH","PRIMARY_SOURCE","RELIABILITY_SCORE","REPLICATION_STATUS","EVIDENCE_STRENGTH","NEGOTIATION_RELEVANCE","TRAINING_VALUE","KNOWN_LIMITATIONS","RETRACTION_STATUS","ACCESS_STATUS","OA_PDF_URL","OPENALEX_ID","FILE_SHA256","FILE_BYTES","DISCOVERED_AT","ACQUIRED_AT","NOTES"]
ALLOW = {"cc-by", "cc0", "public-domain", "cc-by-sa"}
UA = "DonnaPsychologyCorpus/1.0"

def now(): return datetime.now(timezone.utc).isoformat()
def retry_delay(error, attempt):
    value = error.headers.get("Retry-After") if isinstance(error, urllib.error.HTTPError) else None
    if value:
        try: return max(0.0, float(value))
        except ValueError:
            try: return max(0.0, (email.utils.parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError): pass
    return min(60.0, 2 ** attempt) + random.random()

def request_json(url, email=""):
    req=urllib.request.Request(url,headers={"User-Agent":UA+(f" mailto:{email}" if email else "")})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req,timeout=90) as r: return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code not in (429,500,502,503,504): raise
            if attempt==7: raise
            time.sleep(retry_delay(e,attempt))
def load_rows():
    if not MANIFEST.exists(): return []
    with MANIFEST.open(encoding="utf-8",newline="") as f: return list(csv.DictReader(f))
def save_rows(rows):
    MANIFEST.parent.mkdir(parents=True,exist_ok=True)
    tmp=MANIFEST.with_suffix(".csv.part")
    with tmp.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    tmp.replace(MANIFEST)
def taxonomy():
    with (ROOT/"config"/"constructs.csv").open(encoding="utf-8",newline="") as f: return list(csv.DictReader(f))
def inv_abstract(x):
    inv=x.get("abstract_inverted_index") or {}
    if not inv:return ""
    seq=[]
    for word,positions in inv.items(): seq += [(p,word) for p in positions]
    return " ".join(w for _,w in sorted(seq))
def classify_kind(title,abstract):
    s=(title+" "+abstract).lower()
    return ("TRUE" if "systematic review" in s else "FALSE", "TRUE" if "meta-analysis" in s or "meta analysis" in s else "FALSE", "TRUE" if "replicat" in s else "FALSE")
def discover(args):
    rows=load_rows(); seen={r["OPENALEX_ID"] for r in rows}; queries=taxonomy(); qi=args.start_index; empty_queries=0
    if not queries: raise RuntimeError("config/constructs.csv contains no discovery queries")
    while len(rows)<args.target:
        q=queries[qi%len(queries)]; qi+=1; cursor="*"; added=0
        while cursor and len(rows)<args.target and added<args.per_query:
            params={"search":q["query"],"filter":"has_content.pdf:true,best_oa_location.license:cc-by|cc0|public-domain|cc-by-sa,is_retracted:false","per-page":"100","cursor":cursor,"select":"id,doi,title,publication_year,language,type,authorships,primary_location,best_oa_location,open_access,abstract_inverted_index,is_retracted"}
            if args.api_key: params["api_key"]=args.api_key
            data=request_json("https://api.openalex.org/works?"+urllib.parse.urlencode(params),args.email)
            for w in data.get("results",[]):
                if w["id"] in seen: continue
                loc=w.get("best_oa_location") or {}; lic=(loc.get("license") or "").lower()
                if lic not in ALLOW: continue
                sid=w["id"].rsplit("/",1)[-1]; abs_text=inv_abstract(w); review,meta,repl=classify_kind(w.get("title") or "",abs_text)
                authors="; ".join(a.get("author",{}).get("display_name","") for a in w.get("authorships",[]))
                source=(w.get("primary_location") or {}).get("source") or {}
                is_primary = review == "FALSE" and meta == "FALSE"
                row={k:"" for k in FIELDS}; row.update({"SOURCE_ID":sid,"TITLE":w.get("title") or "","AUTHORS":authors,"YEAR":w.get("publication_year") or "","JOURNAL":source.get("display_name") or "","PUBLISHER":source.get("host_organization_name") or "","DOI":w.get("doi") or "","URL":loc.get("landing_page_url") or w.get("doi") or w["id"],"LANGUAGE":w.get("language") or "","SOURCE_TYPE":w.get("type") or "","PSYCHOLOGY_DOMAIN":q["domain"],"SUBDOMAIN":q["construct"],"CONSTRUCTS":q["construct"],"REVIEW":review,"META_ANALYSIS":meta,"REPLICATION":repl,"ORIGINAL_STUDY":"TRUE" if is_primary else "FALSE","OPEN_ACCESS":"TRUE","LICENSE":lic,"COPYRIGHT_STATUS":"PUBLIC_DOMAIN" if lic == "public-domain" else "LICENSED_OPEN_ACCESS","TRAINING_PERMISSION":"PERMITTED_BY_RECORDED_LICENSE","FILE_PATH":f"corpus/{sid}/original.pdf","PRIMARY_SOURCE":"TRUE" if is_primary else "FALSE","RETRACTION_STATUS":"NOT_RETRACTED","ACCESS_STATUS":"DISCOVERED","OA_PDF_URL":loc.get("pdf_url") or "","OPENALEX_ID":w["id"],"DISCOVERED_AT":now(),"NEGOTIATION_RELEVANCE":q["priority"],"NOTES":"License recorded from OpenAlex; preserve attribution and verify upstream terms before redistribution."})
                rows.append(row); seen.add(w["id"]); added+=1
                if len(rows)>=args.target or added>=args.per_query: break
            cursor=data.get("meta",{}).get("next_cursor")
            save_rows(rows); time.sleep(args.delay)
            if not data.get("results"): break
        print(f"{q['construct']}: +{added}; total={len(rows)}")
        empty_queries = empty_queries + 1 if not added else 0
        if empty_queries >= len(queries): break
    save_rows(rows)
def content_url(row,key):
    if key:return f"https://content.openalex.org/works/{row['SOURCE_ID']}.pdf?api_key={urllib.parse.quote(key)}"
    return row.get("OA_PDF_URL","")
def download(args):
    rows=load_rows(); done=0; failures=[]
    for i,row in enumerate(rows):
        if done>=args.limit: break
        dest=ROOT/row["FILE_PATH"]
        if dest.exists() and dest.stat().st_size>1024:
            h=hashlib.sha256(); size=0
            with dest.open("rb") as existing:
                if existing.read(5) != b"%PDF-": row["ACCESS_STATUS"]="RETRY"; continue
                existing.seek(0)
                while chunk:=existing.read(1024*1024): h.update(chunk); size+=len(chunk)
            row.update({"FILE_SHA256":h.hexdigest(),"FILE_BYTES":str(size),"ACCESS_STATUS":"ACQUIRED"})
            continue
        url=content_url(row,args.api_key)
        if not url: row["ACCESS_STATUS"]="REVIEW_REQUIRED"; continue
        dest.parent.mkdir(parents=True,exist_ok=True); part=dest.with_suffix(".pdf.part")
        try:
            for attempt in range(8):
                try:
                    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/pdf"})
                    h=hashlib.sha256(); size=0
                    with urllib.request.urlopen(req,timeout=args.timeout) as src, part.open("wb") as out:
                        head=src.read(5)
                        if head != b"%PDF-": raise ValueError("response is not a PDF")
                        out.write(head);h.update(head);size+=len(head)
                        while chunk:=src.read(1024*1024): out.write(chunk);h.update(chunk);size+=len(chunk)
                    break
                except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
                    part.unlink(missing_ok=True)
                    if isinstance(e, urllib.error.HTTPError) and e.code not in (429,500,502,503,504): raise
                    if attempt==7: raise
                    time.sleep(retry_delay(e,attempt))
            part.replace(dest); row.update({"FILE_SHA256":h.hexdigest(),"FILE_BYTES":str(size),"ACQUIRED_AT":now(),"ACCESS_STATUS":"ACQUIRED"})
            meta={k:row.get(k,"") for k in FIELDS}
            meta_tmp=dest.parent/"metadata.json.part"; meta_tmp.write_text(json.dumps(meta,indent=2,ensure_ascii=False),encoding="utf-8"); meta_tmp.replace(dest.parent/"metadata.json")
            prov={"retrieved_at":row["ACQUIRED_AT"],"retrieval_url":url.split("?api_key=")[0],"sha256":h.hexdigest(),"bytes":size,"license":row["LICENSE"],"openalex_id":row["OPENALEX_ID"]}
            prov_tmp=dest.parent/"provenance.json.part"; prov_tmp.write_text(json.dumps(prov,indent=2),encoding="utf-8"); prov_tmp.replace(dest.parent/"provenance.json"); done+=1
        except Exception as e:
            part.unlink(missing_ok=True); row["ACCESS_STATUS"]="RETRY"; failures.append({"SOURCE_ID":row["SOURCE_ID"],"error":str(e),"at":now()})
        if i%10==0: save_rows(rows)
        time.sleep(args.delay)
    save_rows(rows)
    if failures:
        p=ROOT/"logs"/"failures.csv";p.parent.mkdir(exist_ok=True); exists=p.exists()
        with p.open("a",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=["SOURCE_ID","error","at"]); 
            if not exists:w.writeheader()
            w.writerows(failures)
    print(f"acquired this run: {done}; failures: {len(failures)}")
def export(_):
    rows=load_rows()
    try:
        import pandas as pd
        pd.DataFrame(rows,columns=FIELDS).to_parquet(ROOT/"psychology_sources.parquet",index=False)
    except Exception as e: print("Parquet skipped:",e)
    for name,cols in {"psychology_claims.csv":["CLAIM_ID","CLAIM","CONSTRUCT","SOURCE_ID","POPULATION","CONTEXT","EFFECT_DIRECTION","EFFECT_SIZE","CONFIDENCE_INTERVAL","SAMPLE_SIZE","REPLICATION_STATUS","MODERATORS","LIMITATIONS","CONTRADICTING_SOURCES","SUPPORTING_SOURCES","EVIDENCE_STRENGTH"],"psychology_constructs.csv":["CONSTRUCT","DOMAIN","DEFINITION","PSEUDOSCIENCE_RISK","NOTES"]}.items():
        p=ROOT/name
        if not p.exists():
            with p.open("w",newline="",encoding="utf-8") as f: csv.writer(f).writerow(cols)
def verify(_):
    rows=load_rows(); bad=[]
    for r in rows:
        p=ROOT/r["FILE_PATH"]
        if r["ACCESS_STATUS"]=="ACQUIRED":
            if not p.exists(): bad.append(r["SOURCE_ID"]); continue
            h=hashlib.sha256()
            with p.open("rb") as f:
                while chunk:=f.read(1024*1024): h.update(chunk)
            if h.hexdigest()!=r["FILE_SHA256"]: bad.append(r["SOURCE_ID"])
    print(json.dumps({"manifest_rows":len(rows),"acquired":sum(r["ACCESS_STATUS"]=="ACQUIRED" for r in rows),"integrity_failures":bad},indent=2))
def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest="cmd",required=True)
    d=sub.add_parser("discover");d.add_argument("--target",type=int,default=10000);d.add_argument("--per-query",type=int,default=600);d.add_argument("--start-index",type=int,default=0,help="zero-based taxonomy stream to start with");d.add_argument("--email",default="");d.add_argument("--api-key",default=os.getenv("OPENALEX_API_KEY",""));d.add_argument("--delay",type=float,default=.12);d.set_defaults(fn=discover)
    x=sub.add_parser("download");x.add_argument("--limit",type=int,default=10000);x.add_argument("--api-key",default=os.getenv("OPENALEX_API_KEY",""));x.add_argument("--delay",type=float,default=.2);x.add_argument("--timeout",type=int,default=180);x.set_defaults(fn=download)
    for name,fn in [("export",export),("verify",verify)]: q=sub.add_parser(name);q.set_defaults(fn=fn)
    a=p.parse_args();a.fn(a)
if __name__=="__main__": main()
