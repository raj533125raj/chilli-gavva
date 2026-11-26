"""Small copyable Gumroad fields for the best unlisted, genuinely ready pack."""
import os
from pathlib import Path
import re
from tg_menu import owner_reply
from owner_titles import owner_title

COMMANDS=[{'cmd':'/listnext','words':['listnext'],'category':'Sell',
           'help_en':'Copy the next ready pack listing on your phone',
           'help_te':'Next ready Pack listing field okkokkati copy cheyandi.'}]


def next_pack(ctx):
    import selltest
    import tg_listed
    listed=set(tg_listed.listed_ids(ctx.get('db_path')))
    choices=selltest.candidates(ctx.get('db_path'),ctx.get('products_dir'),ctx.get('now'))
    return next((pack for pack in choices if pack['id'] not in listed),None)


@owner_reply
def handle(text,ctx):
    if not re.fullmatch(r'/?listnext(?:@\w+)?',str(text).strip(),re.I):return None
    owner=os.environ.get('TELEGRAM_CHAT_ID','').strip()
    if not owner or str(ctx.get('chat_id'))!=owner:return None
    pack=next_pack(ctx)
    if pack is None:
        return {'text':'Kotha unlisted READY Pack ledu. /ready lo fixes chudandi.\nNext: /ready'}
    import selltest
    import store_kits
    base=selltest.products_root(ctx.get('products_dir'))/'upload'/pack['folder']
    if not base.is_dir():base=selltest.products_root(ctx.get('products_dir'))/pack['folder']
    info=store_kits.parse_listing_file(base/'listing.txt')
    info['title']=owner_title(base)
    copy=store_kits.gumroad_copy(base,info)
    fields=[('1. Title',info['title']),('2. Short description',copy['hook']),
            ('3. Full description',store_kits.gumroad_description(copy)),
            ('4. Tags',', '.join(copy['tags']))]
    aid=pack['id'];send=ctx.get('send_text')
    if not callable(send):
        return {'text':f'Pack #{aid} ready. Fields kosam /listnext pampandi.\nNext: /upload {aid}'}
    for label,value in fields:
        send(label+' — field text copy cheyandi:\n'+value+f'\nNext: /upload {aid}')
    send(f'5. Files: send /upload {aid}\nNext: /upload {aid}')
    from tg_sell import image_files
    images=image_files(base)
    cover=next((path for path in images if 'cover' in Path(path).stem.lower()),images[0] if images else None)
    if cover and callable(ctx.get('send_photo')):
        ctx['send_photo'](cover,caption=f'6. Cover — Gumroad lo pettandi.\nNext: /upload {aid}')
    send(f'7. Gumroad app/site lo New product -> paste 1,2,3,4 -> files -> Publish -> link copy -> /listed {aid} <link>\n'
         f'Price meere decide cheyandi. Robot list/post cheyadu.\nNext: /listed {aid} <link>')
    return {'handled':True}
