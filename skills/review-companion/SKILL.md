---
name: review-companion
description: Explain committed branch changes in Chinese by feature and file, with a shared browser overview, fixed-version evidence, saved progress and independent explanation/impact conclusions. Use for branch recaps or resuming a recap, not approval-oriented code review.
---

# 回顾伴随

使用当前宿主模型讲解。浏览器保留功能总览、逐文件进度及短结论；对话承担深入追问。先说明行为变化，再解释关键实现。已回顾表示解释已经交付，不是正确性批准或理解测验。

## 可选的对话能力

默认仍是功能总览和逐文件讲解，不自动扫描风险、不推荐或生成行为图。
用户要求“找风险／检查疑点”时使用 [branch-risks](../branch-risks/SKILL.md)，详细分析在对话交付，插件只接收文件风险短说明。用户要求“这些改动如何串起来／画核心行为路径”时使用 [behavior-path](../behavior-path/SKILL.md)，图与解释直接放在对话中，不进入面板。

文档不作为回顾对象：Markdown、reST、AsciiDoc、常见办公文档、README/CHANGELOG/LICENSE 等不计入清单和进度，不默认检索它们作代码证据。保留代码、配置、MDX 和代码/文档互转的重命名；适用 AGENTS.md 等仓库规则仍须遵守。

插件文件上的风险线索独立于已回顾标记，不代表确定缺陷或全面扫描。普通讲解不自动清除风险线索。旧试用版中的风险总览和行为图不再作为独立界面或操作流程。

## 本轮交付边界

“回顾某分支／梳理改动／打开总览”默认交付功能总览、固定范围和面板链接，然后结束本轮，等待用户选择文件。不自动开始第一份、批量写结论或标记已回顾。用户明确要求从第一份开始时才讲第一份；明确要求批量回顾时按其指定范围处理。

“请解释／下一份／重新解释”默认只讲当前指定的一份文件，保存后结束本轮。为理解它读取关联文件，不代表那些文件已向用户讲解，不得顺手标记它们。内部阅读、分析或给面板写短评不能代替对话中的讲解。仅恢复聊天时也不自动推进。

## 建立和恢复范围

若有 `connect_session` 工具（Claude/Codex MCP），在每个新聊天第一次回顾时传入实际项目 `repository_path`；宿主提供 session_id 时一并传入，否则省略，让服务生成新绑定。不要使用插件安装目录代替业务仓库。Pi 扩展自动绑定会话，不需要这个工具。

恢复或收到面板问题时先 `read_review`，传入问题里的 review_id。核对仓库、固定 base_sha/target_sha 和 file_id；提交说明只是标签。只读恢复不改变回顾状态，不自动解释或发送消息。

用户指定分支时先核对已保存比较是否对应它；有可复用总览就直接恢复，不重建。分支已合入、当前分支指针已移动或比较为空时，说明历史比较依据；不能把不存在的远程 ref 当作有效目标。

新回顾使用 `start_review`，固定主分支、merge base 和开发分支已提交范围。不 fetch、不切分支、不把工作区未提交文件当成固定版本。主分支无法确定时再问。

用 `list_review_files` 分页读取，按 next_offset 继续。先看代表性的固定证据，轻量梳理功能分组、行为变化、实现概括与文件职责，用 `set_review_overview` 保存。不要仅凭文件名编造行为；不明确时说明。其他文件保留在未归类分组，不强制全量深入分析才能打开总览。

建立总览时，先复用 `start_review.first_files`，从它的 next_offset 继续文件清单；只读足以支持分组的代表证据，不逐份展开全部源码。已返回的同版本证据在本轮复用，只有缺失的分页或具体疑点才继续读取。`overview_ready=false` 表示仍有未归类文件，允许先展示，不是要求不断补齐的失败状态。

保存总览时 title 至多 100 字，behavior、implementation 各至多 240 字，responsibilities 每项至多 300 字；长解释留在对话。file_ids 从返回清单原样复制，不手工重写哈希。校验错误按返回的字段位置、长度或错误 ID 定点修正，不重读全分支或重写所有内容；同一次保存修正后仍失败，说明失败并展示已有总览，等待后续处理。未归类文件会自动保留，不必为了覆盖全部文件反复提交。

首次或用户明确要求时 `open_review`。Pi 交互模式可打开浏览器，Claude/Codex 返回 browser_url：把链接展示给用户；若宿主提供且允许打开浏览器的工具，可在这次用户请求内使用。不要为每份文件或保存结果重复打开。数据查询用 `read_review`。

## 讲解和记录

以功能为理解单位，文件为证据与覆盖单位。按入口、状态/数据流、关联实现、测试组织阅读。一次“请解释”（包括下一份）依次说明：职责与行为前后变化、关键实现和文件之间的关系、影响范围与兼容性及需要注意的地方。影响先给重要结论，证据不足时明确未验证；深入内容由后续对话承担。

优先 `read_fixed_evidence` 读取本次固定提交的源码或 Diff 两端，按 next_offset 分页；它不接受任意路径。必要时 `prepare_editor` 导出后读取返回路径。不得把当前工作区当作固定版本证据。源码和 Diff 窗口由用户点击面板明确打开。

讲解完成后重读最新 version，调用一次 `set_file_review(reviewed=true, conclusion=改动短结论, impact_conclusion=影响短结论)`，同时保存两部分和进度。两类结论各一两句，仍独立存储；面板放在同一个“回顾结论”区域中。省略任一字段时保留原值。不额外要求用户确认理解。

用户在对话中单独追问影响时，停留当前文件，用 `set_file_conclusion(conclusion_kind="impact")` 更新影响结论，保持改动结论和 reviewed 状态。面板不再提供单独的“问影响”按钮。追问不推进；“下一份”才使用 next_file。全部已回顾时如实说明，让用户选择重看。

保存失败明确说明，并在开始下一份时提醒用户手工确认上一份。version_conflict 时重新读取、核对后再更新，不能盲目重试或覆盖另一个会话的结果。

## 宿主交互与共享数据

Pi 终端 TUI 面板优先将问题填入空闲且为空的输入框；其他情况保留文本供复制。Pi Web/RPC、Claude/Codex 面板只复制问题。Pi Web 中 open_review 返回 browser_url 时展示可点击链接；/review 命令也会直接显示链接，不在服务端自动打开浏览器。始终由用户发送，不调用发消息接口替代用户，不覆盖已有输入。

三个宿主使用同一份记录与数据目录解析规则。新安装默认共享项目目录；唯一旧 Pi/Claude 默认记录可原地复用，多份候选会要求明确选择。指定 REVIEW_COMPANION_DATA_DIR 或用户配置映射时沿用它，不擅自搬迁、合并或删除记录。只绑定同一个实际 Git 根才视为同项目；不同 worktree 保持隔离。

Pi 的门神自动分流只是可选模型项，由用户手动选择；Claude/Codex 使用其现有模型连接。插件不会更改其他扩展、默认模型或认证。配置与宿主能力边界见包根 INSTALL.md，不自行扩展为通用配置或迁移框架。
