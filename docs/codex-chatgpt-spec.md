# Codex 订阅适配：实现与验收约定

基线：`351ca8422f7b5b54e80a9c1ce03a222e02415b6b`。工作分支：`feat/codex-chatgpt-provider`。

需求：用户在已登录官方 Codex CLI 的 Mac 上，通过 ChatGPT 订阅运行 ARTEMIS 的模型推理，无需模型 API Key；保留原有 API 模式。仅向个人 fork 分支提交，不向上游提 PR、合并或部署，不修改旧部署与个人 App 数据。

实现约定：

- 新增 `codex` provider，复用 CLI 管理的 ChatGPT 登录；不读取、复制、打印登录令牌，不直接调用未公开订阅端点。
- 通过官方非交互 `exec`、图片附件、JSON Schema 输出传输；每轮传完整 ARTEMIS 消息，不复用 Codex 会话。
- ARTEMIS 保留设备工具执行权；Codex 输出动作意图，经工具名、参数 schema 校验转换为 `AIMessage.tool_calls`。工具结果含调用 ID 返回下一轮。
- 禁用 Codex shell、浏览器、电脑、MCP 用户配置、插件与 hooks；不传入 API Key，强制 ChatGPT 登录，不因失败转向付费 API。
- 截图仅接受有大小上限的内联 PNG/JPEG/WebP，在私有临时目录按序写入；成功、失败、超时、取消后清理。音频、视频、远端图片 URL 明确拒绝。
- 结构化结果通过外层 JSON Schema 封装，内层结果严格经过原始领域 schema 与 Pydantic 校验；不声称 CLI 支持完整原生函数调用协议。
- 订阅配置覆盖主节点、辅助判定和后台记忆模型；关闭 Gemini 专用缓存、原生视频、需外部 OCR/感知服务的工具。订阅节点禁止 API fallback。

已授权测试接口：`ModelFactory`／LangChain 模型接口、ARTEMIS action 声明与工具结果、Explorer 通用循环、Checker schema、配置和后台摘要路由。离线测试只在 CLI 进程边界使用替身，不能作为订阅成功证据。

真实验收使用合成图片与协议回执，不连接设备：文本、截图识别、单工具（Explorer submit）、多工具（Operator action schema）、工具结果续接、Checker 结构化输出。额度／认证故障、超时／取消、无效 schema 用确定性边界测试覆盖，不主动耗尽真实额度。

MATT：读取本机 `ask-matt` → `implement` → `tdd`／`code-review` 工作流。当前需求本身作为已确认规格，保留此本地规格供两轴 Review 使用，不发布 Issue。既有全局 skill 与仓库 agent 配置不修改。

不在范围：原生视频／音频、Gemini Robotics ER 等价定位、真实个人 App 测试、全设备回归、吞吐与长期稳定性、所有辅助工具费用为零的保证。
