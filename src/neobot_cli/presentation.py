"""交互菜单中的简明操作结果；命令行机器报告仍由原入口输出。"""

from __future__ import annotations

import json
from typing import Any


def format_result(result: Any) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return str(result)
    if result.get("ok") is False or result.get("error"):
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if "stopping" in result and result["stopping"]:
        return "正在等待机器人完成清理。"
    if "plugins" in result:
        plugins = result["plugins"]
        developing = sum(bool(item.get("development")) for item in plugins)
        state = "已连接当前实例" if result.get("running", result.get("instance_id")) else "实例尚未运行"
        return f"{state} · {len(plugins)} 个插件 · {developing} 个开发中"
    if "artifact" in result:
        count = result.get("tests", {}).get("passed", 0)
        summary = f"检查通过 · {count} 项业务测试\n安装包：{result['artifact']}"
        if result.get("unverified"):
            summary += "\n未验证：" + "、".join(result["unverified"])
        return summary
    if result.get("synced"):
        return f"环境已同步 · {len(result.get('requirements', []))} 项插件依赖"
    if "template" in result and "name" in result:
        return f"已创建 {result['name']}\n{result.get('path', '')}\n从主菜单选择“开发插件”，开启后保存源码会自动重载。"
    if "root" in result or "path" in result:
        return f"实例目录：{result.get('root', result.get('path'))}"
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
