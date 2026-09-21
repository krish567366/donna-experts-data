# Psychology case-study acquisition

This isolated acquisition run queried the authoritative Europe PMC REST API for open-access
case reports, case studies, and case series whose titles also contain psychology, psychotherapy,
psychiatric, behavioral, trauma, depression, anxiety, psychosis, conflict, mediation, or
negotiation terminology.

Run date: 2026-09-21 UTC

- API records inspected: 100
- Eligible records retained: 74
- Explicit licenses: 74 CC BY
- Direct PDF URLs resolved from NCBI PMC article metadata: 19
- PDFs acquired: 0
- Negotiation/conflict/mediation title matches: 0

The attempted PMC PDF requests returned HTML bot-verification interstitials rather than PDF
bytes. No interstitial was bypassed and no invalid HTML was saved as a PDF. The 19 resolved PDF
URLs remain in the manifest for a compliant later retrieval environment. All records have a PMCID,
an explicit compatible license from Europe PMC, and a title that satisfies the case-evidence query.

`case_study_sources.csv` uses the core schema of the parent corpus manifest. `provenance.json`
records the exact API query and filtering method. `acquire_case_studies.py` reproduces the run.
