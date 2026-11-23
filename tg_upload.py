"""Telegram plugin for preparing and sending a price-free Gumroad upload pack."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional

import gumroad_preflight
import tg_preflight

REPO_ROOT = Path(__file__).resolve().parent

COMMANDS = [
    {
        "cmd": "/upload",
        "words": ["upload"],
        "help_en": "Prepare the Gumroad hand-upload files for a pack: /upload N",
        "help_te": "Pack Gumroad lo hand-upload cheyadaniki files pampisthundi: /upload N",
    },
]

_WITH_ID = re.compile(r"^(?:/upload(?:@\w+)?|upload)(?:\s+(?:pack\s+)?#?(\d+)(?:\s+(sample))?)?$", re.IGNORECASE)
_STEPS = (
    "Gumroad website lo New product click cheyandi.",
    "Digital product select cheyandi.",
    "Listing.txt nunchi product name enter cheyandi.",
    "Product.zip ni buyer file ga upload cheyandi.",
    "Cover image upload cheyandi.",
    "Listing.txt nunchi description copy cheyandi.",
    "Listing.txt nunchi tags add cheyandi.",
    "Price ni owner decide chesi set cheyali.",
    "Publish button owner click matrame; robot publish cheyyadu.",
)


def _reply(text: str, ctx: dict) -> dict:
    send_text = ctx.get("send_text")
    if callable(send_text):
        if send_text(text) is False:
            return {"text": "Telegram reply send avvaledu. Mallee try cheyandi."}
        return {"handled": True}
    return {"text": text}


def _help(ctx: dict) -> dict:
    return _reply("Gumroad upload files prepare cheyadaniki `/upload N` pampandi.", ctx)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _stage_flat_product(pack: Path, workspace: Path) -> Path:
    staged = workspace / "source" / pack.name
    staged.mkdir(parents=True)
    for path in sorted(pack.iterdir()):
        if path.is_symlink():
            raise ValueError(f"Symlink is not allowed in the product folder: {path.name}")
        if path.is_dir():
            if path.name == "gumroad-images":
                continue
            raise ValueError(f"Product folder must be flat; found directory: {path.name}")
        if path.is_file():
            shutil.copyfile(path, staged / path.name)
    return staged


def _send_prepared(pack_id: int, pack: Path, ctx: dict) -> dict:
    thumbnail_source = pack / "gumroad-images" / gumroad_preflight.THUMBNAIL_NAME
    cover_source = pack / "gumroad-images" / gumroad_preflight.COVER_NAME
    send_document = ctx.get("send_document")
    send_photo = ctx.get("send_photo")
    if not callable(send_document) or not callable(send_photo):
        return _reply("Upload file send callbacks dorakaledu; inbox setup check cheyandi.", ctx)

    temp_base = Path(tempfile.gettempdir()).resolve()
    if _inside(temp_base, REPO_ROOT.resolve()):
        return _reply("Safe temporary folder repo bayata dorakaledu; upload aapanu.", ctx)

    import upload_pack

    try:
        with tempfile.TemporaryDirectory(prefix="chilli-upload-", dir=str(temp_base)) as temp_dir:
            workspace = Path(temp_dir).resolve()
            staged = _stage_flat_product(pack, workspace)
            prepared = upload_pack.make(staged, workspace / "products")
            thumbnail = prepared / gumroad_preflight.THUMBNAIL_NAME
            cover = prepared / gumroad_preflight.COVER_NAME
            shutil.copyfile(thumbnail_source, thumbnail)
            shutil.copyfile(cover_source, cover)

            for sender, path, caption, label in (
                (send_document, prepared / "product.zip", "Buyer download: product.zip", "Product ZIP"),
                (send_photo, thumbnail, "Gumroad thumbnail", "Thumbnail"),
                (send_photo, cover, "Gumroad cover", "Cover"),
                (send_document, prepared / "listing.txt", "Price-free Gumroad listing", "Listing"),
            ):
                if sender(str(path), caption=caption) is False:
                    return _reply(f"{label} Telegram lo send avvaledu; `/upload {pack_id}` malli try cheyandi.",
                                  ctx)
            message = (f"Gumroad hand-upload steps — Pack #{pack_id}\n"
                       + "\n".join(f"{index}. {step}" for index, step in enumerate(_STEPS, 1)))
            return _reply(message, ctx)
    except (OSError, ValueError, zipfile.BadZipFile, ET.ParseError, RuntimeError) as exc:
        return _reply(f"Upload files prepare cheyalekapoyam ({type(exc).__name__}). "
                      "Pack files check chesi malli `/upload " + str(pack_id) + "` try cheyandi.", ctx)



def _send_handoff(pack_id, pack, ctx):
    import gumroad_handoff
    try:
        folder=gumroad_handoff.build(pack)
        large=gumroad_handoff.oversized(folder)
        if large:
            return _reply("Pack #"+str(pack_id)+" lo "+", ".join(large)+
                          " Telegram file limit minchindi; ee files computer nunchi copy cheyandi. Folder: "+str(folder),ctx)
        sender=ctx.get('send_document')
        if not callable(sender):
            return _reply("Upload files send callback dorakaledu; inbox setup check cheyandi.",ctx)
        # Documents preserve the exact upload image dimensions and pixels.
        for name in gumroad_handoff.FILES:
            if sender(str(folder/name),caption="Gumroad hand-upload: "+name) is False:
                return _reply("Pack #"+str(pack_id)+" lo "+name+" send avvaledu; /upload "+str(pack_id)+" malli try cheyandi.",ctx)
        return _reply("Pack #"+str(pack_id)+" Gumroad files oke folder lo ready: "+str(folder)+
                      "\nBuyer ZIP, Cover, Thumbnail, 3 Previews, listing.txt pampanu. Gumroad lo files meere drag-and-drop cheyandi. "
                      "Price meere set chesi Publish meere click cheyandi.",ctx)
    except (OSError,ValueError,zipfile.BadZipFile,RuntimeError):
        return _reply("Pack #"+str(pack_id)+" upload kit ready kaaledu; /ready "+str(pack_id)+" lo fixes chudandi.",ctx)

def handle(text: str, ctx: dict) -> Optional[dict]:
    if not isinstance(text, str):
        return None

    owner_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if owner_id and str(ctx.get("chat_id", "")) != owner_id:
        return None

    raw = " ".join(text.strip().split())
    if not raw or len(raw) > 80:
        return None
    match = _WITH_ID.fullmatch(raw)
    if not match:
        return None
    if match.group(1) is None:
        return _help(ctx)

    pack_id = int(match.group(1))
    if match.group(2):
        import free_sample
        try:
            folder=free_sample.build(pack_id,ctx.get('products_dir'),ctx.get('db_path'))
            sender=ctx.get('send_document')
            if not callable(sender):
                return _reply('Sample files pampadaniki callback kavali. Next: /upload '+str(pack_id)+' sample malli pampandi.',ctx)
            for name in free_sample.FILES:
                if sender(str(folder/name),caption='Free sample hand-upload: '+name) is False:
                    return _reply('Sample file send avvaledu. Next: /upload '+str(pack_id)+' sample malli pampandi.',ctx)
            return _reply('Free sample: 3 real content pages + Full version page pampanu. Gumroad lo free ga meere list cheyandi.\nNext: /selltest sample '+str(pack_id)+' <link> pampandi; Pin destination save cheyandi.',ctx)
        except (OSError,ValueError,RuntimeError,ImportError,zipfile.BadZipFile):
            return _reply('Sample kit ready kaaledu. Next: /ready '+str(pack_id)+' lo Full Pack fixes chudandi.',ctx)
    pack = tg_preflight._find_pack(pack_id, ctx)
    if pack is None:
        return _reply(f"Pack #{pack_id} kanipinchaledu. `/upload` ani pampithe help vastundi.", ctx)

    if (pack/"product.zip").is_file() and (pack/"listing.txt").is_file():
        return _send_handoff(pack_id,pack,ctx)

    checks = gumroad_preflight.check(pack)
    failures = [item for item in checks if not item.get("ok")]
    if not checks:
        failures = [{"name": "Preflight checks", "fix_te": "Preflight checks malli run cheyandi."}]
    if failures:
        lines = [f"❌ Gumroad preflight — Pack #{pack_id}", "Files ippudu pampanu; mundu ivi fix cheyandi:"]
        for item in failures:
            label = tg_preflight._LABELS.get(item.get("name", ""), item.get("name", "Preflight"))
            lines.append(f"- {label}: {item['fix_te']}")
        return _reply("\n".join(lines), ctx)

    return _send_prepared(pack_id, pack, ctx)
