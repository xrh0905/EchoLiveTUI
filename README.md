# EchoLiveTUI

面向 Echo Live 的 Python / Textual 终端编辑器，优先满足直播时的文字输入、广播和端点控制。

当前仓库包含**第一版设计与上游兼容性调查**，尚未实现可运行的 TUI。

- [第一版设计](docs/v1-design.md)：范围、界面、广播路由、端点设置、按当前目录托管、配置响应覆盖、实施与验收。
- [1.6.6 → 1.8.12 差异和适配缺口](docs/upstream-compatibility.md)：源码结论、旧客户端缺陷、优先级和证据。
- [固定参考版本](docs/reference-lock.json)：便于复现的仓库和提交号。

## 复现调查

需要 Git、Python 3.12+；行为探针另外需要 Node.js，Node 不是计划中的产品运行依赖。

```powershell
git clone https://github.com/sheep-realms/Echo-Live.git .reference/Echo-Live
python scripts/audit-upstream.py
node scripts/probe-upstream.cjs
```

已有 `.reference/Echo-Live` 时跳过克隆。审计按固定标签读取 Git 对象，不改变参考仓库工作区。

输出位于 `.reference/audit/comparison.json` 和 `.reference/audit/upstream.patch`；该目录不提交、不打包。

行为探针直接调用两个上游版本的协议方法，使用内存浏览器与时钟替身。它验证协议假设，不代表通过了浏览器、OBS 或 TUI 端到端验收。

## 第一版边界

保留 Python、aiohttp、旧消息语法、拼音/注音模拟打字、自动停顿与 VRChat OSC；使用 Textual 重建输入优先的界面。

本程序作为本地 WS 服务端。仅在启动工作目录具有 Echo Live 结构时，额外托管该目录并覆盖 HTTP 配置响应。未识别到资源时保持独立 WS 用法。

第一版不做 Companion、所见即所得、Echo Live 下载/升级/版本切换，以及外部 WS 服务端连接模式。

第一版即实现每客户端的身份、页面职责、版本/能力和连接来源识别，通过适配层分别处理 live、history、character、editor 和未知客户端，为后续功能提供基础。

命令采用新规范：`//` 发送以 `/` 开头的文字，`/settings` 打开可搜索、分组、校验并保存各项功能的 TUI 设置页；`/set` 提供统一快捷修改。`/name` 保留为正式命令，仅保留 `/nocc`、`/clear`、`/exit` 等明确列出的兼容入口，不兼容整套旧命令。

直播高频操作保留 `/quote` 引号切换与 `/paren once|on|off` 括号控制。顶栏集中展示连接与目标，输入框附近展示说话人，底栏展示下一条消息的增强设置和上下文操作提示。

## 阶段 1.5

增加局域网发送模式：托管原版 Echo Live editor，识别本机局域网 IP 并提供选择、二维码/URL，用户打开链接即可访问 editor。复用第一版 WS 握手、客户端识别与广播路由，不另设签到协议。具体范围见[设计中的阶段 1.5](docs/v1-design.md#9-阶段-15局域网发送模式)。

## 阶段二

重新设计 `/skip` 与播放/取消控制，明确区分下一条、结束打印、清队列和隐藏显示。第一版不提供旧 `/skip` 或 `skip_mode` 设置；具体行为在阶段二定案。
