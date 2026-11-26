"""Map a knowledge-target bundle to open, assigned exercises for the signed-in student."""
from datetime import datetime
from urllib.parse import urlencode

from src.education import business as b
from src.education.security import allowed_class_ids, fail, require_course


def _time(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))


def attempt_option(*, opens_at, due_at, max_attempts, attempts, grants, checked_at):
    """Choose a currently usable route, matching assert_attempt_window's rules.

    The database clock is supplied by the candidate query. A start request reuses
    an existing draft, so another grant must never make an unusable draft look
    resumable. Actual start/submission still enforce the database constraints.
    """
    now = _time(checked_at)
    normal_open = _time(opens_at) <= now <= _time(due_at)
    active = {str(g['id']): g for g in grants if g['revoked_at'] is None
              and _time(g['starts_at']) <= now < _time(g['expires_at'])}
    drafts = [a for a in attempts if a['status'] == 'draft']
    if drafts:
        if len(drafts) != 1:
            return None
        draft = drafts[0]
        grant_id = draft['resubmission_grant_id']
        grant = active.get(str(grant_id)) if grant_id is not None else None
        if ((grant_id is None and normal_open) or
                (grant is not None and draft['grant_slot'] is not None
                 and 1 <= draft['grant_slot'] <= grant['allowed_attempts'])):
            return {'kind': 'resume', 'attempt_id': str(draft['id']),
                    'resubmission_grant_id': str(grant_id) if grant_id is not None else None}
        return None
    normal_used = sum(a['resubmission_grant_id'] is None for a in attempts)
    if normal_open and normal_used < max_attempts:
        return {'kind': 'normal', 'attempt_id': None, 'resubmission_grant_id': None}
    # Start allocation uses max(grant_slot)+1, rather than the number of rows.
    for grant_id, grant in sorted(active.items(), key=lambda pair: (_time(pair[1]['expires_at']), pair[0])):
        used_slot = max((a['grant_slot'] or 0 for a in attempts
                         if str(a['resubmission_grant_id']) == grant_id), default=0)
        if used_slot < grant['allowed_attempts']:
            return {'kind': 'grant', 'attempt_id': None, 'resubmission_grant_id': grant_id}
    return None


async def resolve_exercises(db, actor, course_id, concept_ids, *, binding_id=None,
                            profile_version_id=None):
    if actor.role != 'student':
        fail('SCOPE_DENIED', 403)
    await require_course(db, actor, course_id, student_id=actor.user_id)
    if not concept_ids or any(not isinstance(code, str) or not code.strip() for code in concept_ids):
        fail('INVALID_KNOWLEDGE_POINTS', 422)
    current = await b.one(db, """
        SELECT id FROM ea.profile_version
        WHERE org_id=:org AND course_id=:course AND student_id=:student
        ORDER BY version DESC LIMIT 1
    """, org=actor.org_id, course=course_id, student=actor.user_id)
    # A lookup before the first profile is also a specific baseline.
    baseline = str(current['id']) if current else 'none'
    if binding_id is not None and profile_version_id is None:
        fail('PROFILE_VERSION_REQUIRED', 409, '请刷新推荐以获取带画像版本的入口')
    if profile_version_id is not None and profile_version_id != baseline:
        fail('STALE_PROFILE', 409, '画像版本已变化，请刷新推荐')
    targets = list(dict.fromkeys(concept_ids))
    classes = await allowed_class_ids(db, actor, course_id)
    items = await b.rows(db, """
        WITH targets AS (
            SELECT DISTINCT ON (code) id, code, version
            FROM ea.knowledge_point
            WHERE org_id=:org AND course_id=:course AND code=ANY(CAST(:targets AS text[]))
            ORDER BY code, version DESC
        )
        SELECT ab.id AS binding_id, ab.version AS binding_version, rec.id AS recipient_id,
               v.id AS resource_version_id, v.version AS resource_version, v.provenance,
               ab.publication_event_id, COALESCE(ab.rules_snapshot->>'title',r.title) AS title,
               (SELECT jsonb_object_agg(code,id) FROM targets) AS knowledge_point_ids,
               ab.opens_at AS _opens_at, ab.due_at AS _due_at,
               ab.max_attempts AS _max_attempts, clock_timestamp() AS _checked_at,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'id',a.id,'status',a.status,'resubmission_grant_id',a.resubmission_grant_id,
                   'grant_slot',a.grant_slot)) FROM ea.attempt a WHERE a.recipient_id=rec.id),
                   '[]'::jsonb) AS _attempts,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'id',g.id,'starts_at',g.starts_at,'expires_at',g.expires_at,
                   'revoked_at',g.revoked_at,'allowed_attempts',g.allowed_attempts))
                   FROM ea.resubmission_grant g WHERE g.recipient_id=rec.id),
                   '[]'::jsonb) AS _grants
        FROM ea.assignment_recipient rec
        JOIN ea.assignment_binding ab ON ab.id=rec.binding_id
        JOIN ea.resource_version v ON v.id=ab.resource_version_id
        JOIN ea.resource r ON r.id=v.resource_id
        JOIN ea.publication_event pub ON pub.id=ab.publication_event_id
        WHERE ab.org_id=:org AND ab.course_id=:course AND rec.student_id=:student
          AND ab.usergroup_id=ANY(CAST(:classes AS integer[]))
          AND (CAST(:binding AS uuid) IS NULL OR ab.id=CAST(:binding AS uuid))
          AND ab.status='assigned'
          AND r.kind='exercise' AND r.archived_at IS NULL AND v.processing_state='ready'
          AND pub.action='publish' AND NOT EXISTS (
              SELECT 1 FROM ea.publication_event newer
              WHERE newer.resource_version_id=v.id AND newer.usergroup_id=ab.usergroup_id
                AND newer.event_no>pub.event_no)
          AND (SELECT count(*) FROM targets)=:target_count
          AND NOT EXISTS (SELECT 1 FROM targets t WHERE NOT
              (COALESCE(v.content->'knowledge_point_ids','[]'::jsonb) @> jsonb_build_array(t.id)))
        ORDER BY ab.id
    """, org=actor.org_id, course=course_id, student=actor.user_id, classes=classes,
        targets=targets, target_count=len(targets), binding=binding_id)
    usable = []
    for item in items:
        option = attempt_option(**{key: item.pop('_' + key) for key in (
            'opens_at', 'due_at', 'max_attempts', 'attempts', 'grants', 'checked_at')})
        if option is None:
            continue
        item['start_option'] = option
        query = urlencode([('concept_id', code) for code in targets] + [
            ('resource_version_id', item['resource_version_id']),
            ('publication_event_id', item['publication_event_id']),
            ('profile_version_id', baseline),
        ])
        item['open_api_path'] = (
            f"/courses/{course_id}/recommendation-exercises/{item['binding_id']}?{query}"
        )
        usable.append(item)
    return b.clean({'state': 'ready' if usable else 'no_content',
                    'profile_version_id': baseline, 'concept_ids': targets, 'items': usable})
