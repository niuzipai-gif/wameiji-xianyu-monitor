import subprocess
import sys


def test_cli_web_command_is_discoverable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cd_monitor.cli", "web", "--help"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--static-dir" in result.stdout
