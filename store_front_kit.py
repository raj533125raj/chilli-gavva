"""Generate a private, owner-reviewed store setup kit without a store connection."""
from pathlib import Path
import pdf_themes
import selltest

NAMES=('Quiet Page Nook','Small Steps Paperroom','Gentle Grid Corner','Everyday Draft Shelf','Sage Notes Alcove')
BIO='Practical digital planning files for calmer work, study and everyday routines.'
ABOUT=('Start with one task and a clear page. Browse printable planners, spreadsheets and editable templates '
       'with real previews and a small free sample where available. Each listing names the included files and '
       'compatible apps. Download your files, read the included guide and adapt the examples to your own routine. '
       'These are digital downloads for personal use; no physical item is shipped.')
REFUND=('Draft policy for owner review: If files are damaged, missing or different from the listing, reply to '
        'your purchase receipt with the problem. We will review the issue and offer a replacement or refund as '
        'appropriate. The refund policy displayed at checkout and applicable consumer rights also apply. '
        'Owner: choose the actual refund policy in your store before using this text.')
FAQ=('How do I get the files? Download from your receipt and keep a backup.\n'
     'Which app do I need? Check the file types and compatibility in each product listing before buying.\n'
     'Can I try it first? Use the free sample link when a listing provides one.\n'
     'Can I resell the files? No. These packs are for personal use.\n'
     'Need help opening a file? Reply to your purchase receipt and describe the issue.')
SOURCES=('https://gumroad.com/help/article/67-the-settings-menu',
         'https://gumroad.com/help/article/124-your-gumroad-profile-page',
         'https://gumroad.com/help/article/51-what-is-gumroads-refund-policy')


def artwork(directory):
    """House Calm Sage stationery artwork, no logo, brand, text or borrowed assets."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise ValueError('Store pictures kosam Pillow install cheyali; /storekit malli pampandi.') from exc
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    colors=pdf_themes.PREMIUM_THEMES['Calm Sage']['tokens']
    paths=[]
    for name,size in [('profile.png',(800,800)),('store-cover.png',(1600,900))]:
        image=Image.new('RGB',size,colors['bg']);draw=ImageDraw.Draw(image)
        w,h=size
        draw.ellipse((-w//6,h//2,w//2,h+h//3),fill=colors['accent_soft'])
        draw.ellipse((w*2//3,-h//3,w+w//4,h//2),fill=colors['surface_soft'])
        left,top,right,bottom=w*3//10,h//5,w*7//10,h*4//5
        draw.rounded_rectangle((left+18,top+18,right+18,bottom+18),radius=20,fill=colors['accent_soft'])
        draw.rounded_rectangle((left,top,right,bottom),radius=20,fill=colors['surface'],outline=colors['accent'],width=4)
        for y in range(top+70,bottom-30,50):
            draw.rounded_rectangle((left+30,y,left+43,y+13),radius=3,outline=colors['accent'],width=2)
            draw.line((left+62,y+7,right-30,y+7),fill=colors['muted'],width=3)
        path=directory/name;image.save(path,'PNG');paths.append(path)
    return paths


def copy(db_path=None, products_dir=None, now=None):
    run=selltest.active(db_path)
    packs=run['packs'] if run else selltest.candidates(db_path,products_dir,now)[:8]
    lines=['Store name ideas (owner okati enchukondi):',*['- '+name for name in NAMES],
           '', 'Short bio:',BIO,'','About:',ABOUT,'','Refund draft:',REFUND,'','FAQ:',FAQ,
           '',f'Product order: {len(packs)}/8 test Packs available.']
    lines += [f"{i}. Pack #{p['id']}: {p['title']} | /upload {p['id']}" for i,p in enumerate(packs,1)]
    if len(packs)<8:lines.append('Inka Packs READY ayyaka /selltest list; /storekit malli pampandi.')
    lines += ['', 'Gumroad settings lo ekkada em pettali:',
              '1. Name ideas lo okati meere enchukondi; availability meere check cheyandi.',
              '2. Settings > Username lo handle pettandi; Profile settings lo Name, Bio, profile.png pettandi.',
              '3. Profile page kosam About copy, store-cover.png artwork, paina product order vaadandi. Current Profile customization help kinda undi; cover upload option lekapothe page artwork ga vaadandi.',
              '4. Products lo prathi /upload N kit nunchi files, real Preview images, Description pettandi; order paina unnattu review cheyandi.',
              '5. Settings lo Refund policy meere enchukondi; ee Refund draft ni actual policy tho match chesi FAQ paste cheyandi.',
              '6. Update Settings click chesi Profile preview chudandi. Owner review ayyaka meere publish cheyandi.',
              'Robot online Settings marchaledu. Next: /selltest list pampandi.',
              '', 'Official setup references (checked 2026-11-24):',*SOURCES]
    return '\n'.join(lines)


def build(directory, db_path=None, products_dir=None, now=None):
    directory=Path(directory)
    pictures=artwork(directory)
    document=directory/'store-kit.txt'
    document.write_text(copy(db_path,products_dir,now),encoding='utf-8')
    return [document,*pictures]
