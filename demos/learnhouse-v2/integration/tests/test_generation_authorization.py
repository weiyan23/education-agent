"""Offline worker authorization regression; no app boot, .env, DB or model I/O.

Load the real security functions and complete generation handler by AST to avoid
LearnHouse's import-time model/config/database initialization. Only boundary I/O
is replaced: a mutable in-memory DB, queued callbacks, and a model response.
"""
import ast
import __future__
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

EDUCATION = Path(__file__).resolve().parents[2] / 'upstream/learnhouse/apps/api/src/education'


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail['code'])


class CandidateError(ValueError):
    pass


class DBAPIError(Exception):
    pass


def load_functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    selected = [node for node in tree.body if getattr(node, 'name', None) in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError('Test target function missing')
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)
    def mappings(self):
        return self
    def scalars(self):
        return self
    def first(self):
        return self.rows[0] if self.rows else None
    def all(self):
        return self.rows
    def scalar_one_or_none(self):
        return self.first()


class MemoryDB:
    def __init__(self):
        self.identity = dict(id='teacher-principal', org_id=1, user_id=11,
                             username='Synthetic teacher', email='synthetic@example.invalid',
                             is_superadmin=False, role_id=3, disabled_at=None)
        self.grants = [{'action': action, 'course_id': 7} for action in ('content.generate', 'content.read')]
        self.member = True
        self.student_active = True
        self.service_active = True
        self.policy_active = True
        self.status = None
        self.task_id = None
        self.resources = []
        self.queries = []
        self.identity_checks = 0

    async def execute(self, sql, params):
        sql = str(sql)
        self.queries.append((sql, params))
        if 'SELECT p.id,p.org_id' in sql:
            self.identity_checks += 1
            return Result([dict(self.identity)] if self.member else [])
        if 'SELECT * FROM ea.action_grant' in sql:
            return Result(self.grants)
        if "service_name='education-demo-worker'" in sql:
            return Result(['service-principal'] if self.service_active else [])
        if 'SELECT id,org_id,course_uuid FROM public.course' in sql:
            return Result([{'id': 7, 'org_id': 1, 'course_uuid': 'course-uuid'}])
        if 'SELECT g.id FROM ea.action_grant g' in sql:
            return Result([{'id': 1}] if any(g['action'] == params['a'] for g in self.grants) else [])
        if 'SELECT DISTINCT ug.id' in sql:
            return Result([])
        if 'SELECT m.id FROM public.usergroupuser m' in sql:
            return Result([{'id': 1}] if self.student_active else [])
        raise AssertionError('Unexpected query: ' + sql)

    async def rollback(self):
        pass

    async def commit(self):
        pass


class GenerationAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = MemoryDB()
        self.calls = 0
        self.on_model = lambda: None
        self.jobs = []
        self.env_file = Path('/synthetic/not-read.env')
        self.source = dict(id='source-v1', content_hash='source-hash', archived_at=None,
                           processing_state='ready', course_id=7, content={'body_text': 'Synthetic material'})
        security = dict(__name__=__name__, dataclass=dataclass, HTTPException=HTTPException,
                        text=lambda value: value, TEACHING_ACTIONS={'content.generate', 'content.read'},
                        ADMIN_ACTIONS={'access.manage'})
        load_functions(EDUCATION / 'security.py', ['Actor', 'fail', '_actor_from_row',
            'require_current_actor', '_course', 'allowed_class_ids', 'require_course'], security)
        self.security = security
        self.actor = security['Actor']('teacher-principal', 11, 1, 'teacher', 'service-principal',
                                       'Synthetic teacher', 'synthetic@example.invalid', tuple(self.db.grants))

        async def one(db, sql, **params):
            if 'SELECT code FROM ea.knowledge_point' in sql:
                return {'code': 'java.condition'}
            if 'SELECT status FROM ea.task_run' in sql:
                return {'status': db.status}
            raise AssertionError('Unexpected one query: ' + sql)

        async def execute(db, sql, **params):
            if 'INSERT INTO ea.task_run' in sql:
                db.task_id, db.status = params['id'], 'running'
            elif "SET status='failed'" in sql:
                if db.status == 'running':
                    db.status = 'failed'
            elif "SET status='succeeded'" in sql:
                db.status = 'succeeded'
            elif 'INSERT INTO ea.resource(' in sql:
                db.resources.append(params['id'])
            elif 'INSERT INTO ea.resource_version' not in sql:
                raise AssertionError('Unexpected write: ' + sql)

        async def resource_version(db, actor, version, action):
            await security['require_course'](db, actor, 7, action=action)
            return self.source

        async def write_once(db, actor, request, operation, data, course_id, reserve):
            return await reserve()

        async def current_policy(db, actor, course_id, policy_id):
            if not db.policy_active:
                security['fail']('POLICY_VERSION_CONFLICT', 409)

        async def read_task(task_id, request, actor, db):
            return {'id': str(task_id), 'status': db.status}

        async def content_valid(*args):
            pass

        async def sessions():
            yield self.db

        def model(*args, **kwargs):
            self.calls += 1
            self.on_model()
            version = SimpleNamespace(content={'title': 'Synthetic candidate'}, restricted={}, question_hash='question-hash')
            return SimpleNamespace(versions=(version,), provenance='simulated', artifact_hash='artifact-hash')

        async def offline_dispatch(fn, *args, **kwargs):
            # Exercise revocation at the model boundary without spawning threads.
            return fn(*args, **kwargs)

        candidate = SimpleNamespace(CandidateError=CandidateError, build_real_snapshot=lambda **kwargs: kwargs,
                                    run_real_candidate=model, run_fixture_candidate=model)
        business = SimpleNamespace(one=one, execute=execute, resource_version=resource_version,
            generation_env_file=lambda: self.env_file, write_once=write_once, digest=lambda value: 'input-hash',
            canonical=json.dumps, must=lambda value: value, matching=lambda *args: None, course_etag=lambda value: 'etag')
        self.namespace = dict(__name__=__name__, __file__=str(EDUCATION / 'business_routes.py'),
            asyncio=SimpleNamespace(to_thread=offline_dispatch), aclosing=aclosing, os=os,
            sys=sys, Path=Path, UUID=UUID, uuid4=uuid4,
            HTTPException=HTTPException, DBAPIError=DBAPIError, b=business,
            current_policy=current_policy, content_valid=content_valid, read_task=read_task,
            get_db_session=sessions, **{name: security[name] for name in
                ('require_current_actor', 'require_course', 'fail')})
        load_functions(EDUCATION / 'business_routes.py', ['generate_research'], self.namespace)
        for guard in (patch.dict(sys.modules, generation_candidate=candidate),
                      patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN')),
                      patch.object(subprocess, 'run', side_effect=AssertionError('SUBPROCESS_FORBIDDEN'))):
            guard.start()
            self.addCleanup(guard.stop)

    async def queue(self):
        data = dict(target_student_id=22, run_mode='execute', policy_version_id='policy-v1',
                    knowledge_point_ids=['knowledge-v1'], requirement='Synthetic exercise',
                    max_calls=4, budget_limit=1, max_attempts=1)
        request = SimpleNamespace(headers={'idempotency-key': 'synthetic-once'})
        await self.namespace['generate_research'](7, request, self.actor, self.db, data, {}, self.source,
            env_file=self.env_file, background_tasks=SimpleNamespace(add_task=self.jobs.append))
        self.assertEqual(self.calls, 0)
        self.assertEqual(len(self.jobs), 1)

    async def finish(self):
        await self.jobs[0]()

    async def test_active_teacher_dispatches_and_imports(self):
        await self.queue()
        await self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.db.status, 'succeeded')
        self.assertEqual(len(self.db.resources), 1)
        self.assertEqual(self.db.identity_checks, 2)

    async def test_disabled_after_queue_never_dispatches(self):
        await self.queue()
        self.db.identity['disabled_at'] = 'synthetic-disabled'
        await self.finish()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.db.status, 'failed')
        self.assertEqual(self.db.resources, [])

    async def test_disabled_during_model_does_not_import(self):
        await self.queue()
        self.on_model = lambda: self.db.identity.update(disabled_at='synthetic-disabled')
        await self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.db.status, 'failed')
        self.assertEqual(self.db.resources, [])

    async def test_membership_removed_after_queue_never_dispatches(self):
        await self.queue()
        self.db.member = False
        await self.finish()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.db.status, 'failed')

    async def test_role_changed_during_model_does_not_import(self):
        await self.queue()
        self.on_model = lambda: self.db.grants.append({'action': 'access.manage', 'course_id': None})
        await self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.db.status, 'failed')
        self.assertEqual(self.db.resources, [])

    async def test_generate_grant_revoked_during_model_does_not_import(self):
        await self.queue()
        self.on_model = lambda: self.db.grants.pop(0)
        await self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.db.status, 'failed')
        self.assertEqual(self.db.resources, [])

    async def test_source_read_revoked_after_queue_never_dispatches(self):
        await self.queue()
        self.db.grants.pop(1)
        await self.finish()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.db.status, 'failed')

    async def test_policy_changed_after_queue_never_dispatches(self):
        await self.queue()
        self.db.policy_active = False
        await self.finish()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.db.status, 'failed')

    async def test_cancel_during_model_stays_cancelled_without_import(self):
        await self.queue()
        self.on_model = lambda: setattr(self.db, 'status', 'cancelled')
        await self.finish()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.db.status, 'cancelled')
        self.assertEqual(self.db.resources, [])

    async def test_cancel_before_dispatch_never_calls_model(self):
        await self.queue()
        self.db.status = 'cancelled'
        await self.finish()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.db.status, 'cancelled')
        self.assertEqual(self.db.resources, [])


if __name__ == '__main__':
    unittest.main()
