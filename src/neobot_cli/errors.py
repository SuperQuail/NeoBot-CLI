"""命令行和可视化入口共用的公开错误。"""

from __future__ import annotations


class CliError(Exception):
    def __init__(self, code: str, message: str, *, hint: str | None = None, details: object = None):
        super().__init__(message)
        self.code = code
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict:
        result = {"code": self.code, "message": str(self)}
        if self.hint:
            result["hint"] = self.hint
        if self.details is not None:
            result["details"] = self.details
        return result
