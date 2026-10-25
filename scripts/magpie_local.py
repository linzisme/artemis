"""Local Magpie Responses entrypoint, sharing the existing Artemis runtime data."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.codex_local import configure_local_runtime


def configure_magpie_runtime() -> Path:
    # Only this process talks to loopback; Magpie manages its upstream transport.
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(name, None)
    return configure_local_runtime(
        profile_name="magpie-sol.jsonc",
        provider_environment={
            "OPENAI_API_KEY": "magpie",
            "OPENAI_BASE_URL": "http://127.0.0.1:3425/v1",
        },
    )


def main() -> None:
    configure_magpie_runtime()
    from artemis.interfaces.cli.main import cli

    cli()


if __name__ == "__main__":
    main()
