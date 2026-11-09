# Posting guide (Tenglish house style)

Mee packs chusi, meeku post cheyali antha chupisthundi. No auto publish — meeru cheytha matrame post avthundi.

## Mee packs evaru

- Upload packs live in `exported_content/products/upload/<pack>/` matrame.
- Pin images, captions, A/B titles unchi: `exported_content/marketing/<pack>/pins/`.
- Instagram carousel slides unchi: `exported_content/marketing/<pack>/carousel/`.

## Pin chusi post cheyandi

1. `exported_content/marketing/<pack>/pins/` lo meeku pdf/pins chupisthayi.
2. `pins.json` chusi, pin image, title, description, alt text onee.
3. Meeru Pinterest lo okka pin upload cheyandi manual ga.
4. Meeru ab_titles.txt lo 5 titles chupisthayi; meeru choose cheyandi mee ku saray.
5. Meeru ab_titles.txt files use cheyali; titles okka leda kodu ledu.

## Carousel chusi post cheyandi

1. `exported_content/marketing/<pack>/carousel/` lo slides unchi.
2. Meeru caption.txt chusi caption okati, alt.txt chusi alt okati.
3. Meeru Instagram lo okka carousel post cheyandi manual ga.
4. Meeru slides okati chusi caption copy cheyandi, meeru post cheyandi.

## Meeru "posted 2" ani cheyandi

- Pin okati post chesi, robot loki "posted 1" cheyandi.
- Pin ikkada post chesi, robot loki "posted 2" cheyandi.
- Carousel post chesi, robot loki "posted 3" cheyandi.
- Meeru "posted 2" cheste robot post 2 done ayyindi telusukuntundi.
- Meeru "post chesa 2" ani Tenglish lo cheyali; robot sari ga chupisthayi.
- Meeru date chusi posted cheyali, robot plan json me okati update chesthayi.

## A/B titles emiti?

- Pin_variants robot 5 titles gurthinchayi mee pack kosam.
- Meeru ab_titles.txt chusi 5 titles chupisthayi.
- Meeru okka title choose cheyandi mee ku saray; no auto post.
- Meeru meeku saray title use cheyandi; pin okati post cheyandi.
- Meeru pin okati post chesi, "posted N" cheyandi, robot okasari marchesa.

## Mee cheyali leda cheyyi leda emiti

- No auto post cheyyi; meeru approve cheyali, meeru cheytha matrame.
- No auto publish cheyyi Pinterest lo, Instagram lo, or Gumroad lo.
- No auto upload cheyyi; meeru cheytha matrame upload cheyandi.
- No auto money/price print cheyyi; meeku listing kosam price set cheyandi.
- No auto token/keys print cheyyi; secret meeku leda chupisthayi.

## Mee post plan chupisthayi

- `post_planner.py` vaade 30 roju plan unchisthayi.
- Robot meeku daily posts chupisthayi: "Today post (3 pins, 1 Instagram): ...".
- Robot "posted 1" reply kosam chupisthayi.
- Meeru /today chesthe chupisthayi, /posted N chesthe done mark chesthayi.
- Meeru plan json okati chupisthayi, meeru posted cheytha okati chupisthayi.

## Commands

- `/today` — meeku today posts chupisthayi.
- `/posted N` — post N done ayyindi mark chesthayi.
- `posted N` — Tenglish/English lo same kosam.
- `post chesa N` — Tenglish lo same kosam.

Tenglish: meeru cheyandi, robot okasari marchesa.
