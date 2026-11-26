"""Bundle only reviewed source/artifacts. Exclude private runtime and dependencies."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
if (ROOT.parent.parent / 'profiling').is_dir():
    raise RuntimeError('V2.1 requires the complete repository. Use scripts/package_v21.py instead of the historical V2-only packager.')
UP=ROOT/'upstream/learnhouse'
OUT=ROOT/'教育智能体V2_本地Demo源码与交付包_2026-11-20.zip'
BLOCK={'.git','.runtime','node_modules','.next','__pycache__','.venv','ee'}

def eligible(p):
    return p.is_file() and not p.is_symlink() and not BLOCK.intersection(p.relative_to(ROOT).parts) and p.suffix not in {'.pyc','.zip'} and not p.name.endswith('.local.json') and p.name not in {'.env','package-validation.json'}

def git_list(*args):
    return [UP/n for n in subprocess.check_output(['git',*args,'-z'],cwd=UP).decode().split('\0') if n]

tracked=git_list('ls-files')
new=git_list('ls-files','--others','--exclude-standard')
new=[p for p in new if '/src/education/' in str(p) or '/components/education/' in str(p) or '/app/education/' in str(p)]
changed=git_list('diff','--name-only')+new
manifest={'upstream_repository':'https://github.com/learnhouse/learnhouse','upstream_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=UP).decode().strip(),
          'modified_files':[{'path':str(p.relative_to(UP)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(changed)) if eligible(p)],
          'enterprise_implementation_included':False,'runtime_credentials_included':False}
(ROOT/'SOURCE_CHANGES.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
files=set(p for p in tracked+new if eligible(p))
for directory in ['scripts','integration','deliverables','third_party']:
    files.update(p for p in (ROOT/directory).rglob('*') if eligible(p))
for name in ['README.md','RUN_LOCAL.md','THIRD_PARTY_NOTICES.md','.gitignore','requirements-demo.txt','SOURCE_CHANGES.json']:
    files.add(ROOT/name)
# Fail if any generated local password or signing secret appears in the source bundle.
private_values=[]
for name in ['demo-accounts.local.json','v2-server.local.json','foundation.local.json']:
    path=ROOT/'.runtime'/name
    if path.exists():
        data=json.loads(path.read_text())
        if name=='demo-accounts.local.json':private_values += [a['password'].encode() for a in data['accounts']]
        else:private_values += [v.encode() for v in data.values() if isinstance(v,str) and len(v)>15]
entries=[]
with zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(files):
        content=p.read_bytes()
        if any(secret in content for secret in private_values):raise RuntimeError('Private value found; packaging stopped')
        rel=str(p.relative_to(ROOT));z.write(p,'education-agent-v2-demo/'+rel)
        entries.append({'path':rel,'size':len(content),'sha256':hashlib.sha256(content).hexdigest()})
    z.writestr('education-agent-v2-demo/FILE_MANIFEST.json',json.dumps(entries,ensure_ascii=False,indent=2))
with zipfile.ZipFile(OUT) as z:
    if z.testzip():raise RuntimeError('Archive CRC validation failed')
sha=hashlib.sha256(OUT.read_bytes()).hexdigest()
OUT.with_suffix('.zip.sha256').write_text(sha+'  '+OUT.name+'\n')
result={'file':OUT.name,'files':len(entries)+1,'bytes':OUT.stat().st_size,'sha256':sha,'private_runtime_excluded':True,'generated_secret_scan_passed':True,'crc_passed':True}
(ROOT/'deliverables/05-demo-acceptance/package-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
