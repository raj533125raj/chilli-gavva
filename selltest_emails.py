"""Three follow-up drafts in a sample upload kit; no email service or send action."""
import json
from pathlib import Path
import selltest
import selltest_site

FILE = 'emails.txt'


def write(aid, folder, db_path=None):
    """Require the owner's paid link before claiming paste-ready email drafts."""
    import tg_listed
    pack = selltest_site.member(aid, db_path)
    folder = Path(folder)
    if folder.name != pack['folder'] or folder.is_symlink():
        raise ValueError('Sample email destination mismatch')
    for name in ('sample.json', 'sample.pdf', 'README.txt'):
        selltest_site.safe_path(folder, name)
    meta = json.loads((folder/'sample.json').read_text())
    if meta.get('full_pack_id') != aid or not meta.get('full_pack_title'):
        raise ValueError('Real sample provenance required')
    listed = tg_listed.get_listed(aid, db_path)
    target = selltest_site.safe_path(folder, FILE)
    if not listed:
        # A removed link must not leave stale drafts ready for accidental use.
        if target.exists(): target.unlink()
        return None
    url, reason = tg_listed.validate_url(listed['url'])
    if not url: raise ValueError(reason)
    title = meta['full_pack_title']
    rows = [
        (0, 'Thanks for trying the sample',
         'Thank you for downloading the free sample of '+title+'.\n'
         'Unzip the download and open sample.pdf in a PDF reader. The first three pages show real pack content; '
         'the final page names the full version. Keep README.txt with your sample.\n'
         'Take a look at the previews and see whether the layout suits your needs.'),
        (3, 'A quick way to review your sample',
         'Choose one page in sample.pdf and zoom in to review the text and layout. '
         'Compare that preview with the way you plan to use the files. '
         'The sample contains previews; the full pack provides the complete files.\n'
         'You can review '+title+' and its file list here:\n'+url),
        (7, 'One last note about the full pack',
         'This is the final reminder about '+title+'. If the sample fits your needs, '
         'you can review the full pack and its previews here:\n'+url+'\n'
         'If it is not a fit, you can keep the sample. Thank you for taking a look.'),
    ]
    text = ('EMAIL DRAFTS — OWNER SENDS\n'
            'Review each draft. In Gumroad, paste its Subject and Body into your email tool.\n'
            'Day numbers are days after the sample download. The owner sets timing and sends each email.\n'
            'The robot does not collect addresses, schedule email or send anything.\n\n')
    text += '\n\n'.join(f'Day {day} DRAFT\nSubject: {subject}\nBody:\n{body}' for day, subject, body in rows)+'\n'
    selltest_site.public_check(text)
    temp = selltest_site.safe_path(folder, FILE+'.tmp')
    temp.write_text(text, encoding='utf-8'); temp.replace(target)
    return target
