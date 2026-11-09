# Upload day guide (Tenglish house style)
Mee pack ready ayyindi kani, upload day lo meeru kuda chusi correct ga undi ani
confirm chesthayi. Mee manual ga upload chesthayi; auto upload leda, auto publish
leda, auto price set cheyyi ledu.
## Upload day order
Mee eela order lo chesthayi:
1. `pack_report.py` — pack ready ga undi ani check chesthayi.
2. `listing_lint.py` — listing.txt ni lint chesthayi.
3. `listing_fields.py` — copy-paste listing blocks chupisthayi.
4. `launch_checklist.py` — LAUNCH.md checklist generate chesthayi.
5. `faq_builder.py` — FAQ chusi generate chesthayi.
6. Post plan — mee post cheyali antha chusi plan chesthayi.
Mee eela order lo chesthayi mee ku saray. Meeru order change cheyali leda.
## Step 1 — pack_report
```bash
python3 pack_report.py
```
Mee `exported_content/products/upload` lo packs chusi ready count chupisthayi.
Mee "Ready: N of M" chusi confirm chesthayi. Meeru marketing folder chusi
marketing ready leda ani check chesthayi.
## Step 2 — listing_lint
```bash
python3 listing_lint.py <upload_pack_folder>
```
Mee listing.txt ni chusi:
- Title length correct ga undi leda.
- Description length correct ga undi leda.
- Tags count and duplicates correct ga undi leda.
- Money amount undu leda.
- Store brand names undu leda.
Mee "PASS listing lint" chusi chusi. Meeru "FAIL" chusi fix chesthayi.
## Step 3 — listing_fields
```bash
python3 listing_fields.py <upload_pack_folder>
```
Mee listing.txt chusi Gumroad, Topmate, Instamojo blocks chupisthayi.
Mee copy chesthayi mee ku saray. Meeru blocks ni store kosam use chesthayi.
Meeru money amount ni blocks lo use cheyali ledu.
## Step 4 — LAUNCH.md checklist
```bash
python3 launch_checklist.py --pack <upload_pack_folder>
```
Mee `exported_content/marketing/<pack>/LAUNCH.md` me checklist generate chesthayi.
Mee chusi:
- Files to upload.
- Cover and preview order.
- Store listings blocks.
- Pin titles if available.
- Blog path if available.
- Price: owner set chestaru.
Mee LAUNCH.md ni chusi check chesthayi. Meeru store lo upload chesthayi mee
checklist follow cheyali.
## Step 5 — FAQ
```bash
python3 faq_builder.py --all
```
Mee `exported_content/products/faq/<pack>.md` me FAQ generate chesthayi.
Mee chusi:
- Files formats.
- Page sizes.
- Licence line.
- Print instructions.
- Apps that open files.
- Digital delivery note.
Mee FAQ ni chusi check chesthayi mee ku saray. Meeru store kosam FAQ use chesthayi.
## Step 6 — post plan
Mee post plan chusi mee ku chupisthayi mee roju post cheyali antha.
Mee post plan ni chusi mee Pinterest and Instagram kosam post cheyali antha
chuste chupisthayi. Meeru posted cheyadanu undi mee post plan follow chesthayi.
## By eye chusi
Mee upload cheyadanu undi mee by eye chusi:
- product.zip contents manchi ga undi.
- Cover and preview images manchi ga undi.
- Listing title, description, tags manchi ga undi.
- Pin titles meeku saray ga undi.
- Caption and alt texts manchi ga undi.
- FAQ facts only ga undi.
- Price: owner set chestaru — meeru store kosam price set chesthayi.
Mee store lo upload chesthayi meeru price set chesthayi. Meeru listing kosam
price set chesthayi, money amount ni text lo use cheyali ledu.
## Store lo upload
Mee store lo new product page open cheyandi.
Mee listing blocks ni copy chesthayi mee ku saray.
Mee files ni upload chesthayi.
Mee cover and preview images ni upload chesthayi.
Mee price set chesthayi — meeru price set chestaru.
Mee publish chesthayi mee ku saray.
Mee store kosam buttons ni describe cheyali ledu; meeru manual ga chesthayi.
## Quick order
- `pack_report.py` → `listing_lint.py` → `listing_fields.py` →
  `launch_checklist.py` → `faq_builder.py` → post plan.
- Meeru eela order lo chesthayi mee ku saray.
## No auto
- Auto upload leda.
- Auto publish leda.
- Auto price set cheyyi ledu.
- Auto pin post cheyyi ledu.
- Auto carousel post cheyyi ledu.
- Meeru decide chesthayi mee ki change chesthayi.
Tenglish: mee pack ready ayyindi kani, meeru upload day lo eela order lo
chuste chupisthayi, meeru manual ga upload chesthayi.
