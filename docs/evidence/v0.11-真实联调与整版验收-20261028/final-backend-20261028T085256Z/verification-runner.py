from pathlib import Path
import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path('/Users/niannian/Documents/VScode/电商工作平台')

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

def sources():
    paths = list((ROOT / 'backend/app').rglob('*.py')) + list((ROOT / 'backend/tests').glob('test_*.py'))
    paths += [ROOT / 'backend/tools_director_chat_tests.py', ROOT / 'scripts/check-v011-legacy.py', ROOT / 'scripts/real-director-acceptance.py']
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(set(paths))}

def check(mode, run):
    sys.path.insert(0, str(ROOT / 'backend'))
    os.chdir(ROOT)
    read = Path.read_text
    with patch.object(Path, 'read_text', lambda path, *a, **kw: '' if path.name == '.env' else read(path, *a, **kw)), patch.object(Path, 'mkdir', lambda *a, **kw: None):
        from app import config
    with patch.object(Path, 'read_text', lambda path, *a, **kw: '' if path.name == '.env' else read(path, *a, **kw)), patch('socket.create_connection', side_effect=AssertionError('Network forbidden')), patch('socket.socket.connect', side_effect=AssertionError('Network forbidden')), patch('socket.socket.connect_ex', side_effect=AssertionError('Network forbidden')):
        if mode == 'backend':
            suite = unittest.defaultTestLoader.discover(str(ROOT / 'backend/tests'))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            summary = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped), 'passed': result.wasSuccessful()}
        else:
            spec = importlib.util.spec_from_file_location('retained_legacy', ROOT / 'backend/tools_director_chat_tests.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.OUT = run / 'legacy'
            code = module.run()
            summary = {'tests': len(module.RESULTS), 'passed_tests': sum(row['ok'] for row in module.RESULTS), 'exit_code': code, 'passed': code == 0}
        write(run / (mode + '-summary.json'), summary)
        return 0 if summary['passed'] else 1

if len(sys.argv) > 1:
    raise SystemExit(check(sys.argv[1], Path(sys.argv[2])))

run = Path(__file__).resolve().parent
write(run / 'source-fingerprints-before.json', sources())
def business_inputs():
    paths = [ROOT / 'data/app.db'] + list((ROOT / 'frontend/dist').rglob('*'))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths)) if path.is_file()}
write(run / 'business-inputs-before.json', business_inputs())
print('RUN_DIR=' + str(run), flush=True)
results = {}
for mode in ('backend', 'legacy'):
    cmd = [sys.executable, __file__, mode, str(run)]
    proc = subprocess.run(cmd, cwd=ROOT,
        env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True)
    (run / (mode + '.stdout.txt')).write_text(proc.stdout)
    (run / (mode + '.stderr.txt')).write_text(proc.stderr)
    (run / (mode + '.log')).write_text(proc.stdout + proc.stderr)
    results[mode] = {'command': cmd, 'exit_code': proc.returncode}
    print(mode + '_EXIT=' + str(proc.returncode), flush=True)
after = sources()
write(run / 'source-fingerprints-after.json', after)
before = json.loads((run / 'source-fingerprints-before.json').read_text())
results['source_files'] = len(after)
results['source_changes_during_checks'] = [key for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]
business_after = business_inputs()
write(run / 'business-inputs-after.json', business_after)
business_before = json.loads((run / 'business-inputs-before.json').read_text())
results['business_input_changes_during_checks'] = [key for key in sorted(business_before.keys() | business_after.keys()) if business_before.get(key) != business_after.get(key)]
results['real_model_calls'] = 0
results['network_blocked_in_verifier'] = True
results['business_db_and_frontend_dist_not_written'] = not results['business_input_changes_during_checks']
results['finished_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
write(run / 'machine-summary.json', results)
manifest = {str(path.relative_to(run)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(run.rglob('*')) if path.is_file() and path.name != 'manifest-sha256.json'}
write(run / 'manifest-sha256.json', manifest)
print(json.dumps(results, ensure_ascii=False), flush=True)
raise SystemExit(any(row['exit_code'] for row in (results['backend'], results['legacy'])) or bool(results['source_changes_during_checks']) or bool(results['business_input_changes_during_checks']))
