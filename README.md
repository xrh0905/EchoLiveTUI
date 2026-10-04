# EchoLiveTUI

面向 Echo Live 的 Python / Textual 终端编辑器，优先满足直播时的文字输入、广播和端点控制。

当前版本 0.4.0b3：Python 3.12+、Textual、aiohttp。主屏持续聚焦输入框，历史区域支持鼠标框选复制；设置、端点、连接和演出页替换历史区域，保留顶栏、工作区和底栏。承载模式提供 Echo-Live 字体与版本管理，演出页逐行发送等待实际打印完成，并支持暂停与连接恢复。

## 启动

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[test]"
.\.venv\Scripts\python -m echolivetui
```

也可以运行安装后的 `echolivetui` 命令。默认监听 `127.0.0.1:3000`，WS 接口为 `/ws`（兼容根路径 WS）。配置位于启动工作目录的 `echolivetui.yaml`，或使用 `--config 文件路径`。通过 `--import-legacy 旧config.yaml` 显式导入旧设置，不修改原文件，不导入 `skip_mode`。

在 Echo Live 目录中运行会自动托管当前目录，OBS 使用 `http://127.0.0.1:3000/live.html`，历史页使用 `/history.html`。覆盖 HTTP 返回的 `config.js`，并对 1.8.12 的脚本加载顺序和弹窗焦点错误做响应层修正，磁盘文件不变；网页 `settings.html` 获得原配置。其他目录只启动 WS 服务，此外只检测当前目录下的 `EchoLive` 子目录，不递归扫描；当前目录优先。

托管启动成功时，主屏历史区会出现来自 EchoLiveTUI 的入口消息，链接带实际端口及 `/live.html?name=main`。仅显示监听范围内的本机地址和首个有效 IPv4 地址，不显示不可用候选；启用配对后另行显示 editor 远程入口。

## 常用操作

- 输入文字并按 Enter；`//settings` 发送字面量 `/settings`。
- `/settings` 打开设置；`/settings log` 调整日志；`/settings history` 调整 EchoLiveTUI 历史策略。
- `/name 名字`、`/quote [on|off|en|cn|jp|custom]`、`/paren once|on|off`。
- `/endpoints` 查看客户端档案及能力覆盖；`/target set UUID或@名称`、`/target exclude ...`、`/target all`。
- `/compose` 打开演出页；默认整段发送多行消息，也可开启逐行发送并指定行间隔。多行粘贴也会打开此页，不执行粘贴的命令。
- `/history clear` 清空历史端；`/help` 查看完整命令；`/quit` 退出。

默认发送给所有在线字幕端，包括仅接收定向广播的字幕端。历史端独立从已接受消息生成记录，不依赖字幕端打印回报；只有历史端在线也可发送。托管配置关闭上游“隐藏最新一条”，默认立即显示历史；可选择由 EchoLiveTUI 暂存最新一条，直到下一条消息到达。非托管页面需手动关闭 `history.message.latest_message_hide` 和 `live_display_hidden_latest_message_show`。

server/editor 发来的消息也记录在 TUI 和独立 history 端；客户端 heartbeat 会转发给所有 server 端。

默认日志级别 `info`，显示端点连接与断开；发送成功只记 DEBUG，写入失败记 WARN；主屏保留用户消息记录。`debug` 显示接受、写入成功和无效协议消息。框选历史后可用 Ctrl+C 复制，未选中文字时 Ctrl+C 默认不退出。窗口标题栏、状态栏和历史区均不夺取输入焦点。

独立 WS 使用时，页面应只经 WS 发送：启用 WS，设置对应地址，并设置 `editor.websocket.disable_broadcast=true`。混合使用独立 BroadcastChannel 仍可能绕过 EchoLiveTUI 的去重边界。未知版本字幕端默认视为支持输入状态；已知旧版本或手动禁用仍会排除。输入状态默认启用，可在设置中关闭。

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

本程序作为本地 WS 服务端。在启动工作目录或其 `EchoLive` 子目录具有 Echo Live 结构时，额外托管该目录并覆盖 HTTP 配置响应。未识别到资源时保持独立 WS 用法。

第一版不做 Companion、所见即所得、Echo Live 下载/升级/版本切换，以及外部 WS 服务端连接模式。

第一版即实现每客户端的身份、页面职责、版本/能力和连接来源识别，通过适配层分别处理 live、history、character、editor 和未知客户端，为后续功能提供基础。

命令采用新规范：`//` 发送以 `/` 开头的文字，`/settings` 打开可搜索、分组、校验并保存各项功能的 TUI 设置页；`/set` 提供统一快捷修改。`/name` 保留为正式命令，仅保留 `/nocc`、`/clear`、`/exit` 等明确列出的兼容入口，不兼容整套旧命令。

`/paren once|on|off` 控制姓名括号，不修改消息正文；正文引号由 `/quote` 控制。姓名括号和正文引号均支持预设与自定义，预设下左右输入框禁用并显示所选符号，自定义下可分别编辑。底栏依次为「连接、端点、多行、设置、退出」，均可点击；在设置页切换端点分类不会另开窗口。单行顶栏不参与历史文本框选。

## 阶段 1.5

已实现局域网发送模式：托管原版 Echo Live editor，识别本机局域网 IP 并提供选择、二维码/URL，用户打开链接即可访问 editor。复用第一版 WS 握手、客户端识别与广播路由，不另设签到协议。具体范围见[设计中的阶段 1.5](docs/v1-design.md#9-阶段-15局域网发送模式)。

## 阶段二

重新设计 `/skip` 与播放/取消控制，明确区分下一条、结束打印、清队列和隐藏显示。第一版不提供旧 `/skip` 或 `skip_mode` 设置；具体行为在阶段二定案。


### 配对操作

在 Echo Live 目录启动，点击「连接」或执行 `/connect`。选择网卡或输入本机 IPv4，启用后左侧显示二维码，右侧显示真实监听的 editor URL 和接口控制。手机与电脑网络互通时，扫码即可使用原版 editor 发送。关闭连接会停止局域网入口并断开该入口的远程连接，本机 OBS 连接保留。设置会保存，下一次启动恢复。未启用时不会开放远程入口；远程页面不能访问 settings.html。

LAN 入站消息默认启用模拟打字，可在「模拟打字」或「网络与托管」设置中切换，也可执行 `/set lan.typewriting off` 关闭。此开关独立于本机消息的模拟打字开关，使用同一拼音／注音方案；下一条远程消息生效，保留编辑器的说话人、样式、速度和已有打字数据，不额外添加引号或后缀。

模拟打字时，连续的标点、长破折号、省略号、线条和数学符号，以及包含连接符或变体标记的 emoji 保留为完整符号片段，直接打印；中文仍按词模拟拼音／注音输入。保留样式与停顿边界，不合并编辑器已分开的格式片段。演出页匹配打印回报时忽略上游过滤器插入的零宽分隔符，仍等待打印完成事件。

### Windows 打包与发布

```powershell
python -m pip install -e ".[build,test]"
python scripts/build.py --mode standalone
python scripts/build.py --mode onefile
```

需要 Python 3.12 x64 和 Visual Studio C++ 工具链。产物位于 `dist`；onefile 直接提供 EXE 与 SHA256，不额外套 ZIP 或目录；可选本地 standalone 提供平铺 ZIP；打包后自动运行 `--self-test` 验证 TUI、词典、二维码、HTTP 和 WS，并在独立隐藏控制台验证真实 Ctrl+C 的拦截和退出。解压后将整个 standalone 文件夹放在任意位置，从 Echo Live 工作目录运行其中的 EXE；onefile 可直接放入 Echo Live 目录运行。两者均按启动工作目录识别资源。

单文件使用 `--onefile-child-grace-time=infinity`，避免 Nuitka 父进程在 Ctrl+C 后强行结束启用了拦截的子进程。`/nocc` 动态控制 Ctrl+C，SIGTERM 仍请求退出。底栏「退出」可正常结束。

GitHub Actions 在提交和 PR 上运行测试；手动运行打包工作流只产生 Windows onefile EXE 和 SHA256，推送 `v*` 标签时，全部测试与打包自检通过后发布 GitHub Release；带 `-alpha` / `-beta` 或 `aN` / `bN` / `rcN` 后缀的标签发布为 Pre-Release。版本标签应与 pyproject.toml 的版本一致。Echo Live 资源和个人配置不包含在包中。

开发验证可执行 `python scripts/download-echolive.py` 下载上游最新发布版（不改动工作目录下已有的 Echo Live），再用 `scripts/browser-smoke.py 路径` 验证融合模式与 LAN editor。


### 阶段二：选区、短码和实时预览

主输入框可鼠标拖选文字或用 Shift+方向键选区，Ctrl+C 复制选中内容。点击粗体、斜体、下划线、删除线、字号、颜色和重置工具，可以给选中文字应用格式。短码源文与实时预览同步更新；发送使用相同的解析结果。终端预览显示文字样式与颜色，字号、动画、CSS 类和透明度以 Echo Live 网页为准。

`Shift+Enter` 把当前输入缓冲区复制到 compose，原缓冲区保留；compose 内 Enter 换行。主输入框默认用上下键切换历史（包括 `/` 开头的命令）；先按 Tab 进入补全选择后，上下键才切换候选，Enter 确认，Esc 退出；compose 用 Ctrl+Space 补全当前短码。主页选中文字时显示格式操作，输入 `/` 或 `@` 时显示补全提示，二者共用一个提示行；演出页选区或短码时显示格式栏。可在 `/settings input` 选择格式栏上下文显示、始终显示或始终隐藏。预览默认开启，可独立关闭；提示同样可关闭。

主页顶栏显示版本、OSC / 发送目标和 L（字幕）/ H（历史）/ E（发送端）数量及承载状态。工作区为提示或选区操作行、单行预览、单行输入和发送 / 输入状态行；底栏显示消息修饰与页面入口。历史按时间、姓名、内容显示，系统生成的消息名称带下划线；姓名列按名称长度自适应，并随窗口宽度限制上限，为正文留出空间。默认保留 500 条完整记录，超限按 FIFO 淘汰，可在 `/settings history` 修改。

演出页的逐行发送从游标所在行开始，跳过空行；等待所有目标字幕端反馈本句打印完成后，才开始计毫秒行间隔并发送下一句，不等待窗口隐藏。发送期间锁定编辑并高亮当前行，发送按钮变为暂停；暂停后可以修改内容或游标，从新游标所在行继续，并继续等待上一句尚未完成的打印。取消或 Esc 返回会停止后续发送并保留演出草稿；最后一句打印完成后清空演出草稿并自动返回主页。字幕端断开或没有打印完成反馈时暂停，保留草稿。断开的目标回连后自动清除断开提示，保持暂停，手动继续时从游标所在行重发；没有输出端的提示也会随可用输出恢复而清除，无需重新打开演出页。

各页导航由居中 Tab 标签和下一行整行色带组成，中括号标识当前选中的页面；设置分类包含「所有设置」及各个分项。操作按钮统一单行、靠右固定在页面底部；演出页逐行开关、间隔和发送 / 返回共用一行，连接页客户端信息位于底部操作栏上方。格式操作使用中文名称。程序使用 `Capabilities` 位标志描述环境，目前仅定义 `HAS_ECHO_LIVE`，检测当前目录或 `EchoLive` 子目录，供页面可见性及后续 Companion 集成扩展使用。新配置默认使用「」引号和 20ms 模拟打字，已有配置保留显式值。

检测到 Echo Live 时，设置导航均分为「TUI 设置」和「Echo-Live 设置」。后者使用侧边栏切换「字体」与「版本管理」：字体保存直接修改 `res/style/live-common/font-family.css` 的 `--echo-default-font-family` 变量，输入框只显示字体名称，读取时移除引号，保存时自动添加引号；支持逗号分隔的字体列表，`sans-serif` 等通用字体保留 CSS 关键词形式。保留其余样式，可恢复默认思源黑体，刷新前台页面生效。版本管理读取本地 `app.js`，可保存 GitHub 仓库选项（`owner/repo`），默认仍为 `sheep-realms/Echo-Live`；检查更新时也会保存当前仓库，通过所选仓库的发布 API 获取最新正式版并比较版本，可打开其发布页查看更新。网络失败或请求受限时可重试，检查更新不会安装或替换本地文件。

底栏的模拟打字与模拟停顿关闭时显示空心圆 `○`，开启时显示毫秒数；停顿按配置的打印步数乘以打印间隔换算，例如 10 步 × 20ms 显示 200ms。

- `@@`：任意位置输出一个字面 `@`，后续继续解析。例如 `@@b` 显示 `@b`，`@@ @b粗体` 显示 `@` 后接粗体。
- `@b`、`@i`、`@u`、`@s`：粗体、斜体、下划线、删除线，叠加到 `@r` 重置。
- `@+`、`@-`：在五档字号间调整；`@[#66ccff]` 支持 3/4/6/8 位十六进制颜色。
- `@shout` 触发一次上游喊叫事件，`@rainbow` 应用彩虹样式；预览显示静态彩虹色，实际动画以网页为准。
- `@<rainbow>` 使用 `echo-text-rainbow` 类；`@<:custom>` 使用自定义 CSS 类；`\@` 输出字面 @。

表情码和图片码均保留为普通文本，不提供补全；Companion 和 Echo Live 版本托管继续后置。已有配置中的明确设置保留，新默认值不会覆盖个人配置。`/skip` 的播放与取消语义尚待独立实现。
