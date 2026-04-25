from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from aunic.image_inputs import is_supported_image_path


def pick_image_files() -> tuple[Path, ...]:
    system = platform.system()
    if system == "Darwin":
        return _pick_image_files_macos()
    if system == "Linux":
        return _pick_image_files_linux()
    if system == "Windows":
        return _pick_image_files_windows()
    raise RuntimeError("No supported native image picker is available on this system.")


def _pick_image_files_macos() -> tuple[Path, ...]:
    script = """
set chosenFiles to choose file with prompt "Select image attachments" with multiple selections allowed
set outputLines to {}
repeat with aFile in chosenFiles
    set end of outputLines to POSIX path of aFile
end repeat
set AppleScript's text item delimiters to linefeed
return outputLines as text
"""
    return _run_picker_command(["osascript", "-e", script])


def _pick_image_files_linux() -> tuple[Path, ...]:
    if shutil.which("zenity"):
        return _run_picker_command(
            [
                "zenity",
                "--file-selection",
                "--multiple",
                "--separator=\n",
                "--title=Select image attachments",
                "--file-filter=Images | *.png *.jpg *.jpeg *.webp *.gif",
            ]
        )
    if shutil.which("kdialog"):
        return _run_picker_command(
            [
                "kdialog",
                "--getopenfilename",
                str(Path.home()),
                "*.png *.jpg *.jpeg *.webp *.gif",
                "--multiple",
                "--separate-output",
            ]
        )
    raise RuntimeError("No supported native image picker is available. Install zenity or kdialog.")


def _pick_image_files_windows() -> tuple[Path, ...]:
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Filter = "Image files|*.png;*.jpg;*.jpeg;*.webp;*.gif"
$dialog.Multiselect = $true
$dialog.Title = "Select image attachments"
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  $dialog.FileNames -join "`n"
}
"""
    return _run_picker_command(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            script,
        ]
    )


def _run_picker_command(command: list[str]) -> tuple[Path, ...]:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stderr.strip():
            raise RuntimeError(result.stderr.strip())
        return ()
    paths = tuple(
        Path(line.strip()).expanduser().resolve()
        for line in result.stdout.splitlines()
        if line.strip()
    )
    supported = tuple(path for path in paths if is_supported_image_path(path))
    if paths and not supported:
        raise RuntimeError("The selected files are not supported image types.")
    return supported
