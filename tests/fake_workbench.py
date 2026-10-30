from pathlib import Path
from types import SimpleNamespace
import threading


class FakeWorkbench:
    def __init__(self, root: Path, *, selected: bool = True) -> None:
        self.root = root
        self.project = SimpleNamespace(root=root) if selected else None
        self.calls: list[tuple] = []
        self.running = False
        self.stopping = False
        self.plugins: list[dict] = []
        self.start_entered = threading.Event()
        self.start_release: threading.Event | None = None
        self.verify_result = {"ok": True, "checks": ["business tests"]}

    def templates(self):
        return [
            {"id": "command", "title": "聊天命令", "description": "处理聊天命令"},
            {"id": "event", "title": "定时与事件", "description": "处理后台事件"},
            {"id": "page", "title": "带自定义页面", "description": "在浏览器打开插件页面"},
        ]

    def create_project(self, path):
        self.calls.append(("create_project", str(path)))
        self.project = SimpleNamespace(root=Path(path))
        return {"path": str(path)}

    def adopt_project(self, path):
        self.calls.append(("adopt_project", str(path)))
        self.project = SimpleNamespace(root=Path(path))
        return {"path": str(path)}

    def status(self):
        return {"running": self.running, "stopping": self.stopping, "plugins": self.plugins}

    def start(self):
        self.calls.append(("start",))
        self.start_entered.set()
        if self.start_release is not None:
            assert self.start_release.wait(5)
        self.running = True
        return self.status()

    def stop(self):
        self.calls.append(("stop",))
        self.running = False
        return self.status()

    def sync(self):
        self.calls.append(("sync",))
        return {"synced": True, "requirements": ["httpx>=0.27"]}

    def create_plugin(self, name, template):
        self.calls.append(("create_plugin", name, template))
        self.plugins.append({"name": name, "state": "unloaded", "development": False})
        return {"name": name, "template": template}

    def develop(self, name, enabled=True):
        self.calls.append(("develop", name, enabled))
        for plugin in self.plugins:
            if plugin["name"] == name:
                plugin.update(state="running", development=enabled)
        return self.status()

    def verify(self, name):
        self.calls.append(("verify", name))
        return self.verify_result

    def logs(self, lines=100):
        self.calls.append(("logs", lines))
        return "机器人正在运行"
