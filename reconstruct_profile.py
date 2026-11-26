"""Explicitly reconstruct a student profile upon grade revision or access revocation; preview by default."""
import argparse
import hashlib
import json
from pathlib import Path
from profiling.api_client import CachedChatClient, read_env, send_chat, model_config
from profiling.run_store import RunStore, RecordedTransport, fingerprint, atomic_json
from profiling.student_profile import PROMPT_VERSION
from profiling.reconstruction import reconstruct_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-profile', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--correction-event', type=Path, help='Path to LearningEvent JSON')
    parser.add_argument('--revoke-record-id', type=str, help='Specific record_id to revoke')
    parser.add_argument('--reason', type=str, default='teacher_grade_revision')
    parser.add_argument('--operator-id', type=str, default='teacher_01')
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

    correction = None
    if args.correction_event:
        correction = json.loads(args.correction_event.read_text(encoding='utf-8-sig'))
    elif args.revoke_record_id:
        correction = {
            'event_type': 'grade_revised',
            'payload': {'record_id': args.revoke_record_id, 'reason': args.reason},
            'provenance': {'operator_id': args.operator_id}
        }

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
        'code': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    }

    with RunStore(args.output, identity, limit, resume=args.resume, secret=client.key) as store:
        client.transport = RecordedTransport(store, send_chat)
        client.event_sink = store.api_event
        atomic_json(args.output / 'base-profile-snapshot.json', base)
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


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
