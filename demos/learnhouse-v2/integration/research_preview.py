"""Read-only synthetic sequence preview; never a producer of learning evidence."""
import json
from pathlib import Path

from profiling.sequence import compute_sequence_trajectories
from profiling.synthetic_sequence import generate_synthetic_sequences


def build_research_preview():
    repository = Path(__file__).resolve().parents[3]
    catalog = json.loads((repository / 'config/java-basics.json').read_text(encoding='utf-8'))
    titles = ('结果由错误转为正确', '连续错误记录', '评分更正记录')
    scenarios = []
    for index, sequence in enumerate(generate_synthetic_sequences(), start=1):
        if sequence.get('data_source') != 'synthetic_demo_data' or sequence.get('is_synthetic') is not True:
            raise ValueError('The preview accepts synthetic scenarios only')
        analysis = compute_sequence_trajectories(sequence, catalog)
        scenarios.append({
            'id': f'scenario-{index}',
            'title': titles[index - 1],
            'actions': sequence['actions'],
            'analysis': {
                'total_actions': analysis['total_actions'],
                'total_duration_seconds': analysis['total_duration_seconds'],
                'concept_trajectories': {
                    key: value for key, value in analysis['concept_trajectories'].items()
                    if value['events_count'] > 0
                },
            },
        })
    return {
        'release': 'V2.1',
        'data_source': 'synthetic_demo_data',
        'learning_evidence_eligible': False,
        'method_kind': 'rule_sequence_preview',
        'model_called': False,
        'scenarios': scenarios,
    }
