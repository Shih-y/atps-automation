"""Source and frozen executable share the same release provenance contract."""
import json
import subprocess
import sys
from pathlib import Path
from functools import lru_cache

VERSION='2026.11.14'

@lru_cache(maxsize=1)
def version_info():
    info={'version':VERSION,'commit':'unknown'}
    path=Path(__file__).with_name('build_info.json')
    if getattr(sys,'frozen',False) and path.exists():
        info.update(json.loads(path.read_text(encoding='utf-8')))
    elif not getattr(sys,'frozen',False):
        try:
            info['commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=path.parent,
                stderr=subprocess.DEVNULL,text=True,timeout=3,creationflags=0x08000000 if sys.platform=='win32' else 0).strip()
            result=subprocess.run(['git','diff','--quiet','HEAD'],cwd=path.parent,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3,
                creationflags=0x08000000 if sys.platform=='win32' else 0)
            info['uncommitted_changes']=result.returncode!=0
        except (OSError,subprocess.SubprocessError):pass
    return info
