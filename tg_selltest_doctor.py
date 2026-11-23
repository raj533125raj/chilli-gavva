"""Owner-only diagnostic subcommand; no repairs or publishing happen here."""
import os
import re
import selltest_doctor

PRIORITY = 45
COMMANDS = [{'cmd':'/doctor', 'words':['doctor selltest'],
             'help_en':'Check every sell-test kit, link, page and daily stats: /doctor selltest',
             'help_te':'Sell test missing steps kosam /doctor selltest pampandi.'}]


def handle(text, ctx):
    if not isinstance(text, str): return None
    match = re.fullmatch(r'/?doctor(?:@\w+)?(?:\s+(selltest))?', text.strip(), re.I)
    if not match: return None
    owner = os.environ.get('TELEGRAM_CHAT_ID', '')
    if owner and str(ctx.get('chat_id')) != owner: return None
    if not match.group(1):
        return {'text':'Sell test kit, sample, links, Pins, Site, stats check cheyadaniki subcommand pampandi.\nNext: /doctor selltest pampandi.'}
    return {'text':selltest_doctor.report(ctx.get('db_path'), ctx.get('products_dir'), ctx.get('now'))}
