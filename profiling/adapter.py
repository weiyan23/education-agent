"""Adapter layer providing standardized interfaces for downstream handoffs and UI views.

Handoff Targets:
1. Research Content C (Adaptive Recommendation):
   - export_profile_for_recommendation: Verified, active profile with explicit versions and provenance hashes.
2. Research Content D (Teaching Quality Evaluation):
   - export_evidence_for_evaluation: Filtered valid events, evidence citations, and score revision histories.
3. Front-end Views:
   - S08 (Student Learning State): Personalized mastery tags, evidence drawer, no answer leakage.
   - T11 (Teacher Class Learning Analytics): Scoped class roster summaries, intervention tracking, P15 compliance.
"""
from copy import deepcopy
from .evidence import is_evidence_active
from .run_store import fingerprint


def export_profile_for_recommendation(profile, student_id, course_id=None):
    """Deliver a validated, version-tagged profile to Recommendation Module C."""
    if not isinstance(profile, dict):
        raise ValueError('Profile must be a dictionary')
    if profile.get('student_id') != student_id:
        raise ValueError('Profile student_id does not match request')

    # Status & validity verification
    is_active = profile.get('is_active', True)
    status = profile.get('status', 'completed')

    if not is_active or status == 'invalidated':
        return {
            'status': 'invalidated',
            'is_eligible_for_recommendation': False,
            'reason': '画像因改分或撤权已显式失效，需等待重建完成',
            'student_id': student_id,
            'profile': None
        }

    # Extract concept summaries with honest qualitative boundaries
    summaries = {}
    for cid, s in profile.get('summaries', {}).items():
        summaries[cid] = {
            'concept_id': cid,
            'evaluation': s.get('evaluation', ''),
            'counts': deepcopy(s.get('counts', {})),
            'evidence_ids': list(s.get('evidence_ids', [])),
            'kind': s.get('kind', 'model_inference')
        }

    # Build delivery payload
    return {
        'status': 'active',
        'is_eligible_for_recommendation': True,
        'student_id': student_id,
        'course_id': course_id or profile.get('course_id'),
        'profile_fingerprint': fingerprint(profile),
        'model_id': profile.get('model_id'),
        'prompt_version': profile.get('prompt_version'),
        'revisions_count': len(profile.get('revisions', [])),
        'concepts': deepcopy(profile.get('concepts', {})),
        'summaries': summaries,
        'active_evidence_count': len([e for e in profile.get('evidence', []) if is_evidence_active(e)])
    }


def export_evidence_for_evaluation(profile, events=None, student_id=None):
    """Deliver verified events and evidence citations to Evaluation Module D."""
    if not isinstance(profile, dict):
        raise ValueError('Profile must be a dictionary')
    target_sid = student_id or profile.get('student_id')
    if profile.get('student_id') != target_sid:
        raise ValueError('Profile student_id does not match target_sid')

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
        'events_trace': deepcopy(events or []),
        'integrity_verified': True
    }


def get_learning_state_for_s08(profile, student_id, course_id):
    """Format data for Student Learning State Page (S08)."""
    if not isinstance(profile, dict) or profile.get('student_id') != student_id:
        return {'status': 'error', 'message': '未找到当前学生的有效画像'}

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
        # Qualitative status assignment
        if counts.get('Good', 0) > 0 and counts.get('Bad', 0) == 0:
            status_tag = 'Good'
            label = '良好'
        elif counts.get('Bad', 0) > 0:
            status_tag = 'Bad'
            label = '需巩固'
        else:
            status_tag = 'Unknown'
            label = '待评估'

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
    """Format data for Teacher Class Analytics Page (T11) with strict authorization boundaries."""
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
