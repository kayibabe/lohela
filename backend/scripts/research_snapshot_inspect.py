"""Read-only helper for inspecting a production evidence snapshot."""
import json
from collections import Counter
from pathlib import Path

path = Path('/tmp/fresh_source.json')
data = json.loads(path.read_text())
predictions = data['tables']['predictions']
print('predictions=', len(predictions))
print('keys=', sorted(predictions[0].keys()) if predictions else [])
print('created_at_min=', min((p.get('created_at') for p in predictions), default=None))
print('created_at_max=', max((p.get('created_at') for p in predictions), default=None))
print('outcome_values=', sorted({str(p.get('outcome')) for p in predictions}))
print('prediction_sample=', predictions[0] if predictions else None)
print('markets=', Counter((p.get('market'), p.get('selection')) for p in predictions).most_common(30))
for name in ('accumulator_tickets', 'ticket_selections', 'ticket_results', 'matches'):
    rows = data['tables'].get(name, [])
    print(name, 'count=', len(rows), 'keys=', sorted(rows[0].keys()) if rows else [])
    if rows:
        print(name, 'sample=', rows[0])
