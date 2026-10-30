"""Page composition, separate from operation and instance state."""

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from .activity_log import ActivityLog

from textual.widgets import ContentSwitcher, Button, DataTable, Footer, Input, Label, Select, Static


def compose_workbench(templates: list[dict]) -> ComposeResult:
    with Horizontal(id="topbar"):
        yield Label("NeoBot", id="brand")
        yield Static("你的机器人工作台", id="tagline")
        yield Static("未接入实例", id="instance-state", markup=False)
    with VerticalScroll(id="workspace", classes="no-project"):
        with Vertical(id="project-section"):
            yield Label("接入你的 NeoBot", id="project-heading")
            yield Static("新目录或空目录可创建，已有工程请接入。", id="project-hint", markup=False)
            yield Input(value=str(Path.home() / "NeoBot"), placeholder="实例目录", id="project-path")
            with Horizontal(id="setup-feedback"):
                yield Static(id="project-status", markup=False)
                yield Button("查看详情", id="setup-details", classes="compact")
            with Horizontal(id="project-actions"):
                yield Button("创建工程", id="create-project", classes="compact primary")
                yield Button("接入已有工程", id="adopt-project", classes="compact")
        yield Static(id="instance-path", markup=False)
        with Horizontal(id="navigation"):
            yield Button("概览", id="nav-overview", classes="nav active")
            yield Button("插件", id="nav-plugins", classes="nav")
            yield Button("活动", id="nav-activity", classes="nav")
        with Horizontal(id="activity-strip"):
            with Vertical(id="activity-summary"):
                yield Static("就绪", id="operation-state", markup=False)
                yield Static("选择一个操作开始。", id="operation-detail", markup=False)
            yield Button("查看输出", id="open-activity", classes="compact")
        with ContentSwitcher(initial="overview-page", id="pages"):
            with VerticalScroll(id="overview-page", classes="page"):
                yield Label("你的 NeoBot", classes="page-title")
                yield Static("运行、配置与开发，共用一个实例。", classes="page-subtitle")
                with Horizontal(id="overview-cards"):
                    with Vertical(id="runtime-card", classes="card"):
                        yield Label("实例状态", classes="eyebrow")
                        yield Static("尚未运行", id="runtime-summary", markup=False)
                        yield Static("启动后即可连接平台与使用插件。", id="runtime-hint", markup=False)
                    with Vertical(id="plugins-card", classes="card"):
                        yield Label("本地插件", classes="eyebrow")
                        yield Static("0 个插件", id="overview-plugins", markup=False)
                        yield Button("管理插件 →", id="go-plugins", classes="compact")
                with Horizontal(id="instance-actions"):
                    yield Button("启动", id="start", classes="compact primary")
                    yield Button("停止", id="stop", classes="compact")
                    yield Button("同步环境", id="sync-environment", classes="compact")
                    yield Button("刷新", id="refresh", classes="compact")
                    yield Button("查看日志", id="show-logs", classes="compact")
                with Vertical(id="next-step", classes="card"):
                    yield Label("从使用到开发", classes="eyebrow")
                    yield Static(
                        "01  启动机器人，接入聊天平台。\n02  在插件页选择模板，写下第一个功能。\n03  开启开发，代码修改会在同一个实例热重载。",
                        markup=False,
                    )
                    yield Static("关闭工作台后，机器人仍会继续运行。", classes="muted")
            with Horizontal(id="plugins-page", classes="page"):
                with Vertical(id="plugin-section"):
                    yield Static("本地插件", id="plugin-summary", markup=False)
                    yield Static("还没有插件。\n选择模板，创建第一个。", id="plugin-empty", markup=False)
                    yield DataTable(id="plugin-table", cursor_type="row", show_header=False)
                with VerticalScroll(id="plugin-editor"):
                    yield Static("输入名称创建新插件，或从左侧选择。", id="plugin-context", markup=False)
                    with Vertical(id="plugin-form"):
                        with Vertical(classes="field", id="name-field"):
                            yield Label("插件名称")
                            yield Input(placeholder="例如 hello", id="plugin-name")
                        with Vertical(classes="field"):
                            yield Label("新建模板")
                            yield Select(
                                [(item["title"], item["id"]) for item in templates], allow_blank=False, id="template"
                            )
                    yield Static(id="template-description", markup=False)
                    with Horizontal(id="plugin-actions"):
                        yield Button("创建", id="create-plugin", classes="compact primary")
                        yield Button("开启开发", id="develop-plugin", classes="compact")
                        yield Button("检查", id="check-plugin", classes="compact")
                        yield Button("结束开发", id="end-development", classes="compact")
                    yield Static("插件保存在当前实例。开启开发前请先启动机器人。", id="plugin-tip", classes="muted")
            with Vertical(id="activity-page", classes="page"):
                yield Label("活动记录", classes="page-title")
                yield Static("输出实时更新 · 向上滚动可查看历史", classes="page-subtitle")
                with Vertical(id="result-pane"):
                    yield Static("操作结果和日志会显示在这里。", id="result-empty", markup=False)
                    yield ActivityLog(id="result-log", min_width=1, wrap=True, markup=False, max_lines=2000)
    yield Footer()
