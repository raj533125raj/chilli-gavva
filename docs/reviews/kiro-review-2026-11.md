# Code Review — November 2026
**Files reviewed:** sell_ready.py, store_kits.py, tg_kits.py, tg_ready.py, builder_flashcards.py, builder_eventplanner.py, format_registry.py

---

## Critical (data-loss / always-wrong)

### 1. builder_flashcards.py:164 — `io` used without being imported
`_build_pdf_page` calls `__import__("io").BytesIO()`. When Pillow is available this works, but `_generate_previews` (line 569) calls `io.BytesIO()` directly while `io` is never imported at the module level. The function is guarded by `HAS_PILLOW`, but the missing import makes the whole module fail to run under any path that reaches `_generate_previews` with Pillow present. Add `import io` at the top of the file.

### 2. builder_flashcards.py:302-309 — Back card rendered at the same (x, y) as the front card
Both `front_elem` and `back_elem` are placed at `{"x": x, "y": y}`, so they stack exactly on top of each other on the same page. A duplex-aligned back page should either be generated as a separate page (flipped horizontally) or placed on a second pass. As-is, buyers get two identical overlapping rectangles per card position — the back side of every card is never visible.

### 3. builder_flashcards.py:238-241 — `cover.pdf` contains PNG bytes
`_build_pdf_page` saves a Pillow image with `format="PNG"` and returns those bytes. They are then written to `cover.pdf`. A file named `.pdf` containing raw PNG bytes will fail to open in any PDF reader. The file should be saved as `cover.png` or the builder should create a real PDF page (e.g. by using `pdf_design`).

### 4. sell_ready.py:342 — `find_marketing_dir` uses a CWD-relative path
`base = marketing_base or Path("exported_content/marketing")` creates a relative path. When `sell_ready` is imported as a module from a different working directory (e.g. a test runner in a temp dir or the web server), `is_dir()` returns False and the whole marketing check always scores 0. Use `Path(__file__).resolve().parent / "exported_content" / "marketing"` as the fallback or require the caller to supply an absolute path.

### 5. format_registry.py:213 — `dict | None` annotation fails on Python 3.9
`def get_format_info(format_id: str) -> dict | None:` uses the PEP 604 union syntax introduced in Python 3.10. This module-level annotation is evaluated at import time on Python 3.9, raising `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`. Same issue on lines 207 (`list[str]`), 86 (`list[str]`), and 229 (`str | None`). Every caller that imports `format_registry` will also fail. Fix: use `Optional[dict]` / `Optional[str]` with `from typing import Optional`, or add `from __future__ import annotations` to defer evaluation.

### 6. store_kits.py:593 — `Path | str` annotation fails on Python 3.9
`def build(pack_dir: Path | str)` and `discover_ready_packs(products_dir: Path | str = …)` have the same 3.10+ union syntax problem. Same fix as #5.

### 7. tg_kits.py:221 — `dict | None` annotation fails on Python 3.9
`def handle(text: str, ctx: dict) -> dict | None:` — same issue. This is the shared plugin interface; every plugin that uses this return annotation will fail to import on Python 3.9.

---

## High (wrong output / data skew)

### 8. sell_ready.py:721 — SELLABLE requires `not all_fixes` — unreachable with any warning
The condition is `overall_score >= SELLABLE_MIN_SCORE and not all_fixes`. Because `all_fixes` accumulates issues from all nine checkers (including low-weight ones like "Design theme not specified"), a pack scoring 90/100 will never reach SELLABLE if it has *any* issue — including optional items like a missing reel or blog post. In practice every pack ends up ALMOST rather than SELLABLE. The gate should distinguish hard blockers from optional improvements; check `not hard_fails` instead of `not all_fixes`, or use a separate `blocker_fixes` list.

### 9. sell_ready.py:553-600 — `inspect_design_theme` scores missing theme at 40, not 0
`score = 100 if has_theme else 40`. This non-zero partial score when the theme is absent inflates the weighted contribution, masking the absence. If a theme is genuinely optional, weight it 0; if it matters, the score should be 0 when missing to reflect that in the weighted total.

### 10. format_registry.py:148-151 — `_DISCOVERED` flag prevents discovering newly added builders
Once `discover_builders()` has run once, the flag stays True and subsequent calls skip the scan even if a new `builder_*.py` was just written. Any new builder added after the first run is invisible until the process restarts. If `force_reload=False` is the default, this is acceptable for production, but tests that create temp builders in the same process will silently miss them. Document this clearly or check file modification times.

### 11. builder_eventplanner.py:1366 — `build()` uses `Dict[str, Any] | None` annotation (Python 3.9 incompatible)
Return annotation is `Union[List[str], Dict[str, Any]]`, which is fine, but the parameter `spec: Dict[str, Any] | None` is a 3.10+ union at the function signature level. Same fix as issues #5-7.

### 12. tg_kits.py:96 — `aid: int | str` parameter annotation is Python 3.10+ syntax
`def find_ready_pack_dir(aid: int | str, ...)` Same issue. At function definition time Python 3.9 will raise `TypeError` before the function can be called.

### 13. store_kits.py:582-584 — Silent currency sanitization in generated kit content
`generate_kit_text` calls `sanitize_no_money(content)` if money is detected, silently replacing amounts with `[PRICE SET BY OWNER]`. If listing.txt passed `parse_listing_file`'s money check, this branch should never fire — but if it does fire it means a currency amount slipped through, and the right response is to raise an error (stop, tell the owner), not silently mangle the output. The owner might not notice the substitution and publish a kit missing real content.

### 14. builder_flashcards.py:173-243 — All files written inside `out_dir/marketing/` subdirectory
The `build()` contract says it writes files to `out_dir` and returns their names. Everything — including the README, LICENSE, and cover — is written under `out_dir/marketing/` instead of `out_dir`. The file names returned are bare names (e.g. `"README-HOW-TO-INSTALL.txt"`), not relative to `out_dir`, so callers resolving `out_dir / name` will not find the files. This breaks every downstream tool that assembles the upload pack.

---

## Medium (reliability / correctness)

### 15. sell_ready.py:342 — `find_marketing_dir` only matches by exact name or numeric prefix
If a pack folder is named `adhd-planner` (no leading digit), the function only tries an exact match. Any folder without a numeric prefix that doesn't have an exact-name marketing folder is silently scored 0 for marketing. Add a fuzzy name match (e.g. slug comparison after removing digits).

### 16. store_kits.py:271-298 — `grapheme_clusters` does not handle Regional Indicator pairs (flag emoji)
Flag emoji like 🇮🇳 are two Regional Indicator characters (U+1F1EE U+1F1F3). The function checks for ZWJ and combining marks but not Regional Indicator pairs (`0x1F1E6`-`0x1F1FF`). Trimming mid-flag would produce a broken emoji. Add a check: if both `current_cluster` and `char` are Regional Indicators, merge them.

### 17. tg_kits.py:267 — `store_kits.build(pack_dir)` is called without safety check
`_is_safe_path` is checked for the final `target_kit_file` (line 274) but `store_kits.build(pack_dir)` is called at line 267 — before the safety check. If `pack_dir` somehow resolves outside `exported_content`, build() can write arbitrary files. Move the safety check for `pack_dir` itself before calling `build()`.

### 18. sell_ready.py:432-433 — `inspect_preview_images` returns score 0 when no images found, treated as a non-hard-fail
When no preview images exist the function returns `{"score": 0, ...}`. This contributes `0 * 5% = 0` to the weighted score. The issue is that `issues` is not empty, so it shows up in `top_fixes`, but the pack is not hard-failed — a pack with zero cover images can still reach ALMOST (65+) status. Missing cover images should be a hard fail for upload readiness.

### 19. format_registry.py:170-176 — Module re-registration silently skipped if already-imported module has a different path
`if str(root_path) not in sys.path: sys.path.insert(0, str(root_path))` only inserts the path if absent, but the check to reuse an existing module uses `Path(getattr(existing, "__file__", "")...`.resolve() == file_path.resolve()`. If two builders have the same module name (e.g. `builder_planner.py` in two directories), the second is silently ignored. This is not a problem today but is a hidden invariant that should be documented.

### 20. builder_eventplanner.py — `render_cover_image` truncates title at 42 characters with hard-coded `...`
Line (inside `render_cover_image`): `if len(title) > 42: title = title[:40] + "..."`. This uses byte/character length, not grapheme count, and does not respect word boundaries. A title ending mid-word or mid-emoji produces a broken display. Use `store_kits.trim_to_word_boundary(title, 42)` instead.

### 21. sell_ready.py:472-490 — `inspect_store_kits` checks for `store_kits` module as a fallback
If `import store_kits` succeeds and `listing.txt` exists, `has_kits` is set True and `stores_found = ["generator-ready"]`. This means every pack with a listing.txt will pass the store-kit check even when no actual kit files exist, so the kit missing state is never surfaced to the owner. Remove this fallback or flag it clearly.

### 22. builder_flashcards.py — `_build_worksheet_trace` layout uses unbounded `y` extrapolation
`y = 40 + (i // 2) * (card_h + 40)`: for more than 10 characters, `y` goes below the page boundary (card_h ≈ 3400 px for A4; already with 2 characters per column the second card overflows). The function only processes `characters[:10]`, but the grid size is miscalculated (`2 columns * infinite rows`). Should calculate `rows = ceil(len(characters) / 2)` and clamp elements to the page.

### 23. tg_ready.py — `_find_target_pack` matches `^{aid}(?:-.*)?$` including leading zeros
Pack folder `01-adhd-planner` would match `aid=1` via regex `^1(?:-.*)?$` but NOT `01-adhd-planner` (the regex requires the string to start with `1`, not `01`). If folder names have zero-padded IDs, the lookup fails silently. Use `re.match(rf"^0*{aid}(?:-.*)?$", ...)` or strip leading zeros from the folder name before comparison.

---

## Low (code quality / maintainability)

### 24. format_registry.py:79 — `set[str]` lowercase generic in function return annotation (Python 3.9)
`def _tokenize(text: str) -> set[str]:` — `set[str]` as a return annotation is supported as of Python 3.9 (PEP 585). This is fine on 3.9+ but worth noting given the broader 3.9 compatibility issues in this codebase. No change needed if 3.9 is the minimum supported version.

### 25. store_kits.py:379-399 — `get_images_to_upload` appends any image not already in `images`, including internal manifest/debug images
If the pack folder contains any `.png` files other than `cover*.png` and `preview*.png` (e.g. debug outputs, CI artifacts), they are appended to the upload list. An owner following this list would upload unintended files to the store.

### 26. tg_kits.py:139-168 — `extract_first_3_fields` uses regex anchored to `^` but kit files include headers before the fields
The pattern `re.search(r"^Title:\s*(.*?)$", ...)` with `re.MULTILINE` will find the first line matching `Title:`, which may be the section header `--- FIELD: TITLE ---` on the previous line — not an issue since the actual value is on the `Title:` line. But if a title or description contains a line starting with `Category:`, the `cat_m` match could pick up a user-provided line. Low risk today, but fragile.

### 27. sell_ready.py:621-637 — `format_pack_card` renders up to 5 fixes but `evaluate_pack` only guarantees `top_fixes[:3]`
`format_pack_card` iterates `fixes[:5]` while the docstring for `evaluate_pack` says "top 3 fixes". The actual `fixes` list may be very long (all issues from all 9 checkers). Align the two functions: if the intent is top 5, update the docstring and the RULES; if top 3, change `fixes[:5]` to `fixes[:3]`.

### 28. builder_eventplanner.py — `_draw_table_row` uses `self.c_line` for rectangle stroke but fills background first with `row_bg`
When `row_bg` is `None` (odd rows), `pdf.rect(...)` is called with `color=self.c_line` which is the border color. The function reuses the same `color` parameter for both fill and stroke depending on context. Verify that `pdf_design.rect` uses `color` as stroke-only, not fill — if it fills, odd rows get the line color as a fill, washing out the alternating row style.

### 29. format_registry.py:197-200 — Module loaded into `sys.modules` before `exec_module` completes
```python
sys.modules[mod_name] = module
spec.loader.exec_module(module)
```
If `exec_module` raises an exception, the partially-initialized module stays in `sys.modules`. A subsequent import or `discover_builders` call reuses the broken module. Remove from `sys.modules` in the exception handler.

### 30. builder_flashcards.py — `discover_builders` function at module level re-implements format_registry logic
`builder_flashcards.discover_builders()` calls `format_registry.register_format()` directly. This duplicates the discovery mechanism that `format_registry.discover_builders()` already performs by scanning `builder_*.py` files. Having two registration paths means the builder might be registered twice, or the custom path runs before the registry is initialized and silently fails. Remove `discover_builders` from the builder module; rely solely on `format_registry.discover_builders()`.

### 31. sell_ready.py — `inspect_deep_qa` and `inspect_truth_check` share the same error return pattern but differ in verdicts
`inspect_truth_check` returns `verdict = "ready"` on `ImportError`, while `inspect_deep_qa` returns `verdict = "ready"` for the same case. Both set `score = 80` and `hard_fail = False`, effectively saying "assume pass" when the checker can't be loaded. This is a reasonable default for CI without all deps, but should be documented explicitly and surfaced as a warning in the issues list so the owner knows a check was skipped.

### 32. tg_kits.py:200-215 — `extract_first_3_fields` truncates silently if tags line spans multiple lines
`re.search(r"^Tags \(.*?\):\s*\n(.*?)$", kit_text, re.MULTILINE)` only matches one line of tags. If `format_tags_for_store` returned comma-separated tags that wrapped to a second line in the kit file, only the first line is surfaced in the summary. Kit files currently keep tags on one line, but this is fragile.

### 33. builder_eventplanner.py — No rollback on partial write failure in `build()`
`build()` creates files one-by-one. If the second PDF (`Letter`) or any subsequent step fails mid-way, the A4 PDF is already written to `out_path` but the caller gets an exception, leaving a partial pack. `_atomic_write_bytes` is atomic per file but `build()` as a whole is not. Consider writing everything to a temp subdirectory and renaming atomically at the end.

### 34. store_kits.py:593 — `build()` overwrites existing kit files without warning
`os.replace(tmp_file, str(target_path))` silently overwrites previously built kits. If an owner manually edited a kit file, the edits are lost silently. Either check for an existing file and refuse unless `force=True`, or log that the file was overwritten.

### 35. sell_ready.py:432-433 — `inspect_preview_images` resolves `marketing_base` from caller, not from module root
If `marketing_base` is `None`, `find_marketing_dir` defaults to `Path("exported_content/marketing")` relative to CWD. `inspect_preview_images` calls `find_marketing_dir(pack_dir, marketing_base)` passing the already-resolved marketing_base from `evaluate_pack`. This is correct, but if `evaluate_pack` is called with `marketing_base=None` from outside the repo root, both functions silently score 0. Already noted in issue #4; raised here as a second point of failure.

### 36. tg_ready.py — `handle()` does not guard against `sell_ready.evaluate_all_packs` raising on a corrupt pack
`evaluations = sell_ready.evaluate_all_packs(...)` is called without a try/except. A corrupt pack directory (e.g. a broken zip file) can bubble an unhandled exception up through the Telegram handler, silently killing the plugin for that cycle. Wrap in try/except and return a Tenglish error message.

### 37. builder_flashcards.py — `_generate_deterministic_id` uses `Random` seeded with `seed + index`
`random.Random` is not cryptographically safe, but that's fine here. The concern is that `index` passed from the caller is the loop counter `i` within a topic (0..25 for ALPHABET), so two different topics both generate IDs from indices 0-9 and 0-9 respectively, producing identical IDs (e.g. both `alphabet` card 0 and `numbers` card 0 get `card_1234`). IDs are only used in manifest JSON files today, but if they are ever used as keys, collisions will occur.

### 38. format_registry.py:231-266 — `classify` returns the longest-matched format, but `planner` keywords include generic words like "tracker", "organizer"
Keywords like `"tracker"`, `"checklist"`, `"printable"` match almost any digital product. A `Habit Tracker Spreadsheet` might score higher for `planner` than `sheets` depending on keyword weights. The priority function gives a small bonus to non-builtin formats; built-in `sheets` might be under-scored against `planner` for mixed-topic ideas. Run scoring tests for the ambiguous cases and tune weights.

### 39. sell_ready.py — `format_ready_summary` uses Markdown bold (`*word*`) but Telegram MarkdownV2 uses `**word**`
`lines.append("🟢 *SELLABLE NOW* ...")` uses single-asterisk Telegram Markdown v1. If `telegram_inbox` sends messages with `parse_mode="MarkdownV2"`, the formatting will appear as literal asterisks or raise a Telegram API error. Verify which parse_mode is used and align the format strings.

### 40. tg_kits.py:74-89 — `_is_safe_path` does not reject symlinks that escape the allowed root
`p = Path(path).resolve()` resolves symlinks, so a symlink inside `exported_content` pointing to `/etc/passwd` would resolve outside the allowed roots and be rejected. This is correct. However, `find_ready_pack_dir` does not call `_is_safe_path` before iterating the pack directory entries — a symlinked pack directory could be followed. Low risk since `listing.txt` must exist, but symlink-based traversal should be explicitly excluded.

---

*Review completed 2026-11-14. All items are observations only — no code was changed. Suggested fixes reference the standard pattern already used elsewhere in the codebase.*
