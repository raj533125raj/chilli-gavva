"""Owner entry point for private landing-page drafts."""
import os
import re
import selltest_site

COMMANDS = [{'cmd': '/site', 'words': ['site'], 'help_en': 'Prepare landing drafts: /site prepare N',
             'help_te': 'Landing Draft kosam /site prepare N pampandi.'}]


def handle(text, ctx):
    if not isinstance(text, str) or not re.match(r'^/?site(?:@\w+)?(?:\s|$)', text.strip(), re.I):
        return None
    owner = os.environ.get('TELEGRAM_CHAT_ID', '')
    if owner and str(ctx.get('chat_id')) != owner:
        return None
    args = text.strip().split()[1:]
    try:
        if len(args) in (2, 3) and args[0] == 'preview' and args[1].isdigit() and (len(args) == 2 or args[2] == 'sample'):
            path = selltest_site.preview(int(args[1]), len(args) == 3, ctx.get('products_dir'), ctx.get('db_path'))
            sender = ctx.get('send_document') or ctx.get('send_photo')
            if not callable(sender) or sender(str(path), caption='Landing Draft content PNG; HTML layout kosam /site prepare N tho Draft chudandi.') is False:
                return {'text':'Page PNG send avvaledu.\nNext: /site preview '+args[1]+' malli pampandi.'}
            return {'handled':True}
        if len(args) == 2 and args[0] == 'base':
            selltest_site.set_base(args[1], ctx.get('db_path'))
            return {'text':'Site URL save chesanu; pages malli review cheyandi.\nNext: /site prepare pampandi.'}
        if args == ['prepare'] or len(args) == 2 and args[0] == 'prepare' and args[1].isdigit():
            aid = int(args[1]) if len(args) == 2 else None
            lines = selltest_site.prepare(aid, ctx.get('products_dir'), ctx.get('db_path'))
            return {'text': '\n'.join(lines or ['Sell test inka ledu.'])+
                    '\nNext: /view ID tho Draft chudandi; approve ayyaka publish-content ni meere run cheyandi.'}
        return {'text': 'Landing Draft kosam /site prepare N pampandi.\nNext: /selltest list lo Pack number chudandi.'}
    except (OSError, ValueError, ImportError):
        return {'text': 'Landing Draft ready kaaledu.\nNext: /upload N tho Pack files check cheyandi.'}
