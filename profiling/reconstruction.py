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
from .evidence import is_evidence_active, invalidate_evidence, validate_timestamp
from .run_store import atomic_json, fingerprint, stamp


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


def apply_evidence_correction(evidence_list, correction_event):
    """Apply a correction or revocation event to an evidence collection."""
    if not isinstance(correction_event, dict):
        raise ValueError('Correction event must be a dict')
    event_type = correction_event.get('event_type')
    payload = correction_event.get('payload', {})
    operator_id = correction_event.get('provenance', {}).get('operator_id', 'system')
    reason = payload.get('reason', 'Correction applied')
    ts = correction_event.get('timestamp') or current_iso_timestamp()

    target_sub_id = payload.get('submission_id')
    target_rec_id = payload.get('record_id')

    updated_list = []
    affected_any = False

    for item in evidence_list:
        sub_info = item.get('submission', {})
        matched = False
        if target_rec_id and item.get('record_id') == target_rec_id:
            matched = True
        elif target_sub_id and sub_info.get('submission_id') == target_sub_id:
            matched = True

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
                    updated_list.append(inv)
                    updated_list.append(revised_item)
                else:
                    updated_list.append(inv)
            else:
                updated_list.append(deepcopy(item))
        else:
            updated_list.append(deepcopy(item))

    return updated_list, affected_any


def reconstruct_profile(store, client, base_profile, catalog, *, correction_event=None,
                        reason='explicit_reconstruction', operator_id='system'):
    """Reconstruct an active profile based on only valid, non-revoked learning evidence."""
    if not isinstance(base_profile, dict) or not base_profile.get('student_id'):
        raise ValueError('Base profile is required for reconstruction')

    # 1. Invalidate previous profile snapshot
    stale_profile = invalidate_profile(base_profile, reason=reason, operator_id=operator_id)
    if store is not None:
        store.state['status'] = 'reconstructing'
        store.commit('profile_invalidated', base_sha256=fingerprint(base_profile), reason=reason)

    # 2. Update evidence set according to correction event if provided
    all_evidence = deepcopy(base_profile.get('evidence', []))
    if correction_event:
        all_evidence, _ = apply_evidence_correction(all_evidence, correction_event)

    # 3. Filter down to strictly active evidence
    active_evidence = [r for r in all_evidence if is_evidence_active(r)]

    builder = StudentProfile(base_profile['student_id'], catalog['concepts'], client.complete, model_id=client.model)
    reconstructed_profile = None

    if not active_evidence:
        # Empty profile when all evidence is revoked or invalid
        reconstructed_profile = {
            'student_id': base_profile['student_id'],
            'model_id': client.model,
            'stage_models': {'extract': builder.model_for('extract'), 'summarize': builder.model_for('summarize')},
            'prompt_version': PROMPT_VERSION,
            'concepts': deepcopy(catalog['concepts']),
            'observations': [],
            'summaries': {},
            'evidence': all_evidence,
            'is_active': True,
            'status': 'completed',
            'revisions': deepcopy(stale_profile.get('revisions', [])) + [{
                'type': 'reconstruction',
                'reason': reason,
                'supersedes_profile_sha256': fingerprint(base_profile),
                'active_evidence_count': 0
            }]
        }
        if store is not None:
            atomic_json(store.path / 'profile.json', reconstructed_profile)
            store.state['status'] = 'completed'
            store.commit('profile_reconstructed', active_evidence_count=0)
            store.export()
        return {'status': 'completed', 'stale_profile': stale_profile, 'profile': reconstructed_profile}

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

    if not failed:
        reconstructed_profile = {
            'student_id': base_profile['student_id'],
            'model_id': client.model if builder.model_for('extract') == builder.model_for('summarize') else 'mixed',
            'stage_models': {'extract': builder.model_for('extract'), 'summarize': builder.model_for('summarize')},
            'prompt_version': PROMPT_VERSION,
            'concepts': deepcopy(catalog['concepts']),
            'observations': observations,
            'summaries': summaries,
            'evidence': all_evidence,
            'is_active': True,
            'status': status,
            'revisions': deepcopy(stale_profile.get('revisions', [])) + [reconstruction_revision]
        }
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

