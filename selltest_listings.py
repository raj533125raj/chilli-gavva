"""Buyer-focused A/B copy for the owner's private sell-test upload kits."""
from pathlib import Path
import zipfile

# Primary keyword, two concrete buyer benefits, and the buyer's opening problem.
PROFILES={
 'sheets':('Budget Spreadsheet','See your totals clearly','Keep daily records organized','Scattered records make your next financial check harder than it needs to be.'),
 'debt_payoff':('Debt Payoff Tracker','See your payoff order clearly','Watch each balance go down','Paying several debts at once makes it hard to know which one to focus on next.'),
 'couples_budget':('Couples Budget Spreadsheet','Split shared bills fairly','See who owes whom at a glance','Shared bills get confusing when two people pay from different accounts.'),
 'freelance_budget':('Budget Spreadsheet','Plan around uneven months','Set money aside for tax and slow months','Uneven months make it hard to plan a steady budget.'),
 'sinking_funds':('Sinking Funds Tracker','See every savings goal in one place','Know what to set aside each month','Planned costs feel bigger when you have not saved a little for them each month.'),
 'adhd_planner':('ADHD Planner','Plan one clear step at a time','Use undated pages on any day','Busy days are hard to plan when every task looks equally urgent.'),
 'biz_sheets':('Small Business Workbook','Track orders and expenses','Keep your business records together','Orders, expenses and stock notes are easy to lose across separate files.'),
 'planner':('Focus Planner','Choose your next manageable step','Plan one calm and manageable day','A crowded to-do list can make it hard to choose your next step.'),
 'dated_planner':('Dated Planner','See your full year more clearly','Keep dates and plans together','Appointments and plans are harder to follow when they live in separate places.'),
 'resume':('Resume Bundle','Organize your job search','Prepare your next application','Starting each job application from scattered documents takes extra effort.'),
 'notion':('Client Workspace Templates','Keep client work together','Organize your next follow-up','Client notes and follow-ups can disappear across different documents.'),
 'digital_planner':('Digital Planner','Navigate your plans quickly','Keep your planning pages together','Finding the right planning page can interrupt your daily routine.'),
 'baby_tracker':('Baby Routine Tracker','Keep daily care notes together','Share a clear routine record','Remembering every care detail is difficult when notes are scattered.'),
 'email_signature':('Email Signature Templates','Keep contact details consistent','Create a clear sign-off','Updating contact details in separate signatures can become repetitive.'),
 'event_planner':('Event Planner','Keep event details together','Plan your next event step','Event details are easy to miss when lists live in different places.'),
 'fitness_planner':('Fitness Planner','Record your training routine','Review your next training step','Training notes are difficult to review when they are spread across loose pages.'),
 'flashcards':('Printable Flashcards','Make review sessions easier','Keep study prompts together','Preparing a study session takes longer when your review prompts are scattered.'),
 'habit_tracker':('Habit Tracker','Review your daily routine','Keep small habits visible','Small routine steps are easy to forget without a simple daily record.'),
 'kids_chore_reward':('Kids Chore Planner','Make daily chores clear','Keep routines and rewards together','Daily chores are harder to explain when the routine changes from day to day.'),
 'recipe_book':('Recipe Book Templates','Keep favorite recipes together','Find your next meal idea','Favorite recipes can be difficult to find across loose notes and saved pages.'),
 'small_business_log':('Small Business Log','Keep daily records together','Review your business routine','Loose business notes make regular record keeping harder to maintain.'),
 'study_planner':('Study Planner','Choose your next study step','Keep study plans visible','A long study list can make it difficult to decide where to start.'),
 'teacher_planner':('Teacher Planner','Keep lesson plans together','Prepare your next school day','Lesson notes and classroom records can become difficult to find across separate pages.'),
 'wall_art':('Printable Wall Art','Create a coordinated display','Refresh your space simply','Choosing pieces that work together can take time when each design is separate.'),
}
FILE='listing-ab.txt'


def make(pack,kind=None):
    import store_kits
    import sell_ready
    import listing_fields
    import listing_lint
    pack=Path(pack)
    if pack.is_symlink() or (pack/'product.zip').is_symlink():raise ValueError('Pack symlink refused')
    info=store_kits.parse_listing_file(pack/'listing.txt')
    kind=kind or store_kits.gumroad_product_type(pack,info)
    if kind not in PROFILES:raise ValueError('Product buyer profile missing')
    keyword,a,b,problem=PROFILES[kind]
    with zipfile.ZipFile(pack/'product.zip') as archive:
        if archive.testzip() is not None:raise ValueError('Buyer ZIP is damaged')
        files=sorted(n for n in archive.namelist() if not n.endswith('/'))
        if not files or any(Path(n).is_absolute() or '..' in Path(n).parts for n in files):raise ValueError('Buyer ZIP has unsafe or missing files')
    tags=[]
    for tag in store_kits.gumroad_tags(info['tags'],kind):
        tag=' '.join(tag.split())
        if tag and len(tag)<=20 and tag.casefold() not in {t.casefold() for t in tags}:tags.append(tag)
    tags=tags[:10]
    if len(tags)<3:raise ValueError('At least three real search tags are required')
    description=store_kits.trim_to_word_boundary(problem+' '+info['description'].strip(),3000)
    fields=listing_fields.parse_fields(info['raw'])
    result={'A':keyword+' - '+a,'B':keyword+' - '+b,'description':description,
            'files':files,'tags':tags,'keyword':keyword,'format':kind,
            'details':{label:fields[key] for key,label in (('how_to_use','How to use'),('license','License'),('format','File format'),('size','File size')) if fields[key]}}
    text='\n'.join((result['A'],result['B'],description,', '.join(tags),'\n'.join(files)))
    if sell_ready.check_money_text(text):raise ValueError('Public listing must not contain amounts')
    for variant in ('A','B'):
        failures=[name for name,level,_ in listing_lint.quality_checks(render(result,variant),pack,keyword) if level=='FAIL']
        if failures:raise ValueError('Listing gate: '+', '.join(failures))
    return result


def render(copy,variant='A'):
    return ('Title A: '+copy['A']+'\nTitle B: '+copy['B']+'\nActive title: '+variant+'\n\n'
            'Title: '+copy[variant]+'\n\nDescription:\n'+copy['description']+'\n\nWhat you get:\n'+
            '\n'.join('- '+name for name in copy['files'])+'\n\n'+'\n\n'.join(label+':\n'+value for label,value in copy['details'].items())+
            '\n\nTags: '+', '.join(copy['tags'])+'\n\nMain keyword: '+copy['keyword']+'\n')


def write(aid,pack,destination,db_path=None):
    import selltest
    run=selltest.active(db_path)
    member=next((p for p in (run or {}).get('packs',[]) if p['id']==aid),None)
    if member is None:return None
    pack,destination=Path(pack),Path(destination)
    if pack.name!=member['folder'] or destination.is_symlink():raise ValueError('Sell-test Pack folder mismatch')
    copy=make(pack,member['format'])
    target=destination/FILE
    if target.is_symlink():raise ValueError('Listing destination symlink refused')
    destination.mkdir(parents=True,exist_ok=True)
    staged=destination/(FILE+'.tmp')
    if staged.is_symlink():raise ValueError('Listing temporary symlink refused')
    staged.write_text(render(copy,selltest.title_variant(aid,db_path)))
    staged.replace(target)
    return target


def ensure_all(products_dir=None,db_path=None):
    import selltest
    import gumroad_handoff
    products=selltest.products_root(products_dir)
    result={}
    for p in (selltest.active(db_path) or {}).get('packs',[]):
        try:
            if Path(p['folder']).name!=p['folder']:raise ValueError('Invalid Pack folder')
            pack=products/'upload'/p['folder']
            destination=pack/gumroad_handoff.FOLDER
            if not destination.is_dir():destination=gumroad_handoff.build(pack)
            result[p['id']]=str(write(p['id'],pack,destination,db_path))
        except (OSError,ValueError,zipfile.BadZipFile) as exc:
            result[p['id']]='Blocked: '+str(exc)
    return result
