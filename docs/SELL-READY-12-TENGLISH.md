# 12 product types — anni Gumroad lo sell cheyagalam (SELL-READY proof)

Date: 2026-11-15 · Tool: `python3 tools/sell_proof.py`

## Em chesam (simple ga)

Robot nijamaina daari lo (real path) prati product type ki okka pack build chesi chusindi:

1. Product Builder files tayaru chestundi (PDF / Excel / Word / HTML / Notion zip / PNG).
2. Truth Checker files ni chusi "ready" ani cheppali (listing lo cheppindi files lo undali).
3. Upload pack tayaru avutundi (`product.zip` + `listing.txt` + `checklist.txt` + `cover.png`).
4. Gumroad kit, Gumroad images, marketing files tayaru avutayi.
5. `/sell` check (sell_ready) — SELLABLE / ALMOST / NOT YET.
6. `/sell` message — Gumroad fields + files + pictures owner ki vellagalava ani check.
7. `product.zip` lo listing "What you get" lo cheppina prati file undha ani check.

Antha temporary folder lo. Emi upload avvadu, emi post avvadu, emi sell avvadu. Internet vaadaledu.

## Result: 12/12 SELLABLE

| Type | Status | Truth check | product.zip | /sell message |
|------|--------|-------------|-------------|---------------|
| sheets | SELLABLE | ready 100 | ok | ok |
| planner | SELLABLE | ready 100 | ok | ok |
| notion | SELLABLE | ready 100 | ok | ok |
| resume | SELLABLE | ready 100 | ok | ok |
| dated_planner | SELLABLE | ready 100 | ok | ok |
| event_planner | SELLABLE | ready 100 | ok | ok |
| flashcards | SELLABLE | ready 100 | ok | ok |
| habit_tracker | SELLABLE | ready 100 | ok | ok |
| recipe_book | SELLABLE | ready 100 | ok | ok |
| email_signature | SELLABLE | ready 100 | ok | ok |
| study_planner | SELLABLE | ready 100 | ok | ok |
| wall_art | SELLABLE | ready 100 | ok | ok |

Migilina "fixes" anni chinna Listing warnings matrame (block cheyavu): title / description length, tags count,
"LICENSE, README" capital words warning, Instagram carousel / reel files levu. Vatini owner Gumroad lo paste
chese mundu listing lo saricheyochu.

## Mundu enduku pani cheyaledu (em fix chesam)

| Problem (mundu) | Fix |
|-----------------|-----|
| Habit, recipe, study, event planner PDFs lo pages kinda khali (empty bottom) | Nijamaina content: Start here card, recipe index, exam plan table, countdown task lines, extra rows |
| Flashcards: 1-page cover matrame PDF | A4 + Letter lo nijamaina deck: fronts + mirrored backs, cut lines, how-to page |
| Email signature: Truth Checker ki proof ledu (HTML matrame) | A4 + Letter PDF install guide (Gmail, Outlook, Apple Mail steps, prati template page) |
| Wall art: PDF pages lo pictures matrame — checker ki "empty" | Full-bleed vector art PDFs (A4 + Letter), PNG lu 5 sizes lo alage unnayi |
| Notion: upload pack ledu ("No buyer PDF or XLSX") | Printable Notion import guide PDF (A4 + Letter) |
| `product.zip` lo .docx / .html / PNG / Notion zip miss ayyevi | Listing "What you get" lo unna prati file zip lo add avutundi |
| Flashcards folder lo khali `marketing/` folder — upload pack refuse | Khali folders teesestam |
| `/sell` Truth Checker upload pack lo zip lopala files chudaledu — anni PDF packs NOT YET | Bug fix: upload pack ki, daani product folder ni check chestundi (rules same) |
| PDF, cover, previews veru veru colours | Anni 12 types lo okate theme (pdf_themes) |

## Owner ki: ela run cheyali

```bash
python3 tools/sell_proof.py                 # table
python3 tools/sell_proof.py --only notion   # okka type
python3 tools/sell_proof.py --keep /tmp/proof   # packs chudali ante (repo bayata folder)
```

Exit code 0 = anni SELLABLE / ALMOST. 1 = kanisam okati NOT YET (fixes column chudandi).
