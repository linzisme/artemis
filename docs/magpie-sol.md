# Magpie Sol 通道试用（2026-10-25）

## 当前默认：Terra Medium（2026-10-27）

默认入口 `scripts/magpie_local.py` 改为加载 `config/magpie-terra.jsonc`：通过 Magpie `http://127.0.0.1:3425/v1/responses` 使用 `codex/gpt-6.1-terra`，主角色、辅助角色、视觉摘要、memory 和 fallback 均设为 `reasoning.effort=medium`。保留原运行数据目录，不请求额外的 `service_tier`。CA、WorkBuddy 和 Sol 配置保留，可显式加载。

推荐 Medium 作为当前界面回归的起点：任务以截图定位、按步骤执行、检查断言和整理观察结果为主，需要兼顾操作准确率与每轮等待时间。[官方指南](https://developers.openai.com/api/docs/guides/latest-model)建议 Terra 从 Medium 起步，仅在质量收益值得额外时间和 token 时提高等级；[Terra 模型说明](https://developers.openai.com/api/docs/models/gpt-6.1-terra)要求工具调用使用 Responses API。High 可用于后续困难定位、复杂断言或恢复流程的对照评估；目前没有真实设备 Medium/High 对照数据，不能声称 Medium 已被证明最优。

本机 Magpie 模型列表确认 `codex/gpt-6.1-terra` 支持 text/image、原生 `/v1/responses` 和 Medium。网关发布的 context window 为 500,000，与官方模型页的 1,050,000 不同；实际接入以网关限制为准。网关列出的推理等级也与官方不完全一致，本次只采用双方均支持的 Medium。

真实合成协议探针 9 项通过、1 项失败：文本、图片识别、逐步第二个工具调用、模拟工具回执续接、Checker、Hopper、Explorer 坐标、缓存首轮和缓存续接均通过；同一响应双工具调用只返回第一个，失败项保留，`all_checks_passed=false`、退出码 1。请求审计确认 10 次请求均为 Terra、Medium、Responses、`store=false`，未指定 service tier，模型响应为 `gpt-6.1-terra` / `default`。缓存追加轮 7,374 输入 token 中命中 7,168；该合成样本不能代替设备流程的命中率和延迟评估。证据：`.local-validation/terra-20261027/probe.json`。44 项相关单元测试及 Ruff、diff check 通过；未运行真实设备 E2E。

8000 服务切换前复核为 idle、queue 和 active_tasks 均为空，重启后 `/api/status` 确认模型为 `codex/gpt-6.1-terra`，仍为空闲、空队列；保留 `.codex-artemis-state/` 数据目录。切换记录：`.local-validation/terra-20261027/service-switch.json`。

复现 Terra 合成数据协议探针（不执行设备动作）：

```bash
.venv/bin/python scripts/probe_magpie_responses.py --live --profile magpie-terra.jsonc --output-dir .local-validation/terra-next-probe
```

## CA 与 Sol 历史记录

> 2026-10-25 更新：当时默认入口切到 `config/magpie-ca.jsonc`，使用 `ca/7670:deepseek-v4.1-flash`、Chat Completions。主模型、辅助角色、摘要、memory 和 fallback 均采用该模型；运行数据沿用原目录。下文为 Sol 配置和历史验证记录。Sol 探针默认显式加载 `magpie-sol.jsonc`，不受默认入口切换影响。
>
> CA 验证：真实文本、合成图片识别、工具调用、模拟工具回执续接及 Checker 均通过；65 项相关单元测试通过，Ruff 与 diff check 通过。没有执行设备动作，尚未进行设备 E2E。证据：`.local-validation/ca-20261025/`。
>
> 曾尝试 WorkBuddy，但账号登录失效（401 / refresh token 12153）；用户随后指定改用 CA。`magpie-workbuddy.jsonc` 保留为备选配置，当前不启用。

用户最新确认：改走 Magpie HTTP，继续使用同一 Sol 模型；允许 Magpie 的 Codex 通道继续使用其已登录账号。此切换替换 ARTEMIS 的逐次 Codex CLI 调用方式，不承诺绕开 ChatGPT 订阅额度。

## 当前配置

- 服务：`http://127.0.0.1:8000`。
- 模型入口：`http://127.0.0.1:3425/v1/responses`，`model=codex/gpt-6.1-sol`。
- ARTEMIS provider：`openai`，显式 `use_responses_api=true`。
- 请求参数：`reasoning.effort=medium`、`service_tier=priority`（Fast）、`stream=true`、`store=false`。
- 配置：`config/magpie-sol.jsonc`；入口：`scripts/magpie_local.py`。
- 运行数据继续使用 `.codex-artemis-state/`，原任务记录保留。
- 主角色、辅助角色、视觉摘要、memory 及 fallback 均使用该 Responses 配置；Flash 初始化失败会向上报错，不转到 Gemini。
- 本机请求使用 Magpie 的 loopback 占位 key；上游凭据由 Magpie 管理，未读取或复制其 token。

现有 `Projects/artemis/local/artemis.sh` 已改为调用本仓 `scripts/magpie_local.py`，其 `start.command` 也沿用该入口。切换前脚本备份在 `.local-validation/magpie-sol-20261025-capabilities/artemis.sh.before`。

从本仓启动服务（先确认端口空闲）：

```bash
.venv/bin/python -u -c 'from scripts.magpie_local import configure_magpie_runtime; configure_magpie_runtime(); from apps.admin_console.server import main; main(["--host", "127.0.0.1", "--port", "8000"])'
```

若需回退，先停止当前 8000，再使用 `docs/codex-local-usage.md` 的直接 Codex 启动命令；要恢复默认脚本则显式恢复上述备份。不要在有设备任务时重启。

## 验证与边界

真实协议探针通过 9 项、失败 1 项。通过项：文本、合成图片、逐步第二个工具调用、工具回执续接、Checker schema、Hopper schema、Explorer 坐标、缓存首轮、缓存追加轮。全部通过实际 Magpie `/v1/responses`，请求审计核对模型、Medium、priority、stream 和 store 参数。工具回执为明确标注的 dry-run fixture，没有执行设备动作。

缓存样本：固定的合成 reference 加原生消息历史；首轮输入 7,417 token、命中 3,968（53.5%），追加一轮后输入 7,438、命中 7,296（98.09%）。这证明该合成样本能复用追加历史，不外推为真实设备流程的命中率或速度提升倍数。

已知限制：

1. **Fast 实际档位未确认。** `priority` 和 `fast` 两种写法都能返回，但响应 `service_tier` 均为 `default`。配置保留 Fast 请求，不能声称上游实际采用 Fast。
2. **同一响应的双工具调用未通过。** 请求同一响应依次返回 click 和 press_key，只得到 click；显式启用 `parallel_tool_calls=true` 后仍如此。失败记录保留，探针总结果 `all_checks_passed=false`、退出码 1。独立的逐步 click → 模拟回执 → press_key → 模拟回执续接通过；ARTEMIS 当前逐步工具流程可用，但不能声称与原 CLI 批量工具行为完全等价。
3. 尚未完成设备 E2E，不以协议通过代替 App 验收。

初次接入验证：49 + 117 = 166 passed。后组最初有 18 项因旧 Google 构造路径缺少测试凭据而失败；仅在测试子进程设置不联网的占位 key 后，117 项全部通过。没有用该占位 key 发真实 Google 请求。Ruff 和 diff check 通过。

提交前 review 修正了探针的验收口径：超时或取消会记录未完成检查，只有所有必需检查均执行完且通过才可报告 `all_checks_passed=true`；缓存续接还必须实际报告有效缓存命中。新增回归覆盖取消、部分完成、非致命能力失败保留、零缓存和无效缓存计数，以及跨工作目录启动时覆盖继承的 provider/Fake LLM 设置。相关专项 56 passed；核心类型检查、完整 lint 和质量检查通过。额外检查的生产模块类型错误与 HEAD 对照一致，没有新增。

最终 `make test`：2507 passed、82 failed、11 skipped、8 deselected。隔离 HEAD 快照在相同环境下为 2494 passed、82 failed、11 skipped、8 deselected；82 个失败用例的集合完全一致，新增失败 0。完整测试仍未全绿，不能将基线对照结果表述为全量通过。对照证据保存在 `.local-validation/review-magpie-20261025/`。

证据目录：

- `.local-validation/magpie-sol-20261025/`：首次双工具探针失败。
- `.local-validation/magpie-sol-20261025-retry1/`：显式并行复测仍失败。
- `.local-validation/magpie-sol-20261025-capabilities/probe.json`：完整能力与缓存记录（保留失败项）。

复现探针：

```bash
.venv/bin/python scripts/probe_magpie_responses.py --live --output-dir .local-validation/magpie-sol-next-probe
```
