"""Shared eligibility checks for historical and received student evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import math
from .run_store import fingerprint

ALLOWED_EVIDENCE_KINDS = {'code_submission', 'boolean_choice', 'teacher_grade', 'tool_observation'}
ALLOWED_LIFECYCLE_STATUSES = {'active', 'revoked', 'superseded'}
ALLOWED_GRADE_STATUSES = {'effective', 'under_review', 'revoked'}


def validate_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must include a timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Invalid timestamp') from None
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')


def validate_effective_grade(grade):
    if not isinstance(grade, dict):
        raise ValueError('effective_grade must be a dictionary')
    for k in ('score', 'max_score', 'status', 'graded_by', 'graded_at'):
        if k not in grade:
            raise ValueError(f'effective_grade missing required field: {k}')
    if any(type(grade[k]) not in (int, float) or not math.isfinite(grade[k])
           for k in ('score', 'max_score')):
        raise ValueError('score and max_score must be numeric')
    if grade['score'] < 0 or grade['max_score'] <= 0 or grade['score'] > grade['max_score']:
        raise ValueError('score must be between 0 and max_score')
    if grade['status'] not in ALLOWED_GRADE_STATUSES:
        raise ValueError(f'Invalid grade status: {grade["status"]}')
    if not isinstance(grade['graded_by'], str) or not grade['graded_by'].strip():
        raise ValueError('graded_by must be a non-empty string')
    validate_timestamp(grade['graded_at'])


def validate_learning_evidence(row, student_id):
    if not isinstance(row, dict) or row.get('student_id') != student_id:
        raise ValueError('Evidence belongs to another student')
    origin = row.get('evidence_origin')

    # Optional multi-type and lifecycle validation
    kind = row.get('evidence_kind')
    if kind is not None and kind not in ALLOWED_EVIDENCE_KINDS:
        raise ValueError(f'Invalid evidence_kind: {kind}')

    lifecycle = row.get('lifecycle_status')
    if lifecycle is not None and lifecycle not in ALLOWED_LIFECYCLE_STATUSES:
        raise ValueError(f'Invalid lifecycle_status: {lifecycle}')

    grade = row.get('effective_grade')
    if grade is not None:
        validate_effective_grade(grade)
    if not is_evidence_active(row):
        raise ValueError('Inactive or unconfirmed evidence cannot enter a current profile')

    if row.get('split') == 'train':
        if origin not in (None, 'historical_student_submission') or 'submission' in row:
            raise ValueError('Synthetic or live evidence cannot be relabelled as training data')
        return
    if row.get('split') != 'live' or origin != 'student_submission':
        raise ValueError('Only training or declared real student submissions are learning evidence')
    meta = row.get('submission')
    required = ('submission_id','question_id','question_sha256','generation_run_id','received_at','program_sha256','question_text_sha256')
    if not isinstance(meta, dict) or any(not isinstance(meta.get(k),str) or not meta[k].strip() for k in required):
        raise ValueError('Live evidence requires a bound submission receipt')
    if type(meta.get('question_version')) is not int or meta['question_version'] < 1:
        raise ValueError('Invalid question version')
    validate_timestamp(meta['received_at'])
    if meta.get('submitted_at') is not None:
        validate_timestamp(meta['submitted_at'])
    expected_id = 'submission:' + fingerprint([student_id, meta['submission_id']])
    if (row.get('record_id') != expected_id or row.get('problem_id') != meta['question_id']
            or fingerprint(row.get('program')) != meta['program_sha256']
            or fingerprint(row.get('question')) != meta['question_text_sha256']):
        raise ValueError('Submission evidence binding changed')


def is_evidence_active(row):
    """Check if an evidence record is currently active (not revoked or superseded)."""
    if not isinstance(row, dict):
        return False
    if row.get('lifecycle_status') in ('revoked', 'superseded'):
        return False
    grade = row.get('effective_grade')
    if isinstance(grade, dict) and grade.get('status') != 'effective':
        return False
    return True


def invalidate_evidence(row, reason, revoked_by, revoked_at=None, new_status='revoked'):
    """Return an invalidated/revoked copy of the evidence record with reason and provenance."""
    if new_status not in ('revoked', 'superseded'):
        raise ValueError(f'new_status must be revoked or superseded, got {new_status}')
    ts = revoked_at or datetime.now(timezone.utc).isoformat()
    validate_timestamp(ts)
    updated = deepcopy(row)
    updated['lifecycle_status'] = new_status
    updated['invalidation'] = {
        'revoked_by': revoked_by,
        'revoked_at': ts,
        'reason': reason,
        'previous_status': row.get('lifecycle_status', 'active')
    }
    if 'effective_grade' in updated and isinstance(updated['effective_grade'], dict):
        updated['effective_grade']['status'] = 'revoked'
    return updated


def create_evidence_packet(student_id, problem_id, program, question, *,
                           submission_id=None, origin='student', evidence_kind='code_submission',
                           effective_grade=None, received_at=None, submitted_at=None, generation_run_id='manual_intake',
                           question_version=1):
    """Factory creating a standardized EvidencePacket compliant with live evidence requirements."""
    ts = received_at or datetime.now(timezone.utc).isoformat()
    validate_timestamp(ts)
    sub_id = submission_id or f'sub_{fingerprint([student_id, problem_id, ts])[:16]}'
    rec_id = 'submission:' + fingerprint([student_id, sub_id])

    meta = {
        'submission_id': sub_id,
        'question_id': problem_id,
        'question_sha256': fingerprint(question),
        'generation_run_id': generation_run_id,
        'received_at': ts,
        # Unknown student-side time remains unknown; do not copy the server receipt time.
        'submitted_at': submitted_at,
        'program_sha256': fingerprint(program),
        'question_text_sha256': fingerprint(question),
        'question_version': question_version
    }

    packet = {
        'record_id': rec_id,
        'student_id': student_id,
        'problem_id': problem_id,
        'question': question,
        'program': program,
        'split': 'live' if origin == 'student' else 'test_fixture',
        'evidence_origin': 'student_submission' if origin == 'student' else 'test_fixture',
        'evidence_kind': evidence_kind,
        'lifecycle_status': 'active',
        'source': {'file': rec_id, 'index': 0, 'sha256': fingerprint(program)},
        'submission': meta
    }
    if effective_grade:
        validate_effective_grade(effective_grade)
        packet['effective_grade'] = deepcopy(effective_grade)

    validate_learning_evidence(packet, student_id)
    return packet

