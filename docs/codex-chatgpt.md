# 使用 Codex CLI 的 ChatGPT 订阅

`codex` 是独立 provider，原 `openai` 等 API provider 保持兼容。它调用官方 CLI，不把网页登录令牌转换成 API Key，也不连接第三方订阅代理。

本机迁移到 Projects 后的已实测命令、服务状态和证据位置，见[本地使用与迁移](codex-local-usage.md)。

## 启动

先自行安装官方 Codex CLI 并登录 ChatGPT。本实现按 CLI **0.161.0** 的 flags 验证；旧版本缺少 flags 时会失败，不降级到 API。

```bash
codex --version
codex login status  # 应为 Logged in using ChatGPT
uv sync --locked --dev --python 3.12

# 使用独立 ARTEMIS 数据目录，避免影响原有部署。
export ARTEMIS_APP_DIR="$PWD/.codex-artemis-state"
export ARTEMIS_ARTEMIS_JSONC="$PWD/config/codex-chatgpt.jsonc"

# 明确消耗订阅额度；只使用合成图片，不连接 Android 或操作个人 App。
PYTHONPATH=. uv run python scripts/probe_codex_subscription.py --live

# 完成自己测试设备的准备后，明确指定目标 serial 再执行任务。
# uv run artemis run '你的测试任务' --profile pro --device-serial <测试设备serial>
```

示例模型为 `gpt-6.1-sol`，实际可用模型以自己的 Codex CLI 为准。修改配置时同时修改 `default.model`、同 provider fallback、Flash step summarizer 与 memory chunking 模型。未登录、额度耗尽、模型不可用、请求超时均报错；不会创建新登录或跨到 API provider。

## 适配边界

| 功能 | 行为 |
|---|---|
| 文本／截图 | 原角色、消息顺序、工具 ID 保留；图片转为临时附件，按顺序标记。每轮完整重传历史。 |
| ARTEMIS 工具 | CLI 按外层 JSON Schema 生成动作列表，逐项验证原工具 schema 后转成 `AIMessage.tool_calls`。动作仍经过 ARTEMIS 原执行器的语义与设备检查。 |
| 结构化输出 | 外层要求 JSON，内层 JSON 字符串再经原 schema／Pydantic 校验。无效结果拒绝，不将普通文本伪装为成功。 |
| 流式输出 | 只返回完整最终消息；不模拟 token 流或暴露推理过程。 |
| 错误 | 仅返回清理过的错误信息，不记录 CLI 原始 stderr、令牌或完整请求。订阅错误为终止错误，不自动重试／API fallback。 |
| 多模态限制 | 仅内联 PNG/JPEG/WebP，最多 32 张、总计 40 MB、单张最多 25 MP。URL、音频、原生视频／文件块明确拒绝。 |
| 感知 | 示例使用 Explorer 通用路径，关闭 Gemini cache、OCR／专用 detection／image processing 工具。截图定位不等于 Robotics ER 定位精度。 |
| 视频 | 示例关闭录屏分析；未验收通用关键帧视频路径。 |
| 用量 | CLI 返回的 token 统计用于遥测，包含 CLI 指令开销；不代表 API 账单价格或剩余订阅额度。 |

CLI 运行使用独立临时目录、只读沙箱、无 daemon、ephemeral 模式，并禁用用户配置以及 shell／插件／电脑等能力。保留 CLI 自己管理的登录位置；ARTEMIS 从不打开 auth 文件。临时图片权限为 `0600`，调用结束或取消时删除；CLI 仍可能保留其自身诊断或用量记录，`ephemeral` 不承诺整个 CLI 完全不写磁盘。

订阅 profile 必须整体使用 `codex` 节点，不能从默认配置局部只改 operator；原默认配置中的 Google 辅助节点、API 工具不因此自动变成订阅能力。不要把真实凭据或私有部署 `.env` 加入仓库。

## 验证

```bash
uv run pytest tests/unit/test_codex_provider.py -q
make test
make lint
make typecheck
```

离线 CLI 替身用于错误与清理测试，和 `--live` 的真实订阅验收明确分开。真实探针覆盖 Operator 的动作声明／返回接口、Explorer 的实际通用循环与 Checker schema，不声称完整 Flash／Pro 手机流程通过。

另已完成专用 Android 模拟器 Settings→Battery 的真实 Flash 与 Pro strict 流程，包含独立 ADB、电量及设置未变核验；结果与范围见[端到端验收记录](codex-e2e-validation.md)。这不代表其它 App 或长期稳定性已经通过。

官方依据：[认证](https://learn.chatgpt.com/docs/auth)、[非交互调用和结构化输出](https://learn.chatgpt.com/docs/non-interactive-mode)、[CLI 配置](https://learn.chatgpt.com/docs/config-file/config-reference)。App Server 动态工具仍是实验接口，本实现不依赖它。
