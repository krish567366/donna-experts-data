# Corpus storage and release policy

The Git repository is the control plane for acquisition code, source metadata,
checksums, and provenance. It is **not** the binary store for the 10,000-document
corpus. Store acquired PDFs in an S3-compatible object store and publish only
records that have passed the license checks described below.

## Why object storage is the default

GitHub blocks ordinary Git objects larger than 100 MiB and recommends keeping a
repository below 10 GB on disk. Git LFS avoids the per-object Git limit, but it
is metered: every version of a binary consumes storage, and every download uses
the repository owner's bandwidth. GitHub Free and Pro currently include 10 GiB
each of LFS storage and monthly bandwidth. A 10,000-PDF collection is therefore
likely to need paid LFS or, preferably, a separate object store.

Official references:

- [GitHub repository limits](https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits)
- [Git LFS billing and included quotas](https://docs.github.com/en/billing/concepts/product-billing/git-lfs)
- [About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)

## Object layout

Use one immutable object per acquired file:

```text
s3://BUCKET/PREFIX/corpus/<SOURCE_ID>/original.pdf
```

Keep `metadata.json` and `provenance.json` beside the local PDF while acquiring.
Commit the generated source manifest after verification; its `FILE_SHA256` and
`FILE_BYTES` fields are the integrity index. Do not overwrite a published object
with different bytes. If a source changes, preserve the earlier object and use a
versioned key or object-store versioning.

Recommended bucket controls:

- private by default, with public access granted only to an explicitly approved
  redistributable corpus;
- object versioning enabled;
- server-side encryption enabled;
- lifecycle rules for abandoned multipart uploads, but no automatic deletion of
  released objects;
- least-privilege upload credentials kept outside the repository;
- a download/CDN budget and rate limits appropriate for the corpus size.

## Configure an S3-compatible remote

Copy `config/storage.env.example` to a file outside the repository or inject the
same variables through the shell/CI secret store. Never commit credentials.

The examples below use `rclone`; configure a remote named `psychology-store`
for AWS S3, Cloudflare R2, Backblaze B2 S3, MinIO, or another compatible service.

Estimate the upload before transferring:

```powershell
$files = Get-ChildItem corpus -Recurse -Filter original.pdf
$bytes = ($files | Measure-Object Length -Sum).Sum
"files=$($files.Count) bytes=$bytes GiB=$([math]::Round($bytes / 1GB, 2))"
rclone size corpus --include "*/original.pdf"
```

Upload without deleting remote data:

```powershell
rclone copy corpus psychology-store:BUCKET/PREFIX/corpus `
  --include "*/original.pdf" `
  --checksum `
  --immutable `
  --transfers 16 `
  --checkers 32 `
  --progress
```

`copy` is intentionally used instead of `sync`: an accidental local deletion
must not remove a released remote document. `--immutable` rejects attempts to
replace an object whose contents differ. Adjust concurrency to provider limits.

Audit the remote after upload:

```powershell
rclone check corpus psychology-store:BUCKET/PREFIX/corpus `
  --include "*/original.pdf" `
  --one-way `
  --download
rclone lsf psychology-store:BUCKET/PREFIX/corpus `
  --include "*/original.pdf" `
  --recursive `
  --files-only | Measure-Object -Line
```

The release gate is: 10,000 successful acquisition records, 10,000 distinct
object keys, matching byte counts and SHA-256 values, and zero unresolved license
or retraction-review flags. Do not treat a successful HTTP download as permission
to redistribute.

## License and provenance gate

Before an object can be shared, its manifest record must contain:

1. a stable source identifier and canonical landing-page URL;
2. the direct acquisition URL and acquisition timestamp;
3. an allow-listed license with evidence from the publisher or repository;
4. `FILE_SHA256` and `FILE_BYTES` computed from the stored file;
5. an access state indicating successful verification;
6. a non-retracted status, or a documented review decision.

If license terms are missing, ambiguous, non-redistributable, or limited to
personal access, retain metadata and source links only. Publicly accessible does
not mean redistributable. Takedown requests should quarantine the object first,
then remove public access after validating the requested identifier and hash;
retain an auditable tombstone in the manifest.

## Optional Git LFS profile

The repository's `.gitattributes` routes force-added corpus PDFs through Git LFS
as a safety net, while `.gitignore` keeps them out of commits by default. Use LFS
only for a deliberately selected, quota-sized sample—not the full corpus:

```powershell
git lfs install
git lfs env
git check-attr filter -- phsycology/corpus/example/original.pdf
git lfs ls-files
```

Before pushing any sample, set a GitHub LFS budget and verify the account's
current storage/bandwidth allowance. Never use `git add -f phsycology/corpus` for
the complete collection.

## Restore

To materialize a working copy without mixing binaries into Git history:

```powershell
rclone copy psychology-store:BUCKET/PREFIX/corpus corpus `
  --include "*/original.pdf" `
  --transfers 16 `
  --checkers 32 `
  --progress
python acquire.py verify
```

Keep a second independent copy or provider replication for disaster recovery.
Object versioning is useful recovery history, but it is not a substitute for a
separately controlled backup.
