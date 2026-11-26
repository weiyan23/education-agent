"""Persist trusted B decisions and resolve their frozen exercise set under live scopes."""
import os
import re
import sqlite3
from uuid import uuid4

from src.education import business as b
from src.education.recommendation_mapping import resolve_exercises
from src.education.security import fail, require_course

EVENT = 'recommendation.imported.B.v1'


async def current_profile(db, actor, course, student):
    if actor.role != 'student' or actor.user_id != student:
        fail('SCOPE_DENIED', 403)
    await require_course(db, actor, course, student_id=student)
    await b.execute(db, 'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',
                    key=f'profile:{actor.org_id}:{course}:{student}')
    return await b.one(db, """SELECT * FROM ea.profile_version
        WHERE org_id=:org AND course_id=:course AND student_id=:student
        ORDER BY version DESC LIMIT 1""", org=actor.org_id, course=course, student=student)


async def evidence_scope(db, profile):
    """Recheck every input evidence, including evidence omitted from the model's citations."""
    manifest = profile['evidence_manifest']
    ids = {entry['evidence_id'] for entry in manifest}
    records = {}
    for entry in manifest:
        record = entry.get('record_id', entry['evidence_id'])
        records.setdefault(record, set()).add(entry['evidence_id'])
    if not ids:
        return records, {}
    # The membership check already locked the student's active scope. Lock note
    # consents before inspecting them so a committed revocation cannot be imported.
    await b.execute(db, """SELECT p.id FROM ea.note_permission p JOIN ea.evidence_event e
        ON e.note_permission_id=p.id WHERE e.id=ANY(CAST(:ids AS uuid[]))
        ORDER BY p.id FOR SHARE OF p""", ids=list(ids))
    rows = await b.rows(db, """SELECT e.id,e.knowledge_point_id FROM ea.evidence_event e
        WHERE e.org_id=:org AND e.course_id=:course AND e.student_id=:student
          AND e.id=ANY(CAST(:ids AS uuid[]))
          AND NOT EXISTS (SELECT 1 FROM ea.grade_revision n WHERE n.supersedes_id=e.grade_revision_id)
          AND (e.note_permission_id IS NULL OR EXISTS (SELECT 1 FROM ea.note_permission p
            WHERE p.id=e.note_permission_id AND p.purpose='profile_use' AND p.revoked_at IS NULL
              AND p.starts_at<=now() AND (p.expires_at IS NULL OR p.expires_at>now())))""",
        org=profile['org_id'], course=profile['course_id'], student=profile['student_id'], ids=list(ids))
    return records, {str(e['id']): str(e['knowledge_point_id']) for e in rows}


async def register(db, actor, course, student, expected, request):
    profile = await current_profile(db, actor, course, student)
    if not profile or profile['method_kind'] != 'research_prototype':
        fail('RESEARCH_PROFILE_REQUIRED', 409)
    if str(profile['id']) != str(expected):
        fail('STALE_PROFILE', 409)
    link = profile['research_fields'].get('recommendation_input', {})
    digest = link.get('profile_sha256', '')
    if (profile['capability_status'] not in {'connected','unvalidated'}
            or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
        fail('PROFILE_ARTIFACT_REQUIRED', 409)
    refs = {'student_id': student, 'profile_version_id': str(expected),
            'version': profile['version'], 'profile_sha256': digest}
    async def change():
        task = str(uuid4())
        await b.execute(db, """INSERT INTO ea.task_run(id,org_id,course_id,requested_by,operation,
            idempotency_key,input_hash,input_refs,max_attempts) VALUES
            (:task,:org,:course,:actor,'recommend',:key,:hash,CAST(:refs AS jsonb),1)""",
            task=task, org=actor.org_id, course=course, actor=actor.principal_id,
            key=request.headers['idempotency-key'], hash=b.digest(refs), refs=b.canonical(refs))
        return {'task_id': task, **refs}
    return await b.write_once(db, actor, request, 'recommendation.register', refs, course, change)


async def live_matches(db, actor, course, concepts, profile_id, *, lock):
    if not concepts:
        return []
    result = await resolve_exercises(db, actor, course, concepts, profile_version_id=profile_id)
    matches = result['items']
    if matches and lock:
        # Publication, assignment and attempt writers lock these same rows.
        # Repeat the visibility query after waiting and only freeze locked bindings.
        candidates = [item['binding_id'] for item in matches]
        locked = await b.rows(db, """SELECT ab.id FROM ea.assignment_binding ab
            JOIN ea.resource_version v ON v.id=ab.resource_version_id
            JOIN ea.resource r ON r.id=v.resource_id
            JOIN ea.assignment_recipient rec ON rec.binding_id=ab.id AND rec.student_id=:student
            JOIN public.usergroupuser m ON m.usergroup_id=ab.usergroup_id AND m.user_id=:student
                AND m.org_id=ab.org_id
            JOIN public.usergroupresource g ON g.usergroup_id=m.usergroup_id AND g.org_id=m.org_id
            JOIN public.course c ON c.id=ab.course_id AND c.org_id=g.org_id AND c.course_uuid=g.resource_uuid
            WHERE ab.id=ANY(CAST(:ids AS uuid[])) ORDER BY ab.id
            FOR SHARE OF rec,ab,r,m,g""", ids=candidates, student=actor.user_id)
        locked_ids = {str(row['id']) for row in locked}
        checked = await resolve_exercises(db, actor, course, concepts, profile_version_id=profile_id)
        matches = [item for item in checked['items'] if item['binding_id'] in locked_ids]
    return matches


async def import_result(db, actor, course, student, task_id):
    profile = await current_profile(db, actor, course, student)
    task = b.must(await b.one(db, """SELECT * FROM ea.task_run WHERE id=:id AND org_id=:org
        AND course_id=:course AND requested_by=:actor AND operation='recommend' FOR UPDATE""",
        id=task_id, org=actor.org_id, course=course, actor=actor.principal_id))
    refs = task['input_refs']
    if refs.get('student_id') != student:
        fail('SCOPE_DENIED', 403)
    if not profile or str(profile['id']) != refs['profile_version_id']:
        fail('STALE_PROFILE', 409)
    if task['status'] == 'cancelled' or task['execution_epoch'] != 0:
        fail('TASK_CANCELLED', 409)
    records, live = await evidence_scope(db, profile)
    if len(live) != len({e['evidence_id'] for e in profile['evidence_manifest']}):
        fail('STALE_EVIDENCE', 409)
    if task['status'] == 'succeeded':
        return task['result_refs']  # Receipt only. Never replay previous access links.
    if task['status'] not in {'queued', 'running'}:
        fail('TASK_TERMINAL', 409)
    storage = os.environ.get('EA_RECOMMENDATION_STORE')
    if not storage:
        fail('ADAPTER_STORE_UNCONFIGURED', 503)
    from recommendation_artifact import read_result
    from research_adapter import AdapterError
    try:
        decision = read_result(storage, task, profile, actor.principal_id)
    except AdapterError as exc:
        fail(exc.code, 409)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
        fail('INVALID_RESEARCH_ARTIFACT', 409)
    citations = {}
    for rid in decision['research_evidence_ids']:
        if rid not in records:
            fail('UNMAPPED_EVIDENCE', 409)
        citations[rid] = sorted(records[rid])
    matches = await live_matches(db, actor, course, decision['concept_ids'], refs['profile_version_id'], lock=True)
    pins = [{k: item[k] for k in ('binding_id','binding_version','recipient_id',
            'resource_version_id','publication_event_id','knowledge_point_ids')} for item in matches]
    version = str(uuid4())
    receipt = {'recommendation_version_id': version, 'task_id': str(task_id)}
    snapshot = {**decision, **refs, **receipt, 'org_id': actor.org_id, 'course_id': course,
                'strategy': 'research_B', 'evidence_refs': citations, 'resources': pins}
    await b.execute(db, """INSERT INTO ea.recommendation_version
        (id,org_id,course_id,student_id,profile_version_id,method_version,task_id,provenance,state,reason)
        VALUES(:id,:org,:course,:student,:profile,:method,:task,:source,:state,:reason)""",
        id=version, org=actor.org_id, course=course, student=student, profile=profile['id'],
        method='research-B-v1:'+str(task_id), task=task_id, source=decision['provenance'],
        state='ready' if pins else 'no_content', reason=decision['reason'])
    # The existing item table requires one actual knowledge/evidence pair. Keep all pairs
    # and even evidence-free decisions in the immutable handoff without inventing UUIDs.
    rank = 0
    cited = sorted({eid for values in citations.values() for eid in values})
    for pin in pins:
        targets = set(pin['knowledge_point_ids'].values())
        for eid in cited:
            if live[eid] in targets:
                rank += 1
                await b.execute(db, """INSERT INTO ea.recommendation_item
                    (org_id,course_id,student_id,recommendation_version_id,rank,knowledge_point_id,
                     evidence_id,binding_id,recipient_id,reason)
                    VALUES(:org,:course,:student,:version,:rank,:kp,:evidence,:binding,:recipient,:reason)""",
                    org=actor.org_id, course=course, student=student, version=version, rank=rank,
                    kp=live[eid], evidence=eid, binding=pin['binding_id'], recipient=pin['recipient_id'],
                    reason=decision['reason'])
    await b.execute(db, """INSERT INTO ea.outbox_event(org_id,course_id,event_type,aggregate_type,
        aggregate_id,deduplication_key,payload) VALUES(:org,:course,:event,'recommendation_version',
        :version,:key,CAST(:snapshot AS jsonb))""", org=actor.org_id, course=course, event=EVENT,
        version=version, key=str(task_id), snapshot=b.canonical(snapshot))
    await b.execute(db, """UPDATE ea.task_run SET status='succeeded',completed_at=now(),updated_at=now(),
        lease_until=NULL,result_refs=CAST(:receipt AS jsonb) WHERE id=:id""",
        receipt=b.canonical(receipt), id=task_id)
    await db.commit()
    return receipt


async def read_decision(db, actor, course, student, version=None):
    profile = await current_profile(db, actor, course, student)
    decision = await b.one(db, """SELECT r.id,e.payload,t.id AS task_id,t.created_at
        FROM ea.recommendation_version r JOIN ea.task_run t ON t.id=r.task_id
        JOIN ea.outbox_event e ON e.aggregate_id=r.id AND e.org_id=r.org_id AND e.event_type=:event
        WHERE r.org_id=:org AND r.course_id=:course AND r.student_id=:student
          AND r.profile_version_id=:profile
        ORDER BY t.created_at DESC,t.id DESC LIMIT 1""", event=EVENT, org=actor.org_id,
        course=course, student=student, profile=profile['id'] if profile else None)
    if version is not None and (not decision or decision['id'] != version):
        fail('STALE_RECOMMENDATION', 409)
    if not decision:
        return None
    snap = decision['payload']
    view = {k: snap[k] for k in ('course_id','student_id','profile_version_id','strategy','provenance')}
    view.update(id=str(decision['id']), items=[], etag=b.etag(decision['id']))
    _, live = await evidence_scope(db, profile)
    if len(live) != len({e['evidence_id'] for e in profile['evidence_manifest']}):
        return {**view, 'state': 'stale', 'reason': '画像的有效证据已变化，请重建推荐。'}
    view.update(reason=snap['reason'], concept_ids=snap['concept_ids'], evidence_refs=snap['evidence_refs'],
                uncertainties=snap['uncertainties'], method_version=snap['prompt_version'],
                research_decision_id=snap['research_decision_id'])
    if snap['decision'] == 'insufficient_evidence':
        return {**view, 'state': 'insufficient_evidence'}
    if snap['resources']:
        visible = await live_matches(db, actor, course, snap['concept_ids'], snap['profile_version_id'],
                                     lock=version is not None)
        pins = {p['binding_id']: p for p in snap['resources']}
        for item in visible:
            pin = pins.get(item['binding_id'])
            if pin and all(item[key] == value for key,value in pin.items()):
                item['open_api_path'] = (f"/courses/{course}/students/{student}/recommendations/"
                                         f"{view['id']}/exercises/{item['binding_id']}")
                view['items'].append(item)
    return {**view, 'state': 'ready' if view['items'] else 'no_content'}
