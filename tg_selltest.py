"""Owner Telegram entry point for the private sales experiment."""
import os
import re
import selltest

# Own the command before the legacy natural-language sales parser sees its numbers.
PRIORITY = 30

COMMANDS = [{'cmd':'/selltest','words':['selltest'], 'category':'Sell',
             'help_en':'Owner steps: /selltest help; start or list the 14-day test',
             'help_te':'14-day steps kosam /selltest help; Packs kosam /selltest list pampandi.'}]


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
        if args == ['help']:
            return {'text':selltest.help_text()}
        elif args == ['learn']:
            import selltest_learning
            return {'text':selltest_learning.text(db,products,now)}
        elif args == ['numbers']:
            import selltest_learning
            return {'text':selltest.report_block(db,now,products) or (selltest_learning.text(db,products,now) if selltest.active(db) else 'Sell test start cheyandi.\nNext: /selltest pampandi.')}
        elif not args or args == ['start']:
            run = selltest.start(db,products,now)
        elif len(words)==4 and args[0]=='sample' and re.fullmatch(r'\d{1,9}',args[1]):
            import free_sample
            url=free_sample.set_link(int(args[1]),words[3],db)
            import post_planner
            post_planner.refresh_ready_plan(selltest.products_root(products),now=now,db_path=db)
            return {'text':'Free sample link save chesanu: '+url+'\nNext: /post today pampandi; sample Pins chudandi.'}
        elif len(args)==6 and args[0] in ('stats','numbers') and args[2]=='views' and args[4]=='sales' and all(re.fullmatch(r'\d{1,9}',args[i]) for i in (1,3,5)):
            day = selltest.save_stats(int(args[1]),int(args[3]),int(args[5]),db,now)
            import selltest_learning
            selltest_learning.record(db,products,now)
            return {'text':f"Pack #{args[1]} {day} totals save chesanu. Malli pampithe ee roju totals replace avuthayi.\n"+selltest.report_block(db,now,products)}
        elif len(args)==2 and args[0]=='swap-title' and re.fullmatch(r'\d{1,9}',args[1]):
            copy=selltest.swap_title(int(args[1]),db,products,now)
            return {'text':'Title B enchukunnaru: '+copy['B']+
                    '\nGumroad Name field lo meere replace chesi save cheyandi.\nNext: /upload '+args[1]+' lo updated listing-ab.txt chudandi.'}
        elif args == ['list']:
            run = selltest.active(db)
            if not run:
                return {'text':'Sell test inka start kaaledu.\nNext: /selltest pampandi.'}
        elif len(args)==3 and args[0]=='swap' and all(re.fullmatch(r'\d{1,9}',n) for n in args[1:]):
            run = selltest.swap(int(args[1]),int(args[2]),db,products,now)
        else:
            return {'text':'Command sarigga ivvandi.\nNext: /selltest, /selltest list leda /selltest swap A B pampandi.'}
        sample_note=''
        if not args or args[0] in ('start','swap'):
            import free_sample
            prepared=free_sample.ensure_all(products,db)
            import selltest_listings
            copies=selltest_listings.ensure_all(products,db)
            import buyer_check
            buyer_results=buyer_check.prepare_all(db,products)
            import selltest_site
            selltest_site.prepare(products_dir=products,db_path=db)
            sample_note=f"Free samples {sum(prepared.values())}/{len(prepared)} ready; /upload N sample pampandi."
            sample_note+=f" Buyer check {sum('blocked' not in row for row in buyer_results)}/{len(buyer_results)} checked; /buyercheck lo score + top fixes."
            sample_note+=f" Listing copy {sum(not value.startswith('Blocked:') for value in copies.values())}/{len(copies)} ready."
            import post_planner
            post_planner.refresh_ready_plan(selltest.products_root(products),now=now,db_path=db)
        lines = selltest.plan_text(run,now).splitlines()
        if sample_note:
            lines.insert(-1,sample_note)
        decisions = selltest.review(db,now)
        if decisions:
            lines[0] += f" | day {selltest.day_number(run,now)} review"
            for decision in decisions:
                slot = next(p['slot'] for p in run['packs'] if p['id']==decision['id'])
                for i, line in enumerate(lines):
                    if line.startswith(str(slot)+'. '):
                        lines[i] += ' | '+decision['action']+': '+decision['reason']
        return {'text':'\n'.join(lines)}
    except ValueError as exc:
        return {'text':str(exc)+'\nNext: /ready pampandi; READY Packs chudandi.'}
