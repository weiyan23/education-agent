"""Isolated regression: real PostgreSQL and API routes; synthetic identity and model answer.

Requires pgserver, psycopg, SQLAlchemy, sqlmodel, jsonschema, FastAPI and httpx.
Always creates its own temporary database; accepts no database URL or credentials.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import patch
from uuid import uuid4

import pgserver
import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

DEMO = Path(__file__).resolve().parents[1]
TESTS = DEMO / 'integration/tests'
sys.path[:0] = [str(DEMO / 'upstream/learnhouse/apps/api'), str(DEMO / 'integration'),
                str(DEMO.parents[1]), str(TESTS)]
from test_recommendation_input import RecommendationInputTests
from profiling.run_store import fingerprint, RunStore
from recommendation.context import prepare_context
from recommendation.runner import run_recommendation
from research_adapter import (CurrentContext, JobContext, PaidAuthorization, Principal,
                              ResearchAdapter, Snapshot)


def test_api(uri, actor_id):
    # Load the production router without booting unrelated LMS/Redis services.
    # Only external app models, session injection and login are substituted.
    async def dependency():
        raise AssertionError('test dependency was not replaced')
    substitutes = {}
    for module, names in {
        'src.core.events.database': ['get_db_session'], 'src.security.auth': ['get_current_user'],
        'src.db.users': ['PublicUser'], 'src.db.courses.courses': ['Course'],
        'src.db.courses.chapters': ['Chapter','LockType'],
        'src.db.courses.activities': ['Activity','ActivityTypeEnum','ActivitySubTypeEnum','ActivityLockType'],
        'src.db.courses.assignments': ['Assignment','GradingTypeEnum'],
    }.items():
        stub = ModuleType(module)
        for name in names:
            setattr(stub, name, dependency if name.startswith('get_') else type(name, (), {}))
        substitutes[module] = stub
    # Keep production modules loaded for the duration of requests (including lazy imports).
    with patch.dict(sys.modules, substitutes):
        from src.education import business_routes as routes
        from src.education.security import Actor
        app = FastAPI()
        app.include_router(routes.router)
        engine = create_async_engine(uri.replace('postgresql://', 'postgresql+psycopg://'), poolclass=NullPool)
        async def session():
            async with AsyncSession(engine) as db:
                yield db
        app.dependency_overrides[routes.get_db_session] = session
        app.dependency_overrides[routes.require_actor] = lambda: Actor(
            actor_id, 2, 1, 'student', '', 'QA student', '', ())
        return TestClient(app)


def fixture_job(store, actor, task, sample, abstain=False):
    catalog, profile = sample.catalog, sample.profile
    concepts = tuple(catalog['concepts'])
    snapshot = Snapshot('2', catalog, concepts, 'test_fixture', profile=profile)
    principal = Principal(actor, '10', frozenset({'2'}), frozenset(concepts), frozenset({'recommend'}))
    job_id = task['task_id']
    job = JobContext(job_id, job_id, 0, task['version'])
    context = prepare_context(catalog, student_id='2', allowed_concept_ids=concepts, profile=profile)
    class Answer:
        def complete_validated(self, stage, prompt, validate):
            decision = {'decision': 'insufficient_evidence' if abstain else 'recommend',
                        'selected_candidate_id': None, 'concept_ids': [] if abstain else list(concepts),
                        'reason': '合成测试理由', 'relation_ids': [], 'uncertainties': [],
                        'evidence_ids': [] if abstain else ['synthetic-e1']}
            validate(decision)
            return decision
    def transport(argv, **kwargs):
        path = Path(argv[argv.index('--output') + 1])
        with RunStore(path, {'model':'offline-fixture'}, 1) as run:
            run_recommendation(run, Answer(), context)
        return subprocess.CompletedProcess(argv, 0)
    env_file = store.parent / 'offline.env'
    env_file.touch()
    adapter = ResearchAdapter(store, lambda *args: CurrentContext(0, job.expected_version, True),
                              fixture_transport=True, transport=transport, env_file=env_file)
    auth = PaidAuthorization('synthetic-auth', actor, job_id, 'recommend',
                adapter.input_fingerprint('recommend', principal, snapshot, job, 1), 1,
                datetime.now(timezone.utc) + timedelta(minutes=1))
    result = adapter.run('recommend', principal, snapshot, job, mode='execute', max_calls=1, authorization=auth)
    assert result.status in {'recommended','insufficient_evidence'}, result


def verify(db, client, private, actor, sample):
    base = '/courses/10/students/2/recommendations'
    def call(method, path, status=200, code=None, **kwargs):
        response = client.request(method, path, **kwargs)
        assert response.status_code == status, (path, response.status_code, response.text[:300])
        value = response.json()
        if code:
            assert value['detail']['code'] == code, value
        return value
    def register(profile_id):
        return call('POST', base+'/jobs', params={'profile_version_id': str(profile_id)},
                    headers={'Idempotency-Key': str(uuid4())})
    def upload(task, status=200, code=None):
        return call('POST', base+'/jobs/'+task['task_id']+'/import', status, code)
    def profile_version(previous=None):
        row = db.execute("""INSERT INTO ea.task_run(org_id,course_id,requested_by,operation,idempotency_key,input_hash)
            VALUES(1,10,%s,'profile_update',%s,repeat('a',64)) RETURNING id""", (actor,str(uuid4()))).fetchone()
        eid, = db.execute('SELECT id FROM ea.evidence_event').fetchone()
        return db.execute("""INSERT INTO ea.profile_version(org_id,course_id,student_id,version,previous_version_id,
            trigger_type,trigger_event_key,task_id,method_kind,capability_status,method_version,provenance,
            evidence_manifest,research_fields,input_hash) VALUES(1,10,2,%s,%s,'rebuild',%s,%s,'research_prototype',
            'unvalidated','qa','simulated',%s,%s,repeat('d',64)) RETURNING id""",
            (2 if previous else 1,previous,str(uuid4()),row[0],
             json.dumps([{'record_id':'synthetic-e1','evidence_id':str(eid)}]),
             json.dumps({'recommendation_input':{'status':'unvalidated','source':'fixture','method_version':'qa',
                   'profile_sha256':fingerprint(sample.profile)}}))).fetchone()[0]

    pid = profile_version()
    t = register(pid)
    fixture_job(private, actor, t, sample)
    call('POST', base.replace('/2/', '/1/')+'/jobs/'+t['task_id']+'/import', 403, 'SCOPE_DENIED')
    artifact = private / t['task_id'] / 'output/recommendation.json'
    original = artifact.read_bytes()
    artifact.write_bytes(original+b' ')
    upload(t, 409, 'ARTIFACT_HASH_MISMATCH')
    artifact.write_bytes(original)
    assert db.execute('SELECT count(*) FROM ea.recommendation_version').fetchone()[0] == 0
    print('PASS wrong subject and modified report rejected without partial writes')
    # Inject a failure after the version/items insert, before the task receipt.
    db.execute("""CREATE FUNCTION fail_qa_handoff() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic rollback check'; END $$;
        CREATE TRIGGER fail_qa_handoff BEFORE INSERT ON ea.outbox_event
        FOR EACH ROW WHEN (NEW.event_type='recommendation.imported.B.v1')
        EXECUTE FUNCTION fail_qa_handoff()""")
    upload(t,409,'BUSINESS_CONSTRAINT')
    assert db.execute('SELECT count(*) FROM ea.recommendation_version').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM ea.recommendation_item').fetchone()[0] == 0
    assert db.execute('SELECT status FROM ea.task_run WHERE id=%s',(t['task_id'],)).fetchone()[0]=='queued'
    db.execute('DROP TRIGGER fail_qa_handoff ON ea.outbox_event')
    print('PASS late transaction failure rolls back version, items and task status')
    with ThreadPoolExecutor(2) as pool:
        first, replay = pool.map(upload, [t,t])
    assert first == replay
    view = call('GET',base)
    assert view['id'] == first['recommendation_version_id'] and view['strategy'] == 'research_B'
    assert view['reason'] == '合成测试理由' and view['provenance'] == 'simulated' and view['evidence_refs']['synthetic-e1']
    link = view['items'][0]['open_api_path']
    assert call('GET',link)['question_content']['questions']
    snap, = db.execute("SELECT payload FROM ea.outbox_event WHERE event_type='recommendation.imported.B.v1'").fetchone()
    assert snap['resources'][0]['resource_version_id'] == view['items'][0]['resource_version_id']
    assert db.execute('SELECT count(*) FROM ea.recommendation_item').fetchone()[0] == 1
    print('PASS concurrent imports share one version, evidence item and downstream handoff; pinned exercise opens')
    db.execute('DELETE FROM public.usergroupuser WHERE user_id=2')
    call('GET',link,403,'SCOPE_DENIED')
    db.execute('INSERT INTO public.usergroupuser(usergroup_id,user_id,org_id) VALUES(11,2,1)')
    db.execute("""INSERT INTO ea.publication_event(org_id,course_id,usergroup_id,resource_version_id,policy_version_id,
        previous_event_id,event_no,action,actor_id,reason) SELECT org_id,course_id,usergroup_id,resource_version_id,
        policy_version_id,id,2,'withdraw',actor_id,'fixture' FROM ea.publication_event WHERE event_no=1""")
    empty = call('GET',base)
    assert empty['state']=='no_content' and empty['reason']==view['reason'] and empty['items']==[]
    call('GET',link,404,'EXERCISE_UNAVAILABLE')
    print('PASS membership revoked; withdrawn exercise removed while reason and history survive')
    t2 = register(pid)
    fixture_job(private,actor,t2,sample,abstain=True)
    upload(t2)
    abstained = call('GET',base)
    assert abstained['state']=='insufficient_evidence' and not abstained['items']
    call('GET',link,409,'STALE_RECOMMENDATION')
    waiting = register(pid)
    pid2 = profile_version(pid)
    upload(waiting,409,'STALE_PROFILE')
    call('GET',link,409,'STALE_RECOMMENDATION')
    assert call('GET',base)['strategy']=='none'
    cancelled = register(pid2)
    db.execute("UPDATE ea.task_run SET status='cancelled',cancelled_at=now(),execution_epoch=1 WHERE id=%s",
               (cancelled['task_id'],))
    upload(cancelled,409,'TASK_CANCELLED')
    fresh = register(pid2)
    fixture_job(private,actor,fresh,sample)
    upload(fresh)
    assert call('GET',base)['state'] == 'no_content'
    db.execute('UPDATE ea.note_permission SET revoked_at=now()')
    blocked = call('GET',base)
    assert blocked['state']=='stale' and not blocked['items'] and 'evidence_refs' not in blocked
    upload(fresh,409,'STALE_EVIDENCE')
    assert db.execute('SELECT count(*) FROM ea.recommendation_version').fetchone()[0] == 3
    print('PASS abstention, supersession, stale input, cancellation, empty mapping and consent revocation')


if __name__ == '__main__':
    sample = RecommendationInputTests()
    sample.setUp()
    sample.profile['student_id'] = '2'
    for row in sample.profile['evidence'] + sample.profile['observations']:
        row['student_id'] = '2'
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = root / 'private'
        os.environ['EA_RECOMMENDATION_STORE'] = str(store)
        server = pgserver.get_server(root/'postgres', cleanup_mode='stop')
        with server:
            uri = server.get_uri()
            with psycopg.connect(uri,autocommit=True) as db:
                design = DEMO / 'deliverables/02-design'
                stub = (design/'validation-upstream-stub.sql').read_text()
                db.execute('\n'.join(line for line in stub.splitlines() if not line.startswith('\\')))
                db.execute((design/'schema.sql').read_text())
                db.execute((TESTS/'recommendation_import/seed.sql').read_text())
                actor = str(db.execute('SELECT id FROM ea.principal WHERE user_id=2').fetchone()[0])
                with test_api(uri, actor) as client:
                    verify(db, client, store, actor, sample)
    print('OK 正式 B 导入与失效的隔离验收通过（合成模型回答与测试身份，临时 PostgreSQL）。')
