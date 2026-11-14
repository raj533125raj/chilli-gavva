# Code Review B — November 2026
**Files reviewed:** support_kit.py, report_pdf.py, report_charts.py, tg_reportpdf.py, db_maintenance.py, tg_dbcare.py, seo_helper.py, tg_seo.py

---

## Critical — data loss / always-wrong

### 1. report_pdf.py:415,432 — Tenglish text silently dropped from PDF streams
Page content ops are encoded with `.encode("latin-1")` and PDF object strings with `.encode("latin-1")` as well. `_scrub` passes text through `pdf_design._pdf_text`, which calls `_winansi`, which silently drops any character that cannot be encoded in cp1252 — this includes every Telugu character (U+0C00–U+0C7F) and every Unicode symbol outside the cp1252 range. The owner's Tenglish strings (e.g. `"Ee week ki inka data ledu; robot ready ga undi."`) have only Latin characters so they survive today, but any title or provider name that contains a Telugu Unicode character will appear as a blank run in the PDF with no warning. Add a check in `_scrub` that replaces such characters with a safe ASCII fallback rather than relying on silent dropping.

### 2. report_pdf.py:389-395 — `save()` mutates `saved_pages` while iterating it
In `save()`, `self.pages = [ops]` replaces the pages list; then `_footer` calls `_ops()` which returns `self.pages[-1]`, which is `[ops]` — the *same* list object as `saved_pages[index]`. So `_footer` appends its PDF ops in-place to the original page list in `saved_pages`. After the loop `self.pages = saved_pages` is set back. This works as written but is fragile: if `_footer` ever started calling `new_page()` (which does `self.pages.append([])`), it would append to the outer `saved_pages` list rather than the current page, silently skipping the footer on some pages and writing a corrupted PDF. The intent is clear; make it explicit: append the footer ops directly to `saved_pages[index]` instead of relying on aliasing.

### 3. tg_reportpdf.py:57 — Weekly report PDF written to `tempfile.gettempdir()` and never deleted
`build_weekly_report(..., out_dir=tempfile.gettempdir())` writes a PDF file to the system temp directory and returns its path. The plugin sends that file and returns `{"handled": True}` without deleting the file. On a machine where the robot runs continuously, a fresh PDF is written to temp every time the owner asks for the report, accumulating copies that are never cleaned up. Write to a `tempfile.mkdtemp()` folder, send, then delete the folder in a `finally` block (the same pattern used in `tg_seo.py`).

### 4. db_maintenance.py:674 — `safe_check_copy` repo-guard check raises when temp_dir IS inside repo, passes when it is not
The logic is:
```python
try:
    base.relative_to(BASE_DIR.resolve())
except ValueError:
    pass   # not inside repo — safe
else:
    raise ValueError(...)  # inside repo — blocked
```
This is correct. However the guard is applied only when `temp_dir` is supplied explicitly. The default (`Path(tempfile.gettempdir())`) bypasses the guard entirely — if the system temp folder is somehow configured to a path inside the repo (e.g. in CI with `TMPDIR=./tmp`), the copy would be written inside the repo and potentially committed. Add the same check for the default case, or document the assumption that `tempfile.gettempdir()` is always outside the repo.

---

## High — wrong output / significant risk

### 5. support_kit.py:526-535 — `_write_if_changed` temp file leaks when `os.replace` raises
If `os.replace(temp_name, path)` raises an `OSError` (e.g. cross-device rename), the `finally` block runs `os.path.exists(temp_name)` — but after a failed `os.replace` on some platforms the original temp file still exists and is left as `.support-<random>` in the support directory. The `build()` function cleans up `.support-` files at the end, so the leak is only persistent until the next `build()` call, but if the exception propagates before that cleanup runs, stale temp files accumulate.

### 6. support_kit.py:655 — Stale `.support-` prefix files could include the incomplete PDF temp file
If `_thank_you_pdf` raises (e.g. Pillow unavailable), the `finally` block correctly unlinks `temp_name`. But if `_write_if_changed` is then called for a text file and fails, the final cleanup loop only removes files starting with `.support-`. The PDF temp file starts with `.support-` and would be cleaned. The risk is the cleanup loop at line 655 runs regardless of whether earlier writes succeeded — it deletes `.support-` files from the target directory even if `build()` is about to raise, which means partial cleanup is mixed with error propagation.

### 7. report_pdf.py:296-302 — `collect_weekly_data` queries `product_builds` and `product_deliveries` without checking those tables exist
`needs_fix` combines counts from `product_builds` and `product_deliveries` without calling `_table_exists`. Both are wrapped in a `try/finally` that closes the connection, but the individual SQL calls are not guarded by `try/except sqlite3.Error`. If either table is missing, the unhandled `sqlite3.OperationalError` propagates out of `collect_weekly_data`, causing `build_weekly_report` to fail and the plugin to return an error message to the owner. All other queries use `_count()` which swallows `sqlite3.Error` — this pair should too.

### 8. seo_helper.py:210 — Zip path traversal: only `..` and leading `/` checked
`_zip_texts` skips members whose `filename` contains `..` or starts with `/`. On Windows (and in some zip tools) path separators in zip member names can also be backslashes, and a name like `foo\..\..\etc\passwd` passes the check. While this code runs on macOS/Linux, the zip was potentially created on Windows. Add a check that `PurePosixPath(name).parts` contains no `..` component, or use `zipfile.Path` which normalises separators.

### 9. db_maintenance.py:452-458 — `prune` builds WHERE clause using `_quote(column)` from table schema but column name is from `DEFAULT_LOG_RULES`
The column name used in the WHERE clause comes from the caller-supplied `log_rules` (defaulting to `DEFAULT_LOG_RULES`), not from the table's real schema. If a caller passes a custom `log_rules` with a column name that cannot be safely quoted (e.g. it contains a null byte or a crafted string), `_quote` only escapes double-quotes — it does not reject unusual characters. In practice `DEFAULT_LOG_RULES` is a module-level constant and callers rarely pass custom rules, but the function should validate that the column name exists in the table (which it already does via the `if column not in columns: continue` check at line 453). **This is actually correct** — no real bug, but the defence-in-depth order could be clearer: the column check happens after constructing the WHERE string in memory (not yet executed), so there is no SQL injection risk. Document this clearly.

### 10. tg_seo.py:68-82 — Temp directory for SEO document is not cleaned up on `send_document` raising
The `finally` block correctly removes the temp directory. However, if `send_document` raises an exception before `return {"handled": True}`, the exception propagates out of the `try` block. The `finally` still runs, so the temp dir is cleaned — this is correct. No real bug, but the `except` path in `handle()` catches only `PackNotFoundError` and generic `Exception` before the temp-dir block; if `build_for_pack` raises after partial I/O, the SEO document may be partially written, cleaned up, and the owner gets no document. The issue is that `build_seo_document` is called after the temp dir is created — if it raises, the owner gets nothing but the error is swallowed by the outer `except`. Move `build_seo_document(output)` outside the `try/finally` block so its errors are caught by the outer `except` and reported clearly.

---

## Medium — reliability / correctness

### 11. report_pdf.py:392-394 — Footer ops appended without a `new_page()` guard
`_footer` appends PDF operator strings to the page's ops list. If `self.pages` is empty when `save()` is called (e.g. `render_weekly_report` is called without any `_cover`, `_numbers` etc.), `_ops()` raises `IndexError`. Add a guard: `if not self.pages: return` at the start of `save()` or raise a descriptive error.

### 12. seo_helper.py:300-316 — `build_title_variants` enters an infinite loop when no variants fit
The while loop `while len(variants) < 10: add(f"{base} Edition {index}")` increments `index` up to 50 then breaks. If `base` itself fails `_contains_banned()` (which happens when the listing title contains a blocked brand name that wasn't cleaned before calling `_title_variants_for`), every candidate fails the check and the loop terminates with `variants = []`. The function returns an empty list. Downstream `build_all_title_variants` then returns `{"etsy": {"variants": []}, ...}`, and the SEO document shows zero title variants without any warning. Add a fallback: if `variants` is empty after the loop, append the raw `base` string without further filtering.

### 13. seo_helper.py:437-444 — `_meta_description` can produce a string shorter than 150 chars with no warning
The function tries to pad to 150 chars, then trims to 160, then tries to pad again. The inner padding loop only iterates once more over `_META_FILLERS`, and if all fillers have already been used, the final description can be as short as ~60 chars. The function returns without a warning. `build_seo` checks the length and adds a warning only if outside 150-160; this correctly catches the short case. No action needed except ensuring `build_seo` always propagates that warning to the owner.

### 14. db_maintenance.py:482-485 — `vacuum_if_needed` reads the database stats twice, with a window for another writer
`before = database_stats(...)` and then `_work()` calls VACUUM. Between the two calls another process could write data, changing the actual free ratio. The check `needed = float(before.get("free_ratio", 0.0)) > float(threshold)` could then VACUUM when not needed or skip a needed VACUUM. For the robot's single-writer use case this is benign, but it should be documented as "advisory, not atomic."

### 15. support_kit.py:189-199 — `detect_formats` uses `_zip_pack_files` to scan inside `product.zip`, but `_pdf_is_fillable` checks only the unzipped pack root
`detect_formats` calls `_zip_has_fillable_pdf` for the zip case and `_pdf_is_fillable` for root files. `_zip_has_fillable_pdf` reads the first 512 KB of each PDF member. However if the zip is very large and the PDF is the last member, `members[:MAX_ZIP_MEMBERS]` (30 members) may skip it. The result is that `pdf` is detected but `pdf_fillable` is missed, so `HOW-TO-USE-PDF.md` is generated instead of `HOW-TO-USE-PDF-FILLABLE.md`. Low impact (buyer gets print instructions instead of fill instructions) but worth documenting.

### 16. report_charts.py:100-108 — `nice_max(0)` returns `1`, not `0`, making empty charts show a y-axis of 1
When all values are zero, `nice_max(max(values))` = `nice_max(0)` = `max(0.0, 1.0)` = `1`. The chart then shows a y-axis from 0 to 1 with four grid lines at 0.25 increments, while all bars are height 0. The "No data yet" label is printed only when `not any(values)` — so a chart with one non-zero count in an otherwise zero week will show real bars but a y-axis maximum of at least 1. This is correct. The `NO_DATA` label path (`not any(values)`) is the right guard; no bug, but the condition for printing `NO_DATA` should be verified against the case where all values are genuinely 0 — it currently prints the label, which is correct.

### 17. tg_dbcare.py:34-42 — `_CLEAN_WORDS` and `_COPY_WORDS` are plain sets, matched only on exact lowercase
`parse_action` normalises text to lowercase but the sets include strings like `"clean chey"` and `"safe copy check"`. A message like `"Clean Now"` after normalisation becomes `"clean now"`, which is in `_CLEAN_WORDS` — correct. But `"dbcare clean please"` normalises to `"dbcare clean please"`, which is not in the set and not matched by `_CMD_RE` either (the regex only matches `/dbcare [arg]`). The owner could reasonably type a phrase with an extra word and get no response. Use `startswith` for plain-text phrases or extend the regex to cover free-form Tenglish variants.

### 18. seo_helper.py:123-135 — `_candidate_files` does not limit `support/` and `content/` subdirectory depth
If `pack_dir / "support"` or `pack_dir / "content"` is itself a symlink to a large directory tree, `child.iterdir()` returns all files at one level, potentially yielding thousands of files. The function then reads each with `_read_text` up to `MAX_FILE_BYTES`. The `MAX_TOTAL_BYTES` guard stops reading after 1 MB total, but `_candidate_files` itself returns an unbounded list of paths. Add a `[:20]` cap after collecting support files, or check total file count before reading.

### 19. db_maintenance.py:555-583 — `verify_copy` opens both source and copy connections and leaks them on exception
`src, src_owned = _connection(source_path)` and `dst, dst_owned = _connection(copy_path)`. If the first integrity or row-count query raises, the `finally` block closes both connections — but the `finally` only runs `if src_owned: src.close()` and `if dst_owned: dst.close()`. This is correct; connections are always closed. No actual bug — mentioning for completeness.

### 20. report_pdf.py:62-66 — `_scrub` only removes money amounts, not secrets
`_scrub` replaces currency amounts with `[amount]` and strips null bytes. It does not detect API keys, tokens, or email addresses that could appear in provider names or task `kind` fields stored in the database. Provider names from `provider_log` are displayed in the PDF (line 142: `name = _scrub(provider or "unknown")[:40]`). If a misconfigured robot stored a token fragment as a provider name, it would appear in the PDF. Add a check for common token patterns (long hex strings, strings starting with `sk-`, `Bearer `, etc.) alongside the money check.

---

## Low — code quality / maintainability

### 21. support_kit.py:456-478 — `_support_replies` uses `[amount]` correctly but the comment says "refund amount"
The refund reply template says `"The listing shows [amount] as the price; any refund amount follows the store's policy."` The `[amount]` placeholder is correct per RULES — no actual number. However the sentence reads as if `[amount]` is a filled-in value rather than a placeholder the owner must replace. Clarify with a comment or change to `"See the store listing for the price; refunds follow the store's policy."` to avoid any risk of an owner sending the template with the literal `[amount]` to a buyer.

### 22. tg_seo.py:40-47 — `parse_pack_number` returns `0` for a command without a number, not `None`
Returning `0` for "no pack specified" is documented but non-obvious. The caller checks `pack_number <= 0` to send a "please specify a number" message. If the function is ever used in another context where `0` is a valid pack number, the check would incorrectly block it. Use `None` to mean "no pack specified" and check `if pack_number is None`.

### 23. db_maintenance.py:34-39 — `DEFAULT_LOG_RULES` includes `maintenance_log` which may not exist in all deployments
`maintenance_log` is in `DEFAULT_LOG_RULES`. The `prune` function checks `_has_table` before pruning, so a missing table is silently skipped. This is correct. The concern is that the `maintenance_log` table is not created by any `register_migration` call in the file, so there is no guaranteed migration path for it. If `tg_dbcare` is used before the table exists, the prune plan shows `"table not found"` for `maintenance_log` without explanation.

### 24. report_charts.py:60-70 — `_font` cache uses `(size, bold)` as key but `int(size)` normalises floats
`_font(14.5)` and `_font(14)` produce different cache keys but could be called with the same intent. `_font` casts to `int(size)`, so both keys load the same font at size 14. The cache then holds two entries for the same font object. Low impact — wastes one dictionary entry.

### 25. seo_helper.py:347-369 — `build_title_variants` calls `_fit` which uses `len(text)` (byte count) not grapheme count
`_fit(text, max_chars)` uses `len(text)` (Python character count), not grapheme count. For ASCII titles this is identical, but titles with accented characters (e.g. `é`, `ü`) or combining diacritics count as one `len` unit each in Python. For Etsy's 140-char limit and Gumroad's 100-char limit, this means the trimming is correct for all Latin characters. No real bug for the current product niche (English titles), but document the assumption.

### 26. report_pdf.py:97-105 — `_load_plan` silently returns `[]` for any JSON parse error
If `post_plan.json` is corrupted or contains a dict instead of a list, `_load_plan` returns `[]` without logging. The report then shows `posts_planned = 0` and `posts_done = 0`, which could mislead the owner into thinking no posts were scheduled. Add an informational note in the data: when `plan_path` exists but could not be loaded, include a warning in `data["wins"]` or `data["problems"]`.

### 27. tg_dbcare.py:55-62 — `_is_owner_chat` imports `notify` at call time and silently returns `True` on any exception
If `notify` raises for any reason (import error, attribute error), the function returns `True`, meaning any chat is treated as the owner chat. For the `/dbcare clean` action this would allow any Telegram user who can send a message to the bot to trigger a database prune. The RULES say "Owner-only Telegram" — the fallback should be `False` (deny) rather than `True` (allow), or at least log the exception.

### 28. support_kit.py:109-115 — `_iter_pack_files` uses `rglob("*")` which follows symlinks
`pack_dir.rglob("*")` follows symlinks, so a symlinked directory inside the pack could include files outside the pack root. The function then `yield`s their names (not paths), so the caller never knows they came from outside the tree. If the pack contains a symlink to a large directory, `rglob` could take a long time and produce unexpected file names. Use `pack_dir.glob("*")` (single-level) or add `follow_symlinks=False`.

### 29. seo_helper.py:479-495 — `build_seo_document` truncates the seo_helper output at the store loop
The function starts iterating `("etsy", "gum...)` but the loop variable is `style` and the access is `output["all_title_variants"]` — but `build_seo` stores them under both `"title_variants"` and `"all_title_variants"`. If only one key is populated, the document prints nothing for the title variants section. Check which key is used consistently.

### 30. db_maintenance.py:38 — `PROTECTED_TABLES` contains only `"approvals"` — `sales_ledger` table is not protected
The sales ledger table (if it exists) is not in `PROTECTED_TABLES`. A call to `prune` with a custom `log_rules` that includes the sales table and a short retention period would delete sales records permanently. Add `"sales"` and `"sales_ledger"` (the typical names) to `PROTECTED_TABLES`, or document that custom `log_rules` callers are responsible for not including business records.

---

*Review completed 2026-11-14. No code changed. All fixes are suggestions only. Database-related items (1, 3, 4, 7, 27, 30) reviewed with extra attention per task instructions.*
