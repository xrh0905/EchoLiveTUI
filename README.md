# EchoLiveTUI

面向 Echo Live 的 Python / Textual 终端编辑器，支持文字广播、格式预览、多轨道自动翻译、逐行演出、局域网 editor 和 VRChat OSC。

当前版本 **0.6.2**，需要 Python 3.12+。Windows 用户可从 [Releases](https://github.com/xrh0905/EchoLiveTUI/releases) 下载单文件 EXE。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m echolivetui
```

安装后也可使用 `echolivetui` 命令。默认监听 `127.0.0.1:3000`，WebSocket 接口为 `/ws`，兼容根路径 WS。配置保存在启动工作目录的 `echolivetui.yaml`，可用 `--config 文件路径` 指定其他文件；`--headless` 仅运行服务。

在 Echo Live 目录或含 `EchoLive` 子目录的工作目录启动时，会自动托管网页：OBS 使用 `http://127.0.0.1:3000/live.html`，历史页使用 `/history.html`。仅检查当前目录和单级 `EchoLive` 子目录，当前目录优先；没有上游资源时提供独立 WS 服务。启动后，历史区显示实际监听地址及端口对应的网页入口。

独立 WS 用法需在上游页面开启 WS、配置地址，并设置 `editor.websocket.disable_broadcast=true`，使消息经 TUI 路由。Echo Live 资源和个人配置不包含在发行包中。

## 功能

### 文字编辑与格式预览

主屏保持输入焦点，Enter 发送文字；`//settings` 发送字面量 `/settings`。鼠标拖选或 Shift+方向键选择文字，Ctrl+C 复制选区。无选区时 Ctrl+C 默认不退出，可通过 `/nocc` 或设置调整。

格式栏支持粗体、斜体、下划线、删除线、上下标、字号、颜色和重置，也可直接输入 Markdown 与短码：

| 短码 | 效果 |
| --- | --- |
| `@b`、`@i`、`@u`、`@s` | 粗体、斜体、下划线、删除线 |
| `@^`、`@_` | 上标、下标，例如 `x@^2@r + H@_2@rO` |
| `@+`、`@-` | 在五档字号间调整 |
| `@[#66ccff]` | 颜色，支持 3/4/6/8 位十六进制值 |
| `@r` | 清除累积格式 |
| `@shout`、`@rainbow` | 喊叫事件、彩虹样式 |
| `@<rainbow>`、`@<:custom>` | 内置、自定义 CSS 类 |
| `@@`、`\@` | 输出字面量 `@` |

预览按需扩展为一至三行，上下标文字用灰色区分，发送仍保留实际颜色。格式会累积，同时启用上下标时按上游 CSS 显示为上标，切换前请先重置。字号、动画、CSS 类和透明度的实际效果以 Echo Live 网页为准。

主输入的上下键切换输入历史；Tab 进入补全选择后，上下键切换候选，Enter 确认，Esc 关闭。演出编辑器使用 Ctrl+Space 补全短码。`/settings input` 可调整格式栏、补全和格式预览。表情码及图片码保留为普通文本。

### 消息修饰与模拟打字

正文引号由 `/quote` 控制，姓名括号由 `/paren` 控制，均支持预设和自定义符号；`/paren once` 仅作用于下一次成功发送。新配置默认使用「」引号和 20ms 打印间隔，已有配置保留显式值。

支持后缀、拼音／注音模拟打字和按打印步数设置的自动停顿。连续标点、长破折号、省略号、数学符号和 emoji 保留为完整符号片段，中文按词模拟输入；保留样式、速度、停顿和上游已有打字数据。底栏显示修饰状态，模拟打字或停顿关闭时显示 `○`，开启时显示毫秒数。

### 多轨道自动翻译

在 `/settings multitrack` 开启自动翻译并添加至少一种子轨语言。主轨留空时使用 Microsoft 自动检测，识别结果以淡色 Hint 显示在正文预览行末。语言列表可搜索名称、原生名称和代码，置顶 `zh-Hans`、`en`、`ja`、`ru`；子轨支持添加、删除和排序，禁止重复，日语代码为 `ja`。

输入停键 500ms 后，输入框下方逐语言显示译文或错误。译文区可换行、滚动，最多占视口三分之一且不超过八行，独立于格式预览开关。新输入使旧译文立即失效，缓存命中时直接显示。自动翻译子轨默认使用 `small` 字号，并沿用主轨的引号开关和样式（含自定义引号）；引号变更直接更新预览，不重新请求翻译。

Enter 立即结束去抖，等待当前译文后一起发送主轨及子轨；等待期间编辑只读，Esc 可取消并保留草稿。失败默认不广播、不写历史、不发送 OSC；开启“翻译失败仍发送（仅本机）”后，失败时只发送原主轨并提示。演出页可按整段或游标所在行预览、翻译。

原版 editor（含 LAN）的消息到达 TUI 后才翻译：已有非空子轨或没有正文的段落保持原样，其余正文批量翻译。每连接按接收顺序处理，心跳和控制消息独立响应；任一必需译文失败时，整条原消息照常转发，不受本机错误开关影响。历史与 OSC 始终只取主轨一次。

功能默认关闭。语言表随程序打包；启用后正文纯文本会发送至 Microsoft Edge 翻译接口，不发送用户名、格式短码或模拟打字拼音。请求超时为 8 秒，错误明确显示，不无限重试；最近 128 组成功结果仅缓存在进程内，预览与发送共享请求及结果。

```yaml
multitrack:
  enable: true
  source_language: ""
  target_languages: [en, ja, ru]
  skip_errors: false
```

### 多行输入与演出

`/compose`、Shift+Enter 或多行粘贴打开演出页；粘贴内容作为文字处理，不执行其中的命令。整段模式一次发送全文，逐行模式从游标所在行开始并跳过空行。

逐行发送等待所有目标字幕轨道实际打印完成后，再计算行间隔、发送下一句。发送时锁定编辑并高亮当前行，可暂停后修改内容或游标，从新位置继续。Esc 或“取消/返回”停止后续发送并保留草稿；最后一句完成后清空演出草稿并返回主页。

目标断开或未反馈完成时暂停。连接恢复后清除断开提示，保持暂停，手动继续时从游标所在行重发；没有输出端时保留草稿。

### 端点、路由与历史

`/endpoints` 查看客户端身份、页面职责、软件版本、连接来源和能力覆盖。默认发送给所有在线字幕端，可用 `/target` 定向选择或排除 UUID、`@名称`。未知版本字幕端默认支持输入状态，已知旧版或手动禁用的端点会排除。

历史端独立接收已接受消息，不依赖字幕端打印回报；只有历史端在线也可发送。默认立即显示历史，可在 `/settings history` 暂存最新一条至下次发送。非托管页面需手动关闭上游 `history.message.latest_message_hide` 和 `live_display_hidden_latest_message_show`。

TUI 历史按时间、姓名、内容显示，支持鼠标框选复制，默认保留 500 条完整记录，超限按 FIFO 淘汰。editor 入站也记录主轨。默认 `info` 日志显示连接与断开，`debug` 增加接受和写入记录；发送失败显示错误，用户消息单独保留。

### 局域网 editor

检测到 Echo Live 后，点击底栏“连接”或执行 `/connect`，选择本机网卡／IPv4 并启用。页面提供二维码及实际 editor URL，手机与电脑网络互通时即可使用原版 editor 发送。关闭入口会断开远程连接，保留本机 OBS 连接；配置在下次启动恢复，远程页面不能访问 `settings.html`。

LAN 入站默认启用模拟打字，使用与本机相同的拼音／注音方案，可用 `/set lan.typewriting off` 独立关闭。保留主轨、说话人、样式、速度和已有打字数据，不额外给原主轨添加引号或后缀。

保存用户名、OSC、预览等无关设置不会校验 LAN 网卡或重建监听，失效的旧 IP 不阻止保存。启用／重试入口、切换网卡或修改监听端口时继续校验地址。

### VRChat OSC

`/settings osc` 配置目标。TUI 与 editor 入站的每条已接受消息向 `osc.host:osc.port` 发送一次 `/chatbox/input`，取主轨纯文本，不含短码或打字拼音；同一广播内的多段正文合并为换行文本。OSC 错误不影响字幕和独立历史，也不会因多个字幕端或回报重复发送。

输入提示开启时同步发送 `/chatbox/typing`：编辑时续期，3 秒无输入、清空、提交或关闭提示时停止。

### 设置与 Echo-Live 管理

`/settings` 提供分组、搜索、校验、草稿重置、冲突检查及原子保存；`/set key value` 可快捷修改。通用设置、模拟打字、消息修饰、多轨道、网络、端点、历史、日志和 OSC 使用同一配置。设置、端点、连接和演出页替换历史区域，保留顶栏、输入工作区和底栏入口。

承载模式额外提供 Echo-Live 字体和版本管理。字体设置修改 `res/style/live-common/font-family.css` 的字体变量，支持逗号分隔列表及恢复默认；刷新字幕页后生效。版本管理可配置发布仓库（默认 `sheep-realms/Echo-Live`），检查最新正式版、比较版本并打开发布页，不自动安装或替换上游资源。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `/settings [分类]` | 设置，如 `multitrack`、`history`、`log`、`osc` |
| `/set key value` | 修改并保存单项设置 |
| `/name 名字` | 设置说话人 |
| `/quote [on\|off\|en\|cn\|jp\|custom]` | 正文引号开关／样式 |
| `/paren once\|on\|off` | 姓名括号 |
| `/connect`、`/endpoints` | LAN 入口、客户端与路由设置 |
| `/target set UUID或@名称`、`/target exclude ...`、`/target all` | 选择、排除或恢复全部字幕端 |
| `/compose` | 多行输入与逐行演出 |
| `/history clear` | 清空独立历史 |
| `/source 文件` | 预检查后执行命令脚本 |
| `/status`、`/help` | 状态和完整命令帮助 |
| `/quit`、`/exit` | 退出 |

## 兼容范围

上标、下标及字幕子轨需要 Echo-Live 1.9.0；基础广播兼容已验证的 1.8.12、1.6.6。托管服务覆盖 HTTP 返回的 `config.js`，并修正 1.8.12 的脚本加载顺序与弹窗焦点；原版 `settings.html` 仍读取原配置。

当前不提供播放跳过／清队列的 `/skip`、Companion、所见即所得编辑、上游自动下载升级切换或外部 WS 服务端连接模式。OBS、Windows 中文输入法、终端剪贴板和 OBS 全局热键仍需在实际使用环境验证。

## 开发与验证

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m echolivetui --self-test
```

pytest 和自检使用本地可控翻译替身，不请求外部翻译 API，不以供应商可用性决定测试结果。

可选浏览器验证需要 Playwright 和 Windows Edge：

```powershell
python -m pip install playwright
python scripts/browser-smoke.py EchoLive
python scripts/capture-ui.py
```

真实 Edge 翻译仅按需手工验证：`python scripts/browser-smoke.py EchoLive --translate`。上游资源可用 `scripts/download-echolive.py` 下载至参考目录，不覆盖当前 Echo Live。

内部接口 `prepare(..., tracks=[...])`、`Core.submit(..., tracks=[...])` 接受一层上游子轨段落并深拷贝，保留手工样式、速度、停顿和 `typewrite`，拒绝嵌套。异步 `Core.send(...)` 生成自动翻译子轨，已有显式非空子轨时跳过翻译。子轨文字按字面传入，不再次解析短码，不追加后缀或自动停顿；耗时估算和完成等待覆盖全部轨道，历史及 OSC 只使用主轨。

## Windows 打包与发布

```powershell
python -m pip install -e ".[build,test]"
python scripts/build.py --mode standalone
python scripts/build.py --mode onefile
```

需要 Python 3.12 x64 和 Visual Studio C++ 工具链。`dist` 中的 onefile 直接提供 EXE 与 SHA256；standalone 提供平铺 ZIP，解压后保留整个文件夹。从 Echo Live 工作目录运行程序即可识别资源。

打包后自动运行 `--self-test` 验证 TUI、词典、二维码、HTTP、WS 和翻译流程，并在独立隐藏控制台验证真实 Ctrl+C 的拦截与退出。onefile 使用 `--onefile-child-grace-time=infinity`，避免父进程强行结束启用了退出保护的子进程；`/nocc` 动态控制保护，SIGTERM 请求退出。

GitHub Actions 在提交和 PR 上运行测试；手动打包生成 Windows onefile EXE 与 SHA256。推送 `v*` 标签后，测试、打包和自检通过才发布 Release；带 `-alpha`／`-beta` 或 `aN`／`bN`／`rcN` 后缀的标签发布为 Pre-Release。版本标签应与 `pyproject.toml` 一致。

## 开发资料

- [历史设计与验收记录](docs/v1-design.md)
- [上游版本差异与兼容证据](docs/upstream-compatibility.md)
- [固定参考版本](docs/reference-lock.json)

上游审计需要 Git、Python 和 Node.js；Node 不是程序运行依赖：

```powershell
git clone https://github.com/sheep-realms/Echo-Live.git .reference/Echo-Live
python scripts/audit-upstream.py
node scripts/probe-upstream.cjs
```

已有参考目录时跳过克隆。审计按固定标签读取 Git 对象，输出位于不提交、不打包的 `.reference/audit/`。协议探针使用内存浏览器和时钟替身，不替代真实浏览器、OBS 或 TUI 验证。
