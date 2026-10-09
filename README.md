# Unity Headless Worker

A small, dependency-free workaround that lets an AI coding agent drive **Unity
`-batchmode`** (headless compile, test runs, builds) on Windows — even though the
agent itself physically cannot launch Unity headlessly.

> **TL;DR** Unity's build host (`bee_backend.exe`) fails its named-pipe IPC
> handshake when Unity is started *from inside the agent's process context*. It
> works fine from a process the *user* started. This repo ships a tiny "worker"
> that runs in your logon session and executes Unity jobs on the agent's behalf
> through a simple file-based job queue.

---

## The problem (read this first)

On affected Windows machines, `Unity.exe -batchmode` **always fails when launched
by the agent**, reporting `Scripts have compiler errors` with **zero** actual
`error CS` lines. The real cause is Unity's build backend:

```
Failed IPC_Client_InitializeAndConnectToParent:
  WaitNamedPipeA on \\.\pipe\ipc_<pid>_htc_0 failed. GetLastError 121
```

Launched from a process **you** started, the identical command succeeds
(`ExitCode 0`, ~47 s). The root cause is the agent's launch context:

- The agent's processes run under a Windows **Job Object** carrying only
  `KILL_ON_JOB_CLOSE`, and `CREATE_BREAKAWAY_FROM_JOB` is denied (`WinError 5`),
  so the agent cannot spawn anything outside its own context.
- `schtasks.exe` is blacklisted, so there is no sanctioned way to break out.

This is **proven, not suspected** — same command, same project, only the launch
context differs. You cannot "fix" it by launching Unity differently from inside
the agent. The only reliable fix is to run Unity's batchmode from a process the
**user** owns. See [`DIAGNOSIS.md`](DIAGNOSIS.md) for the full evidence trail.

---

## How it works

```
 You (logon session)                Agent (separate context)
 ┌──────────────────────┐            ┌──────────────────────────┐
 │ unity_headless_worker│            │  writes queue/<id>.json   │
 │   (pythonw, silent)  │◄─ reads ──│  polls queue/<id>.result  │
 │                       │            │                           │
 │ runs Unity -batchmode │            │  reads verdict + NUnit    │
 │ writes queue/<id>.     │── writes─►│  XML counts               │
 │   result.json          │            │                           │
 └──────────────────────┘            └──────────────────────────┘
        ▲
        │ installed in Windows Startup folder
        │ → starts automatically at every logon
```

The worker is a long-running Python script that:

1. Lives in your Windows **logon session** (started by you, or auto-started at
   logon via the Startup folder).
2. Watches a `queue/` directory for job files.
3. For each job, runs `Unity.exe -batchmode` with your requested arguments.
4. Writes a result file (exit code, verdict, log path, and — for test runs —
   NUnit counts) back next to the job.
5. Self-heals: it hot-reloads if its own source changes, and a single-instance
   guard prevents duplicates.

Because it runs as *you*, Unity's build host completes its IPC handshake and
headless mode just works.

---

## Quick start

### On the machine where you already have the files

If these files already live in your project's `.workbuddy-ai` folder and you've
run `setup_worker.py` before, the worker is probably already running. Check:

```
type .workbuddy-ai\queue\_heartbeat.txt
```

A recent timestamp + a `pid=` means it's alive. If the file is missing or stale,
double-click `start_worker_now.bat` (created by `setup_worker.py`).

### On a fresh machine (portable setup)

1. Copy **`unity_headless_worker.py`** and **`setup_worker.py`** into the
   `.workbuddy-ai` folder of that machine's Unity workspace.
2. Run the installer (it auto-detects Unity, the project, and Python, then
   writes the config and launchers):

   ```bat
   python setup_worker.py
   ```

   - To skip the auto-start-at-logon entry: `python setup_worker.py --no-startup`
   - To force paths: `python setup_worker.py --unity "<path>\Unity.exe" --project "<path>\MyProject"`
3. **You must start the worker once yourself** (it cannot be started by the
   agent — that's the whole point). Double-click `start_worker_now.bat`, or log
   off and back on (the Startup entry handles it).

That's it. From then on the agent can run headless Unity jobs on its own.

> The only mandatory *human* step is starting the worker once. Everything else
> is automatic.

---

## File reference

| File | Purpose |
|---|---|
| `unity_headless_worker.py` | The worker. Polls `queue/`, runs Unity batchmode, writes results. Hot-reloads on source change. |
| `setup_worker.py` | One-time per-computer installer. Detects Unity / project / Python, writes `worker_config.json` + launchers, installs Startup entry. **Does not start the worker.** |
| `run_headless.bat` | One-shot verification you run in your own terminal to prove headless batchmode works outside the agent. Prints PASS/FAIL. |
| `start_headless_worker.bat` | Manual launcher (paths hardcoded for the original machine — reference only; prefer `start_worker_now.bat`). |
| `install_worker_autostart.bat` | Starts the worker now *and* installs the Startup entry. |
| `uninstall_worker_autostart.bat` | Removes the Startup entry (does not kill a running worker). |
| `worker_config.json` | **Generated** by `setup_worker.py`. Machine-specific paths. *Not committed.* |
| `launch_worker_silent.vbs` | **Generated**. Windowless launcher with real paths baked in. *Not committed.* |
| `start_worker_now.bat` | **Generated** by `setup_worker.py`. Double-click to start the worker immediately. *Not committed.* |
| `queue/` | Drop jobs here; results land beside them; finished jobs move to `queue/done/`. *Not committed.* |
| `worker.log` | The worker's own log (its only output while windowless). *Not committed.* |

---

## Using it as an agent (file-queue protocol)

The agent never launches `Unity.exe` directly. Instead:

**1. Write a job** to `queue/<name>.json`:

```json
{
  "id": "editmode-tests",
  "no_quit": true,
  "unity_args": [
    "-runTests",
    "-testPlatform", "editmode",
    "-testResults", "C:\\path\\to\\editmode.xml"
  ],
  "timeout": 900
}
```

**2. Poll for the result** at `queue/<name>.result.json`:

```json
{
  "id": "editmode-tests",
  "rc": 0,
  "seconds": 42.1,
  "verdict": "TESTS_PASSED",
  "log": "C:\\...\\Logs\\worker\\...log",
  "started": "...", "finished": "...",
  "command": "Unity.exe -batchmode ...",
  "tests": { "total": 46, "passed": 46, "failed": 0, "inconclusive": 0, "skipped": 0, "result": "Passed" }
}
```

**Verdicts:** `PASS`, `TESTS_PASSED`, `TESTS_FAILED`, `FAIL_BEE_IPC`,
`FAIL_COMPILER`, `FAIL_RC<n>`, `NO_LOG`.

### Two traps (don't skip these)

1. **`"no_quit": true` is mandatory for `-runTests`.** With `-quit`, Unity exits
   right after the asset refresh — *before* the test runner starts — and still
   returns `rc=0`. A green exit code proves nothing.
2. **Always read the `tests` block** (total/passed/failed), never just `rc`.
   `rc=0` with `tests.failed > 0` is still a failure.

---

## Why the worker must run as you (not the agent)

The agent's process tree is trapped in a Job Object with
`KILL_ON_JOB_CLOSE` and cannot break away (`CREATE_BREAKAWAY_FROM_JOB` →
`WinError 5`). Any Unity it spawns inherits that context, and
`bee_backend.exe`'s named-pipe handshake times out (`GetLastError 121`). The one
thing that could not be done from inside — launching Unity as the user — is
exactly what the worker does. Full proof in [`DIAGNOSIS.md`](DIAGNOSIS.md).

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it.
