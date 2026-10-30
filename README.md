# NeoBot CLI

NeoBot 单实例开发工作台。正常终端运行 `neo` 打开 Textual；明确子命令直接执行，支持脚本和开发助手。

## 安装与第一次使用

需要 uv、Python 3.13 或 3.14 和正常终端。当前为源码开发版，尚未发布到 PyPI：

```sh
uv tool install git+https://github.com/SuperQuail/NeoBot-CLI.git
neo
```

也可临时运行：

```sh
uvx --from git+https://github.com/SuperQuail/NeoBot-CLI.git neo
```

源码开发时运行 `uv sync --locked`，再 `uv run neo`。

首页预填 `~/NeoBot`，可直接点击“创建工程”，也可改成其他新目录或已有空目录。清空路径后点击创建仍会使用显示的默认路径；非空目录不会被覆盖，已有实例请选择“接入已有工程”。工具准备实例的 Python 环境、配置与数据目录；随后启动机器人。

创建时会依次显示检查目录、写入工程、准备环境，并持续显示已用时间和最新输出。首次下载 Python、宿主与依赖可能需要几分钟；超过 15 秒没有新输出时会明确显示仍在等待进程返回，不虚构进度百分比。运行中可点“查看详情”或按 F8 查看实时输出，按 Esc 返回，操作会继续。简短错误固定显示在路径下方，完整错误放在详情中，首页不会因错误长度改变布局。

环境下载失败、超时或找不到 uv 时，已创建的工程会保留并接入工作台。检查网络或安装 uv 后点击“同步环境”重试，无需再次创建目录。
已使用 NeoBot 的用户选择“接入已有工程”，不会改写已有配置和数据。当前目录和子目录会识别所属实例，其他目录启动时使用最近接入的默认实例，界面始终显示实际路径。

工作台分成三个页面，共用顶部的实例信息和操作状态：

- **概览（F4）**：查看运行状态与插件数量，启动、停止、同步环境或读取机器人日志。
- **插件（F6 / F7）**：左侧选择本地插件，右侧选择模板、创建插件和开启开发。F6 聚焦列表，F7 聚焦表单。
- **活动（F8）**：查看操作步骤、实时输出和完整结果。向上滚动可阅读历史，回到底部后继续跟随新输出。

耗时操作期间可以切换页面和查看输出；会修改实例的按钮暂时禁用，避免重复执行。F5 刷新状态，Ctrl+Q 关闭控制台（有操作正在执行时需等待结束）。100×30 及以上终端里每页的主要操作可直接看到；窄窗口下插件列表和编辑区改为上下排列，页面内容可滚动。

插件列表同时读取本地目录与机器人状态：离线也能看到已有插件，新建后会立即刷新，状态更新会保留当前选中的插件。模板文件已生成但 SDK 检查失败时，文件也会保留并显示，方便继续修正。热重载失败的完整错误会进入活动输出。

首版宿主配套接口尚未正式发布，因此创建工程固定使用本次验收的 NeoBot Git 提交及全部同版本内部包，避免安装到没有控制接口的旧 PyPI 版本。需安装 Git，并可访问主仓库。正式宿主发版后再更新配方。

## 使用与开发同一个实例

控制台可以连接已经运行的机器人。启动按钮会先检查现有实例，宿主文件锁防止同一数据目录重复启动。
关闭 Textual 保留机器人运行；“停止机器人”走已认证的正常关闭流程。

```sh
neo project new ./my-neobot
neo start
neo plugin templates
neo plugin new hello --template command
neo plugin dev hello
```

创建工程后会记住该实例，上述命令可继续操作它；也可显式 `--project ./my-neobot`。

模板提供聊天命令、消息事件、自定义 HTML 页面三种，均包含测试、配置模型和说明书；页面模板无需 Node。
插件直接创建在当前实例配置的目录（新工程默认 `data/plugins/`），不创建第二个 Bot 或插件虚拟环境。
创建后先执行 SDK 导出和静态检查，再通过“开启开发”加载到当前进程。源码保存触发已有事务热重载；失败保留旧版本，修正后再次保存即可恢复。
旧版本宿主需要先升级到支持控制接口的版本并重启一次，不能直接接管旧进程。

### Python 依赖与验收

```sh
neo stop
neo sync
neo start
neo plugin export hello
neo plugin verify hello
```

`sync` 汇总本地插件清单的 Python 依赖，记录到实例 `pyproject.toml` 与 `uv.lock`，避免精确同步时丢包；运行中的实例会拒绝环境修改。
普通代码保存无需停止或同步。Python 版本、第三方依赖改变时应停止、同步后重启。

`verify` 执行静态检查、契约一致性、作者业务测试、两次确定性构建和最终 ZIP 安装生命周期冒烟。
零用例、跳过、xfail、xpass、失败或构建摘要不一致均不通过。报告会明确真实聊天平台与外部服务未验证。
验收产物在 `.neobot/artifacts/<插件名>/`；它不读取正在运行插件的真实用户数据。

## 命令与 AI 使用

```sh
neo --project ./my-neobot status --json --no-input
neo plugin new hello --template page --json --no-input
neo plugin dev hello --json --no-input
neo plugin dev hello --off --json
neo plugin verify hello --json --no-input
neo schema plugin.new --json
```

JSON 结果带 `schema_version/ok/data` 或 `error`，参数错误也使用同一结构。`schema` 从实际命令定义生成。
非交互环境无参数只显示帮助；`--json` 或 `--no-input` 缺命令直接报错。插件安装只预留内部 `PluginSource` 接口，不提供市场与下载。
控制凭据仅保存在实例 `data/.neobot/control.json`，客户端只访问回环地址、不使用系统代理、不跟随重定向。

## 与主仓库联合开发

```sh
neo --source /path/to/NeoBot project new ./my-neobot
```

源码路径会登记到该实例的 uv sources；其他实例不受影响。运行、导出和测试都使用实例锁定环境，不在控制台自己的工具环境导入宿主。

## 验证与平台

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv build
```

分发为通用 Python wheel / sdist。CI 覆盖 Windows、macOS、Linux × Python 3.13/3.14；完整 NeoBot 还受其自身依赖的平台支持限制。
