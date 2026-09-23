#!/usr/bin/env python3
"""Resumable Europe PMC negotiation PDF harvester (verified bytes only)."""
from __future__ import annotations
import argparse,csv,hashlib,json,re,threading
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date,datetime,timezone
from pathlib import Path
import requests
from urllib.parse import quote
FIELDS=["source_id","pmcid","title","authors","year","doi","query","pdf_url","landing_url","host_source","license","file_path","sha256","file_size_bytes","access_date","access_status","http_status","error"]
UA="donna-negotiation-corpus/1.0 (text-mining; contact: corpus@example.org)"; tls=threading.local()
def sess():
    if not hasattr(tls,"s"): tls.s=requests.Session(); tls.s.headers.update({"User-Agent":UA})
    return tls.s
def load(p):
    out=[]
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try: out.append(json.loads(line))
            except: pass
    return out
def discover(limit, topic_query):
    q=f'OPEN_ACCESS:Y AND FIRST_PDATE:[1900-01-01 TO 2025-12-31] AND ({topic_query})'; cur="*"; out=[]
    while len(out)<limit:
        p={"query":q,"format":"json","resultType":"core","pageSize":1000,"cursorMark":cur,"synonym":"false"}
        r=requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",params=p,headers={"User-Agent":UA},timeout=90); r.raise_for_status(); j=r.json(); got=j.get("resultList",{}).get("result",[])
        for x in got:
            if x.get("pmcid"):
                out.append({"pmcid":x["pmcid"],"title":x.get("title","") or "","authors":x.get("authorString","") or "","year":x.get("pubYear","") or "","doi":x.get("doi","") or "","query":q,"landing_url":f"https://pmc.ncbi.nlm.nih.gov/articles/{x['pmcid']}/","host_source":"PubMed Central","license":x.get("license","") or ""})
                if len(out)>=limit: break
        nxt=j.get("nextCursorMark"); print(f"europepmc discovered={len(out)}",flush=True)
        if not got or not nxt or nxt==cur: break
        cur=nxt
    return out
def fetch(rec,root,maxbytes):
    tmp=None; http=""
    try:
        listing=sess().get("https://pmc-oa-opendata.s3.amazonaws.com/",params={"list-type":"2","prefix":rec["pmcid"]+"."},timeout=45)
        http=str(listing.status_code); listing.raise_for_status()
        keys=re.findall(r"<Key>([^<]+\.pdf)</Key>",listing.text,re.I)
        if not keys: raise ValueError("no_s3_pdf")
        url="https://pmc-oa-opendata.s3.amazonaws.com/"+quote(keys[-1],safe="/")
        with sess().get(url,stream=True,timeout=(20,120)) as p:
            http=str(p.status_code); p.raise_for_status(); h=hashlib.sha256(); size=0; head=b""; tmp=root/(rec["pmcid"]+".part")
            with tmp.open("wb") as f:
                for chunk in p.iter_content(131072):
                    if not chunk: continue
                    if len(head)<1024: head+=chunk[:1024-len(head)]
                    size+=len(chunk)
                    if size>maxbytes: raise ValueError("oversize")
                    h.update(chunk); f.write(chunk)
            if b"%PDF-" not in head: raise ValueError("not_pdf_bytes")
            digest=h.hexdigest(); final=root/(digest+".pdf")
            if final.exists(): tmp.unlink(missing_ok=True)
            else: tmp.replace(final)
            return {**rec,"source_id":"EPMC-"+rec["pmcid"],"pdf_url":url,"file_path":final.as_posix(),"sha256":digest,"file_size_bytes":size,"access_date":date.today().isoformat(),"access_status":"downloaded","http_status":http,"error":""}
    except Exception as e:
        if tmp: tmp.unlink(missing_ok=True)
        return {**rec,"source_id":"EPMC-"+rec["pmcid"],"pdf_url":"","file_path":"","sha256":"","file_size_bytes":"","access_date":date.today().isoformat(),"access_status":"failed","http_status":http,"error":f"{type(e).__name__}: {e}"}
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--target",type=int,default=5000); ap.add_argument("--workers",type=int,default=32); ap.add_argument("--max-mb",type=int,default=80)
    ap.add_argument("--query",default="TITLE_ABS:negotiation OR TITLE_ABS:negotiations OR TITLE_ABS:bargaining"); a=ap.parse_args()
    base=Path(__file__).resolve().parents[1]; root=base/"PAPERS_BULK"; meta=base/"METADATA"/"batches_bulk"; root.mkdir(exist_ok=True); meta.mkdir(parents=True,exist_ok=True)
    state=meta/"europepmc_s3_state.jsonl"; old=load(state); done={x.get("pmcid") for x in old}; hashes={x.get("sha256") for x in old if x.get("sha256")}; good=sum(x.get("access_status")=="downloaded" and bool(x.get("sha256")) for x in old)
    candidates=[x for x in discover(max(1000,a.target-good+len(done)),a.query) if x["pmcid"] not in done]; stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); rows=[]; lock=threading.Lock()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs=[ex.submit(fetch,x,root,a.max_mb*1024*1024) for x in candidates]
        for i,f in enumerate(as_completed(futs),1):
            row=f.result()
            if row.get("sha256") in hashes: Path(row["file_path"]).unlink(missing_ok=True); row["file_path"]=""; row["access_status"]="duplicate_sha256"
            elif row.get("sha256"): hashes.add(row["sha256"]); good+=1
            rows.append(row)
            with lock,state.open("a",encoding="utf-8") as z: z.write(json.dumps(row,ensure_ascii=False)+"\n"); z.flush()
            if i%25==0: print(f"processed={i}/{len(candidates)} verified_unique_total={good}",flush=True)
            if i%100==0:
                cp=meta/f"europepmc_{stamp}_checkpoint_{i:06d}.csv"
                with cp.open("w",newline="",encoding="utf-8-sig") as z: w=csv.DictWriter(z,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
            if good>=a.target:
                for ff in futs: ff.cancel()
                break
    shard=meta/f"europepmc_{stamp}.csv"
    with shard.open("w",newline="",encoding="utf-8-sig") as z: w=csv.DictWriter(z,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    report={"completed_at":datetime.now(timezone.utc).isoformat(),"target":a.target,"verified_unique_total":good,"processed_this_run":len(rows),"downloaded_this_run":sum(x["access_status"]=="downloaded" for x in rows),"failed_this_run":sum(x["access_status"]=="failed" for x in rows),"batch_csv":str(shard)}
    (meta/"europepmc_latest_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8"); print(json.dumps(report,indent=2))
if __name__=="__main__": main()
