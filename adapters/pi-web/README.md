# agegr/pi-web 右侧回顾栏

本目录提供基于上游 v0.11.0 的独立补丁，不是上游已发布功能。

- 上游：https://github.com/agegr/pi-web
- 固定提交：`c9e1513245c7ea73dafb2bb81791e8da20bfe3cf`
- 上游许可证：MIT；补丁保留上游结构与许可证。
- 配套回顾插件：0.3.6 或更新的兼容版本。

顶部“回顾”按钮打开右侧栏；栏内可在回顾、原有文件和终端之间切换，并复用拖宽、展开和关闭能力。回顾跟随当前聊天，跨项目选择也保留侧栏。新聊天尚未初始化时显示说明；已启动的会话直接读取其现有回顾连接，不发模型请求、不新建回顾。

## 构建与安装

先按主安装说明注册回顾 Pi package。保留原有 Pi 配置、门神凭证和默认模型。在独立目录取得上述固定提交；不要修改正在运行实例的目录。确认 `git rev-parse HEAD` 完全匹配后：

```sh
git apply --check /实际插件路径/adapters/pi-web/agegr-0.11.0.patch
git apply /实际插件路径/adapters/pi-web/agegr-0.11.0.patch
npm ci --ignore-scripts --no-audit --no-fund
node bin/prepare-terminal.js
node_modules/.bin/tsc --noEmit
NEXT_TELEMETRY_DISABLED=1 npm run build
node bin/pi-web.js --hostname 127.0.0.1 --port 30141
```

安装脚本默认不运行。`prepare-terminal.js` 只修正包内 macOS spawn-helper 的可执行权限；其他平台需要按 node-pty 的实际预编译支持处理。生产构建会读取上游 Google Fonts。这里执行的是本地发布构建，不运行 `npm run release`，不发布 npm 或 GitHub。

停止旧 Pi Web 前先让用户处理正在运行的会话。重启服务后刷新网页；`/reload` 或仅刷新网页不能更新客户端侧栏代码。升级上游时重新检查补丁，不直接对新版强行应用。

## 边界与验证

仅支持浏览器和服务同机、通过启动时的精确本机 HTTP 地址访问。iframe 只允许这个来源嵌入；后台接口仍要求自己的能力令牌，Pi Web 的登录、Host 和 Origin 检查不变。不把 iframe 改为任意 URL，不开放通用代理，不将回顾地址从聊天内容或查询参数解析为可信输入。

问题仍由面板复制、用户粘贴发送。真实浏览器布局、剪贴板、VS Code 窗口由用户验收。构建、类型检查、定向 lint、会话 URL/来源检查、固定回顾服务与三会话实际 HTTP 集成已验证。
