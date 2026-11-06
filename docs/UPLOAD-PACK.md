# Offline upload packs

`upload_pack.py` prepares files for an owner to upload by hand. It never signs in,
uses an account, contacts a service, sends a message, publishes, or uploads.
The first line of every checklist is `nothing was uploaded`.

Run from the robot checkout:

```sh
python3 upload_pack.py /path/to/built-product /path/to/upload-packs
```

Or use the Python API:

```python
import upload_pack

pack = upload_pack.make("/path/to/built-product", "/path/to/upload-packs")
```

The returned `pathlib.Path` is `/path/to/upload-packs/built-product/`. The product
ID is the input folder's name. Input must be a flat folder made by the robot;
symlinks and subdirectories are refused without following them. Output must not
overlap the input. Existing packs are refused rather than overwritten. Use a new
output directory for a new revision.

## Output files

- `product.zip`: only root PDF and XLSX files, `LICENSE.txt`, and any supplied
  `README`, `README.md`, or `README.txt`. Bytes and filenames are preserved.
  Listing files, covers, previews, internal notes, ZIP/DOCX files, and all
  `.enc`, database and JSON state files are excluded by this allowlist. Database
  journal/WAL/SHM sidecars and files with a state extension before a backup
  extension are also excluded and never scanned.
- `listing.txt`: Title, Description, comma-separated Tags, and a What you get
  list, copied from `listing.md` with Markdown formatting removed. Link labels
  remain; Markdown link targets and image markup are removed.
- `cover.png`: `cover.png` from the input if present, otherwise the first image
  in filename order. PNG bytes are copied; JPEG/WebP images are converted to PNG
  using Pillow. If there is no listing image, this file is omitted and the
  checklist tells the owner to choose a cover. No cover is invented.
- `checklist.txt`: separate plain-English instructions for Gumroad and Topmate,
  including title, description, tags where available, owner-set price, buyer file
  upload, cover, and the owner's final review and publishing decision.

The expected count is four files with a cover, three without one. Files are
assembled in a temporary directory beside the destination and renamed into
place only after successful writing. On validation failure no output is created;
on write failure no partial product pack remains.

## Readiness and prices

The real `truth_check.check(product_folder)` runs with image checks enabled.
Only the exact `ready` verdict passes. Other verdicts raise `ValueError` with
the checker's reasons. A README, licence, listing and at least one PDF or XLSX
buyer file are also required.

The listing uses the builder's Markdown layout:

```markdown
# Product title
## Description
Honest description of the actual files.
## Tags (2)
- printable planner
- focus
## What you get
- A4 PDF
- US Letter PDF
- README and personal use licence
```

`What's included`, `What's inside` and the launch builder's `Included Files` are
accepted as alternate final section headings. Missing fields cause an error; the exporter does not invent listing
copy or infer missing deliverables. Headings and bullet formatting are removed
in the resulting plain text.

Money amounts are refused, never silently removed. This includes zero prices,
currency symbols, codes before or after numbers, and words such as dollars or
rupees with numbers. Zero amounts are also rejected. The owner sets the price
in the platform themselves.
Years, page counts, percentages and currency names without amounts are allowed.

Checks cover filenames, flat-folder text (including listing, README, licence
and sidecar notes), all robot PDF pages, XLSX XML string content including cached
string results, the image text reconstructed by `truth_check.image_texts`, and
the generated listing and checklist. Markdown, HTML entities and Unicode number
forms are normalized before matching. XLSX absolute formula references and
number-format codes are not treated as written money amounts.

This is intended for this robot's own files. PDF inspection uses the robot's
uncompressed text operators; unreadable or non-robot PDFs are refused. Image
text uses the existing checker's reconstruction, not OCR of arbitrary imported
artwork. Encrypted files, databases and JSON state are never opened by the
exporter. The routine does not modify its source or production runtime state.

The current builder writes a numeric price into `listing.md`, including when a
spec supplies zero, and does not emit a What you get section. Such an unchanged
build cannot pass this exporter. Record its refusal in the index; do not disable
the price gate, patch the builder, or erase prices just to create an upload pack.

## Tests

```sh
python3 -m unittest tests.test_upload_pack
```

The tests use temporary folders and clear every inherited `GITHUB_*` environment
variable with `mock.patch.dict` in `setUp`. They exercise the real truth checker,
archive contents, plain text, prices in different file types, optional covers,
CLI exits, source preservation, and failed writes. No account is needed.

For the P3 acceptance run, also run the specifically requested full parity gate:

```sh
CI_PARITY_PYTHON=/path/to/python3.11-or-newer scripts/ci_parity.sh
```

The full gate requires Pillow and Poppler and runs both `CHILLI_TEST=1` and
normal local mode. The focused command above is sufficient during development.
