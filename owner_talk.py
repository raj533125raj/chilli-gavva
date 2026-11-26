"""Read-only short answers for the owner's otherwise unhandled text."""
from __future__ import annotations
import ast
import datetime as dt
import operator
import os
import re

SYSTEM = ('Reply in simple Tenglish, at most 8 short lines. Answer the question directly. '
          'You can only explain, never execute actions. No secrets or private paths. '
          'Money questions: facts only, no financial advice. Never invent robot counts. '
          'End with Next: and a useful command.')


def local_answer(text, now):
    expression = re.sub(r'^(?:what is|calculate|entha)\s+', '', text.strip(), flags=re.I).rstrip('=? ')
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
    if re.fullmatch(r'(?:today|date|today.s date|ee roju date|ivala date)\??', text.strip(), re.I):
        return now.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime('%d %B %Y, %A')
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(mm|cm|m|km|g|kg|min|minutes|hours|h)\s+(?:in|to|=)\s*(mm|cm|m|km|g|kg|min|minutes|hours|h)\??', text.strip(), re.I)
    if match:
        units = {'mm':('length',.001),'cm':('length',.01),'m':('length',1),'km':('length',1000),
                 'g':('mass',1),'kg':('mass',1000),'min':('time',1),'minutes':('time',1),'hours':('time',60),'h':('time',60)}
        value, source, target = match.groups(); a,b=units[source.lower()],units[target.lower()]
        if a[0] == b[0]: return f'{float(value)*a[1]/b[1]:.12g} {target}'
    return None


def answer(text, ctx):
    """No command dispatch or side effects apart from talk state."""
    owner = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not owner or str(ctx.get('chat_id')) != owner: return None
    now = ctx.get('now') or dt.datetime.now(dt.timezone.utc)
    local = local_answer(text, now)
    if local is not None: return {'text': local + '\nNext: /talk pampandi; inko prashna adagandi.'}
    import llm_router
    try:
        result = llm_router.chat(SYSTEM + '\nOwner question:\n' + text[:6000], db_path=ctx.get('db_path'), size='small')
    except Exception:
        result = None
    if result and not result.startswith('BLOCKED'):
        import secret_guard
        lines = secret_guard.scrub_text(result).splitlines()[:7]
        return {'text': '\n'.join(lines)[:2800] + '\nNext: /talk pampandi; inko prashna adagandi.'}
    from tg_menu import find_suggestions
    suggestions = find_suggestions(text)
    return {'text': 'AI ippudu answer ivvalekapoyindi. Malli adagandi.\n' +
            '\n'.join(f'• {cmd} — {help_te}' for _,cmd,help_te in suggestions) +
            '\nNext: /menu pampandi; pani enchukondi.'}
