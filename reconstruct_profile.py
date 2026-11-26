"""Explicitly reconstruct a student profile upon grade revision or access revocation; preview by default."""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.run_store import RunStore, RecordedTransport, fingerprint, atomic_json
from profiling.student_profile import PROMPT_VERSION
from profiling.reconstruction import reconstruct_profile
from profiling.events import create_learning_event, validate_learning_event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-profile', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--course-id', help='Explicit local scope for a legacy profile without course_id; not authorization')
    parser.add_argument('--correction-event', type=Path, help='Path to LearningEvent JSON')
    parser.add_argument('--revoke-record-id', type=str, help='Specific record_id to revoke')
    parser.add_argument('--reason', type=str, default='explicit_reconstruction')
    parser.add_argument('--operator-id', type=str, default='local-unverified')
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output', type=Path, default=Path('runs/profile-reconstruction-001'))
    parser.add_argument('--cache', type=Path, default=Path('cache/profile-reconstruction'))
    parser.add_argument('--max-calls', type=int, choices=range(1, 13), default=12)
    for flag in ('execute', 'resume', 'status', 'trace'):
        parser.add_argument('--' + flag, action='store_true')

    args = parser.parse_args()

    if args.status or args.trace:
        if args.execute or args.resume:
            parser.error('Inspection cannot be combined with execution')
        print(json.dumps(RunStore.inspect(args.output, full=args.trace), ensure_ascii=False, indent=2))
        return

    if not args.base_profile.is_file():
        parser.error(f'Base profile not found: {args.base_profile}')

    base = json.loads(args.base_profile.read_text(encoding='utf-8-sig'))
    catalog = json.loads(args.catalog.read_text(encoding='utf-8-sig'))
    if args.course_id:
        if base.get('course_id') not in (None, args.course_id):
            parser.error('Course differs from the saved profile')
        base['course_id'] = args.course_id
    if args.correction_event and args.revoke_record_id:
        parser.error('Choose correction-event or revoke-record-id, not both')

    correction = None
    if args.correction_event:
        correction = json.loads(args.correction_event.read_text(encoding='utf-8-sig'))
    elif args.revoke_record_id:
        if not base.get('course_id'):
            parser.error('A bound course_id is required; supply --course-id for a legacy local profile')
        payload = {'record_id': args.revoke_record_id, 'reason': args.reason}
        provenance = {'operator_id': args.operator_id, 'source_verification': 'local_unverified'}
        previous = None
        if args.resume:
            previous = json.loads((args.output / 'correction-event.json').read_text(encoding='utf-8'))
            validate_learning_event(previous)
        correction = create_learning_event(
            'submission_voided', base['student_id'], base['course_id'], payload, provenance,
            timestamp=previous['timestamp'] if previous else None,
            event_id='evt_local_' + fingerprint([base, payload, provenance]))
    if correction:
        validate_learning_event(correction)
        if (correction['student_id'] != base.get('student_id')
                or correction['course_id'] != base.get('course_id')):
            parser.error('Correction requires the same bound student and course as the base profile')

    if args.output.resolve() == args.base_profile.resolve().parent:
        parser.error('Use a separate output directory; preserve the base profile')

    preview_info = {
        'mode': 'execute' if args.execute else 'preview',
        'workflow': 'profile-reconstruction-v1',
        'student_id': base.get('student_id'),
        'reason': args.reason,
        'operator_id': args.operator_id,
        'correction_specified': bool(correction),
        'existing_evidence_count': len(base.get('evidence', [])),
        'request_limit_including_retries': args.max_calls
    }
    print(json.dumps(preview_info, ensure_ascii=False, indent=2), flush=True)

    if not args.execute:
        return

    settings = read_env(args.env_file)
    limit = min(args.max_calls, int(settings.get('ZZZ_MAX_CALLS', '12')))
    client = CachedChatClient(
        base_url=settings.get('ZZZ_BASE_URL', ''),
        api_key=settings.get('ZZZ_API_KEY', ''),
        **model_config(settings, profiling=True),
        cache_dir=args.cache,
        max_calls=limit,
        max_tokens=int(settings.get('ZZZ_MAX_TOKENS', '1500')),
        timeout=int(settings.get('ZZZ_TIMEOUT', '60'))
    )

    root = Path(__file__).resolve().parent
    files = [Path(__file__).resolve(), *sorted((root / 'profiling').glob('*.py'))]
    identity = {
        'workflow': 'profile-reconstruction-v1',
        'base_sha256': fingerprint(base),
        'catalog_sha256': fingerprint(catalog),
        'correction_sha256': fingerprint(correction) if correction else None,
        'prompt_version': PROMPT_VERSION,
        'model': client.model,
        'stage_models': client.stage_models,
        'url': client.url, 'max_tokens': client.max_tokens, 'timeout': client.timeout,
        'reason': args.reason, 'operator_id': args.operator_id,
        'code': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    }

    with RunStore(args.output, identity, limit, resume=args.resume, secret=client.key) as store:
        client.transport = RecordedTransport(store, send_chat)
        client.event_sink = store.api_event
        atomic_json(args.output / 'base-profile-snapshot.json', base)
        if correction:
            atomic_json(args.output / 'correction-event.json', correction)
        report = reconstruct_profile(
            store, client, base, catalog,
            correction_event=correction,
            reason=args.reason,
            operator_id=args.operator_id
        )
        print(json.dumps({
            'status': report['status'],
            'affected_concept_ids': report['revision'].get('affected_concept_ids', []),
            'failed_steps': report['failed_steps'],
            'active_evidence_count': report['revision'].get('active_evidence_count', 0)
        }, ensure_ascii=False, indent=2))
        if report['status'] == 'partial':
            raise RuntimeError('Reconstruction partial; inspect status/trace. No active replacement was published')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
