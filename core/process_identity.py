"""Read-only worker identity, including creation time so reused PIDs are distinct."""
from __future__ import annotations

import os
from pathlib import Path


def _started(pid: int) -> str | None:
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # process no longer exists
                return None
            raise OSError('Cannot inspect worker')
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError('Cannot inspect worker creation time')
            if times[1].dwHighDateTime or times[1].dwLowDateTime:
                return None  # exited process objects can outlive their PID while handles remain open
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    path = Path('/proc') / str(pid) / 'stat'
    try:
        # The executable name in parentheses may itself contain spaces.
        fields = path.read_text().rsplit(')', 1)[1].split()
        return None if fields[0] in {'Z', 'X'} else fields[19]
    except FileNotFoundError:
        if Path('/proc').is_dir():
            return None
        raise OSError('Worker inspection unsupported on this platform')


def current_worker() -> dict:
    return {'pid': os.getpid(), 'started': _started(os.getpid())}


def worker_alive(owner: dict | None) -> bool | None:
    """None means unknown, and must never authorize closing a task."""
    if not isinstance(owner, dict) or not owner.get('started'):
        return None
    pid = owner.get('pid')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        return _started(pid) == str(owner['started'])
    except (OSError, ValueError, IndexError):
        return None
