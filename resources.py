"""What the container is giving this process: memory and CPU.

Render's free plan shows no metrics, so the app reports its own. Used in
the retrieval log (rag.py) and once at boot (app.py). Every read is
best-effort: on a Mac there's no /proc or cgroup, and the line just says
less.

Added 2026-09-26, while live questions hung for 60s inside the
knowledge-base search and nothing said why.
"""

import os
from pathlib import Path

MB = 1024 * 1024


def _read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _rss_mb():
    """This process's resident memory, from /proc (Linux only)."""
    status = _read("/proc/self/status") or ""
    for line in status.splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024          # kB -> MB
    return None


def _cgroup_memory():
    """(used MB, limit MB) for the whole container, cgroup v2 then v1."""
    used = _read("/sys/fs/cgroup/memory.current") or \
        _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    limit = _read("/sys/fs/cgroup/memory.max") or \
        _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    used = int(used) / MB if used and used.isdigit() else None
    limit = int(limit) / MB if limit and limit.isdigit() else None
    if limit and limit > 1024 * 1024:      # "unlimited" shows as a huge number
        limit = None
    return used, limit


def _cpu():
    """(CPU quota in cores or None, seconds throttled or None)."""
    quota = None
    cpu_max = _read("/sys/fs/cgroup/cpu.max")                 # "10000 100000"
    if cpu_max and not cpu_max.startswith("max"):
        q, period = cpu_max.split()[:2]
        quota = int(q) / int(period)
    else:
        q = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        period = _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if q and period and q.lstrip("-").isdigit() and int(q) > 0:
            quota = int(q) / int(period)
    throttled = None
    stat = _read("/sys/fs/cgroup/cpu.stat") or _read("/sys/fs/cgroup/cpu/cpu.stat") or ""
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key == "throttled_usec":
            throttled = int(value) / 1e6
        elif key == "throttled_time":                          # v1, nanoseconds
            throttled = int(value) / 1e9
    return quota, throttled


def summary():
    """One short line, e.g. 'mem 412/512MB (rss 398MB) · cpu 0.1 cores, throttled 83.2s'."""
    parts = []
    rss = _rss_mb()
    used, limit = _cgroup_memory()
    if used is not None:
        mem = f"mem {used:.0f}/{limit:.0f}MB" if limit else f"mem {used:.0f}MB"
        if rss is not None:
            mem += f" (rss {rss:.0f}MB)"
        parts.append(mem)
    elif rss is not None:
        parts.append(f"rss {rss:.0f}MB")
    quota, throttled = _cpu()
    cpu = []
    if quota is not None:
        cpu.append(f"{quota:g} cores")
    cpu.append(f"{os.cpu_count()} visible")
    if throttled is not None:
        cpu.append(f"throttled {throttled:.1f}s")
    parts.append("cpu " + ", ".join(cpu))
    return " · ".join(parts)
