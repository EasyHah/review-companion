# 回顾伴随 · 统一版

版本 **0.4.0**。源码与发行版：[EasyHah/review-companion](https://github.com/EasyHah/review-companion)。同一个安装包支持 **Pi、Claude Code、Codex**，共用一份回顾核心、浏览器界面、skill 和记录格式。默认沿用宿主当前模型，不替换其认证、模型配置或其他插件。

| 宿主 | 入口 | 浏览器问题按钮 |
| --- | --- | --- |
| Pi | 标准 Pi package / extension | 空闲且输入框为空时填入，由用户发送；否则复制兜底 |
| Pi Web（agegr 0.11.0） | 同一 Pi package / extension | 回顾面板复制问题，由用户粘贴发送 |
| Claude Code 终端 / 桌面 Code 页 | Claude plugin + 本地 MCP | 复制，由用户粘贴发送 |
| Codex CLI / 桌面 | Codex plugin + 本地 MCP | 复制，由用户粘贴发送 |

本包并没有把三套代码目录捆在一起：backend、assets、skills 只有一份；adapters 保存必要的宿主入口。Pi 的可选门神/JEV 路由仍在 Pi adapter 中，用户手动选择后才启用。Claude/Codex 的模型连接继续由其宿主或已安装的独立网关管理。

## 0.4.0：修正旧记录文件归属

旧风险只在其原功能组内、证据能够唯一定位到一个文件时自动映射。仅作其他功能上下文的文件不会被标亮；同组多文件归属不清或原功能不存在时不猜测。重命名证据按 base/target 区分。明确保存的 risk_notes（包括空数组清除）始终优先；旧记录不迁移、不删除，界面与操作流程不变。

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

### 跟踪 main（Pi / Codex）

希望以后通过更新命令获取最新代码，可以选择跟踪 `main`。固定标签用于保持指定发布版本；`main` 跟随分支最新提交，可能包含尚未正式发布的改动。跟踪分支不表示自动后台更新，仍需执行下面的更新命令。

Pi 首次安装，或从同一仓库的固定标签切换到 `main`：

```sh
pi install git:github.com/EasyHah/review-companion@main
```

Pi 会更新同一 Git 仓库的来源引用，无需先卸载；其他包的注册保持不变。

Codex 首次添加此来源：

```sh
codex plugin marketplace add EasyHah/review-companion --ref main
codex plugin add review-companion@review-companion-unified-local
```

如果 Codex 已添加同名来源，但仍固定在 `v0.4.0` 或指向本地目录，先替换这一项来源：

```sh
codex plugin marketplace remove review-companion-unified-local
codex plugin marketplace add EasyHah/review-companion --ref main
codex plugin add review-companion@review-companion-unified-local
```

来源替换只需做一次；以后更新不需要再次移除。回顾记录独立于插件安装目录保存，无需迁移或重置。

### 跟踪 main 后的日常更新

Pi，只更新回顾伴随：

```sh
pi update --extension git:github.com/EasyHah/review-companion@main
```

Codex，先刷新 marketplace，再更新已安装插件：

```sh
codex plugin marketplace upgrade review-companion-unified-local
codex plugin add review-companion@review-companion-unified-local
```

更新后重启 Pi；Codex 新开聊天，若仍加载旧版则重启客户端。配置、认证和回顾记录沿用原有位置。以上命令已在 Codex CLI 0.145.0 和 Pi 1.1.0 上验证。

终端需要能够访问 GitHub。如果网络依赖代理，请按自己的环境设置 `HTTPS_PROXY` 等变量；插件不会修改系统代理。

## 对话技能与轻量插件

插件恢复原有功能分组和逐文件回顾。文件有风险线索时，卡片高亮并可展开一两句说明；分组标题提示涉及多少份文件。没有风险总览页、路径推荐按钮或图形生成流程。风险标记独立于已回顾状态；未标记不代表安全。

- `branch-risks`：用户请求风险分析时，在对话中给出代码依据、条件、后果与待验证项，再通过 `set_file_risks` 把短说明同步到对应文件。
- `behavior-path`：用户请求理解核心行为时，直接在对话中画主流程并解释改动前后关系、必要原有模块和省略范围，不写入插件。
- `review-companion`：仍负责普通功能总览、固定版本和逐文件讲解；不默认启动上述分析。

用法：“使用分支风险分析，检查这个分支并高亮对应文件”；“用核心行为路径解释这次保存操作经过了哪些模块”。也可独立调用两个 skill，不要求先走插件流程。

保留文档排除：Markdown、reST、AsciiDoc、常见办公文档及 README/CHANGELOG/LICENSE 不进入代码回顾。旧记录保留；旧试用版的有效风险短说明可只读映射到文件，新文件标记优先，不迁移或删除数据。旧行为图不再展示。

三个宿主共用这两个对话技能和文件风险高亮能力；普通回顾流程保持原样。Pi package 与独立启动入口都加载三个 skill，Claude/Codex 原生插件自动发现它们。升级代码后重启对应宿主或 MCP 进程，使新工具和技能生效；不改变模型配置、其他插件或回顾数据。Codex 试用期间的风险总览和画图工具不进入通用接口。

## 日常使用

安装后照常启动 Pi、Claude 或 Codex，在目标项目发送：

> 使用回顾伴随，梳理当前开发分支相对主分支的功能变化，并打开回顾总览。

Pi 也可用 `/review`。Claude/Codex 会返回本机浏览器链接。展开功能查看文件职责，点击“请解释／下一份”，确认问题后自行发送。新聊天先恢复固定回顾，不自动解释。

“请解释”和“下一份”都一次覆盖行为变化、关键实现与影响范围。讲解完成即记为已回顾，两类短结论独立保存，在同一个折叠区域展示。单独追问影响仍可在对话里进行，不改动原结论和进度。保留简介和长短评折叠、提交说明与SHA悬停、资源增改过滤，以及固定提交源码/Diff导出后由显式点击打开 VS Code。

Claude 桌面端支持范围指 **Code 页的本地会话**。Cowork + 第三方 API 的插件入口、项目访问和浏览器连接尚未适配验收，CLI 安装不等于 Cowork 安装。

## 安装与升级

agegr/pi-web 的本机接入、命令和边界见 [Pi Web 说明](docs/pi-web.md)。沿用现有 Pi 配置；网页多会话分别建立连接，不共享进程级单例。

把解压目录和 [INSTALL.md](INSTALL.md) 交给使用者自己的 AI：

> 请按 INSTALL.md 安装回顾伴随统一版到我正在使用的宿主，保留已有模型、其他插件和回顾记录，完成后验证并报告。

本包包含三个原生入口和两个本地 marketplace；无需通用安装平台或配置向导。使用者的 AI 处理本机解释器、认证引用、编辑器位置和实际客户端差异。不要将别人的凭证或配置目录随包复制。

以前的 Pi、Claude 和 Codex 开发版源码、安装和数据未被此合并删除。切换到统一版时应停用该宿主的旧回顾来源，避免重复工具/skill；其他插件保持原样。安装与兼容测试使用隔离配置，不会强行重启用户正在运行的客户端。

## 共用记录

新记录默认位于 `~/.local/share/review-companion/projects/<实际Git根路径摘要>/`，三个宿主使用同一解析规则。不同 worktree 根仍隔离。

- `REVIEW_COMPANION_DATA_DIR` 表示精确记录目录，直接包含 reviews.json，不再追加哈希目录。
- 可在 `~/.config/review-companion/config.json` 用 project_data_dirs 映射仓库到已有记录目录；也可通过 REVIEW_COMPANION_CONFIG 指定此配置文件。
- 若未配置，新共享目录还没有记录，且旧 Pi/Claude 默认位置只有一个存在，直接原地复用，不复制数据。
- 若同项目两种旧位置均有记录，明确报出歧义，交由用户/其AI选择；不会自动合并或覆盖。
- 旧 Codex 开发版或自定义安装的数据位置需显式映射。可以映射其已有全局记录目录；服务只展示当前绑定项目的记录。

示例（路径由本机AI填写）：

```json
{
  "project_data_dirs": {
    "/actual/git/root": "/existing/directory/containing/reviews.json"
  }
}
```

配置可选字段还有 data_root（新项目根目录）与 tools.git / tools.editor。保持小范围，模型与凭证不进入该共享配置。升级仅更新代码，不迁移或重置记录。

## Pi 的可选门神模型

手动在 `/model` 选择“门神回顾自动分流”。服务地址、DeepSeek/JEV/GLM模型和协议沿用既定团队预设；认证仍取本机 MENSHEN_API_KEY、Pi /login 或显式凭证引用。已有 Pi 配置目录中的 review-companion.json 继续有效。

Pi 1.1.0 内置目录对应 DeepSeek 1M上下文/384K输出、GLM 1M/131072输出；已保留取消旧4K限制和 .ts 热重载修复。当前门神GLM读图未通过，含图上下文留给DeepSeek。模型价格元数据未配置，零费用显示不代表免费。

## 验证与边界

- Pi 1.1.0：配置/路由/桥接测试，实际 /new、/reload、模型限制及保留原有扩展与模型通过。
- Claude Code 2.1.201：原生插件加载、12个MCP工具、连接/创建回顾/保存风险的三次工具往返通过；推理用本机合成SSE服务，不是外部模型效果测试。
- Codex 0.145.0：本地原生安装、三个共享skill发现、12个MCP工具、固定回顾创建和浏览器HTTP连接通过；未发送模型回合。
- 共享核心：Pi创建 → Claude保存解释 → Codex保存影响 → Pi恢复；以及 Pi写风险 → Claude/Codex读取 → Codex更新 → Pi Web桥接读取 → Claude清除，进度不变。
- 浏览器逻辑：按宿主切换复制/填入能力，变更会话清除旧问题，版本冲突和隐私边界有自动化验证。

详细证据在 docs/。桌面窗口、真实剪贴板、输入框手感和VS Code窗口仍由用户操作验收。支持本地 macOS，Linux属于POSIX支持范围但未实机验收；原生Windows尚未适配。云会话无法直接连接用户电脑的localhost。部署不开放公网端口。

Codex当前本机版本需要 .codex-plugin 兼容清单；纯 portable plugin.json/mcp.json 在该版本未加载组件。包内两者均保留，适配代码已验证，不能仅凭最新网页规范宣称当前客户端全部支持。


## 0.3.1：安装流程与发布清单修正

本统一版继续使用三个宿主的原生安装入口，不包含旧Claude0.2.0的prepare/apply安装器。安装说明收敛为一次预检、一组原生命令、一次注册确认；仅对明确的权限服务临时故障原命令重试一次，持续失败交由用户人工审批，明确拒绝和组织策略不绕过。

release-files.json是固定发布文件清单。打包脚本只读取这些文件，额外计划、日志和缓存不会改变发布摘要或进入ZIP。新增/移除正式文件时维护该清单，不通过扫描工作目录自动扩大发布范围。

```sh
python3 scripts/build_release.py --check
python3 scripts/build_release.py
```

第一条只读，不写安装计划或配置；第二条生成可重复构建的ZIP和SHA-256。缺失文件、符号链接、版本不一致分别报告错误码与具体项；验证失败保留上一份ZIP。权限分类器故障属于宿主服务问题，本包不会修改它或承诺消除该类等待。

## 0.3.2：合并讲解入口

文件卡片只保留“请解释”这一讲解入口，“下一份”继续保留。一次讲清行为变化、关键实现和影响范围；影响先给重要结论，深度由后续对话决定。

“回顾结论”用同一个区域展示“改动”和“影响”。长内容统一折叠，展开隐藏预览；旧历史短评保留。后端一次原子更新保存两类结论与进度；单独追问影响仍只更新影响字段。现有记录格式无需迁移。

更新后请重新启动宿主或其MCP进程，并重新打开面板；只刷新网页无法加载新的后端工具参数。Pi本次建议退出后在原目录 `pi --continue` 恢复。已保存进度保留。

## 0.3.3：在项目中打开源码与 Diff

总览顶部新增“在 VS Code 打开项目”。源码、Diff 按钮也会在同一次打开请求中携带本次回顾的项目目录，让 VS Code 查找对应项目窗口；未打开的项目由 VS Code 按用户设置打开。不会强制复用其他项目的活动窗口。

项目树对应当前工作区；源码与 Diff 仍是固定提交的只读导出文件。两者可能不同，固定文件中的跳转与依赖解析不保证完整还原历史项目。找不到编辑器或启动失败时，面板提供包含项目路径的可复制命令。仅显式点击按钮发起打开请求，讲解和进度更新不自动打开编辑器。

本次自动化验证启动参数、项目范围、进度不变与界面状态；实际 VS Code 窗口由用户验收。更新后重新启动宿主或 MCP 进程，再打开面板。

## 0.3.4：限制回顾自动推进与明确校验错误

默认总览请求交付总览后结束，解释/下一份只处理当前文件后结束；用户明确要求批量时仍按其范围处理。读取关联文件不计为已向用户讲解。轻量总览允许未归类文件，复用已返回证据。保存参数超长时返回准确字段下标、实际长度和上限；错误/重复文件ID返回准确位置和值，不做模糊匹配或自动改写。

这些改动针对一次GLM-5.3会话中47次插件调用、23次文件进度写入和两次总览失败。错误诊断已用合成测试验证；技能停止行为仍需实际模型回合确认，不能据此承诺门神推理耗时。Claude桌面支持范围明确为Code页，Cowork尚未适配验收。

## 0.3.6：Pi Web 同页右侧回顾栏

提供 agegr/pi-web v0.11.0 的固定版本补丁与本机发布构建。顶部“回顾”打开原有右侧栏中的回顾视图，保留文件/终端切换、拖宽和收起。面板跟随当前会话，关闭时停止轮询，切换会话清除旧内容。

此功能需要配套侧栏版 Pi Web，不是只升级 Pi package 就能增加客户端 UI。安装与复现见 adapters/pi-web/README.md。只给明确的本机 Pi Web origin 开放 iframe 嵌入，其他宿主继续禁止嵌入，原令牌与项目检查保留。复制问题后仍由用户发送。
