"""Stop this checkout's Streamlit UI on the requested port (Linux/Windows)."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def ui_port(args):
    for index, arg in enumerate(args):
        if arg.startswith('--server.port='):
            return arg.split('=', 1)[1]
        if arg == '--server.port' and index + 1 < len(args):
            return args[index + 1]
    return '8501'


def linux_stop(port):
    stopped = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / 'cwd').resolve() != ROOT:
                continue
            args = (entry / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
            if not any(args[i:i + 3] == ['streamlit', 'run', 'app.py'] for i in range(len(args))):
                continue
            if ui_port(args) != str(port):
                continue
            os.kill(int(entry.name), signal.SIGTERM)
            stopped.append(entry)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    deadline = time.monotonic() + 5
    for entry in stopped:
        while entry.exists() and time.monotonic() < deadline:
            try:
                if not (entry / 'cmdline').read_bytes():
                    break  # Already exited; parent has not reaped it yet.
            except FileNotFoundError:
                break
            time.sleep(.1)
        else:
            if entry.exists():
                raise RuntimeError(f'UI PID {entry.name} has not stopped after SIGTERM')
    print(f'Stopped {len(stopped)} UI process(es) on port {port}.' if stopped else f'No project UI running on port {port}.')


def windows_stop(port):
    # Environment values avoid interpolating filesystem paths into PowerShell code.
    command = r'''
$ErrorActionPreference = 'Stop'
$expected = [IO.Path]::GetFullPath((Join-Path $env:MONEY_GRAPH_ROOT '.venv\Scripts\python.exe'))
$owners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -eq [int]$env:MONEY_GRAPH_PORT } |
    Select-Object -ExpandProperty OwningProcess -Unique)
$count = 0
foreach ($owner in $owners) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $owner"
    if ($process.ExecutablePath -eq $expected -and
        $process.CommandLine -match '(?i)\bstreamlit\s+run\s+"?app\.py"?(\s|$)') {
        Stop-Process -Id $owner -ErrorAction Stop
        $count++
    }
}
Write-Output "Stopped $count project UI process(es) on port $env:MONEY_GRAPH_PORT."
'''
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                   env={**os.environ, 'MONEY_GRAPH_ROOT': str(ROOT), 'MONEY_GRAPH_PORT': str(port)}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8502)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if sys.platform == 'win32':
        windows_stop(args.port)
    elif sys.platform.startswith('linux'):
        linux_stop(args.port)
    else:
        parser.exit(1, 'Use Ctrl+C in the UI terminal on this platform.\n')


if __name__ == '__main__':
    main()
