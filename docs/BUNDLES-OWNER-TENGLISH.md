# Bundles — owner guide (Tenglish)

## What is a bundle?

A bundle is one PDF made from the real pages of two or more products. The JSON file in `specs/bundles/` has a `bundle_of` list that names the products and their order. The Product Builder makes that one PDF with a cover, a How to use page and a "What's inside" list.

## How the robot suggests groups

Niche agents create approved drafts. From those drafts, the robot suggests which products can sit together in a bundle. The suggestion lives in `specs/bundles/` as a JSON file with `bundle_of`. The robot does not decide by itself: the owner reads the suggestion and approves it.

## Where the bundle folder is

Bundle spec files are in `specs/bundles/`. The built bundle folder is `exported_content/products/bundle-<short_name>/`. That folder has the PDF and the listing text.

## listing_fields.py

`listing_fields.py` reads an upload pack folder's `listing.txt` and prints ready-to-copy Gumroad and Topmate listing blocks.

Usage:
```bash
python3 listing_fields.py <upload_pack_folder>
```

Listing_fields.py chusi listing.txt chusi listing_fields.py chusi listing_fields.py. Nimnani chusi listing_fields.py chusi listing.txt chusi.

## Price and approve

Price is set by the owner. The robot does not set price. The bundle is not marked for sale automatically. The owner reads the listing text first.

Nimnani chusi antha bundle ki price chusi nimnani chusi. Robot chusi price chusi nimnani chusi chusi. Nimnani chusi price set cheyali. After chusi antha bundle ki approved chusi nimnani chusi antha bundle ki price chusi nimnani chusi.

## What never happens automatically

The robot does not publish a bundle. The robot does not set price. The owner must approve and set price. Nimnani chusi antha bundle ki price chusi nimnani chusi.

Nimnani chusi antha bundle ki price chusi nimnani chusi. Nimnani chusi antha bundle ki price chusi nimnani chusi.
