# SEO tools guide (Tenglish house style)
Ee repo lo mee listings chusi SEO improve cheyadanu undi. Ee tools chusi meeru
listing ki Endo improve cheyali anei decide chesthayi. Auto rename leda, auto post
leda, auto price leda — meeru decide chesthayi.
## Tools evaru
- `seo_score.py` — listing ni 0–100 lo score istali, reasons chupisthayi.
- `keyword_bank.py` — offline keywords generate cheyindi, `keywords.json` me write
  chesthayi.
- `seo_meta.py` — images kosam SEO file names + alt texts plan chupisthayi;
  `seo_meta.json` me write chesthayi. File rename cheyyi ledu.
## How to run
### SEO score chusi
```bash
python3 seo_score.py <pack_folder>
```
Mee listing.txt or listing.md chusi score istali. Meeru JSON kosam:
```bash
python3 seo_score.py <pack_folder> --json
```
### Keywords chusi
```bash
python3 keyword_bank.py
```
Default ga `exported_content/products` chusi keywords generate chesthayi and
`exported_content/marketing/keywords.json` me write chesthayi. Meeru output kosam:
```bash
python3 keyword_bank.py --limit 50
```
Mee products dir change cheyadanu undi:
```bash
python3 keyword_bank.py --products-dir <folder> --output <file>
```
### SEO meta plan chusi
Okka pack kosam:
```bash
python3 seo_meta.py --pack <pack_folder>
```
All packs kosam:
```bash
python3 seo_meta.py --all
```
Mee marketing dir change cheyadanu undi:
```bash
python3 seo_meta.py --pack <pack_folder> --marketing-dir <folder>
```
Mee missing alt texts check cheyadanu undi:
```bash
python3 seo_meta.py --pack <pack_folder> --check-missing
```
## Score ni eela
Score 0–100 undi. Meeru checks chusi:
- Main keyword title ki first 40 chars lo undu leda.
- Keyword description ki first sentence lo undu leda.
- Tags title words ni cover cheyali leda.
- Tags duplicates or near-duplicates undu leda.
- Description length and structure manchi leda.
- Sentence length chandukunda chupisthayi.
- Alt texts images kosam undu leda.
Reasons chupisthayi mee ku eela: `keyword_in_title_front`, `keyword_in_first_sentence`,
`tag_coverage`, `tag_uniqueness`, `description_structure`, `readability`, `alt_texts_present`.
Tenglish summary line mee ku chupisthayi mee score kosam.
## Keywords ni eela title/tags kosam
- `keyword_bank.json` chusi mee top keywords chupisthayi.
- Meeru title kosam main keyword choose cheyali.
- Meeru tags kosam relevant keywords use cheyali.
- Meeru near-duplicates avoid cheyali.
- Meeru brand names and money words keywords lo use cheyali ledu.
- Meeru owner ga decide chesthayi mee ku saray keywords.
## seo_meta emiti
- `seo_meta.py` keywords chusi SEO file names propose cheyindi.
- Alt texts ni propose cheyindi.
- Plan file `exported_content/marketing/<pack>/seo_meta.json` me write chesthayi.
- **File rename cheyyi ledu.** Meeru decide chesthayi mee file name change cheyali leda.
- Meeru alt texts ni plan chusi mee images kosam use cheyali.
## Auto emiti leda
- `seo_score.py` never pack files ni modify chesthayi.
- `keyword_bank.py` never pack files ni modify chesthayi; keywords JSON me write chesthayi.
- `seo_meta.py` never pack files ni rename chesthayi; plan JSON me write chesthayi.
- Meeru upload, publish, price set cheyali — auto ga ledu.
## Meeru ki cheyali
- Meeru listing chusi `seo_score.py` run chesthayi.
- Reasons ni chusi mee improve chesthayi.
- Keywords ni chusi meeru title/tags ni improve chesthayi.
- SEO meta plan ni chusi mee images ni plan chesthayi.
- Meeru owner ga decide chesthayi mee ki change chesthayi.
Tenglish: meeru chupisthayi, tools chusi score/tag/plan chupisthayi, meeru decide chesthayi.
