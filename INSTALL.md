# 给安装本包的 AI

**0.4.0 是 Pi、Claude Code、Codex 通用版。只更新用户当前指定的宿主。** 本版新增独立对话技能与文件风险高亮；升级后重启对应宿主或 MCP 进程并新开聊天，旧进程可能仍加载旧工具清单。

使用统一版原生安装入口。**本版没有 prepare/apply 安装器，不生成安装计划，不调用旧 Claude 0.2.0 的 backend/setup.py。** 默认保留当前模型、其他插件和回顾数据。机械复制、缓存和注册由宿主CLI完成。

## 在线安装

前提：已安装对应宿主、Git 和 Python 3.9+。Pi 还需 Node.js 22.19+。以下命令面向首次安装；已配置同名本地 marketplace 时，先按 INSTALL.md 更换来源。

Pi（固定 v0.4.0）：

```sh
pi install git:github.com/EasyHah/review-companion@v0.4.0
```

Codex（固定 v0.4.0）：

```sh
codex plugin marketplace add EasyHah/review-companion --ref v0.4.0
codex plugin add review-companion@review-companion-unified-local
```

Claude Code：

```sh
claude plugin marketplace add EasyHah/review-companion
claude plugin install review-companion@review-companion-unified-local
```

`review-companion-unified-local` 是兼容既有安装的 marketplace 标识，也用于在线来源。升级后重启对应宿主或 MCP 进程再使用。固定标签不会自动跟随新版本。

### 已有本地来源的切换

先用宿主的 marketplace list 确认当前来源。只有用户要求改用线上来源、且同名来源仍指向本地目录时，才替换这一项来源；其他插件和回顾记录不变。Codex 0.145.0 需要 `codex plugin marketplace remove review-companion-unified-local` 后重新执行上面的 add/install 两条命令。保留旧源码，失败时可重新添加原路径。不要把来源切换当作每次升级的固定步骤。

## 1. 一次预检

读取 README.md，确认用户使用 Pi、Claude Code 还是 Codex，以及终端或桌面客户端。将相关只读检查集中到一次工具调用：实际 CLI 的 --version/必要的 --help、Python3.9+、Git、可选 VS Code CLI，以及已有回顾安装和稳定数据目录。Pi另需Node22.19+及其搜索工具。不要使用安装命令探测依赖，也不要为了安装重跑整个开发测试集。

在解压包中执行只读发布检查：

```sh
python3 scripts/build_release.py --check
```

它只检查 release-files.json 中声明的文件、版本和可执行权限，不写计划、ZIP或配置。额外计划、日志和缓存不参与摘要。输出 source_digest 用于记录发布内容，不存在把它与新生成计划反复比较的 apply 步骤。文件缺失/符号链接/版本不一致会给出错误码和具体文件；保留原包，修正文件或重新解压可信发布包后再检查。

按用户目录偏好选稳定安装位置。若旧Pi/Claude默认记录仅一份，统一服务会原地复用；有多份时明确选目录，不合并。旧Codex自定义记录目录用 project_data_dirs 映射。不要复制他人配置或凭证，不要改全局模型和无关插件。

## 2. 一组原生命令安装或更新

只执行用户当前宿主对应的一组命令。下列 /stable/review-companion 必须替换为实际包根。已安装相同版本时先确认状态，跳过重复安装；升级使用当前CLI支持的更新命令。先保留旧源码与数据，切换时只停用旧回顾来源。

**Pi**：

```sh
pi install /stable/review-companion
```

首次加载可用 /reload；本次升级包含后端工具参数变更，请完整重启Pi进程后继续会话。本包只加载共用skill与Pi扩展，保留现有环境和当前模型。门神自动分流由用户在 /model 手动选择。

**agegr/pi-web** 使用同一 Pi package 和现有 Pi 配置，按 [docs/pi-web.md](docs/pi-web.md) 接入。0.3.5 验证了 Pi Web 0.11.0 / Pi 1.1.0。网页端采用复制问题，回顾命令显示链接；不创建另一份认证环境。多会话桥接改动需要完整重启 Pi Web，不能只刷新网页。

**Claude Code**：

```sh
claude plugin marketplace add /stable/review-companion
claude plugin install review-companion@review-companion-unified-local
```

已有来源用CLI支持的 marketplace update 和 plugin update，不反复删除重建。终端/桌面Code页的本地会话分别核实实际加载。Cowork是独立入口，尤其第三方API模式不能沿用CLI安装结果作为验收；本包未验证Cowork，不指导用户把本ZIP直接作为Cowork或MCPB安装包。根 .mcp.json 启动 bin/mcp-launch；自定义Python只在本机绑定 REVIEW_COMPANION_PYTHON 或显式解释器。

**Codex**：

```sh
codex plugin marketplace add /stable/review-companion
codex plugin add review-companion@review-companion-unified-local
```

当前验证Codex0.145.0使用 .codex-plugin/plugin.json 和 adapters/codex/mcp.json；保留兼容清单。Codex桌面也可使用包内 .agents/plugins/marketplace.json 或在现有个人来源仅追加此包。需要用户点击启用时给出具体步骤，不把CLI成功当作桌面已验收。自定义Python只改本机Codex MCP command，不改模型配置。

本机配置保持简单：共享配置 ~/.config/review-companion/config.json（或 REVIEW_COMPANION_CONFIG）只含 data_root、project_data_dirs、tools.git/editor。REVIEW_COMPANION_DATA_DIR 是精确记录目录。门神地址/模型/协议为团队预设；Claude/Codex沿用宿主模型，只有Pi可选分流需要其本机认证。由当前用户的AI处理这些差异，不创建通用配置或迁移系统。

## 3. 一次注册确认，然后区分验收层级

集中检查当前宿主插件列表、版本、启用状态及MCP连接。核对默认模型、其他插件和数据目录保持不变。新聊天由skill绑定实际Git仓库；Pi自动绑定，Claude/Codex通过 connect_session。

注册、启用和doctor通过只算安装检查。用合成仓库验证MCP读取、保存、再读取才能报告工具循环通过；使用用户界面确认复制/填入、手动发送和编辑器窗口，才能报告界面通过。外部模型测试仅使用用户授权目的地与合成内容，不把业务源码当安装样本。不必重复运行已发布包的所有单元/三宿主测试。

最后简短报告：安装版本与入口、稳定数据目录、配置保留情况、实际验证到哪一层、还有哪些用户操作。

## 故障处理与重试边界

- 每次先区分 **命令未获执行** 和 **命令已执行但失败**，保留原错误，不将两者统称为安装失败。
- Auto 分类器超时、服务不可用等明确的临时基础设施错误：原命令最多重试一次。仍失败就停止自动尝试，展示已审阅命令、目的和影响，指导用户按客户端支持的 Manual 方式人工审批；不要循环 sleep，也不要反复换主对话模型试运气。
- 明确权限拒绝、组织策略或 deny 规则：停止相应动作并解释原因。不改写/拆分命令规避审核，不自动切换权限模式，不使用 bypassPermissions。人工审批也必须遵守这些规则。
- 命令已执行：根据具体阶段处理。发布检查错误指出文件；配置/路径错误指出本机配置项；原生注册错误保留宿主的原始提示。确认是否已有成功写入后再决定重试，避免重复安装。
- 工具返回正在运行时，使用其原生等待/进度入口；不要另开多条Bash调用查询同一件事或设置任意睡眠轮询。正常流程保持预检、安装、注册确认三个阶段，异常才追加针对性检查。

Claude Auto 使用独立分类器，Manual 由用户逐项审批；更换主模型不等于修复分类器服务。以当前客户端支持的入口为准：[官方权限模式说明](https://code.claude.com/docs/en/permission-modes)。
