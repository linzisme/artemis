# 使用 Codex CLI 的 ChatGPT 订阅

`codex` 是独立 provider，原 `openai` 等 API provider 保持兼容。它调用官方 CLI，不把网页登录令牌转换成 API Key，也不连接第三方订阅代理。

本机迁移到 Projects 后的已实测命令、服务状态和证据位置，见[本地使用与迁移](codex-local-usage.md)。

## 启动

先自行安装官方 Codex CLI 并登录 ChatGPT。本次按 CLI **0.164.0** 验证；旧版本缺少 flags 时会失败，不降级到 API。

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

当前订阅配置为 `gpt-6.1-sol`、reasoning effort `medium`、service tier `fast`，实际可用模型以自己的 Codex CLI 为准。修改配置时同时修改 `default.model`、同 provider fallback、Flash step summarizer 与 memory chunking 模型；主模型与 fallback 均显式设置 `reasoning_effort` 和 `service_tier`。探针可用 `--model`、`--reasoning-effort`、`--service-tier` 覆盖默认值。未登录、额度耗尽、模型不可用、请求超时均报错；不会创建新登录或跨到 API provider。

适配器使用 `--ignore-user-config`，因此 Fast 必须由 ARTEMIS 配置显式传入 CLI；不能依赖全局 `config.toml`。[官方配置说明](https://learn.chatgpt.com/docs/config-file/config-reference)将 `service_tier="fast"` 映射为请求值 `priority`。响应 metadata 中的 `requested_service_tier` 记录请求档位，不作为服务端实际处理档位或加速倍数的独立证明。

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

## Astra / Medium 验收（2026-10-12）

已将订阅 profile 的主模型、同 provider fallback、Flash step summarizer 和 memory chunking 统一为 `gpt-6-astra`，`default.reasoning_effort=medium`。探针默认值同步更新，并支持 `--reasoning-effort`。

- 真实订阅探针：6/6 通过。覆盖文本、合成蓝色方块图片、双工具调用顺序及参数、工具结果续接、Checker 结构化输出、Explorer 图片坐标 `[500, 500]`。工具收据是明确标记的 dry-run fixture，不代表设备动作。
- 设备 smoke：台北时间 10:00:46–10:02:01，专用 `artemis_api36` / `emulator-5580`，Flash，session `da45cf18-e3ee-4841-b68c-20f0a38004e4`。真实 CLI 完成 Settings→Battery，exit 0。独立 ADB XML、截图、系统电量及设置快照核验通过：Battery / 100%，前台为 Settings，未见 crash/ANR 对话框，system/secure/global 设置未变。
- 调用审计：探针 6 次、设备 7 次，全部成功；请求均为 `gpt-6-astra` / `medium`，实际调用原 `CodexChatModel`。模型工厂护栏拒绝其它 provider、模型或 effort；官方 CLI 测试前后均为 `Logged in using ChatGPT`，未使用付费 API fallback。响应 metadata 由适配层生成，不是服务端独立模型证明。
- 相关回归：`test_codex_provider.py`、`test_codex_local_cli.py` 共 23 passed；探针 Ruff lint/format、diff check 通过。额外直接 Pyright 检查探针有 2 项既有类型错误（`SimpleNamespace` 与 `ArtemisContext`、`dict` 与 `State`），用 HEAD 原文件复核一致，新增 0。未重跑完整测试集或 Pro strict。

本机原始证据：仓内 `.local-validation/astra-medium-20261012/`（Git 忽略）。`probe-result.json` 保存探针断言结果，`probe-calls.jsonl` / `cli-calls.jsonl` 保存调用审计；`device/verification.json`、`settings-diff.json`、`final-screen.png`、`console.log` 保存设备验收。`runner.py` 仅添加调用审计和配置护栏，实际使用本地 launcher 的配置初始化及原 CLI。`smoke.py` 保存独立设备核验流程。

复现探针：`PYTHONPATH=. .venv/bin/python scripts/probe_codex_subscription.py --live --model gpt-6-astra --reasoning-effort medium`。设备入口仍使用[本地运行说明](codex-local-usage.md)中的 `scripts/codex_local.py run`。

结论仅覆盖此次图片/工具协议探针与 Flash Settings→Battery smoke，不代表其它 App、Pro strict 或长期稳定性已完成 Astra 验收。

## Astra / Medium Pro strict 补充验收（2026-10-12）

台北时间 11:13:10–11:17:22，使用同一专用 `artemis_api36` / `emulator-5580`，真实 CLI `--profile pro --verification-level strict` 执行 Settings→Battery。session：`0ab08b23-cfd4-40ef-83e2-7a014fbda407`。任务执行约 243 秒，含外部核验全程约 252 秒。

- `task-result.json` 和 `run_outcome.json` 均为 completed；最终 **4 passed / 0 failed / 0 inconclusive / 0 unchecked / 0 retired**。
- `check_ledger.jsonl`：中途 checkpoint 2 项 passed，最终审查 4 项 passed。共 6 条判定，其中包含重复审查同一检查项，不能记为 6 个独立测试通过。覆盖 Battery 标题和数字电量、观察笔记中的电量与 crash/ANR 状态、最终停留 Battery、仅导航 Settings 且不修改设置。
- 15 次真实模型调用全部成功；主模型与预创建 fallback 均通过 `gpt-6-astra` / `medium` / `codex` 护栏，实际调用原适配器；含 Planner、Operator、摘要和 Checker 的观察工具循环。测试前后官方 CLI 均为 ChatGPT 登录，未回退付费 API。调用 metadata 仍为适配器标记，不是服务端独立模型证明。
- 独立 ADB 核验通过：前台 Settings，Battery / 100% 与系统电量一致，未见 crash/ANR 对话框，system/secure/global 设置前后完全一致。

首轮被审计护栏在 Planner 创建 fallback 时拦截：fallback 仅填写 provider/model，未显式指定 reasoning effort，解析值为 `None`；该轮未发起真实模型调用。现已在 `config/codex-chatgpt.jsonc` 的 fallback 中补齐 `reasoning_effort: medium`，保留原始失败证据，再运行上述成功验收。未放宽护栏或替换模型结果。

本机证据（仓内，Git 忽略）：成功轮 `.local-validation/astra-medium-pro-20261012-retry/`，首轮 `.local-validation/astra-medium-pro-20261012/`。成功轮根目录包含 `task-result.json`、`cli-calls.jsonl`；`device/` 包含独立核验、前后截图和设置快照；Checker ledger、streams、outcome 位于 `cli-state/traces/0ab08b23-cfd4-40ef-83e2-7a014fbda407/`。

本次确认该 Settings→Battery 场景的 Pro strict 中途检查和最终审查可通过 Codex 订阅完成；不外推到其它 App、复杂业务或长期稳定性。未重跑完整回归集。
