from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
from pathlib import Path


def _linux_available_memory_bytes() -> int | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="ascii")
    except OSError:
        return None
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", text, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _macos_available_memory_bytes() -> int | None:
    try:
        result = subprocess.run(
            ["/usr/bin/vm_stat"],
            capture_output=True,
            check=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    page_match = re.search(r"page size of (\d+) bytes", result.stdout)
    if not page_match:
        return None
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        match = re.search(rf"^{re.escape(label)}:\s+(\d+)\.$", result.stdout, re.MULTILINE)
        if match:
            pages += int(match.group(1))
    return pages * int(page_match.group(1)) if pages else None


def _windows_available_memory_bytes() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.available_physical)


def available_memory_gb() -> float | None:
    """Best-effort physical memory availability without adding psutil."""
    if sys.platform.startswith("linux"):
        available = _linux_available_memory_bytes()
    elif sys.platform == "darwin":
        available = _macos_available_memory_bytes()
    elif os.name == "nt":
        available = _windows_available_memory_bytes()
    else:
        available = None

    if available is None and "SC_AVPHYS_PAGES" in getattr(os, "sysconf_names", {}):
        try:
            available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError):
            available = None
    return None if available is None else available / (1024**3)
