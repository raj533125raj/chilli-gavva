# Gumroad Selling Guide — Tenglish
> Mee pack ready ayyindi. Ee guide follow chesi meeru Gumroad lo manual ga upload mariyu publish cheyandi.
> Robot never publishes, never lists, never sets price. Every step below is owner action only.

---

## Overview — Order of Steps

```
/ready          → packs sellable status chupistundi
/sell N         → pack N sell-kit details chupistundi (listing blocks + file list)
gumroad.com     → new product page open cheyandi
fields paste    → title, description, tags copy-paste cheyandi
files upload    → product.zip, cover, preview images order lo upload cheyandi
price set       → meeru price decide chestaru (robot never sets price)
publish         → meeru Publish button press chestaru
/listed N link  → robot ki store URL record cheyandi
```

---

## Before You Start — Pre-publish Checklist

Publish button press cheyadaniki mundu ee oka minute check cheyandi:

### 1. Truth Check — READY ga undi aa?

`/ready N` reply lo **Truth Check: ready** undi ani confirm cheyandi.
"needs fix" unte publish cheyakandi — meeru `/reject N` chesi fix chesaka try cheyandi.

### 2. No Money Claims

- Listing lo "$", "₹", "USD", "INR", "Rs." tho start ayye numbers unte upload cheyakandi.
- Robot always writes **"Price: SET BY OWNER"** in listing blocks — adi correct ga undi aa verify cheyandi.
- Mee own brain lo kuda listing text lo money amounts add cheyakandi.

### 3. License Check

Product.zip lo `LICENSE.txt` file undi ani confirm cheyandi.
"Personal use only" license unte, listing description lo kuda adi mention chesara ani check cheyandi.

### 4. No Fake Claims

- "10x productivity" lanti claims unte remove cheyandi.
- Listing only real file contents describe chesaali: pages, formats, tabs, layout.
- FAQ lo kuda facts only ga undi aa cheyandi.

Pre-publish check pass ayyindi → ippudu Gumroad ki vellavachu.

---

## Step 1 — `/ready` tho Pack Status Check

Telegram lo type cheyandi:

```
/ready
```

Meeku list vasthundi:

```
📦 SELL READY GATE REPORT
Sellable: 2, Almost: 1, Not Yet: 1

🟢 SELLABLE NOW:
• Pack #3: ADHD Daily Planner (Score: 91/100)
• Pack #5: Wedding Checklist (Score: 88/100)

🟡 ALMOST READY:
• Pack #4: Student Tracker (Score: 71/100)
  - Missing Pinterest pin variants
```

SELLABLE packs ni first upload cheyandi. ALMOST packs ki fixes chesaka upload cheyandi.

Specific pack details kosam:

```
/ready 3
```

Full card vasthundi: truth check, deep QA, listing lint, marketing assets — anni oka chota.

---

## Step 2 — `/sell N` tho Sell-Kit Get Cheyandi

Telegram lo type cheyandi:

```
/sell 3
```

Robot reply lo vasthundi:

- Gumroad listing block (title, description, tags, files to upload, cover)
- Topmate listing block
- File list (product.zip size, cover.png, preview images order)
- License line reminder
- Pre-publish checklist summary

Ee reply Telegram lo untundi; meeru adi chusi Gumroad form fill cheyandi.

---

## Step 3 — Gumroad lo New Product Open Cheyandi

Browser lo **gumroad.com** ki vellandi. Mee account login cheyandi (robot adi cheyadu).

1. Dashboard lo **"New Product"** button press cheyandi.
2. **"Digital product"** select cheyandi.
3. Product page open avuthundi.

---

## Step 4 — Fields Paste Cheyandi

`/sell N` reply nunchi copy chesi Gumroad form lo paste cheyandi:

| Gumroad Field | Ee daani nunchi copy cheyandi |
|---|---|
| **Name** | Listing block lo `Name:` line |
| **Description** | Listing block lo `Description:` section |
| **Tags** | Listing block lo `Tags:` line (comma separated) — 10 tags max |
| **Summary** (short) | Description first 150 characters |

**Price field:** Meeru decide chestaru. Robot emi suggest cheyadu. Mee niche, effort mariyu audience batti meeru fix cheyandi.

**Tip:** Tags lo duplicates undakandi. 10 unique tags fill cheyandi.

---

## Step 5 — Files Upload Order lo Cheyandi

Gumroad product page lo **"Add a file"** section ki vellandi. Ee order lo upload cheyandi:

### Order

1. **product.zip** — buyer download avutaadu ee file. First upload cheyandi.
2. **cover.png** — product thumbnail (1280×720 px minimum). Cover section lo upload cheyandi.
3. **preview-1.png** — first preview image (product inside pages). Gallery lo add cheyandi.
4. **preview-2.png** — second preview image (if present). Gallery lo add cheyandi.
5. **preview-3.png** — third preview image (if present). Gallery lo add cheyandi.

Files `/files N` command tho Telegram nunchi get cheyavachu:

```
/files 3
```

Robot `product.zip`, `cover.png`, `listing.txt` pampisthundi.

Covers kosam:

```
/covers 3
```

Robot cover variants pampisthundi.

### File Upload Tips

- Product.zip lo buyer pettukune files only untayi (PDFs, XLSX, LICENSE.txt, README).
- Internal robot files (`.enc`, `.db`, `.json` state) product.zip lo undaavu — robot already filter chestundi.
- Cover image mee product ni represent chesaali: clean, text readable, colors brand tho match avvaali.
- Preview images product lo actual pages chupinchali — fake mockups cheyakandi.

---

## Step 6 — Price Decide Cheyandi

Gumroad **Price** field lo meeru price type cheyandi. Robot emi suggest cheyadu, emi fix cheyadu.

Considerations (meeru decide chestaru):

- Product pages count mariyu quality
- Competitor products on Gumroad
- Mee audience buying capacity
- Bundle vs single pack (bundle ki slightly more)

**Rule:** Price field lo type chesaka listing text lo, description lo, tags lo money amounts add cheyakandi. Store form lo matrame price undi.

---

## Step 7 — Publish Cheyandi

Anni fields fill ayyaka:

1. Gumroad product page lo scroll down cheyandi.
2. **"Publish"** button press cheyandi.
3. Gumroad live product URL copy cheyandi — format: `https://gumroad.com/l/<short-name>`

---

## Step 8 — `/listed N <link>` Robot ki Cheppandi

Telegram lo type cheyandi (mee actual link tho):

```
/listed 3 https://gumroad.com/l/adhd-daily-planner
```

Robot:

- Pack #3 store URL record chestundi.
- Public export lo listing status "listed" ga update avuthundi.
- Mee site public products page lo live product show avuthundi.

Ee step cheyandi — robot ki cheppakunda pothe status "ready" ga untundi, "listed" avvaadu.

---

## Full Quick-Reference Card

```
Step 1:  /ready                    → packs status check
Step 2:  /sell 3                   → listing blocks + file list get
Step 3:  gumroad.com → New Product → Digital product
Step 4:  Name, Description, Tags paste cheyandi
Step 5:  product.zip → cover.png → preview-1 → preview-2 → preview-3 upload
Step 6:  Price: meeru decide chestaru
Step 7:  Publish button press cheyandi
Step 8:  /listed 3 https://gumroad.com/l/<link>
```

---

## Common Mistakes — Avoid Cheyandi

| Mistake | Correct Way |
|---|---|
| Truth check "needs fix" unna publish cheyatam | `/ready N` lo "ready" unna matrame publish cheyandi |
| Description lo money amount add cheyatam | Price only Gumroad price field lo type cheyandi |
| Wrong file order upload cheyatam | product.zip first, cover second, previews in order |
| `/listed` cheyakunda povatam | Publish chesaka `/listed N link` mandatory ga pampandi |
| Preview lo fake screenshots | Real product pages matrame screenshot cheyandi |
| Tags lo duplicates | 10 unique, relevant tags fill cheyandi |
| LICENSE.txt zip lo ledu | `/ready N` pack files check lo license confirm cheyandi |

---

## Extra Commands — Ee Workflow lo Useful

| Command | Emi chestundi |
|---|---|
| `/ready N` | Pack N sell-ready score + top fixes |
| `/sell N` | Listing blocks + file order |
| `/files N` | product.zip + cover.png + listing.txt phone ki |
| `/covers N` | Cover variants phone ki |
| `/lint N` | Listing format PASS/FAIL |
| `/faq N` | Buyer FAQ markdown |
| `/launch N` | LAUNCH.md upload checklist |
| `/listed N link` | Store URL record cheyatam |

---

## Robot Never Does

- Robot never logs into Gumroad.
- Robot never sets price.
- Robot never uploads files to any store.
- Robot never publishes product.
- Robot never sends buyer emails.
- Anni steps above — owner action only.

---

*Tenglish guide — owner kosam. Meeru decide chestaru, robot sahaayam chestundi.*
