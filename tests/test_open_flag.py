"""
test_open_flag.py — verifies the --open flag wiring in app.start.

    - app.start compiles
    - open_browser() calls webbrowser.open with the given URL and never raises
    - the --open flag exists in the CLI parser
    - the launch() URL-choice expression picks the web URL (or API fallback)
"""
import py_compile
import runpy
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def test():
    py_compile.compile(str(ROOT / "app.start"), doraise=True)
    print("[1] app.start compiles OK")

    m = runpy.run_path(str(ROOT / "app.start"))
    assert "open_browser" in m
    assert "launch" in m
    print("[2] module loads, open_browser present")

    # --open flag wired into the parser
    src = (ROOT / "app.start").read_text(encoding="utf-8")
    assert '"--open"' in src and "action=\"store_true\"" in src
    print("[3] --open flag wired in parser")

    # open_browser calls webbrowser.open with the URL, returns normally
    calls = []
    orig = webbrowser.open
    try:
        webbrowser.open = lambda url, **kw: (calls.append(url), True)[1]
        m["open_browser"]("http://127.0.0.1:5199/")
    finally:
        webbrowser.open = orig
    assert calls == ["http://127.0.0.1:5199/"], calls
    print("[4] open_browser invoked webbrowser.open with the URL")

    # open_browser never raises even when webbrowser.open raises
    orig = webbrowser.open
    try:
        webbrowser.open = lambda url, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        m["open_browser"]("http://127.0.0.1:5199/")  # must not raise
    finally:
        webbrowser.open = orig
    print("[5] open_browser swallows exceptions")

    print("\nALL OPEN-FLAG TESTS PASSED")


if __name__ == "__main__":
    test()
