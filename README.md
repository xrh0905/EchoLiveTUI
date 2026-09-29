# EchoLiveTUI

面向 Echo Live 的 Python / Textual 终端编辑器，优先满足直播时的文字输入、广播和端点控制。

当前已实现可运行的第一版：Python 3.12+、Textual、aiohttp。主屏持续聚焦输入框，历史区域支持鼠标框选复制；设置和多行编辑使用独立弹窗。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m echolivetui
```

也可以运行安装后的 `echolivetui` 命令。默认监听 `127.0.0.1:3000`，WS 接口为 `/ws`（兼容根路径 WS）。配置位于启动工作目录的 `echolivetui.yaml`，或使用 `--config 文件路径`。通过 `--import-legacy 旧config.yaml` 显式导入旧设置，不修改原文件，不导入 `skip_mode`。

在 Echo Live 目录中运行会自动托管当前目录，OBS 使用 `http://127.0.0.1:3000/live.html`，历史页使用 `/history.html`。只覆盖 HTTP 返回的 `config.js`，磁盘文件不变；网页 `settings.html` 获得原配置。其他目录只启动 WS 服务，不扫描或管理安装目录。

## 常用操作

- 输入文字并按 Enter；`//settings` 发送字面量 `/settings`。
- `/settings` 打开设置；`/settings log` 调整日志；`/settings history` 调整 ELTUI 历史策略。
- `/name 名字`、`/quote [on|off|en|cn|jp|custom]`、`/paren once|on|off`。
- `/endpoints` 查看客户端档案及能力覆盖；`/target set UUID或@名称`、`/target exclude ...`、`/target all`。
- `/compose` 编辑多行消息；多行粘贴也会打开此页，不执行粘贴的命令。
- `/history clear` 清空历史端；`/help` 查看完整命令；`/quit` 退出。

默认发送给所有在线字幕端，包括仅接收定向广播的字幕端。历史端独立从已接受消息生成记录，不依赖字幕端打印回报；只有历史端在线也可发送。托管配置关闭上游“隐藏最新一条”，默认立即显示历史；可选择由 ELTUI 暂存最新一条，直到下一条消息到达。非托管页面需手动关闭 `history.message.latest_message_hide` 和 `live_display_hidden_latest_message_show`。

默认日志级别 `error`，只报告写入失败，不逐端输出成功或连接日志；主屏保留用户消息记录。`info` 显示连接、接受和写入结果，`debug` 额外显示无效协议消息。框选历史后可用 Ctrl+C 复制，未选中文字时 Ctrl+C 默认不退出。窗口标题栏、状态栏和历史区均不夺取输入焦点。

独立 WS 使用时，页面应只经 WS 发送：启用 WS，设置对应地址，并设置 `editor.websocket.disable_broadcast=true`。混合使用独立 BroadcastChannel 仍可能绕过 ELTUI 的去重边界。未知客户端版本不会自动获得 typing 能力，可在端点页手动指定。

- [第一版设计](docs/v1-design.md)：范围、界面、广播路由、端点设置、按当前目录托管、配置响应覆盖、实施与验收。
- [1.6.6 → 1.8.12 差异和适配缺口](docs/upstream-compatibility.md)：源码结论、旧客户端缺陷、优先级和证据。
- [固定参考版本](docs/reference-lock.json)：便于复现的仓库和提交号。

## 复现调查

需要 Git、Python 3.12+；行为探针另外需要 Node.js，Node 不是产品运行依赖。

```powershell
git clone https://github.com/sheep-realms/Echo-Live.git .reference/Echo-Live
python scripts/audit-upstream.py
node scripts/probe-upstream.cjs
```

已有 `.reference/Echo-Live` 时跳过克隆。审计按固定标签读取 Git 对象，不改变参考仓库工作区。

输出位于 `.reference/audit/comparison.json` 和 `.reference/audit/upstream.patch`；该目录不提交、不打包。

行为探针直接调用两个上游版本的协议方法，使用内存浏览器与时钟替身。它验证协议假设，不代表通过了浏览器、OBS 或 TUI 端到端验收。

## 验证

```powershell
.\.venv\Scripts\python -m pytest -q
node scripts/probe-upstream.cjs
```

可选浏览器测试需要 `pip install playwright` 和 Windows 上已安装的 Edge：

```powershell
.\.venv\Scripts\python scripts/browser-smoke.py .reference/Echo-Live
.\.venv\Scripts\python scripts/capture-ui.py
```

已验证上游 1.6.6、1.8.12 的真实浏览器双字幕/单历史链路、资源载入和配置文件哈希不变。OBS、Windows 中文输入法、终端剪贴板与 OBS 全局热键仍需实际使用环境验证。

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
