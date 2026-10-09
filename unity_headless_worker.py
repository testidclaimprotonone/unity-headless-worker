"""
Unity headless job worker.

WHY THIS EXISTS
    Unity's batchmode build host (bee_backend.exe) cannot complete its named-pipe
    IPC handshake when Unity is launched from inside the WorkBuddy agent's process
    context - it always dies with "WaitNamedPipeA ... GetLastError 121" and Unity
    reports "Scripts have compiler errors" with zero real compiler errors.
    Launched from a normal user terminal it works perfectly (verified: exit code 0,
    bee_backend ExitCode 0, full asset refresh).

    So: run THIS script in a terminal you opened yourself. It sits in the right
    process context and executes Unity batchmode jobs on behalf of the agent.

USAGE
    python  unity_headless_worker.py           (runs until Ctrl-C; shows a console)
    pythonw unity_headless_worker.py           (same, but no window - used by autostart)

AUTOSTART
    Run  install_worker_autostart.bat  ONCE. It (a) starts the worker now and
    (b) drops a launcher into the Windows Startup folder, so from then on the
    worker is running in the user's own context after every logon and the agent
    never needs to ask again.  uninstall_worker_autostart.bat removes it.

    Only one instance runs at a time: a fresh start checks the heartbeat and
    exits if another live worker already owns the queue.

PROTOCOL
    Drop a job file into  <this dir>/queue/<anything>.json :
        {
          "id": "my-run",                       // optional, defaults to file name
          "unity_args": ["-runTests", "-testPlatform", "editmode"],
          "no_quit": true,                      // optional: omit Unity's -quit
          "timeout": 1200                       // optional seconds
        }
    The worker always adds: -batchmode -projectPath <PROJ> -logFile <log>, plus
    -quit UNLESS the job sets "no_quit": true.
    IMPORTANT: for -runTests you MUST set "no_quit": true — with -quit the Editor
    exits right after the asset refresh and the test runner never runs (verified).

    It writes  queue/<name>.result.json :
        { "id", "rc", "seconds", "verdict", "log", "started", "finished", "error" }
    and moves the job file to  queue/done/ .

    queue/_heartbeat.txt  is refreshed every few seconds so the agent can tell
    the worker is alive.
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(HERE, "queue")
DONE = os.path.join(QUEUE, "done")
HEARTBEAT = os.path.join(QUEUE, "_heartbeat.txt")
WORKER_LOG = os.path.join(HERE, "worker.log")
VERSION = "2"

def _is_unity_project(d):
    return (os.path.isdir(os.path.join(d, "Assets"))
            and os.path.isdir(os.path.join(d, "ProjectSettings")))


def _autodetect_unity():
    import glob
    best = sorted(glob.glob(r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe"))
    if best:
        return best[-1]
    alt = sorted(glob.glob(r"C:\Program Files\Unity*\Editor\Unity.exe"))
    return alt[-1] if alt else None


def _autodetect_project():
    import glob
    for root in (HERE, os.path.dirname(HERE)):
        if _is_unity_project(root):
            return root
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if os.path.isdir(d) and _is_unity_project(d):
                return d
    return None


def _load_config():
    """worker_config.json is optional; setup_worker.py writes it. Anything it
    does not specify is auto-detected, so the worker stays portable."""
    path = os.path.join(HERE, "worker_config.json")
    cfg = {}
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    unity = cfg.get("unity") or _autodetect_unity()
    proj = cfg.get("project") or _autodetect_project()
    logdir = cfg.get("logdir") or (os.path.join(proj, "Logs", "worker") if proj else None)
    return unity, proj, logdir


UNITY, PROJ, LOGDIR = _load_config()
START_MTIME = os.path.getmtime(os.path.abspath(__file__))
STARTED_AT = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def say(msg):
    """Print for the console AND append to worker.log.

    Under pythonw.exe sys.stdout is None, so a bare print would crash - the
    log file is what makes the windowless autostart mode observable.
    """
    line = f"[{stamp()}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(WORKER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def verdict_of(log_path, rc):
    try:
        t = open(log_path, encoding="utf-8", errors="replace").read()
    except OSError:
        return "NO_LOG"
    if "IPC_Client_InitializeAndConnectToParent" in t:
        return "FAIL_BEE_IPC"
    if "error CS" in t:
        return "FAIL_COMPILER"
    if rc == 0 and "Exiting batchmode successfully" in t:
        return "PASS"
    if rc == 0:
        return "OK_RC0"
    return f"FAIL_RC{rc}"


def read_test_results(extra):
    """If the job asked for -testResults <path>, summarise the NUnit XML."""
    path = None
    for i, a in enumerate(extra):
        if str(a) == "-testResults" and i + 1 < len(extra):
            path = str(extra[i + 1])
    if not path:
        return None
    if not os.path.exists(path):
        return {"path": path, "present": False}
    try:
        root = ET.parse(path).getroot()
    except Exception as e:
        return {"path": path, "present": True, "parse_error": str(e)}
    node = root if root.tag == "test-run" else root.find(".//test-run")
    out = {"path": path, "present": True}
    if node is not None:
        for k in ("total", "passed", "failed", "inconclusive", "skipped", "result", "duration"):
            if node.get(k) is not None:
                out[k] = node.get(k)
    return out


def pid_alive(pid):
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            k32.CloseHandle(h)
            return True
    except Exception:
        pass
    return False


def another_instance_alive(max_age=20.0):
    """True if a *different* live worker refreshed the heartbeat recently.

    Autostart fires on every logon, so without this you would pile up workers.
    """
    try:
        txt = open(HEARTBEAT, encoding="utf-8").read().strip()
    except OSError:
        return False
    ts = pid = None
    for part in txt.replace("|", " ").split():
        if "=" in part:
            k, v = part.split("=", 1)
            if k == "pid":
                pid = int(v)
            if k == "ts":
                ts = float(v)
    if pid is None or ts is None or pid == os.getpid():
        return False
    if time.time() - ts > max_age:
        return False
    return pid_alive(pid)


def write_heartbeat():
    try:
        open(HEARTBEAT, "w", encoding="utf-8").write(
            f"ts={time.time():.0f} | pid={os.getpid()} | v={VERSION} "
            f"| started={STARTED_AT} | {stamp()}\n")
    except OSError:
        pass


def maybe_restart():
    """Re-exec ourselves if this file changed, so edits take effect without the
    user having to restart the worker (and without losing the launch context)."""
    try:
        if os.path.getmtime(__file__) > START_MTIME:
            say("worker source changed - respawning in place (context preserved)")
            # Invalidate the heartbeat first, otherwise the single-instance guard
            # in the new process sees this (still exiting) pid as a live worker
            # and refuses to start.
            try:
                open(HEARTBEAT, "w", encoding="utf-8").write(
                    f"ts=0 | pid={os.getpid()} | v={VERSION} | restarting\n")
            except OSError:
                pass
            # Spawn a DETACHED successor, then exit. Detached so it is not tied
            # to this process's console/lifetime - os.execv on Windows leaves a
            # child that can die with its parent's console.
            subprocess.Popen(
                [sys.executable, "-u", os.path.abspath(__file__)],
                cwd=HERE,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=0x00000008,  # DETACHED_PROCESS
                close_fds=True)
            os._exit(0)
    except Exception as e:  # noqa: BLE001
        say(f"restart failed: {type(e).__name__}: {e}")


def run_job(job_path):
    name = os.path.basename(job_path)
    try:
        job = json.load(open(job_path, encoding="utf-8"))
    except Exception as e:
        say(f"{name}: bad job json -> {e}")
        return

    jid = job.get("id") or os.path.splitext(name)[0]
    extra = job.get("unity_args", []) or []
    timeout = int(job.get("timeout", 1800))

    os.makedirs(LOGDIR, exist_ok=True)
    log = os.path.join(LOGDIR, f"{jid}.log")
    for i, a in enumerate(extra):
        if str(a) == "-testResults" and i + 1 < len(extra):
            d = os.path.dirname(os.path.abspath(str(extra[i + 1])))
            if d:
                os.makedirs(d, exist_ok=True)
    head = [UNITY, "-batchmode"]
    if not job.get("no_quit"):
        head.append("-quit")
    cmd = head + ["-projectPath", PROJ, "-logFile", log] + [str(a) for a in extra]

    say(f"JOB {jid}: {subprocess.list2cmdline(cmd[1:])}")
    started = time.time()
    started_str = stamp()
    try:
        p = subprocess.Popen(cmd)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            rc = -9
    except Exception as e:
        rc = -1
        say(f"JOB {jid}: launch failed -> {e}")
    secs = round(time.time() - started, 1)

    result = {
        "id": jid,
        "rc": rc,
        "seconds": secs,
        "verdict": verdict_of(log, rc),
        "log": log,
        "started": started_str,
        "finished": stamp(),
        "command": subprocess.list2cmdline(cmd),
    }
    tests = read_test_results(extra)
    if tests:
        result["tests"] = tests
        if tests.get("present"):
            f = int(tests.get("failed", 0))
            result["verdict"] = "TESTS_FAILED" if f else "TESTS_PASSED"
    out = os.path.join(QUEUE, f"{jid}.result.json")
    json.dump(result, open(out, "w", encoding="utf-8"), indent=2)
    say(f"JOB {jid}: rc={rc} in {secs}s -> {result['verdict']}  (log: {log})")

    os.makedirs(DONE, exist_ok=True)
    try:
        os.replace(job_path, os.path.join(DONE, name))
    except OSError:
        pass


def main():
    os.makedirs(QUEUE, exist_ok=True)
    os.makedirs(DONE, exist_ok=True)
    os.makedirs(LOGDIR, exist_ok=True)

    if another_instance_alive():
        say(f"another worker is already live - exiting (pid {os.getpid()}).")
        return

    say(f"Unity headless worker v{VERSION} started (pid {os.getpid()}).")
    say(f"  Unity : {UNITY}")
    say(f"  Project: {PROJ}")
    say(f"  Queue  : {QUEUE}")
    if not UNITY or not os.path.exists(UNITY):
        say("WARNING: Unity.exe not found - run setup_worker.py to fix worker_config.json")
    if not PROJ or not _is_unity_project(PROJ):
        say("WARNING: no Unity project found - run setup_worker.py to fix worker_config.json")
    say("Drop a *.json job file into the queue. Ctrl-C to stop.")
    try:
        while True:
            maybe_restart()
            write_heartbeat()
            for fn in sorted(os.listdir(QUEUE)):
                if fn.endswith(".json") and not fn.endswith(".result.json") and not fn.startswith("_"):
                    run_job(os.path.join(QUEUE, fn))
            time.sleep(2)
    except KeyboardInterrupt:
        say("stopped")


if __name__ == "__main__":
    main()
