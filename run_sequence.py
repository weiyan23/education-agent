"""Process dynamic learning sequence files or generate synthetic demonstration sequences."""
import argparse
import json
from pathlib import Path
from profiling.sequence import validate_sequence_input, compute_sequence_trajectories
from profiling.synthetic_sequence import generate_synthetic_sequences
from profiling.run_store import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sequence-file', type=Path, help='Path to dynamic learning sequence input JSON')
    parser.add_argument('--catalog', type=Path, default=Path('config/java-basics.json'))
    parser.add_argument('--generate-synthetic', action='store_true', help='Generate and export the 3 synthetic demonstration archetypes')
    parser.add_argument('--synthetic-dir', type=Path, default=Path('runs/synthetic-sequences'), help='Target folder for synthetic sequences')
    parser.add_argument('--output', type=Path, default=Path('runs/sequence-analysis.json'))

    args = parser.parse_args()

    if args.generate_synthetic:
        args.synthetic_dir.mkdir(parents=True, exist_ok=True)
        seqs = generate_synthetic_sequences()
        for idx, s in enumerate(seqs, start=1):
            out_path = args.synthetic_dir / f'scenario_{idx}_{s["sequence_id"]}.json'
            atomic_json(out_path, s)
            print(f'Exported synthetic sequence {idx} to: {out_path}')
        return

    if not args.sequence_file or not args.sequence_file.is_file():
        parser.error('--sequence-file is required when not using --generate-synthetic')

    seq = json.loads(args.sequence_file.read_text(encoding='utf-8-sig'))
    catalog = json.loads(args.catalog.read_text(encoding='utf-8-sig'))

    validate_sequence_input(seq)
    result = compute_sequence_trajectories(seq, catalog)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output, result)

    print(json.dumps({
        'status': 'completed',
        'sequence_id': result['sequence_id'],
        'student_id': result['student_id'],
        'total_actions': result['total_actions'],
        'trajectories_count': len([t for t in result['concept_trajectories'].values() if t['current_state'] != 'no_observation']),
        'output_file': str(args.output)
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError) as exc:
        raise SystemExit(str(exc)) from None
