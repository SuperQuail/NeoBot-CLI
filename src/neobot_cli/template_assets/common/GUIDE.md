# 使用说明编写

`plugin.guide(...)` 只展示明确的合成样例，不读取真实配置，也不执行真实业务。每一步的 example 是独立快照。

- `config`：配置表单；`config.greeting`：回复内容字段。
- `action.<动作ID>`：已声明动作，示例结果放在 demo.actions。
- `view.<页面ID>.<锚点ID>`：已声明页面与锚点，页面负责 registerAnchor。

只引用本插件实际声明的目标。删除或改名后同步教程，再导出契约并检查。
