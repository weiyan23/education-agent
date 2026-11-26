"""Local DTO helpers; structural validation is not authentication or source verification.

HTTP authorization, trusted event ingestion and database profile-version binding
are not supplied by this module. Callers must enforce those boundaries separately.
"""
from copy import deepcopy
from .evidence import is_evidence_active
from .events import validate_learning_event, filter_events_for_student
from .run_store import fingerprint


def _scope(profile, student_id, course_id=None):
    if not isinstance(profile, dict) or profile.get('student_id') != student_id:
        raise ValueError('Profile student_id does not match request')
    if course_id is not None and profile.get('course_id') != course_id:
        raise ValueError('Profile course_id does not match request')
    return profile.get('course_id')


def _ready(profile):
    return (profile.get('is_active', True) is True and profile.get('status') == 'completed'
            and not profile.get('failed_steps') and profile.get('access_status') != 'revoked')


def _validate_current_profile(profile, student_id):
    # This catalog supplies definitions only, for the existing evidence contract;
    # it is not a substitute for the real course/catalog at recommendation time.
    from recommendation.context import prepare_context
    concepts = profile.get('concepts')
    if not isinstance(concepts, dict):
        raise ValueError('Missing profile concept definitions')
    try:
        prepare_context({'version': 'dto-structure-only', 'concepts': concepts},
                        student_id=student_id, allowed_concept_ids=list(concepts), profile=profile)
    except KeyError as exc:
        raise ValueError(f'Incomplete profile evidence field: {exc.args[0]}') from None


def export_profile_for_recommendation(profile, student_id, course_id=None):
    """Export a complete local profile, without claiming DB-version authorization."""
    bound_course = _scope(profile, student_id, course_id)
    if not _ready(profile):
        return {
            'status': profile.get('status', 'unverified'),
            'is_eligible_for_recommendation': False,
            'reason': '画像未完成、已失效或课程访问已撤销，不能作为当前推荐依据',
            'student_id': student_id,
            'profile': None
        }
    _validate_current_profile(profile, student_id)
    # Preserve evidence and observations so the original prepare_context can
    # validate this export. The fingerprint is not a database profile_version_id.
    result = deepcopy(profile)
    result.pop('evidence_history', None)
    result.update({
        'status': 'completed',
        'is_eligible_for_recommendation': True,
        'course_id': bound_course,
        'profile_fingerprint': fingerprint(profile),
        'revisions_count': len(profile.get('revisions', [])),
        'active_evidence_count': len(profile['evidence']),
        'integrity_verified': False,
        'source_verification_status': 'unverified_local_input',
        'validation_scope': 'structure_and_evidence_binding_only',
    })
    return result


def export_evidence_for_evaluation(profile, events=None, student_id=None, course_id=None):
    """Export scoped citations; source authenticity remains explicitly unverified."""
    if not isinstance(profile, dict):
        raise ValueError('Profile must be a dictionary')
    target_sid = student_id or profile.get('student_id')
    bound_course = _scope(profile, target_sid, course_id)
    if not _ready(profile):
        raise ValueError('Evaluation requires a complete active profile')
    _validate_current_profile(profile, target_sid)
    if events is not None and not isinstance(events, list):
        raise ValueError('Events must be a list')
    for event in events or []:
        validate_learning_event(event)
        if (event['student_id'] != target_sid or not bound_course
                or event['course_id'] != bound_course):
            raise ValueError('Evaluation event belongs to another student or course')
        declared = [event.get('data_source'), event.get('origin'),
                    event['provenance'].get('data_source'), event['provenance'].get('origin')]
        if event.get('is_synthetic') or any(v in ('test_fixture', 'synthetic_demo_data') for v in declared):
            raise ValueError('Synthetic events cannot join a real profile export')

    active_evidences = []
    for e in profile.get('evidence', []):
        if is_evidence_active(e):
            active_evidences.append({
                'record_id': e.get('record_id'),
                'problem_id': e.get('problem_id'),
                'evidence_origin': e.get('evidence_origin'),
                'evidence_kind': e.get('evidence_kind', 'code_submission'),
                'effective_grade': deepcopy(e.get('effective_grade')),
                'source_hash': e.get('source', {}).get('sha256') if isinstance(e.get('source'), dict) else None,
                'received_at': e.get('submission', {}).get('received_at') if isinstance(e.get('submission'), dict) else None
            })

    return {
        'student_id': target_sid,
        'profile_version_count': len(profile.get('revisions', [])),
        'active_evidence_count': len(active_evidences),
        'active_evidences': active_evidences,
        'events_trace': filter_events_for_student(events or [], target_sid, bound_course),
        'integrity_verified': False,
        'source_verification_status': 'unverified_local_input',
        'validation_scope': 'structure_and_evidence_binding_only',
        'limitations': '已校验范围、结构与事件内容哈希；尚未核验权威来源或接入真实事件认证。'
    }


def get_learning_state_for_s08(profile, student_id, course_id):
    """Format data for Student Learning State Page (S08)."""
    if not isinstance(profile, dict) or profile.get('student_id') != student_id:
        return {'status': 'error', 'message': '未找到当前学生的有效画像'}
    _scope(profile, student_id, course_id)
    if not _ready(profile):
        return {'student_id': student_id, 'course_id': course_id, 'is_active': False,
                'profile_status': profile.get('status', 'unverified'), 'concept_cards': []}
    _validate_current_profile(profile, student_id)

    concepts_catalog = profile.get('concepts', {})
    summaries = profile.get('summaries', {})
    evidence_lookup = {e['record_id']: e for e in profile.get('evidence', []) if 'record_id' in e}

    concept_cards = []
    for cid, cname in concepts_catalog.items():
        summary = summaries.get(cid)
        if not summary:
            concept_cards.append({
                'concept_id': cid,
                'concept_name': cname,
                'status': 'no_observation',
                'status_label': '无观察记录',
                'evaluation': '本课程目前尚无该知识点的评测证据。',
                'evidence_drawer': []
            })
            continue

        counts = summary.get('counts', {})
        # Counts describe observations; they do not vote on overall mastery.
        status_tag = 'observed'
        label = '有观察记录'

        drawer_items = []
        for eid in summary.get('evidence_ids', []):
            ev = evidence_lookup.get(eid)
            if ev:
                drawer_items.append({
                    'record_id': eid,
                    'problem_id': ev.get('problem_id'),
                    'evidence_kind': ev.get('evidence_kind', 'code_submission'),
                    'received_at': ev.get('submission', {}).get('received_at'),
                    'grade_summary': ev.get('effective_grade', {}).get('score')
                })

        concept_cards.append({
            'concept_id': cid,
            'concept_name': cname,
            'status': status_tag,
            'status_label': label,
            'evaluation': summary.get('evaluation', ''),
            'counts': counts,
            'evidence_drawer': drawer_items
        })

    return {
        'student_id': student_id,
        'course_id': course_id,
        'is_active': profile.get('is_active', True),
        'profile_status': profile.get('status', 'completed'),
        'model_id': profile.get('model_id'),
        'concept_cards': concept_cards
    }


def get_class_learning_summary_for_t11(profiles_map, teacher_class_id, authorized_student_ids):
    """Format a roster already authorized by the caller; this is not an auth API."""
    roster_summaries = []
    for sid in authorized_student_ids:
        p = profiles_map.get(sid)
        if not p:
            roster_summaries.append({
                'student_id': sid,
                'class_id': teacher_class_id,
                'status': 'no_profile',
                'active_concepts_count': 0
            })
            continue
        _scope(p, sid)
        if not _ready(p):
            roster_summaries.append({'student_id': sid, 'class_id': teacher_class_id,
                                     'status': p.get('status', 'unverified'), 'is_active': False})
            continue
        _validate_current_profile(p, sid)

        summaries = p.get('summaries', {})
        roster_summaries.append({
            'student_id': sid,
            'class_id': teacher_class_id,
            'status': p.get('status', 'completed'),
            'is_active': p.get('is_active', True),
            'observed_concepts_count': len(summaries),
            'good_count': sum(1 for s in summaries.values() if s.get('counts', {}).get('Good', 0) > 0),
            'bad_count': sum(1 for s in summaries.values() if s.get('counts', {}).get('Bad', 0) > 0),
            'unknown_count': sum(1 for s in summaries.values() if s.get('counts', {}).get('Unknown', 0) > 0)
        })

    return {
        'class_id': teacher_class_id,
        'total_authorized_students': len(authorized_student_ids),
        'roster': roster_summaries
    }
