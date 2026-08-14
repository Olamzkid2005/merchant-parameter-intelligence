import sys, json
sys.path.insert(0, '.')
from merchant_intelligence.tasks import detect_task, execute_task

for q in [
    'get me the change of account details of just chips',
    'get the change of account details for WHITEVILL HOTEL',
]:
    print('QUERY:', repr(q))
    t = detect_task(q)
    if not t:
        print('  NOT A TASK')
        continue
    print('  intent:', t['intent'], '| names:', t['names'])
    res = execute_task(t)
    print('  rows:', len(res.get('rows', [])), '| not_found:', res.get('not_found'))
    for r in res.get('rows', [])[:3]:
        print('   -', r.get('merchant'), '| old:', r.get('Old Bank Acc No'),
              '| new:', r.get('New Bank Acc No'))
    print()
