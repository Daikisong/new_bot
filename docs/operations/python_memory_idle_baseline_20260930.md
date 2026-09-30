# Python Memory Idle Baseline - 2026-09-30

At the user's request, Python process private memory was sampled twice at
2026-09-30 07:36 KST, with ten seconds between samples.

| Metric | First sample | Second sample |
| --- | ---: | ---: |
| Python process count | 46 | 46 |
| Aggregate private memory | 1.85 GiB | 1.85 GiB |
| Largest single Python process | 203.6 MiB | 203.6 MiB |
| Active NSLAB compiler/replay processes | 0 | 0 |
| Host available memory | 15.00 GiB | 14.84 GiB |

The observed Python processes were resident Codex MCP services. None were
terminated. The process count and aggregate private memory were flat, so this
idle sample shows no growing Python allocation over the interval. It does not
prove compiler-time leak freedom: no offline compiler or snapshot replay was
running during the measurement.

## Follow-up sample - 08:11 KST

A second idle check sampled the host at 2026-09-30 08:11:16 and
2026-09-30 08:11:28 KST.

| Metric | First sample | Second sample |
| --- | ---: | ---: |
| Python process count | 46 | 46 |
| Aggregate private memory | 1.85 GiB | 1.85 GiB |
| Aggregate working set | 1.06 GiB | 1.06 GiB |
| Largest single Python process | 203.2 MiB | 203.2 MiB |
| Active NSLAB compiler/replay processes | 0 | 0 |
| Host available memory | 17.72 GiB | 18.03 GiB |

All 46 processes were classified as Codex MCP or the separate Posting VM-domain
queue; 44 were Binance public spot/futures MCP workers. No Python process from
the NSLAB worktree was active. Counts and Python memory were unchanged across
the 12-second interval, so this check found no idle-growth signal. The MCP
workers were left running because they belong to live Codex sessions and are
not NSLAB-owned processes. This is not evidence about memory behavior during a
compiler run.

The next offline compiler run must still be monitored every ten seconds for PID
private bytes, working set, and host available memory, with stage and completed
checkpoint counts. Follow the thresholds and checkpoint-preservation procedure
in [memory_resource_management_20260930.md](memory_resource_management_20260930.md).
