"""Send a store setup draft and locally generated pictures only to the owner."""
from tg_menu import owner_reply
import os
import re
import tempfile
import store_front_kit

COMMANDS=[{'cmd':'/storekit','words':['storekit'], 'help_en':'Get store copy and profile artwork',
           'help_te':'Store setup Draft, pictures kosam /storekit pampandi.'}]


@owner_reply
def handle(text,ctx):
    if not re.fullmatch(r'/?storekit(?:@\w+)?',str(text).strip(),re.I):return None
    owner=os.environ.get('TELEGRAM_CHAT_ID','')
    if owner and str(ctx.get('chat_id')) != owner:return None
    from tg_menu import owner_flow_next
    next_step = owner_flow_next(text,ctx)
    with tempfile.TemporaryDirectory(prefix='store-front-') as temp:
        try:
            paths=store_front_kit.build(temp,ctx.get('db_path'),ctx.get('products_dir'),ctx.get('now'))
        except ValueError as exc:
            return {'text':str(exc)+'\nNext: /storekit malli pampandi.'}
        for path in paths:
            if not ctx['send_document'](str(path),caption='Store setup Draft; owner review cheyandi.\nNext: '+next_step):
                return {'text':'Store kit delivery complete kaaledu.\nNext: /storekit malli pampandi.'}
    return {'text':'Store kit pampanu: 5 Name ideas, Bio, About, FAQ, Refund draft, Pack order, profile + cover pictures. Meere Settings lo pettandi.\nNext: '+next_step}
