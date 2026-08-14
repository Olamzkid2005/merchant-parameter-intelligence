import sys
sys.path.insert(0, ".")
from rebuild_db import _clean_pandas_leak, _row_has_content

cases = [
    # (input, expected)
    ("2ISW2587", "2ISW2587"),  # plain TID untouched
    ("MEDPLUS PHARMACY", "MEDPLUS PHARMACY"),  # plain name untouched
    ("507", "507"),  # owner code untouched (guard logic elsewhere)
    ("terminalId    2ISW2587\nterminalId    2ISW2587\nName: 263, dtype: str",
     "2ISW2587"),
    ("TERMINAL ID    2ISWQ791\nTERMINAL ID    2ISWQ791\nName: 4162, dtype: str",
     "2ISWQ791"),
    ("TERMINAL ID    \nTERMINAL ID    \nName: 3929, dtype: str", ""),  # no TID
    ("TERMINAL ID    ISW-3330052-G0V2B1\nTERMINAL ID                      \nName: 3931, dtype: str",
     "ISW-3330052-G0V2B1"),
    ("terminalId    20821927\nterminalId    20821927\nName: 629, dtype: str",
     "20821927"),
    ("MERCHANT NAME            ERIC KAYSER\nMERCHANT NAME    ERIC KAYSER\nName: 1545, dtype: str",
     "ERIC KAYSER"),
    ("MERCHANT NAME    \nMERCHANT NAME    \nName: 1549, dtype: str", ""),
    ("MERCHANT NAME     SARAH ONI\nMERCHANT NAME    DIOS DLITE\nName: 2552, dtype: str",
     "DIOS DLITE"),  # longest wins (10 > 9)
    ("NEW MERCHANT ACCOUNT NAME    MAGGII BEAUTY PALACE\nNEW MERCHANT ACCOUNT NAME                        \nName: 2528, dtype: str",
     "MAGGII BEAUTY PALACE"),
    ("NEW BANK ACC NO           232\nNEW BANK ACC NO    0067289285\nName: 1755, dtype: str",
     "0067289285"),  # longest wins
    ("NEW BANK ACC NO    \nNEW BANK ACC NO    \nName: 1678, dtype: str", ""),
    ("MERCHANT NAME     SARAH ONI\nMERCHANT NAME    DIOS DLITE", "DIOS DLITE"),  # no footer, longest wins
]

fails = 0
for inp, exp in cases:
    got = _clean_pandas_leak(inp)
    ok = got == exp
    if not ok:
        fails += 1
    print(("[PASS]" if ok else "[FAIL]"), repr(inp[:60]), "->", repr(got), "expected", repr(exp))

# _row_has_content sanity
print()
print("[PASS]" if _row_has_content({"tid": "2ISW2587", "merchant_name": ""}) else "[FAIL]",
      "row with tid has content")
print("[PASS]" if not _row_has_content({"sheet_name": "x", "row_number": 1}) else "[FAIL]",
      "sheet-only row has NO content")
print("[PASS]" if not _row_has_content({}) else "[FAIL]", "empty row has NO content")

print()
print("FAILS:", fails)
sys.exit(1 if fails else 0)
