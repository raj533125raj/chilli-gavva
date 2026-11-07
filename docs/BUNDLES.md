# BUNDLES.md — Bundle Builder

The Bundle Builder packages 2–4 related, ready digital product packs into a unified bundle.
Buyers get multiple complementary products together in a single download, while the owner sets the price.

**Important:** Nothing is listed or published automatically; owner approval stays required before any bundle is offered or uploaded.

---

## 1. Bundle Suggestion (`suggest_bundles`)

The suggestion engine analyzes available packs to find natural groupings:
- **Jaccard Similarity Threshold:** Requires keyword / search tag overlap with Jaccard index $\ge 0.25$ between items.
- **Audience & Niche Matching:** Analyzes audience indicator terms (such as ADHD, Freelancer, Budget, Resume, 2027 Planners). Unrelated product niches are strictly isolated and never mixed together in a bundle.
- **Bundle Size:** Recommends cohesive sets of 2 to 4 packs.
- **Suggestion Cap:** Produces at most 5 ranked, deterministic bundle recommendations.
- **Reason Line:** Every recommendation includes an informative rationale explaining the thematic overlap, pack count, and similarity metric without exposing price amounts or internal paths.

---

## 2. Bundle Structure (`build_bundle`)

When built, a bundle is written into a dedicated private folder under:
`exported_content/products/bundles/<bundle-id>/`

Inside the folder:
- **`bundle.zip`**: Contains the member `product.zip` archives, clearly prefixed and slugified (e.g., `1-pack-name.zip`, `2-pack-name.zip`). Clean archive structure: no `__MACOSX`, no `.DS_Store`, and no absolute filesystem paths. Uncompressed size is capped under 250 MB.
- **`cover.png`**: High-resolution 1280x720 Gumroad cover image created entirely with Pillow. The member covers are rendered as an overlapping fan with gentle drop shadows and non-overlapping borders, ensuring all cover art and text remain fully visible and uncut.
- **`listing.txt`**: Standardized upload listing text containing:
  - Bundle title with pack count
  - Description summarizing each member pack and its contents
  - 13 deduplicated, clean search tags merged from member packs
  - Comprehensive "What you get" section aggregating deliverable items
  - Clear personal use license notice
  - No price or currency amounts (the owner sets the price on Gumroad / Topmate)
- **`LICENSE.txt`**: Standard personal use license copied from a member pack.

---

## 3. Quality Assurance & Reports

Before marking a bundle ready:
- The builder automatically triggers `product_qa_deep.check_folder` if available.
- The resulting QA audit report is written **OUTSIDE** the bundle directory (e.g. `exported_content/products/bundles/<bundle-id>-qa_report.json`) to keep the buyer deliverable folder pristine.
- All operations employ atomic writes using temporary staging directories and atomic renames.

---

## 4. Usage

### Command Line
```bash
# Suggest bundles from available upload packs:
python3 bundle_builder.py suggest exported_content/products/upload

# Build a bundle from specific packs:
python3 bundle_builder.py build exported_content/products/upload/pack-1 exported_content/products/upload/pack-2
```

### Python API
```python
import bundle_builder

# Suggest bundles
suggestions = bundle_builder.suggest_bundles(packs)

# Build a bundle
bundle_dir = bundle_builder.build_bundle(suggestions[0], out_dir="exported_content/products/bundles")
```
