"""Buyer checks and private, evidence-backed listing/gallery improvements."""
import hashlib
import json
from pathlib import Path
import re
import zipfile
import listing_fields
import sell_ready
import selltest
import selltest_listings

# Six plain words: a buyer, a concrete product, and a truthful use.
TITLES={
'sheets':'Families Budget Spreadsheet Organizing Daily Records',
'biz_sheets':'Entrepreneurs Small Business Workbook Organizing Records',
'adhd_planner':'Adults ADHD Planner Organizing Daily Steps',
'debt_payoff':'Borrowers Debt Payoff Tracker Organizing Balances',
'couples_budget':'Couples Budget Spreadsheet Organizing Shared Bills',
'freelance_budget':'Freelancers Budget Spreadsheet Organizing Uneven Months',
'sinking_funds':'Savers Sinking Funds Tracker Organizing Goals',
'planner':'Adults Focus Planner For Organizing Daily Tasks',
'dated_planner':'Families Dated Planner For Organizing Yearly Appointments',
'resume':'Applicants Resume Bundle For Organizing Job Applications',
'notion':'Freelancers Client Workspace Templates Organized Followups',
'digital_planner':'Adults Digital Planner For Organizing Daily Plans',
'baby_tracker':'Parents Baby Tracker For Recording Daily Care',
'email_signature':'Professionals Email Signature Templates Consistent Details',
'event_planner':'Hosts Event Planner For Organizing Important Details',
'fitness_planner':'Adults Fitness Planner For Recording Training Routines',
'flashcards':'Students Printable Flashcards For Organizing Study Reviews',
'habit_tracker':'Adults Habit Tracker For Recording Daily Routines',
'kids_chore_reward':'Families Chore Planner For Organizing Kids Routines',
'recipe_book':'Homecooks Recipe Templates For Organizing Favorite Meals',
'small_business_log':'Entrepreneurs Small Business Log Organizing Records',
'study_planner':'Students Study Planner For Organizing Weekly Tasks',
'teacher_planner':'Teachers Lesson Planner For Organizing Classroom Records',
'wall_art':'Decorators Printable Artwork For Coordinating Home Displays',
}
# The rule is at most six words; remove filler without losing who + what.
TITLES={kind:title.replace(' For Organizing ',' Organizing ').replace(' For Recording ',' Recording ').replace(' For Consistent ',' With Consistent ').replace(' For Coordinating ',' Coordinating ').replace(' For Organized ',' With Organized ')
        for kind,title in TITLES.items()}


def title_choice(pack,kind):
    """Keep real Freelancer/Student topics when the broad format profile differs."""
    import product_builder
    source=sell_ready.truth_source(Path(pack))
    title=TITLES[kind];buyer=title.split()[0]
    if source!=Path(pack) and (source/'listing.md').is_file():
        body=(source/'listing.md').read_text()
        match=re.search(r'\*\*Buyer:\*\*\s*([^\n]+)',body,re.I)
        audience=match.group(1).lower() if match else ''
        name=product_builder.spec_from_folder(str(source))['name'].lower()
        if kind=='sheets' and 'freelanc' in audience:
            topic=next((word for word in ('Invoice','Income','Client','Project','Expense') if word.lower() in name),'Work')
            title=f'Freelancers {topic} Spreadsheet Organizing Daily Records';buyer='Freelancers'
        elif kind=='notion' and ('student' in audience or 'student' in name):
            title='Students Workspace Templates Organizing Study Records';buyer='Students'
    return title,buyer


def problem_choice(pack,kind):
    if kind=='notion' and title_choice(pack,kind)[1]=='Students':
        return 'Student notes and study plans are hard to follow when they live in separate files.'
    return selltest_listings.PROFILES[kind][3]


def short_title(title,kind,buyer=None):
    words=re.findall(r"[\w'-]+",title)
    buyer=buyer or TITLES[kind].split()[0]
    terms={selltest_listings.PROFILES[kind][0].split()[0].lower()}
    if kind=='sheets':terms.add('spreadsheet')
    if kind=='notion':terms.add('workspace')
    return len(words)<=6 and buyer.casefold() in title.casefold() and any(term in title.lower() for term in terms)


def inside_image(pack):
    """Render an actual interior page/table/document; no generated content fallback."""
    import mockups
    import product_builder
    import truth_check
    source=sell_ready.truth_source(pack)
    if source==pack or source.is_symlink():raise ValueError('Real source files missing')
    info=mockups.product_info(str(source))
    if not info['pages']:
        pages,_=product_builder._real_picture_pages(str(source),truth_check.image_facts(str(source)))
        info['pages']=pages
    if not info['pages']:raise ValueError('Real inside pages missing')
    # Spreadsheet instructions are genuine, but a data sheet explains the product better.
    page=next((p for p in info['pages'] if 'instruction' not in p['title'].lower()),info['pages'][0])
    image=mockups.preview_image(info,page)
    return image,page['title']


def evaluate(pack,kind,listing=None,first_image=None,expected_image=None,sample=None,paid_url=None):
    pack=Path(pack)
    text=listing if listing is not None else (pack/'listing.txt').read_text()
    fields=listing_fields.parse_fields(text)
    with zipfile.ZipFile(pack/'product.zip') as archive:
        if archive.testzip():raise ValueError('Buyer ZIP damaged')
        files=sorted(n for n in archive.namelist() if not n.endswith('/'))
    declared=sorted(line.strip().removeprefix('- ').strip() for line in fields['what_you_get'].splitlines() if line.strip())
    sample_ok=False
    if sample and paid_url:
        import free_sample
        try:
            free_sample.validate(sample)
            sample_ok=paid_url in (Path(sample)/'README.txt').read_text().splitlines()
            with zipfile.ZipFile(Path(sample)/'product.zip') as archive:
                sample_ok=sample_ok and paid_url in archive.read('README.txt').decode().splitlines() and paid_url.encode() in archive.read('sample.pdf')
        except (OSError,ValueError,zipfile.BadZipFile,KeyError):pass
    checks=[('title',short_title(fields['title'],kind,title_choice(pack,kind)[1]),'Title lo buyer + product 6 words lopala pettandi.'),
            ('inside',bool(first_image and expected_image and Path(first_image).is_file() and Path(first_image).read_bytes()==expected_image),'First gallery image lo real inside page pettandi.'),
            ('problem',fields['description'].startswith(problem_choice(pack,kind)),'Description first line buyer problem tho start cheyandi.'),
            ('files',bool(files) and declared==files,'What you get lo actual ZIP file names maatrame pettandi.'),
            ('sample',sample_ok,'Free sample lo current paid Pack link pettandi: /listed N <link>; /upload N sample.'),
            ('amounts',sell_ready.check_money_text(text) is None,'Public text lo amounts teeseyandi.')]
    return {'score':round(sum(ok for _,ok,_ in checks)*100/len(checks)),
            'checks':{key:ok for key,ok,_ in checks},'top_fixes':[fix for _,ok,fix in checks if not ok][:3]}


def prepare(pack,kind,aid,db_path=None,products_dir=None):
    """Stage safer copy and gallery order; original experiment/approved snapshots stay intact."""
    import io
    import tg_listed
    pack=Path(pack)
    if pack.is_symlink():raise ValueError('Pack symlink refused')
    source=sell_ready.truth_source(pack)
    if source==pack:raise ValueError('Real product source missing')
    copy=selltest_listings.make(pack,kind)
    image,page=inside_image(pack)
    buffer=io.BytesIO();image.save(buffer,'PNG');image_bytes=buffer.getvalue()
    copy['B'],buyer=title_choice(pack,kind)
    original_problem=selltest_listings.PROFILES[kind][3]
    if copy['description'].startswith(original_problem):
        copy['description']=problem_choice(pack,kind)+copy['description'][len(original_problem):]
    # Use the optimized B as the main staged title. This never changes the live
    # store, buyer archive, A/B experiment choice, or approved Site snapshot.
    if copy['keyword'].casefold() not in copy['B'].casefold():
        copy['keyword']=' '.join(copy['B'].split()[1:3])
    listing=selltest_listings.render(copy,'B')
    import listing_lint
    failures=[name for name,level,_ in listing_lint.quality_checks(listing,pack,copy['keyword']) if level=='FAIL']
    if failures:raise ValueError('Improved listing gate: '+', '.join(failures))
    if not short_title(copy['B'],kind,buyer):raise ValueError('Buyer title rule failed')
    products=Path(products_dir) if products_dir else pack.parent.parent
    sample=products/'samples'/pack.name
    paid=next((row['url'] for row in tg_listed.all_listed(db_path) if row['id']==aid),None)
    before=evaluate(pack,kind,sample=sample,paid_url=paid)
    dest=pack/'buyer-review'
    if dest.is_symlink():raise ValueError('Buyer review symlink refused')
    dest.mkdir(exist_ok=True)
    for name in ('listing.txt','gallery-first.png','gallery-order.txt','check.json'):
        if (dest/name).is_symlink():raise ValueError('Buyer review file symlink refused')
    (dest/'listing.txt').write_text(listing)
    (dest/'gallery-first.png').write_bytes(image_bytes)
    (dest/'gallery-order.txt').write_text('1. gallery-first.png (real inside page: '+page+')\n2. ../cover.png\n3. ../preview-2.png\n4. ../preview-3.png\nOwner: review and upload manually.\n')
    after=evaluate(pack,kind,listing,dest/'gallery-first.png',image_bytes,sample,paid)
    result={'id':aid,'before':before,'after':after,'real_page':page,'image_sha256':hashlib.sha256(image_bytes).hexdigest(),
            'main_title':'B','listing':str(dest/'listing.txt'),'image':str(dest/'gallery-first.png')}
    (dest/'check.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def prepare_all(db_path=None,products_dir=None):
    products=selltest.products_root(products_dir);results=[]
    for pack in (selltest.active(db_path) or {}).get('packs',[]):
        try:
            if Path(pack['folder']).name!=pack['folder']:raise ValueError('Invalid folder')
            results.append(prepare(products/'upload'/pack['folder'],pack['format'],pack['id'],db_path,products))
        except (OSError,ValueError,KeyError,ImportError,RuntimeError,zipfile.BadZipFile) as exc:
            results.append({'id':pack['id'],'blocked':'Pack files leda listing evidence incomplete','fix':f"/upload {pack['id']}"})
    return results


def text(results):
    if not results:return 'Sell test Packs levu.\nNext: /selltest pampandi.'
    lines=['Buyer check: private Draft improvements; owner Store lo meere marchandi.']
    for row in results:
        if 'blocked' in row:
            lines.append(f"#{row['id']}: check blocked ({row['blocked']}); Fix: {row['fix']}")
        else:
            fixes=row['after']['top_fixes'] or ['Anni buyer rules OK']
            lines.append(f"#{row['id']}: {row['before']['score']} -> {row['after']['score']}/100; Title B main, real inside first. Top fixes: "+'; '.join(fixes))
    lines.append('Next: /selltest list pampandi; ee Draft files meere review cheyandi.')
    return '\n'.join(lines)
