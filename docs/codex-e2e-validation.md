# Codex 专用模拟器端到端验收

2026-10-11，在 Mac Studio `CQCA202310645.local` 上完成。使用原有 Android 16 AVD `artemis_api36`（`emulator-5580`），官方 Codex CLI 0.161.0、ChatGPT 登录、`gpt-6.1-sol`，配置为 `config/codex-chatgpt.jsonc`。基于 `2956d66` 加本次启动兼容修复运行。

## 实际结果

| 路径 | UTC 时间 | 结果 | 真实订阅调用 |
|---|---|---|---:|
| Flash | 15:30:50–15:32:01 | completed；独立 ADB 验收通过 | 7 次，全部成功 |
| Pro strict | 15:32:17–15:37:20 | completed；6 passed / 0 failed / 0 inconclusive / 0 unchecked；独立 ADB 验收通过 | 20 次，全部成功 |

两次都从桌面开始，由本项目真实 `execute_task` 入口分别运行 FlashRunner 和 Pro graph，打开 Settings、滚动、点击 Battery。最终截图显示 Battery / 100%，ADB 前台为 `com.android.settings/.SubSettings`，显示电量与 `dumpsys battery` 一致，无 crash/ANR 对话框。运行前后 `settings list system/secure/global` 逐键对比完全相同。Flash 记录 4 步，Pro 记录 6 步（含等待／观察步骤）。Flash 没有 Pro Checker，不能将其独立 ADB 通过写成 Checker 通过。

Pro 的最终六项检查覆盖：观察笔记与截图证据、Battery 标题与数字电量、报告电量与 crash/ANR 状态、保留最终 Battery 页面、未修改设置、仅在 Settings 导航。检查点和最终判定均经真实模型调用，未以替身替代。

模型调用审计在实际 `CodexChatModel._agenerate` 外记录时间、来源、返回工具动作与用量，仍调用原始实现。27 次成功调用均为 `provider=codex, auth=chatgpt, model=gpt-6.1-sol`，包含导航、步骤摘要、Pro 规划、观察工具循环和 Checker。Flash 统计 141487 tokens，Pro 413721 tokens（含 CLI 指令及重复历史，不能视作 API 费用）。执行前与执行后官方 CLI 均确认 ChatGPT 登录。模型工厂护栏拒绝非 Codex provider，进程清除 API Key 环境变量；没有付费 API 回退。

## 首轮失败与修复

首轮 Flash（15:28:51–15:30:18 UTC）正确生成 `manage_app`，但原 Monkey 命令未打开 Settings，重试仍停在桌面；该轮主动取消并完整保留失败证据，设置未改变。旧部署也记录过无实体系统按键的同一模拟器问题。本分支给 launch-only Monkey 添加 `--pct-syskeys 0`，避免默认随机系统按键校验；修复后两条路径均一次成功启动 Settings。增加了 driver 命令回归断言，未修改原部署。

独立核验脚本最初仅读取 XML 的 `text`，将标题误报为 false；实际 `collapsing_toolbar` 的 `content-desc` 是 `Battery`。原始 XML、截图和 `verification.json` 均保留，`verification-reviewed.json` 使用 text/content-desc 重新计算并通过，未改变原始证据或补做设备导航。

## 证据与复现

本机证据根（相对工作根）：`Codex/2026-10-11/task-2/e2e-evidence/`。

- Flash 成功：`20261011T153048Z-flash/`；session `79ad18c5-56b3-4919-8444-16fafa4a4282`。
- Pro 成功：`20261011T153215Z-pro/`；session `a8061791-64d4-4819-bc19-45590d1fd9bb`。
- 首轮失败：`20261011T152849Z-flash/`。
- 各成功目录包含 `verification-reviewed.json`、原始 `verification.json`、`task-result.json`、前后截图、`hierarchy.xml`、`activity.txt`、`battery.txt`、三类 settings 快照与 diff、`model-calls.jsonl`、`actions.json`。
- `traces/data_engine.db` 和 `traces/images/` 保存实际轨迹／截图；Pro session 子目录保存 `check_ledger.jsonl`、`check_streams.jsonl`、`run_outcome.json`。
- 根目录保存 `execution-context.json`、`tested-source.patch`、三次 console 日志、测试日志及本地验收脚本。原始设备数据留在本地，没有上传到公开 fork。

本地复现命令（从本次开发仓根运行；会消耗订阅额度并导航该专用模拟器）：

```bash
.venv/bin/python ../e2e-evidence/run_settings.py flash
.venv/bin/python ../e2e-evidence/run_settings.py pro
# 对各次输出目录运行 review_evidence.py 和 export_actions.py。
```

验收脚本只允许既有 helper attach，禁止自动安装、权限修复与 backend fallback；关闭保持唤醒的设置修改和视频录制；使用独立数据目录并隔离 PAL home。未改系统 HOME、持久登录、原部署或个人 App 数据。没有新安装或新增授权。

## 修复后回归

- `ARTEMIS_APP_DIR=/tmp/artemis-codex-tests .venv/bin/python -m pytest tests/unit/test_drivers.py tests/unit/utils/test_app_launch_utils.py tests/unit/test_codex_provider.py -q`：47 passed。
- 按原验收文档的临时 PAL home 入口运行完整 pytest：2454 passed、91 failed、11 skipped、8 deselected。与上游基线失败用例集合完全一致，新增失败 0；整套测试仍为 exit 1。
- `ruff check`、`ruff format --check`、`scripts/quality_ratchet.py`、`pyright --project pyright-core.json`：通过。

## 限制

这是单一专用 Android 模拟器 Settings→Battery 的真实端到端验收。未覆盖个人 App、其它设备、复杂业务、长期稳定性、原生视频或音频；视频录制关闭，交付截图与动作轨迹。CLI 完整历史重传和多轮检查使本次 Pro 用时约五分钟，不具备原生 Flash API 的低延迟。
