import csv
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_corpus import build_report, normalized_doi, render_markdown


class ValidationTests(unittest.TestCase):
    def test_normalized_doi(self):
        self.assertEqual(normalized_doi("HTTPS://DOI.ORG/10.1/ABC/"), "10.1/abc")

    def test_report_finds_duplicates_and_taxonomy_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.csv"
            taxonomy = root / "taxonomy.csv"
            fields = ["SOURCE_ID", "DOI", "FILE_SHA256", "YEAR", "PSYCHOLOGY_DOMAIN",
                      "CONSTRUCTS", "LICENSE", "ACCESS_STATUS", "RETRACTION_STATUS",
                      "SOURCE_TYPE", "FILE_PATH"]
            rows = [
                dict.fromkeys(fields, "") | {"SOURCE_ID": "A", "DOI": "https://doi.org/10.1/X",
                    "YEAR": "2020", "PSYCHOLOGY_DOMAIN": "trust", "CONSTRUCTS": "TRUST",
                    "LICENSE": "cc-by", "ACCESS_STATUS": "DISCOVERED"},
                dict.fromkeys(fields, "") | {"SOURCE_ID": "a", "DOI": "10.1/x", "YEAR": "bad",
                    "PSYCHOLOGY_DOMAIN": "trust", "CONSTRUCTS": "TRUST; POWER",
                    "LICENSE": "cc0", "ACCESS_STATUS": "ACQUIRED", "FILE_PATH": "missing.pdf"},
            ]
            with manifest.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
            taxonomy.write_text("domain,construct,query,priority\ntrust,TRUST,x,5\nemotion,ANGER,y,5\n", encoding="utf-8")
            report = build_report(manifest, taxonomy)
            self.assertEqual(report["summary"]["manifest_rows"], 2)
            self.assertEqual(report["coverage"]["by_construct"], {"TRUST": 2, "POWER": 1})
            self.assertEqual(len(report["duplicates"]["source_ids"]), 1)
            self.assertEqual(len(report["duplicates"]["dois"]), 1)
            self.assertEqual(report["taxonomy_coverage"]["constructs_without_documents"], ["ANGER"])
            self.assertEqual(len(report["files"]["missing_files"]), 1)
            self.assertIn("Psychology corpus coverage report", render_markdown(report))


if __name__ == "__main__":
    unittest.main()
