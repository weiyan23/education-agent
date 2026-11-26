"""Profile invalidation and explicit reconstruction upon grade revision or access revocation.

Ensures that when teacher revisions or permission revocations occur:
1. The historical profile is invalidated and never silently overwritten.
2. Stale or revoked inferences cannot remain active.
3. A clean baseline profile is reconstructed strictly from currently active evidence.
"""
from copy import deepcopy
from datetime import datetime, timezone
from recommendation.context import prepare_context
from .student_profile import StudentProfile, PROMPT_VERSION
from .evidence import is_evidence_active, invalidate_evidence, validate_timestamp, validate_effective_grade
from .events import validate_learning_event
from .run_store import atomic_json, fingerprint


def current_iso_timestamp():
    return datetime.now(timezone.utc).isoformat()


def invalidate_profile(base_profile, reason, operator_id, invalidated_at=None):
    """Mark a profile as explicitly invalidated, preserving its snapshot."""
    if not isinstance(base_profile, dict) or not base_profile.get('student_id'):
        raise ValueError('Invalid base profile')
    ts = invalidated_at or current_iso_timestamp()
    validate_timestamp(ts)

    invalidated = deepcopy(base_profile)
    invalidated['is_active'] = False
    invalidated['status'] = 'invalidated'
    invalidation_record = {
        'invalidation_id': f'inv_{fingerprint([base_profile["student_id"], ts, reason])[:16]}',
        'invalidated_at': ts,
        'operator_id': operator_id,
        'reason': reason,
        'base_sha256': fingerprint(base_profile)
    }
    invalidated['invalidation'] = invalidation_record
    revisions = deepcopy(invalidated.get('revisions', []))
    revisions.append({'type': 'invalidation', **invalidation_record})
    invalidated['revisions'] = revisions
    return invalidated


def apply_evidence_correction(evidence_list, correction_event, *, student_id=None, course_id=None):
    """Apply a scoped event to a current snapshot; inactive copies are audit only.

    This local helper validates binding, not the operator's authorization. An
    authenticated event adapter is required before exposing it over an API.
    """
    validate_learning_event(correction_event)
    if not isinstance(evidence_list, list):
        raise ValueError('Evidence must be a list')
    if not student_id or not course_id:
        raise ValueError('Correction requires an explicitly bound student and course')
    if (correction_event['student_id'] != student_id
            or correction_event['course_id'] != course_id):
        raise ValueError('Correction event belongs to another student or course')
    if any(not isinstance(e, dict) or e.get('student_id') != student_id
           or e.get('course_id', course_id) != course_id for e in evidence_list):
        raise ValueError('Evidence belongs to another student or course')
    event_type = correction_event.get('event_type')
    if event_type not in ('grade_revised', 'access_revoked', 'submission_voided'):
        raise ValueError('Unsupported correction event')
    payload = correction_event.get('payload', {})
    operator_id = correction_event.get('provenance', {}).get('operator_id')
    if not isinstance(operator_id, str) or not operator_id.strip():
        raise ValueError('Correction event requires an operator ID')
    reason = payload.get('reason', 'Correction applied')
    ts = correction_event['timestamp']

    target_sub_id = payload.get('submission_id')
    target_rec_id = payload.get('record_id')
    if event_type != 'access_revoked' and not (target_sub_id or target_rec_id):
        raise ValueError('Correction requires a submission or record ID')
    if event_type == 'grade_revised':
        validate_effective_grade(payload.get('revised_grade'))
        if payload['revised_grade']['status'] != 'effective':
            raise ValueError('A revised grade must be effective')

    updated_list = []
    affected_any = False

    for item in evidence_list:
        sub_info = item.get('submission', {})
        matched = event_type == 'access_revoked' or (
            (not target_rec_id or item.get('record_id') == target_rec_id)
            and (not target_sub_id or sub_info.get('submission_id') == target_sub_id))

        if matched:
            affected_any = True
            if event_type in ('grade_revised', 'access_revoked', 'submission_voided'):
                inv = invalidate_evidence(item, reason=reason, revoked_by=operator_id,
                                          revoked_at=ts, new_status='superseded' if event_type == 'grade_revised' else 'revoked')
                # If revised grade details are provided in the payload, create the successor evidence
                if event_type == 'grade_revised' and 'revised_grade' in payload:
                    revised_item = deepcopy(item)
                    revised_item['lifecycle_status'] = 'active'
                    revised_item['effective_grade'] = deepcopy(payload['revised_grade'])
                    revised_item['supersedes_record_id'] = item.get('record_id')
                    revised_item['supersedes_evidence_sha256'] = fingerprint(item)
                    revised_item['correction_event_id'] = correction_event['event_id']
                    revised_item.pop('invalidation', None)
                    updated_list.append(inv)
                    updated_list.append(revised_item)
                else:
                    updated_list.append(inv)
            else:
                updated_list.append(deepcopy(item))
        else:
            updated_list.append(deepcopy(item))

    if not affected_any and event_type != 'access_revoked':
        raise ValueError('Correction target is not in the current evidence snapshot')
    return updated_list, affected_any


def reconstruct_profile(store, client, base_profile, catalog, *, correction_event=None,
                        reason='explicit_reconstruction', operator_id='system'):
    """Reconstruct an active profile based on only valid, non-revoked learning evidence."""
    if not isinstance(base_profile, dict) or not base_profile.get('student_id'):
        raise ValueError('Base profile is required for reconstruction')

    # Validate before any model request or checkpoint mutation. Earlier inactive
    # snapshots cannot silently become active without an explicit restoration flow.
    if base_profile.get('status') in ('partial', 'failed') or base_profile.get('failed_steps'):
        raise ValueError('Incomplete base profile cannot be reconstructed as complete')
    if base_profile.get('access_status') == 'revoked':
        raise ValueError('Course access is revoked; explicit reauthorization is required')
    if correction_event:
        validate_learning_event(correction_event)
        for revision in base_profile.get('revisions', []):
            if revision.get('correction_event_id') == correction_event['event_id']:
                raise ValueError('Correction event already applied; use the saved result')
    all_evidence = deepcopy(base_profile.get('evidence', []))
    if correction_event:
        all_evidence, _ = apply_evidence_correction(
            all_evidence, correction_event, student_id=base_profile['student_id'],
            course_id=base_profile.get('course_id'))
        operator_id = correction_event['provenance']['operator_id']
    active_evidence = [r for r in all_evidence if is_evidence_active(r)]
    history = deepcopy(base_profile.get('evidence_history', []))
    history.extend(deepcopy(r) for r in all_evidence if not is_evidence_active(r))
    revoked = bool(correction_event and correction_event['event_type'] == 'access_revoked')
    # Reuse the original recommendation validator before paying for model work.
    candidate = {
        'student_id': base_profile['student_id'], 'status': 'completed',
        'concepts': deepcopy(catalog.get('concepts')), 'observations': [],
        'summaries': {}, 'evidence': active_evidence,
    }
    prepare_context(catalog, student_id=base_profile['student_id'],
                    allowed_concept_ids=list(catalog.get('concepts', {})), profile=candidate)

    # 1. Invalidate previous profile snapshot
    stale_profile = deepcopy(store.state.get('invalidated_profile')) if store is not None else None
    if stale_profile is not None:
        if stale_profile.get('invalidation', {}).get('base_sha256') != fingerprint(base_profile):
            raise ValueError('Saved invalidation does not match base profile')
    else:
        stale_profile = invalidate_profile(base_profile, reason=reason, operator_id=operator_id)
    if store is not None:
        store.state['invalidated_profile'] = deepcopy(stale_profile)
        store.state['status'] = 'reconstructing'
        atomic_json(store.path / 'invalidated-profile.json', stale_profile)
        store.commit('profile_invalidated', base_sha256=fingerprint(base_profile), reason=reason)

    builder = StudentProfile(base_profile['student_id'], catalog['concepts'], client.complete, model_id=client.model)
    reconstructed_profile = None

    # 4. Re-run analysis on active evidence using builder
    added, failed = [], []
    if store is not None:
        store.plan_steps({'reconstruct_extract:' + r['record_id']: {'record_id': r['record_id']} for r in active_evidence})

    def run_step(key, metadata, action):
        if store is not None:
            saved = store.start_step(key, metadata)
            if saved is not None:
                return saved
        try:
            res = action()
        except (ValueError, RuntimeError, OSError) as exc:
            if store is not None:
                store.fail_step(exc)
            failed.append(key)
            return None
        if store is not None:
            store.succeed_step(res)
        return res

    for r in active_evidence:
        step_key = 'reconstruct_extract:' + r['record_id']
        res = run_step(step_key, {'record_id': r['record_id'], 'model_id': builder.model_for('extract')},
                       lambda r=r: builder.insert([r]))
        if res is not None:
            added.extend(res)

    observations = added
    affected_concepts = sorted({o['concept_id'] for o in observations})

    summaries = {}
    if not failed and affected_concepts:
        if store is not None:
            store.plan_steps({'reconstruct_summarize:' + c: {'concept_id': c} for c in affected_concepts})
        for c in affected_concepts:
            step_key = 'reconstruct_summarize:' + c
            res = run_step(step_key, {'concept_id': c, 'model_id': builder.model_for('summarize')},
                           lambda c=c: builder.generate_desc_for_every_kc([o for o in observations if o['concept_id'] == c]))
            if res is not None:
                summaries.update(res)

    status = 'partial' if failed else 'completed'
    if revoked:
        status = 'invalidated'
    reconstruction_revision = {
        'type': 'reconstruction',
        'reason': reason,
        'operator_id': operator_id,
        'supersedes_profile_sha256': fingerprint(base_profile),
        'active_evidence_count': len(active_evidence),
        'total_evidence_count': len(all_evidence),
        'stage_models': {'extract': builder.model_for('extract'), 'summarize': builder.model_for('summarize')},
        'affected_concept_ids': affected_concepts
    }
    if correction_event:
        reconstruction_revision.update(
            correction_event_id=correction_event['event_id'],
            correction_event_sha256=correction_event['event_sha256'])

    if not failed:
        reconstructed_profile = {
            'student_id': base_profile['student_id'],
            'course_id': base_profile.get('course_id'),
            'model_id': client.model if builder.model_for('extract') == builder.model_for('summarize') else 'mixed',
            'stage_models': {'extract': builder.model_for('extract'), 'summarize': builder.model_for('summarize')},
            'prompt_version': PROMPT_VERSION,
            'concepts': deepcopy(catalog['concepts']),
            'observations': observations,
            'summaries': summaries,
            # Stable submission IDs belong only to the current revision here.
            # Superseded copies remain separately available for audit.
            'evidence': active_evidence,
            'evidence_history': history,
            'is_active': not revoked,
            'access_status': 'revoked' if revoked else base_profile.get('access_status', 'unverified'),
            'status': status,
            'revisions': deepcopy(stale_profile.get('revisions', [])) + [reconstruction_revision]
        }
        if not revoked:
            prepare_context(catalog, student_id=base_profile['student_id'],
                            allowed_concept_ids=list(catalog['concepts']), profile=reconstructed_profile)
        if store is not None:
            atomic_json(store.path / 'profile.json', reconstructed_profile)

    report = {
        'status': status,
        'failed_steps': failed,
        'revision': reconstruction_revision,
        'stale_profile': stale_profile,
        'profile': reconstructed_profile
    }

    if store is not None:
        atomic_json(store.path / 'reconstruction.json', report)
        store.state['status'] = status
        store.commit('reconstruction_finished', status=status)
        store.export()

    return report


def rebuild_profile(student_id, course_id, valid_evidences_only, catalog, client, store=None, reason='explicit_rebuild', operator_id='system'):
    """Explicit algorithm: purge invalid/revoked evidence and rebuild knowledge state strictly from valid evidence."""
    # Build pseudo base profile containing only valid evidence
    synthetic_base = {
        'student_id': student_id,
        'course_id': course_id,
        'model_id': getattr(client, 'model', 'default-model'),
        'prompt_version': PROMPT_VERSION,
        'concepts': deepcopy(catalog.get('concepts', {})),
        'observations': [],
        'summaries': {},
        'evidence': deepcopy(valid_evidences_only),
        'is_active': False,
        'revisions': []
    }
    return reconstruct_profile(store, client, synthetic_base, catalog, reason=reason, operator_id=operator_id)
