"""Allowlisted trigger conditions; no eval, scripts or executable request payloads."""
import datetime
import json
import time

import office_tasks
import secret_guard

MAX_TRIGGERS = 100
COOLDOWN = 3600
METRICS = {'drafts_waiting', 'queued_tasks', 'failed_tasks'}
OPERATORS = {'>', '>=', '<', '<=', '=='}


def connect(db_path=None):
    conn = office_tasks.connect(db_path)
    conn.execute('''CREATE TABLE IF NOT EXISTS office_triggers (
        id TEXT PRIMARY KEY, definition TEXT NOT NULL, last_condition INTEGER NOT NULL DEFAULT 0,
        last_fired REAL, updated REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)''')
    conn.commit()
    return conn


def _task_id(value):
    import re
    if not isinstance(value, str):
        raise ValueError('invalid task reference')
    match = re.fullmatch(r'(?:task:|task-|chilli-gavva-task-)?([1-9][0-9]{0,9})', value)
    if not match:
        raise ValueError('invalid task reference')
    return int(match.group(1))


def definition(value, conn):
    base = {'label', 'agent_id', 'enabled', 'kind'}
    allowed = base | {'at', 'watch_id', 'match_state', 'condition', 'action'}
    if not isinstance(value, dict) or not base <= set(value) or set(value) - allowed:
        raise ValueError('invalid trigger')
    if type(value['enabled']) is not bool or not isinstance(value['label'], str) or not 1 <= len(value['label'].strip()) <= 120:
        raise ValueError('invalid trigger')
    recipient = office_tasks.agent(value['agent_id'])['id']
    kind, action = value['kind'], value.get('action', 'ask_agent')
    if kind not in ('at', 'agent_state', 'task_done', 'state') or action not in ('ask_agent', 'summary'):
        raise ValueError('invalid trigger type')
    result = {**value, 'agent_id':recipient, 'label':secret_guard.scrub_text(value['label'].strip()), 'action':action}
    if kind == 'at':
        stamp = value.get('at')
        if not isinstance(stamp, str) or value.get('watch_id') is not None or value.get('match_state') is not None or value.get('condition') is not None:
            raise ValueError('invalid trigger time')
        due = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if due.tzinfo is None:
            raise ValueError('trigger time requires timezone')
        result['at'] = due.astimezone(datetime.timezone.utc).isoformat()
    elif kind == 'state':
        condition = value.get('condition')
        if (not isinstance(condition, dict) or set(condition) != {'metric', 'op', 'value'}
                or not isinstance(condition['metric'],str) or condition['metric'] not in METRICS
                or not isinstance(condition['op'],str) or condition['op'] not in OPERATORS
                or type(condition['value']) is not int or not 0 <= condition['value'] <= 10000
                or any(value.get(k) is not None for k in ('at','watch_id','match_state'))):
            raise ValueError('invalid state condition')
    elif kind == 'agent_state':
        if value.get('match_state') not in ('idle','working','error') or value.get('at') is not None or value.get('condition') is not None:
            raise ValueError('invalid agent state')
        result['watch_id'] = office_tasks.agent(value.get('watch_id'))['id']
    else:
        if any(value.get(k) is not None for k in ('at','match_state','condition')):
            raise ValueError('invalid task condition')
        task_id = _task_id(value.get('watch_id'))
        if not conn.execute('SELECT 1 FROM tasks WHERE id=?',(task_id,)).fetchone():
            raise ValueError('unknown task reference')
        result['watch_id'] = 'task:' + str(task_id)
    return result


def apply(record, db_path=None, now=None):
    now = time.time() if now is None else now
    conn = connect(db_path)
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT definition,archived FROM office_triggers WHERE id=?',(record['target'],)).fetchone()
            action = record['action']
            if action == 'delete_trigger':
                if record['value'] is not None:
                    raise ValueError('delete requires null')
                if not existing:
                    return 'rejected','trigger not found',None
                conn.execute('UPDATE office_triggers SET archived=1,updated=? WHERE id=?',(now,record['target']))
            else:
                if action == 'add_trigger' and existing:
                    return 'rejected','trigger id already exists',None
                if action == 'edit_trigger' and (not existing or existing[1]):
                    return 'rejected','active trigger not found',None
                value = record['value']
                if action == 'edit_trigger':
                    if not isinstance(value,dict) or not value:
                        raise ValueError('invalid trigger edit')
                    value = {**json.loads(existing[0]), **value}
                clean = definition(value,conn)
                count = conn.execute('SELECT COUNT(*) FROM office_triggers WHERE archived=0').fetchone()[0]
                if (not existing or existing[1]) and count >= MAX_TRIGGERS:
                    return 'held','trigger cap reached',None
                conn.execute('''INSERT INTO office_triggers(id,definition,updated) VALUES (?,?,?)
                    ON CONFLICT(id) DO UPDATE SET definition=excluded.definition,last_condition=0,
                    last_fired=NULL,updated=excluded.updated,archived=0''',(record['target'],json.dumps(clean),now))
            for task_id,payload in conn.execute("SELECT id,payload FROM tasks WHERE kind='office_task' AND status='queued'").fetchall():
                if json.loads(payload).get('trigger_id') == record['target']:
                    conn.execute("UPDATE tasks SET status='cancelled' WHERE id=?",(task_id,))
        return 'applied','trigger archived' if action == 'delete_trigger' else 'trigger stored',None
    finally:
        conn.close()


def enabled(ident,conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='office_triggers'").fetchone():
        return False
    row = conn.execute('SELECT definition,archived FROM office_triggers WHERE id=?',(ident,)).fetchone()
    return bool(row and not row[1] and json.loads(row[0])['enabled'])


def _matches(value,conn,now):
    kind = value['kind']
    if kind == 'at':
        return datetime.datetime.fromisoformat(value['at']).timestamp() <= now
    if kind == 'task_done':
        row = conn.execute('SELECT status FROM tasks WHERE id=?',(_task_id(value['watch_id']),)).fetchone()
        return bool(row and row[0]=='done')
    if kind == 'agent_state':
        name = office_tasks.agent(value['watch_id'])['name']
        running = conn.execute("SELECT 1 FROM tasks WHERE agent=? AND status='running' LIMIT 1",(name,)).fetchone()
        last = conn.execute('SELECT status FROM tasks WHERE agent=? ORDER BY id DESC LIMIT 1',(name,)).fetchone()
        state = 'working' if running else 'error' if last and last[0]=='failed' else 'idle'
        return state == value['match_state']
    condition = value['condition']
    metric = condition['metric']
    if metric == 'drafts_waiting':
        number = conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
    else:
        status = 'queued' if metric=='queued_tasks' else 'failed'
        number = conn.execute('SELECT COUNT(*) FROM tasks WHERE status=?',(status,)).fetchone()[0]
    threshold = condition['value']
    op = condition['op']
    return {'>':number>threshold, '>=':number>=threshold, '<':number<threshold,
            '<=':number<=threshold, '==':number==threshold}[op]


def tick(db_path=None, now=None, dry_run=False, limit=None):
    import guardrails
    import rest_window
    now = time.time() if now is None else now
    if not guardrails.automation_allowed(db_path) or rest_window.in_rest_window():
        return {'queued':0}
    limit = office_tasks.MAX_DISPATCH if limit is None else min(limit,office_tasks.MAX_DISPATCH)
    conn = connect(db_path)
    queued = 0
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            pending_rows = conn.execute("SELECT payload FROM tasks WHERE kind='office_task' AND status IN ('queued','running')").fetchall()
            pending = len(pending_rows)
            for ident,raw,last_condition,last_fired in conn.execute('SELECT id,definition,last_condition,last_fired FROM office_triggers WHERE archived=0 ORDER BY id').fetchall():
                value=json.loads(raw)
                if not value['enabled']:
                    continue
                match=_matches(value,conn,now)
                if not match:
                    if not dry_run:
                        conn.execute('UPDATE office_triggers SET last_condition=0 WHERE id=?',(ident,))
                    continue
                if (last_condition or value['kind']=='at' and last_fired is not None
                        or last_fired is not None and now-last_fired<COOLDOWN):
                    continue
                if pending>=office_tasks.MAX_PENDING or queued>=limit:
                    break
                if any(json.loads(row[0]).get('trigger_id')==ident for row in pending_rows):
                    continue
                payload={'trigger_id':ident,'agent_id':value['agent_id'],'text':value['label'],
                         'summary':value['action']=='summary','metric':(value.get('condition') or {}).get('metric')}
                if not office_tasks.task_enabled(payload,db_path,conn):
                    continue
                queued+=1; pending+=1
                if not dry_run:
                    conn.execute("INSERT INTO tasks(ts,kind,payload,priority) VALUES (?,'office_task',?,5)",
                                 (datetime.datetime.fromtimestamp(now,datetime.timezone.utc).isoformat(),json.dumps(payload)))
                    conn.execute('UPDATE office_triggers SET last_condition=1,last_fired=? WHERE id=?',(now,ident))
        return {'queued':queued}
    finally:
        conn.close()
