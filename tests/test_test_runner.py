import json
import subprocess
import sys
from pathlib import Path

import pytest

from neobot_cli import sdk


@pytest.mark.parametrize(
    ("source", "ok", "counter"),
    [
        ("def test_ok():\n    assert 2 + 2 == 4\n", True, "passed"),
        ("import pytest\n@pytest.mark.skip(reason='needs platform')\ndef test_skip(): pass\n", False, "skipped"),
        ("import pytest\n@pytest.mark.xfail\ndef test_expected(): assert False\n", False, "xfailed"),
        ("import pytest\n@pytest.mark.xfail\ndef test_unexpected(): pass\n", False, "xpassed"),
        ("def helper(): pass\n", False, None),
    ],
)
def test_acceptance_requires_real_passes(tmp_path, source, ok, counter):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_business.py").write_text(source)
    report = tmp_path / "report.json"
    runner = Path(sdk.__file__).with_name("_pytest_runner.py")
    result = subprocess.run([sys.executable, str(runner), str(tests), str(report)], capture_output=True, text=True)
    data = json.loads(report.read_text())
    assert data["ok"] is ok
    assert (result.returncode == 0) is ok
    if counter:
        assert data[counter] == 1
