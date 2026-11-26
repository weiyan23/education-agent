"""Archive the committed V2.1 repository, excluding all untracked local runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if output.exists():
        parser.error('Output already exists; choose a new versioned filename')
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=repository).strip():
        parser.error('Commit the reviewed changes first; this packager archives HEAD only')
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repository).decode().split('\0')
    blocked = {'.runtime', 'node_modules', '.next', '__pycache__', '.venv', '.git', 'ee'}
    for name in filter(None, paths):
        path = Path(name)
        if (blocked.intersection(path.parts) or path.name.endswith('.local.json')
                or path.name == '.env' or (path.name.startswith('.env.') and not path.name.endswith(('example', 'sample', 'template')))):
            raise RuntimeError(f'Forbidden runtime/configuration path tracked: {name}')
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'archive', '--format=zip', '--prefix=education-agent-v2.1/',
                    '-o', str(output), sha], cwd=repository, check=True)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise RuntimeError('Archive integrity check failed')
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(f'{digest}  {output.name}\n')
    print(json.dumps({'file': str(output), 'commit': sha, 'sha256': digest,
                      'bytes': output.stat().st_size, 'scope': 'complete committed source; no dependencies or database'}))


if __name__ == '__main__':
    main()
