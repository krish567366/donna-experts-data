#!/usr/bin/env python3
"""Resumable high-throughput OpenAlex OA PDF harvester.

Discovers negotiation scholarship with broad search queries, downloads only files
whose bytes contain a PDF header, deduplicates by SHA-256, and writes append-only
JSONL state plus auditable CSV batch shards. Bulk PDFs are intentionally ignored
by git (see negotiations/PAPERS_BULK/.gitignore).
"""
from __future__ import annotations

import argparse, csv, hashlib, json, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote
import requests

QUERIES = [
    "negotiation", "negotiations", "bargaining", "collective bargaining",
    "international negotiation", "peace negotiation", "conflict negotiation",
    "business negotiation", "labor negotiation", "trade negotiation",
    "contract negotiation", "salary negotiation", "crisis negotiation",
    "hostage negotiation", "diplomatic negotiation", "negotiation strategy",
    "negotiation tactics", "negotiation behavior", "negotiation process",
    "negotiation outcome", "negotiation agreement", "negotiation theory",
    "negotiation analysis", "negotiation training", "negotiation skills",
    "multi-party negotiation", "online negotiation", "automated negotiation",
    "mediation negotiation", "dispute resolution bargaining", "game theory bargaining",
    "integrative negotiation", "distributive negotiation", "principled negotiation",
    "coalition bargaining", "wage bargaining", "collective agreement negotiation",
]
FIELDS = ["source_id","openalex_id","title","authors","year","doi","query","pdf_url",
          "landing_url","host_source","license","oa_status","file_path","sha256",
          "file_size_bytes","access_date","access_status","http_status","error"]
UA = "donna-negotiation-corpus/1.0 (mailto:openalex-harvester@example.org)"
tls = threading.local()

def session():
    if not hasattr(tls, "s"):
        tls.s = requests.Session(); tls.s.headers.update({"User-Agent": UA})
    return tls.s

def clean(s, n=80):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower()
    return s[:n] or "untitled"

def load_jsonl(path):
    out=[]
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try: out.append(json.loads(line))
                except Exception: pass
    return out

def append_jsonl(path, obj, lock):
    with lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False)+"\n"); f.flush()

def discover(query, pages, mailto):
    cursor="*"; results=[]
    for _ in range(pages):
        payload={"results":[],"meta":{}}
        params={"search":query,"filter":"has_fulltext:true,is_oa:true,type:article|book-chapter|dissertation",
                "per-page":200,"cursor":cursor,"mailto":mailto,
                "select":"id,doi,title,publication_year,authorships,best_oa_location,locations,open_access"}
        for attempt in range(6):
            try:
                r=session().get("https://api.openalex.org/works",params=params,timeout=45)
                if r.status_code==429: time.sleep(2**attempt); continue
                r.raise_for_status(); payload=r.json(); break
            except Exception:
                if attempt==5: payload={"results":[],"meta":{}}
                else: time.sleep(min(20,2**attempt))
        for w in payload.get("results",[]):
            locs=[w.get("best_oa_location") or {}]+(w.get("locations") or [])
            urls=[]
            for loc in locs:
                u=loc.get("pdf_url")
                if u and u not in urls: urls.append(u)
            if not urls: continue
            auth=", ".join(a.get("author",{}).get("display_name","") for a in w.get("authorships",[]))
            oa=w.get("open_access") or {}; best=w.get("best_oa_location") or {}
            results.append({"openalex_id":w.get("id","").rsplit("/",1)[-1],"title":w.get("title") or "",
                "authors":auth,"year":w.get("publication_year") or "","doi":w.get("doi") or "",
                "query":query,"pdf_urls":urls,"landing_url":best.get("landing_page_url") or "",
                "host_source":((best.get("source") or {}).get("display_name") or "OpenAlex"),
                "license":best.get("license") or "","oa_status":oa.get("oa_status") or ""})
        cursor=(payload.get("meta") or {}).get("next_cursor")
        if not cursor or not payload.get("results"): break
    return results

def download(rec, root, max_bytes):
    last_err=""; http=""
    for url in rec["pdf_urls"][:4]:
        tmp=None
        try:
            with session().get(url,stream=True,timeout=(20,90),allow_redirects=True) as r:
                http=str(r.status_code); r.raise_for_status()
                clen=int(r.headers.get("content-length") or 0)
                if clen>max_bytes: raise ValueError("oversize")
                h=hashlib.sha256(); size=0; head=b""
                tmp=root/(rec["openalex_id"]+".part")
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(131072):
                        if not chunk: continue
                        if len(head)<1024: head+=chunk[:1024-len(head)]
                        size+=len(chunk)
                        if size>max_bytes: raise ValueError("oversize")
                        h.update(chunk); f.write(chunk)
                if b"%PDF-" not in head: raise ValueError("not_pdf_bytes")
                digest=h.hexdigest(); final=root/(digest+".pdf")
                if final.exists(): tmp.unlink(missing_ok=True)
                else: tmp.replace(final)
                out=dict(rec); out.pop("pdf_urls",None)
                out.update(source_id=f"OA-{rec['openalex_id']}",pdf_url=url,file_path=final.as_posix(),
                    sha256=digest,file_size_bytes=size,access_date=date.today().isoformat(),
                    access_status="downloaded",http_status=http,error="")
                return out
        except Exception as e:
            last_err=f"{type(e).__name__}: {e}"
            if tmp: tmp.unlink(missing_ok=True)
    out=dict(rec); out.pop("pdf_urls",None)
    out.update(source_id=f"OA-{rec['openalex_id']}",pdf_url=rec["pdf_urls"][0],file_path="",sha256="",
        file_size_bytes="",access_date=date.today().isoformat(),access_status="failed",
        http_status=http,error=last_err)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--target",type=int,default=2500)
    ap.add_argument("--workers",type=int,default=20); ap.add_argument("--pages",type=int,default=10)
    ap.add_argument("--mailto",default="openalex-harvester@example.org")
    ap.add_argument("--max-mb",type=int,default=80); ap.add_argument("--query-start",type=int,default=0)
    ap.add_argument("--candidate-cap",type=int,default=0,
                    help="Start downloads after this many new candidates (default: remaining target)")
    args=ap.parse_args(); base=Path(__file__).resolve().parents[1]
    pdfroot=base/"PAPERS_BULK"; metaroot=base/"METADATA"/"batches_bulk"
    pdfroot.mkdir(exist_ok=True); metaroot.mkdir(parents=True,exist_ok=True)
    state=metaroot/"harvest_state.jsonl"; lock=threading.Lock(); old=load_jsonl(state)
    done_ids={x.get("openalex_id") for x in old}; good={x.get("sha256") for x in old if x.get("sha256")}
    success=sum(x.get("access_status")=="downloaded" and x.get("sha256") for x in old)
    candidates=[]; seen=set(done_ids)
    cap=args.candidate_cap or max(500,args.target-success)
    for q in QUERIES[args.query_start:]:
        if success>=args.target or len(candidates) >= cap: break
        found=discover(q,args.pages,args.mailto)
        for x in found:
            if x["openalex_id"] not in seen: seen.add(x["openalex_id"]); candidates.append(x)
        print(f"discovered query={q!r} new={len(candidates)}",flush=True)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shard=metaroot/f"openalex_{stamp}.csv"; rows=[]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs={ex.submit(download,x,pdfroot,args.max_mb*1024*1024):x for x in candidates}
        for i,f in enumerate(as_completed(futs),1):
            row=f.result()
            if row.get("sha256") in good:
                p=Path(row["file_path"]); p.unlink(missing_ok=True); row["access_status"]="duplicate_sha256"; row["file_path"]=""
            elif row.get("sha256"): good.add(row["sha256"]); success+=1
            rows.append(row); append_jsonl(state,row,lock)
            if i%25==0: print(f"processed={i}/{len(candidates)} verified_unique_total={success}",flush=True)
            if i%100==0:
                checkpoint=metaroot/f"openalex_{stamp}_checkpoint_{i:06d}.csv"
                with checkpoint.open("w",newline="",encoding="utf-8-sig") as f:
                    w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
            if success>=args.target:
                for ff in futs: ff.cancel()
                break
    with shard.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    report={"completed_at":datetime.now(timezone.utc).isoformat(),"target":args.target,
            "verified_unique_total":success,"processed_this_run":len(rows),
            "downloaded_this_run":sum(r["access_status"]=="downloaded" for r in rows),
            "failed_this_run":sum(r["access_status"]=="failed" for r in rows),"batch_csv":str(shard)}
    (metaroot/"latest_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))

if __name__=="__main__": main()
