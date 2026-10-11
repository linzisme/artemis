# Codex 订阅验证记录

日期：2026-10-11。基线 `351ca8422f7b5b54e80a9c1ce03a222e02415b6b`。macOS arm64，Python 3.12.15，官方 Codex CLI 0.161.0，GitHub CLI 确认个人 fork 所有者 `linzisme`。

## 真实订阅调用

CLI `login status` 返回 `Logged in using ChatGPT`。首次用旧模型 `gpt-5.4` 试探时被官方明确拒绝；随后依据 CLI 返回的可用模型目录选择 `gpt-6.1-sol`。未使用 API Key、第三方代理协议或从 auth 文件提取令牌。

执行命令（本机独立环境；最初探针使用旧部署的 Python 依赖，导入的新源码路径已核实）：

```bash
ARTEMIS_APP_DIR=/tmp/artemis-codex-live PYTHONPATH=. \
  .venv/bin/python scripts/probe_codex_subscription.py --live
```

| 真实测试 | 结果 | 秒 |
|---|---|---:|
| 文本 | 精确返回 `ARTEMIS_CODEX_OK` | 12.33 |
| 图片识别 | 合成白底蓝色方块识别为 `blue` | 10.50 |
| Operator action schema | `click(target=2)` 后 `press_key(key=BACK)`，通过真实 action 声明校验 | 11.89 |
| 工具结果续接 | 读取两条协议测试回执，返回 `DRY_RUN_ACCEPTED`，未再次发动作 | 9.74 |
| Checker schema | 返回合法 `CheckReport` 与 `passed` 断言 | 11.61 |
| Explorer 通用循环 | 实际 `_run_universal` 调用 `submit_answer`，坐标 `[501,501]`，目标 `[500,500]` | 13.26 |

另在新 `.venv` 中做了一次真实图片历史验证：先传合成蓝图，再经 ARTEMIS `tool_result_messages` 传回合成红图，模型精确返回 `blue red`，响应 metadata 为 `provider=codex, auth=chatgpt`。这验证了工具返回新截图与旧截图的顺序，仍无设备动作。

文本轮统计：input 10684、output 24、total 10708 tokens。统计包含 CLI 提示开销，不表示 API 费用。每轮进程启动和完整历史重传意味着它不应被期待具有上游 Flash 宣传的 3–5 秒延迟。

测试目标仅为合成图片及显式标注的本地协议回执。未实际点击设备；Operator 验证的是绑定与回传接口，Explorer 验证了通用循环。完整手机 Flash／Pro、多步 GUI、原生视频、长期稳定性未验证。

## 离线验证

`uv sync --locked --dev --python 3.12` 创建了独立 `.venv`，未修改旧部署虚拟环境。锁文件只增加 jsonschema 显式依赖及 uv lock 格式版本。

- `ARTEMIS_APP_DIR=/tmp/artemis-codex-tests .venv/bin/python -m pytest tests/unit/test_codex_provider.py -q`：22 passed。
- 模型网关／可靠性／结构化输出、记忆 chunking、Explorer、Checker、action schema 和 provider 集中回归：143 passed。
- `.venv/bin/ruff check`／`ruff format --check`：通过。
- `.venv/bin/python scripts/quality_ratchet.py`：通过。
- `.venv/bin/pyright --project pyright-core.json`：0 errors。
- `.venv/bin/pyright --pythonpath .venv/bin/python artemis/llm/codex.py`：0 errors。

完整测试首次因上游配置初始化试图创建个人 Application Support 目录而收集失败；后续使用临时 `ARTEMIS_APP_DIR` 和测试内 PAL home 隔离运行，不改系统 HOME 或真实个人 App 数据。第一次完整对照：开发分支 2451 passed / 91 failed，原基线 2431 passed / 91 failed；失败用例集合完全一致。既有失败包括无 API key 的客户端构造、本机 SOCKS 依赖、平台路径权限以及旧测试接口差异，未宣称整个项目全绿。

MATT Review 后新增 API 兼容测试的第一次整套运行多出三项失败，原因是测试构造 Google 客户端继承本机 SOCKS 代理而缺少 socksio；已用测试内 monkeypatch 清除代理变量解决，不安装额外包、不改变系统代理。对应 22 项 provider 测试与 143 项集中回归均通过。

最终完整回归（包含 Review 修复）：**2454 passed、91 failed、11 skipped、8 deselected**，耗时 79.54 秒。逐项比较失败用例 ID，和原基线的 91 项完全相同，新增失败 0、消失失败 0。基线为 **2431 passed、91 failed、11 skipped、8 deselected**。完整测试仍以 exit 1 结束，因此不能视为全绿。

真实 CLI 登录 readiness 和示例配置加载也通过：所有主模型与 fallback 的 provider 均为 `codex`；Flash、记忆、Explorer 与视频开关符合订阅配置。此检查没有访问设备或发起 API 调用。

隔离完整回归采用以下等价测试入口（即 `make test` 的默认 pytest 集合）：

```python
import os
import tempfile
from pathlib import Path
import pytest

with tempfile.TemporaryDirectory(prefix="artemis-test-home-") as directory:
    os.environ["ARTEMIS_APP_DIR"] = str(Path(directory) / "app")
    from artemis.platform import platform

    platform.paths._home = Path(directory)
    raise SystemExit(pytest.main(["-q"]))
```

需在仓根以 `PYTHONPATH=. .venv/bin/python <上述脚本>` 执行。基线副本用同一 Python、同一入口和相同环境比较失败用例集合。

## MATT Review

Standards：硬性违规 0，低优先级建议 1——示例模型在 default、fallback、step summarizer、chunking 四处配置，切换时需同步。为保留原节点配置格式，本次保留显式配置并在使用说明提醒。

Spec：发现并修复 1 项 P2——非 Codex 的后台记忆应继续使用原 Google API 路由；现仅 Codex 改走订阅，原 Google／OpenAI／Anthropic 配置回归通过。独立复核确认该问题已关闭。另保护了延迟加载配置时的 Flash／summarizer 路由，避免失败后进入 Google fallback。

## MATT 工作流与权限边界

本机发现并读取了 `ask-matt`、`implement`、`tdd`、`code-review` 技能，使用本地规格与固定基线进行实现和 Review。尝试通过电脑操作工具访问 ChatGPT App 时，工具返回禁止访问 `com.openai.codex`。未绕过限制，也未声称在 App UI 内完成工作流；实际工作由本地执行会话完成。

未向上游提 PR、合并或部署。没有读取／复制／输出 Codex 登录凭据。
