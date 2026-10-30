# __PLUGIN_NAME__

这个插件直接在当前 NeoBot 实例中开发，配置和数据仍由该实例保存。普通 Python 插件和原生 HTML 页面不需要 Node。

## 开始开发

在控制台的插件列表中选择本插件，进入开发；机器人运行时，保存 Python 源码后会自动重载。导入或激活失败会显示原因，并尽可能保留上一版。HTML、JS、CSS 更新沿用现有面板页面刷新。

终端也可在实例目录或本插件目录执行：

```sh
neo plugin dev __PLUGIN_NAME__
neo plugin verify __PLUGIN_NAME__
```

`verify` 使用当前实例的环境检查和测试插件；不需要另外启动机器人。检查结果不代表真实 QQ 或外部服务已经联调通过。

源码与配置分开：`plugin.toml` 声明插件；用户配置保存在实例的 `plugins_data/__PLUGIN_NAME__/`。修改配置后查看面板通知，确认是否需要重载。

## 接着修改

- 业务入口：`__init__.py` 的 `plugin` 实例与处理器。
- 配置：Pydantic `Config`，提供中文标题、说明和约束。
- 测试：`tests/`，用合成端口执行真实 SDK 处理链。
- 使用说明：`GUIDE.md` 和 `plugin.guide(...)`。
- 开发约定：`AGENTS.md`。

修改声明后，在实例环境运行 SDK `export`，检查 `plugin.contract.json` 差异，再运行检查与测试。契约由工具生成，不手写。执行 Python 依赖变更前先同步实例依赖账本；热重载不负责安装依赖。

遇到错误先核对当前实例路径、插件运行状态和检查报告。已有目录不会被模板覆盖。不要把真实凭据、用户数据或整个实例打进插件包。
