# Projects 本地订阅版：使用与迁移

## 本次需求与验收依据

2026-10-12 用户要求迁移到 `Projects/artemis-codex`，保留 `Projects/artemis` 及其未提交修改，保留回退，避免依赖临时任务目录；核实 CLI／SDK／MCP、服务状态，并从新目录运行专用模拟器 Settings→Battery smoke。不得新增常驻服务、开机启动、全局安装、客户端配置或持久授权。本节记录当前请求，不是补造历史 spec/ticket。迁移前基线为 `61ab5921fbd3f0ec5d1b4509fdc89b3274680a01`。

已读取本机 MATT `ask-matt`、`implement`、`tdd`、`code-review`。范围适合单会话 implement，已有明确的公共验收边界：启动／导入、相关回归、真实 CLI smoke。按本次用户要求不创建历史 tickets 或重配 tracker；Review 使用迁移前提交和本节需求。

## 安装状态与日常使用

这是可按需运行的本地订阅版，不是常驻 Web 服务。CLI 与 SDK 可运行；MCP stdio 已验证 initialize/list_tools，未接入任何客户端，也未经 MCP 调度真实设备任务。不要将旧版的 `127.0.0.1:8000` 当成此订阅分支：该端口由原 `Projects/artemis` 服务使用，本次未替换或重启它。

从工作根进入 `Projects/artemis-codex`。前提：官方 `codex` 和 `adb` 在 PATH，`codex login status` 显示 ChatGPT 登录；目标设备已授权且 Artemis Helper 已安装、启用、可连接。本机专用 AVD 为 `artemis_api36` / `emulator-5580`，本次检查时已运行。设备关闭时可自行启动已有 AVD；本脚本不安装系统软件或 helper，不修复权限。

```bash
cd /Users/lin/macdata/linz/Projects/artemis-codex
codex login status
adb devices -l
.venv/bin/python scripts/codex_local.py --version
.venv/bin/python scripts/codex_local.py run --help

# 已实测的入口；真实消耗 ChatGPT 订阅额度，且会导航指定测试设备。
.venv/bin/python scripts/codex_local.py run \
  'Open Android Settings, navigate to Battery. Verify the title and percentage; report crash/ANR status. Leave the device there without changing settings.' \
  --profile flash --device-serial emulator-5580 \
  --without-video-recording-tools --test-name settings-battery

# 需要严格断言时，把上一命令的 profile 与验证参数改为：
# --profile pro --verification-level strict

# MCP stdio 按需入口；启动后等待 MCP 客户端协议输入，Ctrl+C 退出。
.venv/bin/python scripts/codex_local.py mcp --transport stdio
```

`codex_local.py` 通过自身路径定位仓库，调用原 ARTEMIS CLI，并强制 standalone，避免 `run` 自动创建 daemon。它选定 `config/codex-chatgpt.jsonc`，清除当前子进程的 API Key 环境变量，保留系统 CLI 管理的 ChatGPT 登录；不读取或复制登录凭据。模型未登录、无额度、不可用或超时会失败，不回退付费 API。

项目运行数据、配置缓存及轨迹位于 `.codex-artemis-state/`（Git 忽略），不复用旧部署的数据目录。可在启动前设置 `ARTEMIS_CODEX_STATE_DIR` 指定另一独立目录。迁移入口关闭自动保持唤醒、自动安装和 helper 权限修复；必须先有正常运行的已授权 helper。视频录制在日常示例和验收中关闭。

SDK 复用相同的配置入口（在其它 ARTEMIS 导入前执行）：

```python
import asyncio
from scripts.codex_local import configure_local_runtime

configure_local_runtime()
from artemis.interfaces.cli.commands.run import execute_task

asyncio.run(
    execute_task(
        "Open Settings and view Battery; do not change any settings.",
        device_serial="emulator-5580",
        profile="flash",
        video_recording_tools_enabled=False,
        test_name="sdk-settings-battery",
    )
)
```

不要通过 `start.sh` 代替以上订阅入口：上游一键脚本还包含工具安装和全局 MCP 配置流程，不属于本次迁移范围。`ui/restart/init` 等显式管理命令仍是上游功能，不属于本次日常运行验证。

## 迁移内容与回退

目标保存完整源码、配置、Git 历史和开发分支；原 `.venv` 不复制，在目标使用 `uv sync --locked --dev --offline --python 3.12` 重建，191 个已锁定依赖来自本机缓存。Artemis 与 artemis-client editable install 指向 Projects；入口脚本、activate 和 editable 元数据未发现旧任务路径依赖。Python 3.12.15 来自既有系统 uv Python，不依赖任务目录。

原源码与环境仍保留于 `Codex/2026-10-11/task-2/artemis-codex`，作为迁移前回退副本，不是后续开发位置。原 `Projects/artemis` 完全保留。未迁移 `.env` 或 Codex 登录凭据。

旧验收证据已复制到新仓 `.local-validation/e2e-20261011/`，包括截图、轨迹数据库、模型来源和原始报告。历史日志保留原绝对路径作为当时证据；本地 `run_settings.py` 的运行路径已调整为新仓相对位置。新迁移证据位于 `.local-validation/migration-smoke-20261012/`；这些原始数据不提交公开仓库。

## 验证记录

- 新启动入口测试先失败（脚本不存在），实现后跨工作目录 `--help` 通过。
- CLI version/help、SDK 与 editable 路径检查通过。
- 相关回归 48 passed；MCP stdio 初始化和 5 个工具发现通过。
- 完整隔离回归：2461 passed、89 failed、7 skipped、8 deselected。相对原基线没有新增失败，仍非全绿；本次在获准访问 Projects 的宿主环境运行，不能直接将计数差异当成代码修复收益。
- 新目录实际 CLI Flash smoke：2026-10-12 01:34:29–01:35:43 UTC，exit 0；Battery / 100% 与系统电量一致，无 crash/ANR，三类设置完全未变。7 次实际模型调用（4 次导航决策、3 次摘要），来源为 Codex 订阅；未再重复昨日 Pro 验收。
- lint、格式及核心类型检查通过。MATT 双轴复核结论将在完成后追加。
