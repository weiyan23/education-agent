"""Start V2 API on loopback only. Requires initialized private PostgreSQL/Redis."""
import os
from demo_environment import ROOT, environment

if __name__=='__main__':
    env=environment()
    # The import endpoint verifies reports with the same research validators as the CLI.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(ROOT / "integration"), str(ROOT.parent.parent), env.get("PYTHONPATH"))))
    os.chdir(ROOT/'upstream/learnhouse/apps/api')
    python=ROOT/'.runtime/api-venv/bin/python'
    os.execve(str(python),[str(python),'-m','uvicorn','app:app','--host','127.0.0.1','--port','8138'],env)
