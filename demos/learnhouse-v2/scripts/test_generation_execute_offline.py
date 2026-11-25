"""Exercise the paid execution route with an offline model in the local synthesis database."""
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

demo = Path.cwd()
root = demo.parents[1]
sys.path[:0] = [str(demo / 'scripts'), str(demo / 'integration'), str(root),
                str(demo / 'upstream/learnhouse/apps/api')]
from demo_environment import environment
from sqlalchemy.engine import make_url

settings = environment()
database = make_url(settings['LEARNHOUSE_SQL_CONNECTION_STRING'])
if (database.database != 'learnhouse_v2_demo' or
    (database.host and database.host not in ('localhost', '127.0.0.1'))):
    raise SystemExit('Use the local synthetic learnhouse_v2_demo database')
os.environ.update(settings)
os.chdir(demo / 'upstream/learnhouse/apps/api')
from app import app
from fastapi.testclient import TestClient
from generation_candidate import _FixtureResponses
from generation.workflow import DIMENSIONS
from src.education import business
import generate_question

policy_rules = business.policy_rules
def test_policy(policy):
    return dict(policy_rules(policy), maximum_task_budget=10)

accounts = json.loads((demo / '.runtime/demo-accounts.local.json').read_text())
course = accounts['objects']['courses'][0]
teacher = next(a for a in accounts['accounts'] if a['key'] == 'teacher_a')
student = next(a for a in accounts['accounts'] if a['key'] == 'student_a')
responses = _FixtureResponses(DIMENSIONS)
calls = []
real_subprocess = subprocess.run

def fake_chat(url, key, payload, timeout):
    assert url == 'https://offline.invalid/chat/completions'
    messages = payload['messages']
    job = json.loads(messages[-1]['content'])
    calls.append(job)
    stage = 'review_question' if job['candidate'] is not None else 'generate_question'
    output = responses.complete_validated(stage, messages, lambda _: None)
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(output)}}]}

def offline_cli(argv, **kwargs):
    if len(argv) < 3 or Path(argv[2]).name != 'generate_question.py':
        return real_subprocess(argv, **kwargs)
    with patch.object(sys, 'argv', argv[2:]), patch.object(generate_question, 'send_chat', fake_chat):
        with redirect_stdout(io.StringIO()):
            generate_question.main()
    return subprocess.CompletedProcess(argv, 0)

from generation_candidate import run_real_candidate
def project_test_result(*args, **kwargs):
    return replace(run_real_candidate(*args, **kwargs), provenance='simulated')

with TemporaryDirectory() as tmp:
    cfg = Path(tmp) / 'offline.env'
    cfg.write_text('ZZZ_API_KEY=FAKE\nZZZ_BASE_URL=https://offline.invalid\nZZZ_MODEL=offline-model\n')
    with patch.dict(os.environ, EA_GENERATION_ENV_FILE=str(cfg)), patch.object(business, 'policy_rules', test_policy), patch('generation_candidate.run_real_candidate', project_test_result), patch('research_adapter.subprocess.run', offline_cli):
        with TestClient(app, base_url='http://localhost:3300') as client:
            origin = {'Origin': 'http://localhost:3300'}
            login = client.post('/api/v1/auth/login', data={'username': teacher['email'], 'password': teacher['password'], 'org_slug': 'default'}, headers=origin)
            assert login.status_code == 200, login.status_code
            base = '/api/v1/education'
            view = client.get(f"{base}/courses/{course['id']}").json()
            assert any(c['key'] == 'generation_execute' for c in view['capabilities']), view.get('capabilities')
            source = next(r for r in view['resources'] if r['kind'] == 'source_text')
            point = next(p for p in view['knowledge_points'] if p['code'] == 'java.condition')
            payload = dict(mode='research_generator', run_mode='execute', source_version_id=source['id'],
                           policy_version_id=view['policy_version_id'], knowledge_point_ids=[point['id']],
                           target_student_id=student['user_id'], requirement='offline smoke 真实入口',
                           budget_limit=1, max_attempts=1, max_calls=4)
            from uuid import uuid4
            headers = {**origin, 'If-Match': view['etag'], 'Idempotency-Key': 'qa-live-offline-'+uuid4().hex}
            url = f"{base}/courses/{course['id']}/generation-runs"
            denied = client.post(url, json=payload, headers=headers)
            assert denied.status_code == 403 and not calls, denied.text
            payload['confirm_paid'] = True
            created = client.post(url, json=payload, headers=headers)
            assert created.status_code == 200, created.text
            task = created.json()
            assert task['status'] == 'running', task
            result = client.get(f"{base}/tasks/{task['id']}").json()
            assert result['status'] == 'succeeded' and len(calls) == 4, result
            repeated = client.post(url, json=payload, headers=headers)
            assert repeated.status_code == 200 and repeated.json()['id'] == task['id'] and len(calls) == 4
            resource = client.get(f"{base}/resources/{result['result_refs']['resource_id']}").json()
            assert resource['version']['forced_review'] and len(resource['candidate_history']) == 1
            assert 'offline smoke' in json.dumps(calls[0], ensure_ascii=False)
            print('HTTP execute smoke passed: confirmation, durable running state, four offline model responses, replay, candidate history and forced review')
