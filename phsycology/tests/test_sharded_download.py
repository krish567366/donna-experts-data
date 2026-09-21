import hashlib, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from sharded_download import RateLimiter, download_one, safe_retrieval_url, select_shard

class FakeResponse:
    def __init__(self, data): self.data, self.offset = data, 0
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size=-1):
        if size < 0: size = len(self.data)
        chunk = self.data[self.offset:self.offset + size]; self.offset += len(chunk); return chunk

class ShardedDownloadTests(unittest.TestCase):
    def test_shards_are_disjoint_and_complete(self):
        rows = [{"SOURCE_ID": f"W{i}"} for i in range(100)]
        ids = [{r["SOURCE_ID"] for r in select_shard(rows, i, 7)} for i in range(7)]
        self.assertEqual(set.union(*ids), {r["SOURCE_ID"] for r in rows})
        self.assertTrue(all(ids[i].isdisjoint(ids[j]) for i in range(7) for j in range(i)))
    def test_secret_removed_from_provenance_url(self):
        self.assertEqual(safe_retrieval_url("https://x/a?api_key=secret&ok=1"), "https://x/a?ok=1")
    @patch("sharded_download.urllib.request.urlopen")
    def test_download_writes_pdf_and_sidecars(self, urlopen):
        payload = b"%PDF-1.7\nunit test"; urlopen.return_value = FakeResponse(payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); row = {"SOURCE_ID": "W1", "FILE_PATH": "corpus/W1/original.pdf", "LICENSE": "cc-by", "OA_PDF_URL": "https://x/a?token=secret", "OPENALEX_ID": "W1"}
            result = download_one(row, root, "", 5, 1, RateLimiter(0), 0)
            self.assertEqual(result["STATUS"], "ACQUIRED"); self.assertEqual(result["FILE_SHA256"], hashlib.sha256(payload).hexdigest())
            self.assertTrue((root / row["FILE_PATH"]).exists()); self.assertNotIn("secret", (root / "corpus/W1/provenance.json").read_text())
    def test_rejects_unsafe_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            result = download_one({"SOURCE_ID":"W1", "FILE_PATH":"../escape.pdf", "LICENSE":"cc-by"}, Path(directory), "", 5, 1, RateLimiter(0), 0)
        self.assertEqual(result["STATUS"], "SKIPPED_PATH")

if __name__ == "__main__": unittest.main()
