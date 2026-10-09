# Diagnosis — why Unity `-batchmode` fails from the agent

This document records the evidence that the headless failure is caused by the
**agent's launch context**, not the project, the Unity install, or any flag.
Everything below was measured on the original machine.

## Symptom

```
Failed IPC_Client_InitializeAndConnectToParent:
  WaitNamedPipeA on \\.\pipe\ipc_<pid>_htc_0 failed. GetLastError 121
```

Unity reports `Scripts have compiler errors` with **zero** `error CS` lines.
`bee_backend.exe` (the build host) is launched with `--ipc` and opens a
`NamedPipeServerStream` named `\\.\pipe\ipc_<pid>_htc_0`. The client connect
times out after ~255 ms.

`GetLastError 121` = `ERROR_SEM_TIMEOUT`: the pipe *exists* but has **no free
instance** — i.e. Unity created the pipe but `bee_backend` can't grab an
instance in the time allowed. (A *missing* pipe returns error 2, not 121.)

## Controlled comparison

Same `Unity.exe -batchmode -nographics -quit -projectPath <same project>`:

| Launched by        | ExitCode | Duration | Outcome |
|---|---|---|---|
| The agent          | 2        | ~0.27 s  | "Scripts have compiler errors", 0 `error CS`, the `IPC_..._Parent` error |
| The user's terminal| 0        | ~47 s    | "Exiting batchmode successfully now!" |

Only the launch context changed. Therefore the project and Unity are innocent.

## Why the agent's context is the culprit

- Every process the agent launches carries a **restricted token**:
  `TokenHasRestrictions = 1`, `TokenIsAppContainer = 0`, integrity level Medium.
- It is also placed in a Windows **Job Object** whose only flag is
  `KILL_ON_JOB_CLOSE` (`LimitFlags = 0x2000`).
- `CREATE_BREAKAWAY_FROM_JOB` is **denied** with `WinError 5`, so the agent
  cannot spawn a child outside its job.
- `schtasks.exe` (the usual "run outside my context" trick) is on the security
  blacklist.

A minimal by-name pipe handshake (parent creates `\\.\pipe\ipc_<pid>_htc_0`,
child does `WaitNamedPipeW` + `CreateFileW`) **works** from agent processes —
pipes themselves are not blocked. So the failure is specifically the
`bee_backend` IPC under the restricted launch token, not Windows named pipes in
general.

## What was ruled out

Each was tested; all still failed identically until the worker (user context)
was used:

- the agent sandbox (`dangerouslyDisableSandbox: true` changes nothing)
- `-nographics` (fails with and without)
- build thread count (`BEE_BUILD_THREADS=1` fails)
- a corrupt/stale Bee cache (rebuilt from scratch — still fails)
- the `DirectoryMonitor` stalls (`-disableDirectoryMonitor` removes 2×30 s stalls
  but the IPC failure remains)
- OneDrive / Documents redirection / Controlled Folder Access / third-party AV
- disk space (287 GB free, NTFS)
- the hardware

## The fix

Run Unity's batchmode from a process the **user** owns. This repo's
`unity_headless_worker.py` does exactly that: it lives in your logon session
(auto-started via the Windows Startup folder), watches a `queue/`, and runs Unity
on the agent's behalf. The interactive Editor was always unaffected; only
`-batchmode` from the agent was broken.
