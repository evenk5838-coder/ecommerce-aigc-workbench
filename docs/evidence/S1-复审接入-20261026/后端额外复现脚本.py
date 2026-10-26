#!/usr/bin/env python3
"""Offline extra review for copied HEAD 6eeda7e; no business data or network.
Run with: PYTHONDONTWRITEBYTECODE=1 /private/tmp/ecom-local-probe-20261022/venv/bin/python /private/tmp/s1-review-6eeda7e-mkw_oc2r/reproduce-extra-checks.py
The sibling backend/app is the previously copied source snapshot. This script
only imports that snapshot, creates a new temporary DB and synthetic image,
and intercepts provider HTTP construction before any network operation.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import threading
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
assert ROOT.is_relative_to(Path('/private/tmp')), 'Run only from the isolated /private/tmp copy'
assert not (ROOT / '.env').exists(), 'No .env may be present in this snapshot'
os.environ.clear()
os.environ.update({'APP_PROVIDER': 'ark', 'APP_BUDGET_CNY': '100', 'PYTHONDONTWRITEBYTECODE': '1'})
sys.dont_write_bytecode = True
sys.modules['torch'] = None
sys.path.insert(0, str(ROOT / 'backend'))
tempfile.tempdir = str(ROOT)

blocked = {'network': 0, 'thread_start': 0, 'outside_db': 0}
def audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        p = Path(os.fsdecode(args[0]))
        if p.name == '.env':
            raise RuntimeError('Reading .env is forbidden')
    if event == 'sqlite3.connect':
        p = Path(args[0]).resolve()
        if not p.is_relative_to(ROOT):
            blocked['outside_db'] += 1
            raise RuntimeError('Business/outside DB access forbidden')
    if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.gethostbyname'):
        blocked['network'] += 1
        raise RuntimeError('Network forbidden')
sys.addaudithook(audit)
def no_network(*a, **kw):
    blocked['network'] += 1
    raise RuntimeError('Network forbidden')
def no_thread(*a, **kw):
    blocked['thread_start'] += 1
    raise RuntimeError('Worker/thread start forbidden')
socket.create_connection = no_network
socket.socket.connect = no_network
socket.socket.connect_ex = no_network
threading.Thread.start = no_thread

ns = runpy.run_path(str(ROOT / 'backend/tools_s1_fix_tests.py'), run_name='isolated_review_support')
env = ns['setup']()  # fresh DB and synthetic 32x32 PNG only; no full suite rerun
main, db, config = ns['main'], ns['db'], ns['config']
cloud_tasks, ark, AppError = ns['cloud_tasks'], ns['providers_ark'], ns['AppError']
from app.assistant import approval, service
V10, MINI, REF = ns['V10'], ns['MINI'], env['ref']
rows = []
def count_tasks():
    return db.query('SELECT COUNT(*) c FROM tasks')[0]['c']
def params(duration=5):
    return {'prompt': '推近', 'duration': duration, 'ratio': '9:16', 'resolution': '720p'}
def create_video():
    return main.create_task(main.TaskIn(kind='video', model_id=V10, input_asset_id=REF, params=params()))

# New inputs through ordinary POST /api/tasks remain strict.
for raw in (10.0, '10', 5.5, True):
    before = count_tasks()
    try:
        main.create_task(main.TaskIn(kind='video', model_id=V10, input_asset_id=REF, params=params(raw)))
        outcome, code = 'accepted', None
    except AppError as exc:
        outcome, code = 'rejected', exc.code
    row = {'case': 'new_create_strict_control', 'input': raw, 'input_python_type': type(raw).__name__,
           'outcome': outcome, 'error_code': code, 'new_tasks': count_tasks() - before}
    assert outcome == 'rejected' and code == 'MODEL_CAPABILITY_MISMATCH' and row['new_tasks'] == 0, row
    rows.append(row)

# A new RetryIn override is erroneously passed through the legacy compatibility path.
for raw in (10.0, '10', 5.5, True):
    t = create_video()
    db.update('tasks', t['id'], {'status': 'failed', 'error_code': 'TEST'})
    before = count_tasks()
    normalized = None
    try:
        retry = main.retry_task(t['id'], main.RetryIn(params={'duration': raw}))
        normalized = db.loads(db.get('tasks', retry['task_id'])['params_json'])['duration']
        outcome, code = 'accepted', None
    except AppError as exc:
        outcome, code = 'rejected', exc.code
    row = {'case': 'retry_new_duration_override', 'input': raw, 'input_python_type': type(raw).__name__,
           'outcome': outcome, 'error_code': code, 'stored_new_duration': normalized,
           'stored_new_duration_python_type': type(normalized).__name__ if normalized is not None else None,
           'new_tasks': count_tasks() - before, 'violates_strict_new_input_boundary': type(raw) in (float, str) and raw in (10.0, '10')}
    if type(raw) in (float, str) and raw in (10.0, '10'):
        assert outcome == 'accepted' and type(normalized) is int and normalized == 10 and row['new_tasks'] == 1, row
    else:
        assert outcome == 'rejected' and code == 'MODEL_CAPABILITY_MISMATCH' and row['new_tasks'] == 0, row
    rows.append(row)

# Fresh capture for EACH legacy value at the actual HTTP-body-construction boundary.
for raw in (10.0, '10'):
    captured = []
    def capture_request(method, path, body=None, timeout=None):
        captured.append({'method': method, 'path': path, 'body': body})
        raise AppError('PROVIDER_BAD_REQUEST', 'offline recorder: stop before HTTP', http=400)
    original_request = ark._request
    ark._request = capture_request
    try:
        t = create_video()
        historical = params(raw)
        historical['input_asset_id'] = REF
        db.update('tasks', t['id'], {'params_json': db.dumps(historical)})
        stored = db.loads(db.get('tasks', t['id'])['params_json'])
        try:
            cloud_tasks.run_video(t['id'], V10, stored, type('Token', (), {'raise_if_canceled': lambda self: None})())
        except AppError as exc:
            assert exc.code == 'PROVIDER_BAD_REQUEST', exc.code
    finally:
        ark._request = original_request
    assert len(captured) == 1, captured
    body = captured[0]['body']
    assert type(body['duration']) is int and body['duration'] == 10, body
    # Do not save embedded synthetic image bytes; record only the useful wire fields.
    rows.append({'case': 'legacy_duration_provider_http_body', 'input': raw, 'input_python_type': type(raw).__name__,
                 'intercepted_request_count': len(captured), 'method': captured[0]['method'], 'path': captured[0]['path'],
                 'body': {k: v for k, v in body.items() if k not in ('content', 'safety_identifier')},
                 'content_types': [x['type'] for x in body['content']], 'actual_http_requests_sent': 0})

before = count_tasks()
try:
    main.post_batch(main.BatchIn(items=[
        main.TaskIn(kind='video', model_id=V10, input_asset_id=REF, params=params()),
        main.TaskIn(kind='video', model_id=MINI, input_asset_id=REF, params=params(4)),
    ]))
    raise AssertionError('Mixed batch was accepted')
except AppError as exc:
    assert exc.code == 'MODEL_NOT_OPENED', exc.code
    code = exc.code
assert count_tasks() == before
rows.append({'case': 'mixed_batch_unopened_model', 'error_code': code, 'new_tasks': 0})

session = service.ensure_session(None, 'extra-review', 'generate')
payload = {'kind': 'video', 'model_id': V10, 'input_asset_id': REF, 'params': params()}
quote = service.quote(session_id=session['id'], project_id='extra-review', payload=payload)
approved = approval.create_approval(quote_id=quote['quote_id'], session_id=session['id'],
                                    project_id='extra-review', user_action='confirm_generate')
original_activation = config.MODEL_ACTIVATION[V10]
config.MODEL_ACTIVATION[V10] = 'not_opened'
before = count_tasks()
try:
    main.create_task(main.TaskIn(**approved['submit_payload'], approval_id=approved['approval_id']))
    raise AssertionError('Approved submit ignored current unopened state')
except AppError as exc:
    assert exc.code == 'MODEL_NOT_OPENED', exc.code
    code = exc.code
finally:
    config.MODEL_ACTIVATION[V10] = original_activation
assert count_tasks() == before
rows.append({'case': 'approved_submit_after_activation_closed', 'error_code': code, 'new_tasks': 0,
             'approval_used_count_after_rejection': db.get('assistant_approvals', approved['approval_id'])['used_count']})

source_paths = ['backend/app/config.py', 'backend/app/main.py', 'backend/app/cloud_tasks.py',
                'backend/app/providers_ark.py', 'backend/app/assistant/approval.py', 'backend/tools_s1_fix_tests.py']
result = {
    'revision': '6eeda7e75210429ac2be93bfe912993a96f48637', 'captured_at': datetime.now(timezone.utc).isoformat(),
    'scope': 'Additional independent offline review; handler calls, provider HTTP-body capture, no HTTP transport/server',
    'source_snapshot_root': str(ROOT), 'source_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in source_paths},
    'isolation': {'fresh_db': str(config.DB_PATH), 'business_db_opened': False, 'env_read': False,
                  'worker_started': False, 'network_requests_sent': 0, 'paid_calls': 0,
                  'synthetic_image': '32x32 PNG', 'guards_attempted_violation_count': blocked},
    'recorded_cases': len(rows), 'reproduction_assertions_passed': True, 'findings': [
        {'severity': 'boundary_bug', 'summary': 'RetryIn.params duration overrides 10.0 and string 10 are accepted via legacy=True although ordinary new requests reject them.',
         'code': 'backend/app/main.py:527-538', 'recommended_fix': 'Normalize only untouched historical duration; validate a supplied override using strict new-input rules.'}],
    'cases': rows,
    'limits': ['No real HTTP/server transport exercised; direct Python handlers were called.',
               'Provider _request was intercepted before network; this proves outbound body construction, not provider acceptance.',
               'All task writes and approvals exist only in the new isolated temporary DB.',
               'This rerun repeats only the extra checks, not the original 13-case suite.']}
out = ROOT / 'extra-independent-review-results.json'
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'result': str(out), 'recorded_cases': len(rows), 'reproduction_assertions_passed': True,
                  'known_boundary_bug_confirmed': True, 'fresh_db': str(config.DB_PATH)}, ensure_ascii=False))
