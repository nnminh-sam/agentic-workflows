"""A fake `schtasks` / `crontab` for tests/test_schedule.py: records every argv, keeps the "installed" state in files,
and never touches the real Task Scheduler or crontab.

    fake_sched.py schtasks /Create /TN <name> /XML <file> /F   -> stores the task (and its XML text)
    fake_sched.py schtasks /Query /TN <name> [/FO LIST /V]     -> LIST output of a stored task, or rc 1
    fake_sched.py schtasks /Delete /TN <name> /F               -> removes it, or rc 1
    fake_sched.py crontab -l | -                               -> prints / replaces the stored crontab

State lives in $FAKE_SCHED_DIR: calls.jsonl (one argv per line), tasks.json ({name: xml}), crontab.txt.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LIST = """
Folder: \\
HostName:                             FAKEPC
TaskName:                             \\{name}
Next Run Time:                        11/23/2026 10:30:00 AM
Status:                               Ready
Logon Mode:                           Interactive only
Last Run Time:                        11/23/2026 10:15:00 AM
Last Result:                          0
Author:                               FAKEPC\\me
Task To Run:                          {run}
Start In:                             N/A
Comment:                              N/A
Repeat: Every:                        0 Hour(s), 15 Minute(s)
Repeat: Until: Time:                  None
"""


def main() -> int:
    state = Path(os.environ["FAKE_SCHED_DIR"])
    state.mkdir(parents=True, exist_ok=True)
    tool, args = sys.argv[1], sys.argv[2:]
    with (state / "calls.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps([tool, *args]) + "\n")
    if tool == "crontab":
        tab = state / "crontab.txt"
        if args == ["-l"]:
            if not tab.is_file():
                sys.stderr.write("no crontab for tester\n")
                return 1
            sys.stdout.write(tab.read_text(encoding="utf-8"))
            return 0
        if args == ["-"]:
            tab.write_text(sys.stdin.read(), encoding="utf-8")
            return 0
        return 2
    tasks_file = state / "tasks.json"
    tasks = json.loads(tasks_file.read_text(encoding="utf-8")) if tasks_file.is_file() else {}
    name = args[args.index("/TN") + 1] if "/TN" in args else None
    if args[0] == "/Create":
        raw = Path(args[args.index("/XML") + 1]).read_bytes()
        tasks[name] = raw.decode("utf-16") if raw[:2] == b"\xff\xfe" else "NOT-UTF16"
        if os.environ.get("FAKE_SCHED_FAIL") == name:
            sys.stderr.write("ERROR: Access is denied.\n")
            return 1
        tasks_file.write_text(json.dumps(tasks), encoding="utf-8")
        sys.stdout.write(f'SUCCESS: The scheduled task "{name}" has successfully been created.\n')
        return 0
    if name not in tasks:
        sys.stderr.write("ERROR: The system cannot find the file specified.\n")
        return 1
    if args[0] == "/Query":
        if "LIST" in args:
            sys.stdout.write(LIST.format(name=name, run='"C:\\ws\\.sdlc\\bin\\sdlc-schedule.cmd" tick'))
        else:
            sys.stdout.write(f"{name}  11/23/2026 10:30:00 AM  Ready\n")
        return 0
    if args[0] == "/Delete":
        del tasks[name]
        tasks_file.write_text(json.dumps(tasks), encoding="utf-8")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
