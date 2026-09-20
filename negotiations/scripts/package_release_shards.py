"""Package verified bulk PDFs into deterministic GitHub Release ZIP shards.

The repository stores manifests and checksums. Release assets store the actual
PDF payloads without inflating Git history. Each ZIP stays below GitHub's 2 GiB
per-release-asset limit by default.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bytes", type=int, default=1_800_000_000)
    parser.add_argument("--output", type=Path, default=ROOT / "release_shards")
    args = parser.parse_args()
    manifests = sorted((ROOT / "METADATA" / "batches_bulk").glob("*.csv"))
    records = []
    for manifest in manifests:
        with manifest.open(encoding="utf-8-sig", newline="") as handle:
            records.extend(csv.DictReader(handle))
    unique = {}
    for row in records:
        rel = (row.get("FILE_PATH") or "").replace("\\", "/")
        path = (REPO / rel) if rel.startswith("negotiations/") else (ROOT / rel)
        if not path.is_file() or path.read_bytes()[:5] != b"%PDF-":
            continue
        digest = row.get("SHA256") or sha256(path)
        unique.setdefault(digest, (path, row))
    args.output.mkdir(parents=True, exist_ok=True)
    shard_index = []
    shard_no, shard_size, archive = 0, 0, None
    for digest, (path, row) in sorted(unique.items()):
        size = path.stat().st_size
        if archive is None or (shard_size and shard_size + size > args.max_bytes):
            if archive is not None:
                archive.close()
            shard_no += 1
            shard_path = args.output / f"negotiation-corpus-{shard_no:04d}.zip"
            archive = zipfile.ZipFile(shard_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True)
            shard_size = 0
        arcname = f"pdf/{digest[:2]}/{digest}.pdf"
        archive.write(path, arcname)
        archive.writestr(f"metadata/{digest}.json", json.dumps(row, ensure_ascii=False, indent=2))
        shard_index.append({"SOURCE_ID": row.get("SOURCE_ID", ""), "SHA256": digest,
                            "SHARD": f"negotiation-corpus-{shard_no:04d}.zip", "MEMBER": arcname,
                            "FILE_SIZE_BYTES": size})
        shard_size += size
    if archive is not None:
        archive.close()
    with (ROOT / "METADATA" / "release_shard_index.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["SOURCE_ID","SHA256","SHARD","MEMBER","FILE_SIZE_BYTES"])
        writer.writeheader(); writer.writerows(shard_index)
    checksums = []
    for path in sorted(args.output.glob("*.zip")):
        checksums.append(f"{sha256(path)}  {path.name}")
    (args.output / "SHA256SUMS.txt").write_text("\n".join(checksums) + ("\n" if checksums else ""), encoding="utf-8")
    print(json.dumps({"pdfs": len(shard_index), "shards": shard_no}, indent=2))


if __name__ == "__main__":
    main()
