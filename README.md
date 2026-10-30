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

仓库当前为私有，需要已配置的 GitHub 访问权限。源码开发时运行 `uv sync --locked`，再 `uv run neo`。

首次选择“创建工程”，填写一个新目录。工具准备实例的 Python 环境、配置与数据目录；随后启动机器人。
已使用 NeoBot 的用户选择“接入已有工程”，不会改写已有配置和数据。当前目录和子目录会识别所属实例，其他目录启动时使用最近接入的默认实例，界面始终显示实际路径。

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
