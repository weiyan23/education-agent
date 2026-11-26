"""Map a knowledge-target bundle to open, assigned exercises for the signed-in student."""
from urllib.parse import urlencode

from src.education import business as b
from src.education.security import allowed_class_ids, fail, require_course


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
               (SELECT jsonb_object_agg(code,id) FROM targets) AS knowledge_point_ids
        FROM ea.assignment_recipient rec
        JOIN ea.assignment_binding ab ON ab.id=rec.binding_id
        JOIN ea.resource_version v ON v.id=ab.resource_version_id
        JOIN ea.resource r ON r.id=v.resource_id
        JOIN ea.publication_event pub ON pub.id=ab.publication_event_id
        WHERE ab.org_id=:org AND ab.course_id=:course AND rec.student_id=:student
          AND ab.usergroup_id=ANY(CAST(:classes AS integer[]))
          AND (CAST(:binding AS uuid) IS NULL OR ab.id=CAST(:binding AS uuid))
          AND ab.status='assigned' AND ab.opens_at<=now() AND ab.due_at>now()
          AND r.kind='exercise' AND r.archived_at IS NULL AND v.processing_state='ready'
          AND pub.action='publish' AND NOT EXISTS (
              SELECT 1 FROM ea.publication_event newer
              WHERE newer.resource_version_id=v.id AND newer.usergroup_id=ab.usergroup_id
                AND newer.event_no>pub.event_no)
          AND (SELECT count(*) FROM targets)=:target_count
          AND NOT EXISTS (SELECT 1 FROM targets t WHERE NOT
              (COALESCE(v.content->'knowledge_point_ids','[]'::jsonb) @> jsonb_build_array(t.id)))
          AND (EXISTS (SELECT 1 FROM ea.attempt a WHERE a.recipient_id=rec.id AND a.status='draft')
               OR (SELECT count(*) FROM ea.attempt a WHERE a.recipient_id=rec.id)<ab.max_attempts)
        ORDER BY ab.id
    """, org=actor.org_id, course=course_id, student=actor.user_id, classes=classes,
        targets=targets, target_count=len(targets), binding=binding_id)
    for item in items:
        query = urlencode([('concept_id', code) for code in targets] + [
            ('resource_version_id', item['resource_version_id']),
            ('publication_event_id', item['publication_event_id']),
            ('profile_version_id', baseline),
        ])
        item['open_api_path'] = (
            f"/courses/{course_id}/recommendation-exercises/{item['binding_id']}?{query}"
        )
    return b.clean({'state': 'ready' if items else 'no_content',
                    'profile_version_id': baseline, 'concept_ids': targets, 'items': items})
