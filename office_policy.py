"""Owner policy edits may lower runtime permissions/budgets, never raise hard guardrails."""
import guardrails

MODES = {'manual', 'supervised', 'autonomous'}


def apply(record, db_path=None):
    if record['target'] != 'robot':
        raise ValueError('invalid policy target')
    action, value = record['action'], record['value']
    if action == 'set_policy':
        if not isinstance(value,dict) or set(value) != {'autonomy','budget_limit','budget_unit'}:
            raise ValueError('invalid policy')
        autonomy, limit, unit = value['autonomy'], value['budget_limit'], value['budget_unit']
    elif action == 'set_autonomy':
        autonomy, limit, unit = value, guardrails.daily_cap(db_path), 'calls'
    else:
        if type(value) is int:
            limit, unit = value, 'calls'
        elif isinstance(value,dict) and set(value)=={'limit','unit'}:
            limit, unit = value['limit'], value['unit']
        else:
            raise ValueError('invalid budget')
        autonomy = guardrails.autonomy(db_path)
    if not isinstance(autonomy,str) or autonomy not in MODES or type(limit) is not int or limit < 0 or unit not in ('calls','tokens'):
        raise ValueError('invalid policy value')
    if unit == 'tokens':
        return 'held','token accounting is unavailable; use a calls budget',None
    if limit > guardrails.configured_daily_cap():
        return 'rejected','budget exceeds the configured guardrails cap',None
    conn = guardrails.connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT llm_calls FROM usage_log WHERE day=?',(guardrails.today(),)).fetchone()
            if row and row[0]>limit:
                return 'held','budget is below calls already recorded today',None
            for key,value in (('office_autonomy',autonomy),('office_daily_call_cap',str(limit))):
                conn.execute('INSERT INTO control(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
        return 'applied','autonomy and call budget updated within guardrails',None
    finally:
        conn.close()
