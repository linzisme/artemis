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

文本轮统计：input 10684、output 24、total 10708 tokens。统计包含 CLI 提示开销，不表示 API 费用。每轮进程启动和完整历史重传意味着它不应被期待具有上游 Flash 宣传的 3–5 秒延迟。

测试目标仅为合成图片及显式标注的本地协议回执。未实际点击设备；Operator 验证的是绑定与回传接口，Explorer 验证了通用循环。完整手机 Flash／Pro、多步 GUI、原生视频、长期稳定性未验证。

## 离线验证

`uv sync --locked --dev --python 3.12` 创建了独立 `.venv`，未修改旧部署虚拟环境。锁文件只增加 jsonschema 显式依赖及 uv lock 格式版本。

- `ARTEMIS_APP_DIR=/tmp/artemis-codex-tests .venv/bin/python -m pytest tests/unit/test_codex_provider.py -q`：19 passed。
- `.venv/bin/ruff check`／`ruff format --check`：通过。
- `.venv/bin/python scripts/quality_ratchet.py`：通过。
- `.venv/bin/pyright --project pyright-core.json`：0 errors。
- `.venv/bin/pyright --pythonpath .venv/bin/python artemis/llm/codex.py`：0 errors。

完整测试首次因上游配置初始化试图创建个人 Application Support 目录而收集失败；后续使用临时 `ARTEMIS_APP_DIR` 和测试内 PAL home 隔离运行，不改系统 HOME 或真实个人 App 数据。完整回归和未修改基线对照结果将在完成后补充。

## MATT 工作流与权限边界

本机发现并读取了 `ask-matt`、`implement`、`tdd`、`code-review` 技能，使用本地规格与固定基线进行实现和 Review。尝试通过电脑操作工具访问 ChatGPT App 时，工具返回禁止访问 `com.openai.codex`。未绕过限制，也未声称在 App UI 内完成工作流；实际工作由本地执行会话完成。

未向上游提 PR、合并或部署。没有读取／复制／输出 Codex 登录凭据。
