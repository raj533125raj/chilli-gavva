"""Owner-only real buyer checks and staged copy/gallery delivery."""
import os
import re
import buyer_check

PRIORITY=46
COMMANDS=[{'cmd':'/buyercheck','words':['buyercheck','selltest buyercheck'],
           'help_en':'Score buyer clarity and get improved private listing files',
           'help_te':'Buyer check kosam /buyercheck pampandi.'}]


def handle(text,ctx):
    if not re.fullmatch(r'/?(?:buyercheck|selltest\s+buyercheck)(?:@\w+)?',str(text).strip(),re.I):return None
    owner=os.environ.get('TELEGRAM_CHAT_ID','')
    if owner and str(ctx.get('chat_id'))!=owner:return None
    results=buyer_check.prepare_all(ctx.get('db_path'),ctx.get('products_dir'))
    for row in results:
        if 'blocked' in row:continue
        for key in ('listing','image'):
            if not ctx['send_document'](row[key],caption=f"Pack #{row['id']} buyer Draft; review cheyandi."):
                return {'text':'Buyer Draft delivery complete kaaledu. Next: /buyercheck malli pampandi.'}
    return {'text':buyer_check.text(results)}
