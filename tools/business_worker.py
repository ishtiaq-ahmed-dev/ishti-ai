"""Run Business Desk independently of chat/UI; opt-in Windows current-user supervision."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TASK_NAME = "ISHTI Business Observer"


def task_script(operation):
    # PowerShell literal escaping, never command-string interpolation from business data.
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    if operation == "uninstall":
        return f"Stop-ScheduledTask -TaskName {literal(TASK_NAME)} -ErrorAction Stop\nUnregister-ScheduledTask -TaskName {literal(TASK_NAME)} -Confirm:$false -ErrorAction Stop"
    if operation == "status":
        return f"Get-ScheduledTask -TaskName {literal(TASK_NAME)} -ErrorAction Stop | Select-Object TaskName,State | ConvertTo-Json"
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.exists():
        executable = Path(sys.executable)
    args = f'-B "{Path(__file__).resolve()}" --run'
    return "\n".join([
        "$ErrorActionPreference='Stop'",
        f"$taskName={literal(TASK_NAME)}",
        "if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect/uninstall before changing it.' }",
        "$taskUser=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
        f"$action=New-ScheduledTaskAction -Execute {literal(executable)} -Argument {literal(args)} -WorkingDirectory {literal(ROOT)}",
        "$trigger=New-ScheduledTaskTrigger -AtLogOn -User $taskUser",
        "$principal=New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited",
        "$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew",
        "Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Local encrypted business observation; no customer messages or payments.' | Out-Null",
        "Start-ScheduledTask -TaskName $taskName",
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for option in ("run", "once", "install", "uninstall", "status"):
        group.add_argument("--"+option, action="store_true")
    parser.add_argument("--print-task", action="store_true", help="Print the task definition without registering anything")
    parser.add_argument("--max-ticks", type=int, help="Bounded verification run")
    args = parser.parse_args()
    operation = "uninstall" if args.uninstall else "status" if args.status else "install"
    if args.print_task:
        print(task_script(operation))
        return
    if args.install or args.uninstall or args.status:
        if os.name != "nt":
            raise SystemExit("Task supervision currently supports Windows only.")
        if args.install:
            from ishti.business.engine import dashboard
            current = dashboard()
            if not current["profile"]["enabled"]:
                raise SystemExit("Configure a business source and enable observation in Business Desk before installation.")
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", task_script(operation)],
                       check=True, timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return
    from ishti.business import engine, runtime
    if args.once:
        print(json.dumps(engine.cycle(runner="standalone")))
    elif args.run:
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        runtime.run(stop=stop, max_ticks=args.max_ticks)
    else:
        print(json.dumps(engine.dashboard(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
