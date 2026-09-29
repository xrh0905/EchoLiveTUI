# Echo Live 1.6.6 → 1.8.12：功能差异与适配缺口

调查日期：2026-09-30。以实际 Git 标签源码为准；旧讨论提供产品方向，其中的接口例子不作为协议定义。

## 1. 比较基线与结论

| 项目 | 固定基线 |
| --- | --- |
| 上次适配目标 | `1.6.6` / `0139ab2a28b8b565a62e78792f32311403b9f7e6` |
| 最新正式版 | `1.8.12` / `44ff7288e5c3d9dc218947e01220b94fb2f6d6b7` |
| 最新 master | 调查时与 `1.8.12` 相同，无额外开发分支差异 |
| 旧客户端 | `W:/echo-client`，提交 `deb2f07d3dc1e422c495966e912be9fea3a8ed78`，包版本 `0.0.12` |
| 全量差异 | 153 个文件，10,566 行新增、1,611 行删除；包含资源和内部实现变化 |
| 广播动作差异 | 新增 `editor_typing`；没有删除已有 `API_NAME_*` 动作 |
| 配置数据版本 | 13 → 14（1.6.8）→ 15（1.7.10）→ 16（1.8.7） |

基础消息格式仍可复用。优先修复广播拓扑、目标选择和身份信息，再接入输入指示；不需要重做上游全部新增功能。

## 2. 功能差异分组

| 首次版本/范围 | 上游变化 | 对第一版的影响 |
| --- | --- | --- |
| 1.6.7 | 编辑器画中画，通过 WS 转发 | 本地 hub 必须支持网页编辑器入站消息，不能只发送 TUI 消息 |
| 1.6.8–1.6.9 | 同步 Echo 1.1.0；修复 `typewrite` 尾部单引号；零宽空格处理；新增消息过滤器、连续中文语气符号切片 | 复用消息编码；覆盖单引号、拼音、停顿、空格回归；显示文字可能经上游过滤 |
| 1.6.8 | `accessibility.send_on_enter`、短 ISO 语言代码 | 上游网页选项；TUI 自己定义 Enter 行为 |
| 1.6.11–1.7.2 | 网页表单恢复、自动检查更新、统计和无障碍改进 | 上游托管后自然获得；TUI 不复制统计、更新管理 |
| 1.7.3 | `from.expand` 可选元数据；编辑器自带名称和版本；EchoLive/History 内部事件改蛇形命名 | 完整保留 envelope 元数据；不要依赖旧内部事件名；广播 action 名未因此改名 |
| 1.7.4 | 编辑器控制栏/快捷键数据驱动 | TUI 命令注册表独立实现，不搬入上游 Ctrl 快捷键体系 |
| 1.7.5–1.7.10 | 音效与数值提供器、表情资源/字段、色板、设置插槽；历史倒序滚动修复 | 保留资源路径和配置；暂不开发图片选择器或扩展编辑器 |
| 1.8.0–1.8.3 | 扩展 V2、脚本/模块加载改造；入口 `main.js`；异步扩展载入 | 静态托管完整资源树，不能只复制几个 HTML/JS 或写死旧脚本顺序 |
| 1.8.4–1.8.6 | 打印空格修复、新样式与控制栏 | 消息测试覆盖空格；显示功能交由原版 Echo Live |
| 1.8.7 | `editor_typing`、输入提示配置与 `typing_label` 注册表 | P0：加入输入活动节流、稳定发送者 UUID 和能力标识 |
| 1.8.8–1.8.10 | 输入提示翻译修复、输入状态卡住修复；图标表情与 offset/scale | 输入指示首选完整验证 `1.8.12`，不能仅因为 ≥1.8.7 就声称同等稳定 |
| 1.8.11–1.8.12 | 修复编辑器加载时机和依赖关系；扩展示例主题 | 使用完整 `1.8.12` 资源，不单独移植新广播文件到旧版本 |

以上分组来自[发行记录](https://github.com/sheep-realms/Echo-Live/releases)与[固定版本比较](https://github.com/sheep-realms/Echo-Live/compare/1.6.6...1.8.12)。

默认配置叶子字段新增 9 项：`echolive.typing` 下 4 项、`echolive.filter` 下 2 项、`accessibility` 下 2 项、`editor.color_picker.recently_auto_sort`。无叶子字段删除；除 `data_version` 外，默认值变化只有 `editor.function.tabpage_config_enable: true → false`。这不代表注册表、JS API 和资源没有破坏性变化。

## 3. 旧客户端的具体适配缺口

下列问题来自旧项目实现；其中多项在 1.6.6 就已存在，并非本次上游升级引入。

| 优先级 | 旧实现 | 缺口与处理 |
| --- | --- | --- |
| P0 | `protocol.py` 的 `target: dict`，测试使用 `{"name":"live"}` | 上游要求 UUID、`@name`、`@__live` 等字符串或字符串数组；对象目标不匹配。替换类型及测试，以真实上游方法验证 |
| P0 | `message.render()`、PING 和多数指令不含 `from` | typing 清理与端点状态需要稳定 UUID；由一个编码器统一补全 `name/uuid/type/timestamp` |
| P0 | 接收路径主要打印日志，没有转发 | 网页编辑器无法通过该服务向其他 WS 终端发送；关闭 BC 后历史页/形象页失去消息来源。实现有动作和目标约束的 hub |
| P0 | 会话只有本地自增 ID，没有完整上游身份 | 存储 `from.uuid/name/type`、`hello.hidden/targeted`；编辑器以 `ping` 出现也要登记；重连/改名不应产生幽灵终端 |
| P0 | 未知类型默认当 `live`，未列出 `character` | 会错投消息；识别 `live/history/character/server/client/unknown`，未知终端不能默认接收文字 |
| P0 | 未支持 `editor_typing` | 新增输入活动控制器；与消息使用同一发送者 UUID；旧版本降级 |
| 阶段二 | `/skip` 还给 history 发 `echo_next` | 两个目标版本的历史页都不处理 `echo_next`；第一版不提供 `/skip`，阶段二重做播放/取消语义。历史更新用 `echo_printing`，清空用 `history_clear` |
| P0 | 配置仅有监听 `host/port`，发送仅按角色过滤 | 增加端点详情、选择/排除、UUID/名称目标和独立历史投递；本地默认覆盖全部在线字幕端 |
| P0 | 只有 `/`、`/ws`、`/healthz` | 当前目录识别成功后增加静态托管及 `GET /config.js` 响应覆盖 |
| P1 | 每连接队列无上限，消息延迟与指令在同一发送循环 | 有界消息队列；历史清空与 typing 不排在长消息延迟后；断线清理、不自动重播。播放中断另在阶段二设计 |
| P1 | 命令、网络、配置写入与 Rich 输出集中在 `EchoServer` | 将 core、commands、pipeline、protocol/hub、hosting、Textual UI 分开 |
| 保留 | `message.py` 的 Markdown/短码、拼音/注音、停顿，以及 `osc.py` | 迁入独立流水线并用旧样例做回归；OSC 每次用户提交只发一次，不按 WS 连接数发送 |

## 4. 重复广播：原因和不能采用的修复

两个版本的 `EchoLiveBroadcast.sendData()` 默认都会向 BroadcastChannel 和 WS 各发送一次。WS 的接收方法只调用 `getData()`，**并不自动把收到的原始帧再发到 BroadcastChannel**。

```text
网页编辑器 ── BroadcastChannel ──→ live
     └────── WS → hub → WS ─────→ live   ← 同一命令到达两次

live ─────── BroadcastChannel ──→ history
  └──────── WS → hub → WS ──────→ history ← 同一打印事件到达两次
```

- `editor.websocket.disable_broadcast = true` 虽然名字在 editor 配置下，却由广播基类读取，**也约束 live/history/character 的发送**。
- 该开关只禁止 BC 发送，不禁止 BC 接收。受托管页面还应使用本次进程专有的 channel，与同源的非托管页面隔离。
- 不要设 `echolive.broadcast.enable = false`：它会阻止广播对象建立，WS 也随之不可用。
- 不要用“相同文本 + 时间窗”吞消息：用户可能故意重复发送。`allow_send_duplicate_message` 与 `history.message.remove_continuous_duplicate` 是展示行为，不是网络去重协议。
- 即使只有 WS，两个 live 同时打印同一字幕仍会生成两个不同来源的 `echo_printing`。当前实现由 EchoLiveTUI 从已接受消息独立生成历史，字幕打印回报不再送给 history；不依赖内容哈希或 live 来源绑定。
- 外部手动配置的页面不受响应覆盖控制，必须在它们的原配置中采用同样的 WS 单路径设置。第一版不承诺修复任意混合拓扑。

## 5. 输入状态：实际协议

```json
{
  "action": "editor_typing",
  "target": ["目标-live-uuid"],
  "from": {
    "name": "EchoLiveTUI",
    "uuid": "本次进程固定的发送者-uuid",
    "type": "server",
    "timestamp": 1790697600000
  },
  "data": {"username": "Someone"}
}
```

1. 1.8.7 加入该 action；1.6.6 无相应处理分支。
2. 上游按发送者 UUID 维护输入者；每次心跳更新 3,000 ms 超时。
3. `message_data` 会清除**同一个 `from.uuid`** 对应的输入状态。
4. 没有 `typing_stop` 或 `idle` 广播动作。清空、离开输入框、断线时停止心跳，等待接收端超时；不能发送空白字幕冒充停止 typing。
5. `echolive.typing.enable` 默认 false。只发送心跳而没有启用显示，观众仍看不到输入提示。

## 6. 配置和端点的容易误判之处

- 文件是 `config.js`，声明 `const config = {...}`；不是 `settings.json`。JS `const` 对象仍能修改属性，可在 HTTP 响应末尾追加窄范围覆盖，不在 Python 执行用户 JS。
- 原版页面是小写 `settings.html`。它通过文件选择器或下载保存 `config.js`，没有 HTTP PUT 接口。第一版不编造配置保存 API。
- 编辑器 `auto_url` 在两个参考版本中会拼出根路径 `ws://<location.host>`，不带 `/ws`；覆盖为 `auto_url=false` + 明确 URL，保留旧根路径 WS 握手兼容。
- `target` 数组是**按顺序匹配**：`["@__live", "-@main"]` 与反向排列结果不同。UI 选择/排除在本地计算最终 UUID 列表；转发外部帧时保留其原始目标语义。
- `from.name` 不等于唯一身份；编辑器名称可能含版本，多页面可能同名。身份索引使用 UUID，重名需显式选择，不能随意合并。
- `hello` 不携带标准化软件版本/能力列表。不能从 `hidden`、角色或握手成功推断 typing 能力。

## 7. 验证与证据

已运行 `python scripts/audit-upstream.py` 和 `node scripts/probe-upstream.cjs`。后者 **18 项通过**，覆盖两个版本的目标类型/顺序、双通道发送、禁广播作用范围、WS 不自动回播、history action、typing 能力，以及新版超时和同 UUID 清理。

探针用 Node VM 执行实际标签中的方法，仅替代浏览器环境、计时器和初始化。实施后另已运行 1.6.6/1.8.12 的 Edge 无头浏览器双字幕/单历史检查；OBS 与真实输入法/热键仍待人工验收。

实施期间还修正了旧消息渲染的线格式：分段速度映射到 `speed`，整条速度放入消息级 `data.printSpeed`，表情放入 `data.emoji`，多个 class 以空格连接。这些格式在两个参考版本中一致。

主要源码：

- [1.8.12 广播实现](https://github.com/sheep-realms/Echo-Live/blob/44ff7288e5c3d9dc218947e01220b94fb2f6d6b7/res/class/EchoLiveBroadcast.js)：`sendData`、`checkTargetIsSelf`、`getDataPortal`、`getDataHistory`。
- [1.8.12 输入状态与消息调度](https://github.com/sheep-realms/Echo-Live/blob/44ff7288e5c3d9dc218947e01220b94fb2f6d6b7/res/class/EchoLive.js)：`setTypingEditor`、`removeTypingEditor`、`send`。
- [1.8.12 配置](https://github.com/sheep-realms/Echo-Live/blob/44ff7288e5c3d9dc218947e01220b94fb2f6d6b7/config.js)、[设置保存](https://github.com/sheep-realms/Echo-Live/blob/44ff7288e5c3d9dc218947e01220b94fb2f6d6b7/res/script/settings.js)。
- [旧客户端协议](https://github.com/xrh0905/echo-client/blob/deb2f07d3dc1e422c495966e912be9fea3a8ed78/echo_client/protocol.py)、[旧服务实现](https://github.com/xrh0905/echo-client/blob/deb2f07d3dc1e422c495966e912be9fea3a8ed78/echo_client/server.py)。
- [用户提供的 Echo 开发入口](https://sheep-realms.github.io/Echo-Live-Doc/dev/echo/)明确提示文档可能滞后；[广播 API](https://sheep-realms.github.io/Echo-Live-Doc/dev/broadcast/api/)与标签源码联合使用。


## 阶段 1.5 实际浏览器兼容修正

针对 1.8.12 的 HTTP 响应增加两处有限修正，保留磁盘文件：

- `ResourceLoader._loadScript` 动态 script 默认 async，虽然递归插入依赖，实际执行可能倒序，导致 editor-help 的 `uniWindow` / `localStorageManager` 尚未初始化。将未显式声明 async 的脚本设为 `async=false`，按插入顺序执行。
- `UniverseWindow.autoSetFocusButton` 无按钮分支引用不存在的 `data.closable`。改为查询对应 windowList 记录。

真实 Edge 测试覆盖 LAN editor 按钮发送、两路字幕显示、独立 history 恰好一条、TUI 远程消息记录以及关闭 LAN。三个响应覆盖文件的磁盘 SHA256 在测试前后相同。源文件已修复或不匹配时，精确替换不会修改其他代码。

`websocket_heartbeat` 原目标是 `@__ws_server`；ELTUI 将其原 sender/data 转发给每个 server 的 UUID，兼容 targeted server。不会作为内容历史或转发给字幕/history。server 的 message_data 则独立生成 TUI 和 history 记录，不等待 live 回报。
