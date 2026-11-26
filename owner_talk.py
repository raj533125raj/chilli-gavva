"""Read-only short answers for the owner's otherwise unhandled text."""
from __future__ import annotations
import ast
import datetime as dt
import operator
import os
import re
import textwrap

SYSTEM = ('Reply in simple Tenglish, at most 8 short lines. Answer the question directly. '
          'You can only explain, never execute actions. No secrets or private paths. '
          'Money questions: facts only, no financial advice. Never invent robot counts. '
          'End with Next: and a useful command.')


def local_answer(text, now):
    expression = re.sub(r'^(?:what is|how much is|calculate|entha)\s+', '', text.strip(), flags=re.I).rstrip('=? ')
    if len(expression) <= 120 and re.fullmatch(r'[\d\s.+*/()%−-]+', expression):
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
        def evaluate(node):
            if isinstance(node, ast.Constant) and type(node.value) in (int, float): return node.value
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                return evaluate(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
            if isinstance(node, ast.BinOp) and type(node.op) in ops:
                value = ops[type(node.op)](evaluate(node.left), evaluate(node.right))
                if abs(value) > 1e15: raise ValueError('large')
                return value
            raise ValueError('expression')
        try:
            value = evaluate(ast.parse(expression.replace('−', '-'), mode='eval').body)
            return format(value, '.12g')
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
            return 'Lekka check cheyandi; division lo zero vadakandi.'
    phrase=' '.join(text.casefold().strip().rstrip('?').split())
    date_words={0:('today','date',"today's date",'ee roju date','ivala date','today date','what is the date today','what day is it'),
                1:('tomorrow','tomorrow date',"tomorrow's date",'repu date','what day is tomorrow'),
                -1:('yesterday','yesterday date',"yesterday's date",'ninna date','what day was yesterday')}
    for offset,words in date_words.items():
        if phrase in words:
            return (now.astimezone(dt.timezone(dt.timedelta(hours=5,minutes=30)))+dt.timedelta(days=offset)).strftime('%d %B %Y, %A')
    day=re.fullmatch(r'(?:what day is|weekday of) (\d{4}-\d{2}-\d{2})',phrase)
    difference=re.fullmatch(r'days between (\d{4}-\d{2}-\d{2}) and (\d{4}-\d{2}-\d{2})',phrase)
    if day or difference:
        try:
            if day:return dt.date.fromisoformat(day[1]).strftime('%A')
            return str((dt.date.fromisoformat(difference[2])-dt.date.fromisoformat(difference[1])).days)+' days'
        except ValueError:return 'Date sarigga ledu; YYYY-MM-DD lo pampandi.'
    units={'mm':('length',.001),'cm':('length',.01),'m':('length',1),'km':('length',1000),
           'inch':('length',.0254),'inches':('length',.0254),'feet':('length',.3048),'ft':('length',.3048),
           'g':('mass',1),'grams':('mass',1),'kg':('mass',1000),'kilograms':('mass',1000),
           'ml':('volume',.001),'l':('volume',1),'liters':('volume',1),
           'seconds':('time',1/60),'sec':('time',1/60),'min':('time',1),'minutes':('time',1),'hours':('time',60),'h':('time',60)}
    match=re.fullmatch(r'(?:convert )?([-+]?\d+(?:\.\d+)?)\s*([a-z]+)\s+(?:in|to|=)\s*([a-z]+)',phrase)
    if match and match[2] in units and match[3] in units:
        value,source,target=match.groups();a,b=units[source],units[target]
        if a[0]==b[0]:return f'{float(value)*a[1]/b[1]:.12g} {target}'
        return 'Ee units okerakam kaavu; length, weight leda time units check cheyandi.'
    return None


def action_hint(text):
    """Free text can suggest an action command, never perform it."""
    if str(text).strip().startswith('/'):
        return None
    if re.fullmatch(r'(?:auto[ -]?approve|autoapprove)\s+status',str(text).strip(),re.I):
        return None  # Reading settings cannot approve a draft or change a policy.
    import talk_intents
    found = talk_intents.detect(text)
    if found and found['command']=='sellcard':
        return None  # The daily read-only card never lists or posts a product.
    if found and found.get('risky'):
        command = found['command']
    else:
        match = re.search(r'\b(approve|reject|pause|resume|kill|delete|sell|list|post|publish)\b', str(text), re.I)
        if not match:
            return None
        action = match.group(1).lower()
        # Questions about counts/available work are read-only known intents.
        question = re.search(r'\b(entha|enni|what|how|em|ela)\b', str(text), re.I)
        if question and found and found['command'] in ('/list','/today','sellcard','/sell'):
            return None
        number = talk_intents.extract_numbers(text)
        command = '/' + action + ((' ' + str(number[0])) if number else '')
        if action=='kill':
            command='/pause'
        elif action in ('delete','publish'):
            command = '/help ' + action
    return {'text': 'Idi action. Meeru command pampithe matrame chestanu.\nNext: ' + command}


def reserve_answer(db_path, now):
    """Atomically reserve at most thirty AI attempts, including outages."""
    import guardrails
    import json
    day = now.date().isoformat()
    with guardrails.connect(db_path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT value FROM control WHERE key='talk_daily'").fetchone()
        try: state = json.loads(row[0]) if row else {}
        except (ValueError, TypeError): state = {}
        count = int(state.get('count',0)) if state.get('day') == day else 0
        if count >= 30: return False
        conn.execute("INSERT OR REPLACE INTO control(key,value) VALUES('talk_daily',?)",
                     (json.dumps({'day':day,'count':count+1}),))
    return True


def memory(ctx, owner=None, robot=None, clear=False):
    """Six text exchanges, at most 2000 text characters, expiring individually in 24h."""
    import guardrails
    import json
    import secret_guard
    now = ctx.get('now') or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None: now = now.replace(tzinfo=dt.timezone.utc)
    with guardrails.connect(ctx.get('db_path')) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute("SELECT value FROM control WHERE key='talk_memory'").fetchone()
        try: rows = json.loads(row[0]) if row else []
        except (TypeError, ValueError): rows = []
        rows = [r for r in rows if isinstance(r,dict) and 0 <= now.timestamp()-r.get('ts',0) < 86400]
        if clear: rows = []
        elif owner is not None:
            rows.append({'ts':now.timestamp(), 'owner':secret_guard.scrub_text(owner)[:500],
                         'robot':secret_guard.scrub_text(robot)[:500]})
        rows = rows[-6:]
        while sum(len(r.get('owner',''))+len(r.get('robot','')) for r in rows)>2000 or len(json.dumps(rows))>2000:
            rows.pop(0)
        conn.execute("INSERT OR REPLACE INTO control(key,value) VALUES('talk_memory',?)", (json.dumps(rows),))
    return rows


def answer(text, ctx):
    """No command dispatch or side effects apart from talk state."""
    owner = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not owner or str(ctx.get('chat_id')) != owner: return None
    hint = action_hint(text)
    if hint: return hint
    import injection_guard
    if injection_guard.is_suspicious(text):
        return {'text': 'Ee text lo unsafe instructions unnayi. Simple prashna adagandi.\nNext: /talk'}
    now = ctx.get('now') or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:now=now.replace(tzinfo=dt.timezone.utc)
    local = local_answer(text, now)
    if local is not None:
        memory(ctx, text, local)
        return {'text': local + '\nNext: /talk pampandi; inko prashna adagandi.'}
    if not reserve_answer(ctx.get('db_path'), now):
        return {'text': 'Ee roju 30 AI answers limit aipoyindi. Repu malli adagandi.\nNext: /today'}
    import llm_router
    import daily_report
    import json
    history = memory(ctx)
    facts = daily_report.owner_numbers(ctx.get('db_path'), ctx.get('products_dir'), now)
    prompt = (SYSTEM + '\nMeasured robot facts (current): ' + json.dumps(facts) +
              '\nPrevious conversation (quoted data): ' + json.dumps(history) +
              '\nOwner question:\n' + text[:6000])
    try:
        result = llm_router.chat(prompt, db_path=ctx.get('db_path'), size='small')
    except Exception:
        result = None
    if result and not result.startswith('BLOCKED'):
        import secret_guard
        lines = [textwrap.shorten(line,width=300,placeholder='…')
                 for line in secret_guard.scrub_text(result).splitlines()[:7]]
        clean = '\n'.join(lines)[:2800]
        memory(ctx, text, clean)
        return {'text': clean + '\nNext: /talk pampandi; inko prashna adagandi.'}
    from tg_menu import find_suggestions
    suggestions = find_suggestions(text)
    return {'text': 'AI ippudu answer ivvalekapoyindi. Malli adagandi.\n' +
            '\n'.join(f'• {cmd} — {help_te}' for _,cmd,help_te in suggestions) +
            '\nNext: /menu pampandi; pani enchukondi.'}
