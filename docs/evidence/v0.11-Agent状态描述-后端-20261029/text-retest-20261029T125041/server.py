from pathlib import Path
import importlib.util,sqlite3,json
R=Path('/Users/niannian/Documents/VScode/电商工作平台');E=R/'docs/evidence/v0.11-Agent澄清修复-后端-20261029';D=Path('/private/tmp/ecom-v011-real-acceptance/closing-20261029-113020')
c=sqlite3.connect((D/'acceptance.db').as_uri()+'?mode=ro',uri=True);assert not c.execute("SELECT 1 FROM tasks WHERE status NOT IN ('succeeded','failed','canceled','interrupted')").fetchall();c.close()
spec=importlib.util.spec_from_file_location('guard',R/'scripts/real-director-acceptance.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g);g.closing_profile()
def no_media(*args,**kwargs):raise ValueError('This resumption authorizes text only; all media requests are blocked')
g.Guard.ark=no_media
original_text = g.Guard.deepseek
start_count = sum(c['kind']=='llm' for c in json.loads((D/'provider-ledger.json').read_text())['calls'])
assert start_count == 15
limit = min(20, start_count + 2)
def bounded_text(self, *args, **kwargs):
    with self._state() as state:
        if sum(c['kind']=='llm' for c in state['calls']) >= limit:
            raise ValueError('Status retest limited to two remaining text requests')
    return original_text(self, *args, **kwargs)
g.Guard.deepseek = bounded_text
g.serve(str(R/'docs/evidence/v0.11-收口真实复验-20261029/approval.json'),52665)
