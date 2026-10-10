# 统一包实施记录

目标：将Pi0.2.0-dev.3与Claude精简0.2.0共用核心，并增加Codex原生入口。保持现有模型/其他插件和原记录；不复活通用配置向导、旧门神批次管线或自动数据迁移。

源对照：store/file_policy完全相同；采用Claude新增的Git/编辑器路径选择、HTTP重复头校验和并发上限；保留Pi的草稿协议、可选路由和.ts重载修复。将Claude的固定证据分页纳入共享工具。

实现：backend/和assets/唯一，skills/唯一；adapters/pi注册Pi扩展，adapters/claude提供session hook，adapters/codex提供经实测的独立MCP配置。Claude/Codex共用host_mcp.py，Pi共用同一Service与browser_server。默认模型不变。

数据：schema_version=1不变。统一解析新项目目录、精确覆盖和显式项目映射；唯一旧默认目录原地复用，多候选拒绝隐式选择。跨宿主读写同一记录的集成测试已加入。

原生验证：Pi保留设置、跨会话和重载；Claude正常plugin自动加载和本机SSE工具往返；Codex原生注册、skill/MCP发现、建记录和HTTP。真实桌面交互仍归用户验收。旧安装/数据没有被本次构建覆盖。


0.3.1跟进：用户明确仅修统一版，不修改旧Claude0.2安装器。固定release-files.json与确定性ZIP，增加只读发布检查，安装文档采用预检/原生安装/注册确认三阶段。保留人工审批与策略限制，无sleep重试循环。
