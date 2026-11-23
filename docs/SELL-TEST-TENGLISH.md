# 14 rojula Sell test — coding avasaram ledu

Robot files, Drafts, Pins prepare chestundi. Pack, Price, Publish, posts decisions meere teesukondi. Rojulu India time lo count avuthayi. Ee test ki 5–8 READY Packs select avuthayi; daily report lo target 8 Packs.

**Pack N** ante `/selltest list` lo `/upload` pakkana unna Pack number. **Post N** ante `/post today` message lo ee roju post number. Command lo N badulu aa number type cheyandi.

## Phone lo quick steps

```text
14-day Sell test: India rojulu; files, Store, posts meere review cheyandi.
Day 0: /ready; /list; /view N chusi /approve N tho meere decide cheyandi.
Day 1: /selltest pampandi; /selltest list lo selected Pack numbers chudandi.
Prathi Pack: /upload N; listing-ab.txt nunchi Title A, Description, Tags copy cheyandi.
Gumroad lo ZIP, Cover, Thumbnail, Previews add chesi meere Publish; /listed N <link>.
/upload N sample; free listing meere chesi /selltest sample N <link> pampandi.
Day 1-14: /post today; Pin images, captions, links tho meere post cheyandi.
Post chesaka /posted N pampandi; ikkada N ee roju Post number.
Rojuku paid Pack totals: /selltest stats N views 120 sales 1; mee numbers pettandi.
Day 7: /selltest swap-title N; /upload N lo Title B ni Store Name lo meere save cheyandi.
Day 14: stats save chesi /selltest list lo Keep, Change, Drop reasons chudandi.
Upload, listed, stats lo N Pack number; Price, Publish, next rotation meere decide cheyandi.
Next: /selltest pampandi.
```

## Day 0 — Packs, Store siddham cheyandi

1. `/ready` pampandi. Daily report lo `Sell test ready: N/8 packs` line chudandi. `Blocks:` lo reason unte `/ready N` lo Pack fixes chudandi.
2. Draft decisions migilithe `/list` pampandi. `/view N` tho contents chadivi, nachina Draft ki `/approve N` pampandi. Robot build chesaka `/ready` malli chudandi. Auto wait enduko teliyalante `/autoapprove status` pampandi.
3. Mee Gumroad Store website open chesi product files save cheyadaniki oka folder siddham cheyandi. Kit files phone/computer lo save cheyandi.
4. Day 1 start cheyadaniki mundu READY Packs chudandi. `/selltest` pampina India date nunche day 1 start avuthundi; ade command malli pampithe ade test continue avuthundi.

## Day 1 — Paid listing, free sample, modati Pins

1. `/selltest` pampandi. `/selltest list` lo selected Packs chudandi. List lo unna Pack N prathidaniki migilina steps cheyandi.
2. `/upload N` pampandi. Buyer ZIP, Cover, Thumbnail, moodu Previews, `listing.txt`, **listing-ab.txt** save cheyandi.
3. Gumroad product editor lo ee mapping follow avvandi. Paid listing ki **listing-ab.txt** lo **Title A** use cheyandi; ade file lo Description, What you get, usage, license, file details, Tags unnayi.

| Gumroad field | Emi pettali |
|---|---|
| Name | **listing-ab.txt** lo Title A line taruvatha unna title |
| Description | Ade file lo Description, What you get, How to use, License, File format, File size sections copy cheyandi |
| Content / buyer download | `product.zip` upload cheyandi |
| Cover | `cover.png` mundu add cheyandi; `preview-1.png`, `preview-2.png`, `preview-3.png` kuda product previews ga add cheyandi |
| Thumbnail | `thumbnail.png` add cheyandi |
| Tags | Share tab lo Gumroad Discover section daggara kit lo unna Tags add cheyandi |
| Price | Mee Price meere set cheyandi |
| Publish | Files, copy chusi meere click cheyandi |

Cover, previews, Thumbnail setup ki [official Gumroad image help](https://gumroad.com/help/article/60-adding-a-cover-image) chudandi. Share tab lo Tags kosam [official category/tag help](https://gumroad.com/help/article/79-gumroad-discover) chudandi.

4. Paid product public link copy chesi `/listed N <link>` pampandi. `<link>` badulu mee full product link pettandi, udaharanaki `https://example.gumroad.com/l/full-pack`.
5. Ippudu `/upload N sample` pampandi. Idi vere free listing kit. Sample kit lo `product.zip`, images, **sample kit `listing.txt`** use cheyandi. Sample ni free ga meere list cheyandi. Sample Description full Pack peru chepthundi; PDF lo Full version page paid link chupisthundi.
6. Free listing link save cheyadaniki `/selltest sample N <link>` pampandi, udaharanaki `https://example.gumroad.com/l/free-sample`. Paid link, free link rendu veruga save avuthayi.
7. `/post today` pampandi. Okkokka Pin ki image, caption, board, destination link copy chesi meere post cheyandi. Carousel use cheyalanukunte robot pampina slides/caption tho meere post cheyandi.
8. Oka post nijanga post chesaka `/posted N` pampandi. Ikkada N aa roju **Post number**.
9. Rojuki paid Pack views, sales Gumroad lo chusi `/selltest stats N views 120 sales 1` pampandi. Ikkada 120, 1 example counts; mee aa roju nijamaina totals type cheyandi. Ee command India time lo aa roju totals save chestundi. Same roju malli pampithe totals replace avuthayi. Prathi selected Pack ki record cheyandi.

## Day 2 nunchi Day 14 varaku

**Daily routine:** `/post today` → meere post cheyandi → `/posted N` → paid Pack roju totals tho `/selltest stats N views 120 sales 1` → `/selltest list`.

| Roju | Emi cheyali |
|---|---|
| Day 2 | Daily routine follow avvandi; prathi Pin destination link open chusi sari undho chudandi. |
| Day 3 | Daily routine; daily report lo READY count, Blocks chudandi. |
| Day 4 | Daily routine; sample PDF lo Full version page, paid link open chusi chudandi. |
| Day 5 | Daily routine; paid Pack prathidaniki aa roju totals save cheyandi. |
| Day 6 | Daily routine; day 7 review kosam missing roju numbers unte mee records chudandi. |
| Day 7 | Rojuki totals save chesi `/selltest list` lo Keep/Change reasons chudandi. Title B test cheyalanukunna Pack ki `/selltest swap-title N` pampandi. `/upload N` lo updated **listing-ab.txt** chusi **Title B** ni Gumroad Name field lo meere replace chesi save cheyandi. Daily routine continue cheyandi. |
| Day 8 | Daily routine; Title B save chesina Packs ni mee notes lo gurthu pettukondi. |
| Day 9 | Daily routine; links, published Pins chusi chudandi. |
| Day 10 | Daily routine; prathi selected Pack ki numbers record cheyandi. |
| Day 11 | Daily routine; free sample nunchi full Pack link sari undho chudandi. |
| Day 12 | Daily routine; mee Store lo files, images open avuthunnayo chudandi. |
| Day 13 | Daily routine; day 14 review kosam mee daily records siddham cheyandi. |
| Day 14 | Chivari roju totals save chesi `/selltest list` pampandi. Keep, Change, Drop reasons chadivi next rotation meere decide cheyandi. |

## Edaina aagithe

- `/selltest` READY Packs saripovu ante `/ready` chudandi; Drafts ki `/list`, approval wait ki `/autoapprove status` chudandi.
- Upload kit lo file miss/corrupt ayithe `/ready N` lo fixes chudandi; ready ayyaka `/upload N` malli pampandi.
- `/post today` Quiet hours ani chepthe morning lo malli pampandi. Notifications mee settings prakaram vasthayi.
- Stats lo views, sales whole counts pettandi; sales count views ni minchakudadhu. Meeru enter chesina numbers tho review vasthundi.
- Title B command day 7 nunchi day 14 varaku pani chestundi. Store Name field meere save cheyandi.
- Robot public uploads, Publish, Pins, emails automatic ga cheyyadu. Mee Store, posts meere control cheyandi.
