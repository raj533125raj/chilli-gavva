"""Owner Telegram entry point for the private sales experiment."""
import os
import re
import selltest

# Own the command before the legacy natural-language sales parser sees its numbers.
PRIORITY = 30

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
        elif len(args)==6 and args[0]=='stats' and args[2]=='views' and args[4]=='sales' and all(re.fullmatch(r'\d{1,9}',args[i]) for i in (1,3,5)):
            day = selltest.save_stats(int(args[1]),int(args[3]),int(args[5]),db,now)
            return {'text':f"Pack #{args[1]} {day} totals save chesanu. Malli pampithe ee roju totals replace avuthayi.\n"+selltest.report_block(db,now,products)}
        elif args == ['list']:
            run = selltest.active(db)
            if not run:
                return {'text':'Sell test inka start kaaledu.\nNext: /selltest pampandi.'}
        elif len(args)==3 and args[0]=='swap' and all(re.fullmatch(r'\d{1,9}',n) for n in args[1:]):
            run = selltest.swap(int(args[1]),int(args[2]),db,products,now)
        else:
            return {'text':'Command sarigga ivvandi.\nNext: /selltest, /selltest list leda /selltest swap A B pampandi.'}
        lines = selltest.plan_text(run,now).splitlines()
        decisions = selltest.review(db,now)
        if decisions:
            lines[0] += f" | day {selltest.day_number(run,now)} review"
            for i, decision in enumerate(decisions,2):
                lines[i] += ' | '+decision['action']+': '+decision['reason']
        return {'text':'\n'.join(lines)}
    except ValueError as exc:
        return {'text':str(exc)+'\nNext: /ready pampandi; READY Packs chudandi.'}
