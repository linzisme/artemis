"""Project-local Codex subscription launcher; no daemon or global setup required."""

from functools import wraps
import os
from pathlib import Path
import sys


def configure_local_runtime() -> Path:
    """Configure this checkout before importing the CLI or SDK."""
    root = Path(__file__).resolve().parents[1]
    state = Path(os.environ.get("ARTEMIS_CODEX_STATE_DIR", root / ".codex-artemis-state")).resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    sys.path.insert(0, str(root))
    for name in list(os.environ):
        if name.endswith("API_KEY") or name in {"OPENAI_BASE_URL", "ARTEMIS_FAKE_LLM"}:
            os.environ.pop(name, None)
    os.environ.update(
        ARTEMIS_APP_DIR=str(state),
        ARTEMIS_TRACES_DIR=str(state / "traces"),
        ARTEMIS_ARTEMIS_JSONC=str(root / "config/codex-chatgpt.jsonc"),
        ARTEMIS_STANDALONE="1",
        ARTEMIS_KEEP_DEVICE_AWAKE="false",
        ARTEMIS_HELPER_AUTO_INSTALL="false",
        ARTEMIS_HIERARCHY_BACKEND="helper",
    )
    for name in ("NO_PROXY", "no_proxy"):
        os.environ[name] = ",".join(
            filter(None, [os.environ.get(name, ""), "127.0.0.1", "localhost", "::1"])
        )
    from artemis.platform import platform

    # Upstream's macOS config/cache paths do not all honor ARTEMIS_APP_DIR.
    platform.paths._home = state / "pal-home"
    from artemis.runtime.helper_manager import AccessibilityHelperManager

    original_attach = AccessibilityHelperManager.attach

    @wraps(original_attach)
    def attach_existing(self, serial, **kwargs):
        # Connect to an already authorized helper; never install, enable or repair it.
        return original_attach(self, serial, provision=False, revive=False)

    AccessibilityHelperManager.attach = attach_existing
    return root


def main() -> None:
    configure_local_runtime()
    from artemis.interfaces.cli.main import cli

    cli()


if __name__ == "__main__":
    main()
