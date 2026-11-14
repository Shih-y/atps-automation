"""Downgrade a test-only database to exercise packaged legacy migration."""
import json
import sqlite3
import sys
from pathlib import Path

data = Path(sys.argv[1]).resolve()
if 'test_output' not in data.parts:
    raise SystemExit('Only browser test_output data directories are allowed')
with sqlite3.connect(data / 'experiments' / 'experiments.sqlite3') as db:
    state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
    project = state['projects'][0]
    state.update(schema_version=1, running=True)
    state['samples'] = [dict(id='legacy-interrupted', project_id=project['id'], bottle=1,
                             status='preparing', target={}, recipe={}, frames=[],
                             mixed_at=None, label=None, exclusion='')]
    state['bottles'][0]['sample_id'] = 'legacy-interrupted'
    db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state),))
