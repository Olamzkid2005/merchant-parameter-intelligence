"""Self-improvement feedback loop — request log, rephrase detection, pattern mining, apply/reject."""

import os
import sys
import tempfile
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Use temp files so the real data/ directory is never touched.
_fd, _FB_PATH = tempfile.mkstemp(suffix='.jsonl'); os.close(_fd)
_fd2, _REJ_PATH = tempfile.mkstemp(suffix='.json'); os.close(_fd2)
_fd3, _CAL_PATH = tempfile.mkstemp(suffix='.jsonl'); os.close(_fd3)
os.environ['MERCHANT_FEEDBACK_FILE'] = _FB_PATH
os.environ['MERCHANT_FEEDBACK_REJECTIONS_FILE'] = _REJ_PATH
os.environ['MERCHANT_CALIBRATION_FILE'] = _CAL_PATH

from merchant_intelligence import feedback, calibration
from merchant_intelligence.tasks import vocab, detect_task

PASS = 0
FAIL = 0


def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  PASS  {name}')
    else:
        FAIL += 1
        print(f'  FAIL  {name}  {extra}')


# ── 1. Request log basics ─────────────────────────────────────────────────
print('\n[1] Request log: append + read')
feedback.log_request(kind='task', text='get the static account for MX184380',
                     intent='static_account', rows=3, entity_sig=['MX184380'])
entries = feedback.load()
check('log has 1 entry', len(entries) == 1, repr(entries))
e = entries[0]
check('entry has id', e.get('id') == 1, repr(e.get('id')))
check('entry has kind', e.get('kind') == 'task', repr(e.get('kind')))
check('entry has intent', e.get('intent') == 'static_account', repr(e.get('intent')))
check('entry has rows', e.get('rows') == 3, repr(e.get('rows')))
check('entry has entity sig', e.get('entity_sig') == ['MX184380'], repr(e.get('entity_sig')))

# ── 2. Rephrase detection ─────────────────────────────────────────────────
print('\n[2] Rephrase detection')
# A previous task with 0 rows, same entity → tagged as rephrased
feedback.log_request(kind='task', text='please get the dealer settlement for MEDPLUS',
                     intent='static_account', rows=0, entity_sig=['MEDPLUS'])
check('2 entries', len(feedback.load()) == 2, None)
feedback.log_request(kind='task', text='get the static account of medplus',
                     intent='static_account', rows=5, entity_sig=['MEDPLUS'])
entries = feedback.load()
check('3 entries', len(entries) == 3, None)
# Entry 2 (the empty one) should be tagged rephrased
e2 = next(e for e in entries if e['id'] == 2)
check('prev tagged rephrased', e2.get('outcome') == 'rephrased', repr(e2.get('outcome')))
check('corrected_to = follow-up intent', e2.get('corrected_to') == 'static_account',
      repr(e2.get('corrected_to')))

# No rephrase if prev had rows > 0
feedback.log_request(kind='task', text='another query for LAGOON',
                     intent='email', rows=3, entity_sig=['LAGOON'])
feedback.log_request(kind='task', text='rephrase of lagoon',
                     intent='email', rows=2, entity_sig=['LAGOON'])
e4 = next(e for e in feedback.load() if e['id'] == 4)
check('prev with rows not tagged', e4.get('outcome') is None, repr(e4.get('outcome')))

# No rephrase without shared entity
feedback.log_request(kind='task', text='get the mx for SOMEONE',
                     intent='mxcode', rows=0, entity_sig=['SOMEONE'])
feedback.log_request(kind='task', text='other thing for DIFFERENT',
                     intent='email', rows=2, entity_sig=['DIFFERENT'])
e6 = next(e for e in feedback.load() if e['id'] == 6)
check('no shared entity -> not tagged', e6.get('outcome') is None, repr(e6.get('outcome')))

# ── 3. Pattern mining — 3-sample guard ────────────────────────────────────
print('\n[3] Pattern mining: 3-sample guard')
# Seed calibration override entries (source="override") with the correction
# intent. Each is 1 sample.
for phrase in [
    "please get the dealer settlement for MX184380",
    "i need the dealer settlement for this merchant",
    "show me the dealer settlement account",
]:
    calibration.record(text=phrase, predicted='profile',
                       confidence=50, chosen='static_account',
                       source='override', gap=3.0)
sugs = feedback.mine_patterns()
dealer = [s for s in sugs if 'dealer' in s['ngram'] and 'settlement' in s['ngram']]
check('dealer settlement suggested with 3 samples',
      len(dealer) >= 1 and dealer[0]['samples'] >= 3,
      repr(dealer))
check('dealer settlement intent = static_account',
      len(dealer) >= 1 and dealer[0]['intent'] == 'static_account',
      repr(dealer))

# 2 samples: should NOT be suggested
calibration.record(text='get the bank details for lagoons',
                   predicted='change_details', confidence=50,
                   chosen='profile', source='override', gap=2.0)
calibration.record(text='show me the bank details for lagoons',
                   predicted='change_details', confidence=50,
                   chosen='profile', source='override', gap=2.0)
sugs2 = feedback.mine_patterns()
bank_details = [s for s in sugs2
                if s['ngram'] == 'bank details' and s['intent'] == 'profile']
check('bank details (2 samples) not suggested',
      len(bank_details) == 0, repr(bank_details))

# ── 4. Coverage check ────────────────────────────────────────────────────
print('\n[4] Coverage check')
# 'static account' is already covered by static_account's keywords → skip
cfg = vocab.get_intent_config().get('intents') or {}
spec = cfg.get('static_account') or {}
import re
from merchant_intelligence.feedback import _covered
check('static account covered by keywords',
      _covered(['static', 'account'], spec),
      'keywords: ' + repr(spec.get('keywords')))
# 'dealer settlement' is NOT covered
check('dealer settlement NOT covered',
      not _covered(['dealer', 'settlement'], spec),
      'keywords: ' + repr(spec.get('keywords')))

# ── 5. Rejections ─────────────────────────────────────────────────────────
print('\n[5] Rejections')
feedback.reject('dealer settlement', 'static_account')
sugs3 = feedback.mine_patterns()
dealer2 = [s for s in sugs3
           if 'dealer' in s['ngram'] and 'settlement' in s['ngram']
           and s['intent'] == 'static_account']
check('dealer settlement not suggested after reject',
      len(dealer2) == 0, repr(dealer2))

# ── 6. Apply pattern ──────────────────────────────────────────────────────
print('\n[6] Apply pattern')
# Use a temp intents config with a known static_account spec
_cfg_path = tempfile.mkstemp(suffix='.json')[1]
_minimal = {
    'intents': {
        'static_account': {
            'patterns': [{'pattern': r'\\bstatic account\\b', 'weight': 8}],
            'keywords': ['static account', 'beneficiary'],
        }
    }
}
with open(_cfg_path, 'w', encoding='utf-8') as _fh:
    json.dump(_minimal, _fh, indent=2)
os.environ['MERCHANT_INTENTS_CONFIG'] = _cfg_path

# Force reload so the engine reads from the temp file
vocab.reload_intents()

spec = feedback.apply_pattern('dealer settlement', 'static_account', weight=5)
check('apply succeeded', spec is not None, repr(spec))
check('pattern added',
      any('dealer' in p.get('pattern', '') and 'settlement' in p.get('pattern', '')
          for p in (spec.get('patterns') or [])),
      repr(spec.get('patterns')))
check('keyword added',
      'dealer settlement' in (spec.get('keywords') or []),
      repr(spec.get('keywords')))

# Check hot-reload: the engine's compiled patterns now include it
compiled = dict(vocab.COMPILED_INTENT_PATTERNS)
sa_pats = [str(p.pattern) for p, _w in compiled.get('static_account', [])]
check('pattern hot-reloaded',
      any('dealer' in p and 'settlement' in p for p in sa_pats),
      repr(sa_pats))

# Apply again (idempotent): shouldn't duplicate
spec2 = feedback.apply_pattern('dealer settlement', 'static_account', weight=5)
check('re-apply does not duplicate pattern',
      sum(1 for p in (spec2 or {}).get('patterns') or []
          if 'dealer' in p.get('pattern', '') and 'settlement' in p.get('pattern', '')) == 1,
      repr(spec2 and spec2.get('patterns')))

# Clean up
del os.environ['MERCHANT_INTENTS_CONFIG']
vocab.reload_intents()

# ── 7. Outcome stats ──────────────────────────────────────────────────────
print('\n[7] Outcome stats')
report = feedback.report()
stats = report.get('stats', {})
check('stats has logged count', stats.get('logged', 0) >= 7, repr(stats))
check('stats has accepted', stats.get('accepted', 0) >= 3, repr(stats))
check('stats has rephrased', stats.get('rephrased', 0) >= 1, repr(stats))
check('stats has overridden from calibration',
      stats.get('overridden', 0) >= 3, repr(stats))

# ── 8. Entity signature ──────────────────────────────────────────────────
print('\n[8] Entity signature')
task = {'identifiers': {'tid': ['2103O338'], 'mxcode': ['MX184380']},
        'names': ['MEDPLUS']}
sig = feedback.entity_signature(task)
check('entity sig includes tid', '2103O338' in sig, repr(sig))
check('entity sig includes mxcode', 'MX184380' in sig, repr(sig))
check('entity sig includes name', 'MEDPLUS' in sig, repr(sig))

# ── Summary ────────────────────────────────────────────────────────────────
print(f'\n  RESULT: {PASS} passed, {FAIL} failed')
sys.exit(1 if FAIL else 0)