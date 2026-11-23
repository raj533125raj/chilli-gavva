"""Owner Telegram entry point for the private sales experiment."""
import os
import re
import selltest

COMMANDS = [{'cmd':'/selltest','words':['selltest'], 'category':'Sell',
             'help_en':'Start or list the 14-day test; swap packs with /selltest swap A B',
             'help_te':'14-day Sell test Packs enchukondi; /selltest list leda /selltest swap A B pampandi.'}]


def handle(text,ctx):
    if not isinstance(text,str):
        return None
    words = text.strip().split()
    if not words or words[0].split('@')[0].lower() not in ('/selltest','selltest'):
        return None
    owner = os.environ.get('TELEGRAM_CHAT_ID','')
    if owner and str(ctx.get('chat_id')) != owner:
        return None
    db = ctx.get('db_path'); products = ctx.get('products_dir'); now = ctx.get('now')
    args = [w.lower() for w in words[1:]]
    try:
        if not args:
            run = selltest.start(db,products,now)
        elif args == ['list']:
            run = selltest.active(db)
            if not run:
                return {'text':'Sell test inka start kaaledu.\nNext: /selltest pampandi.'}
        elif len(args)==3 and args[0]=='swap' and all(re.fullmatch(r'\d{1,9}',n) for n in args[1:]):
            run = selltest.swap(int(args[1]),int(args[2]),db,products,now)
        else:
            return {'text':'Command sarigga ivvandi.\nNext: /selltest, /selltest list leda /selltest swap A B pampandi.'}
        return {'text':selltest.plan_text(run,now)}
    except ValueError as exc:
        return {'text':str(exc)+'\nNext: /ready pampandi; READY Packs chudandi.'}
