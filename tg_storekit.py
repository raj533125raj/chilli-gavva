"""Send a store setup draft and locally generated pictures only to the owner."""
import os
import re
import tempfile
import store_front_kit

COMMANDS=[{'cmd':'/storekit','words':['storekit'], 'help_en':'Get store copy and profile artwork',
           'help_te':'Store setup Draft, pictures kosam /storekit pampandi.'}]


def handle(text,ctx):
    if not re.fullmatch(r'/?storekit(?:@\w+)?',str(text).strip(),re.I):return None
    owner=os.environ.get('TELEGRAM_CHAT_ID','')
    if owner and str(ctx.get('chat_id')) != owner:return None
    with tempfile.TemporaryDirectory(prefix='store-front-') as temp:
        try:
            paths=store_front_kit.build(temp,ctx.get('db_path'),ctx.get('products_dir'),ctx.get('now'))
        except ValueError as exc:
            return {'text':str(exc)}
        for path in paths:
            if not ctx['send_document'](str(path),caption='Store setup Draft; owner review cheyandi.'):
                return {'text':'Store kit delivery complete kaaledu. Next: /storekit malli pampandi.'}
    return {'text':'Store kit pampanu: 5 Name ideas, Bio, About, FAQ, Refund draft, Pack order, profile + cover pictures. Meere Settings lo pettandi. Next: /selltest list pampandi.'}
