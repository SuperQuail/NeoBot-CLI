"""在实例解释器执行作者测试，记录不能冒充验收通过的跳过和预期失败。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


class Results:
    def __init__(self):
        self.counts = {name: 0 for name in ("passed", "failed", "skipped", "xfailed", "xpassed", "deselected")}

    def pytest_runtest_logreport(self, report):
        if hasattr(report, "wasxfail"):
            self.counts["xpassed" if report.passed else "xfailed"] += 1
        elif report.failed:
            self.counts["failed"] += 1
        elif report.skipped:
            self.counts["skipped"] += 1
        elif report.when == "call" and report.passed:
            self.counts["passed"] += 1

    def pytest_deselected(self, items):
        self.counts["deselected"] += len(items)


def main():
    tests, report_file = sys.argv[1:]
    results = Results()
    code = int(pytest.main([tests, "-q"], plugins=[results]))
    report = {"exit_code": code, **results.counts}
    report["ok"] = (
        code == 0
        and report["passed"] > 0
        and not any(report[key] for key in ("failed", "skipped", "xfailed", "xpassed", "deselected"))
    )
    Path(report_file).write_text(json.dumps(report), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
