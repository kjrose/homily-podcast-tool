import logging
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .config_loader import CFG, get_base_dir

APP_LOG_FILE = "homily_monitor.log"
DEFAULT_LOG_DIR = "logs"
DEFAULT_HOT_RETENTION_DAYS = 7
DEFAULT_ARCHIVE_RETENTION_DAYS = 28
DEFAULT_CLEANUP_INTERVAL_HOURS = 6
_MIB = 1024 * 1024


def _logging_cfg():
    return CFG.get("logging", {})


def _get_int_logging_option(name, default):
    value = _logging_cfg().get(name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_log_dir():
    configured_dir = _logging_cfg().get("log_dir", DEFAULT_LOG_DIR)
    if os.path.isabs(configured_dir):
        return Path(configured_dir)
    return Path(get_base_dir()) / configured_dir


def ensure_log_dir():
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def configure_logging():
    log_dir = ensure_log_dir()
    log_file = log_dir / APP_LOG_FILE

    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger('HomilyMonitor')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
    )
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)

    return logger


def get_cleanup_interval():
    hours = _get_int_logging_option("cleanup_interval_hours", DEFAULT_CLEANUP_INTERVAL_HOURS)
    if hours < 1:
        hours = DEFAULT_CLEANUP_INTERVAL_HOURS
    return timedelta(hours=hours)


def _format_mib(value):
    return f"{value / _MIB:.1f} MiB"


def _windows_process_memory(pid):
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCountersEx),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        close_handle = False
        if pid == os.getpid():
            handle = kernel32.GetCurrentProcess()
        else:
            process_query_limited_information = 0x1000
            process_vm_read = 0x0010
            handle = kernel32.OpenProcess(
                process_query_limited_information | process_vm_read,
                False,
                int(pid),
            )
            close_handle = True

        if not handle:
            return None

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        ok = psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        )
        if close_handle:
            kernel32.CloseHandle(handle)
        if not ok:
            return None
        return {
            "working_set": int(counters.WorkingSetSize),
            "peak_working_set": int(counters.PeakWorkingSetSize),
            "private": int(counters.PrivateUsage),
        }
    except Exception:
        return None


def _windows_system_memory():
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatusEx)]
        kernel32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return {
            "load": int(status.dwMemoryLoad),
            "total": int(status.ullTotalPhys),
            "available": int(status.ullAvailPhys),
        }
    except Exception:
        return None


def _windows_process_entries():
    if os.name != "nt":
        return []

    try:
        import ctypes
        from ctypes import wintypes

        class ProcessEntry32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle_value = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle_value:
            return []

        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        entries = []
        if kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            while True:
                entries.append({
                    "pid": int(entry.th32ProcessID),
                    "parent_pid": int(entry.th32ParentProcessID),
                    "name": str(entry.szExeFile),
                })
                if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                    break
        kernel32.CloseHandle(snapshot)
        return entries
    except Exception:
        return []


def _process_tree_entries(root_pids):
    root_pids = {int(pid) for pid in root_pids if pid}
    if not root_pids:
        return []

    entries = _windows_process_entries()
    by_parent = {}
    by_pid = {}
    for entry in entries:
        by_pid[entry["pid"]] = entry
        by_parent.setdefault(entry["parent_pid"], []).append(entry["pid"])

    seen = set()
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(by_parent.get(pid, []))

    return [by_pid[pid] for pid in seen if pid in by_pid]


def log_memory_snapshot(logger=None, label="", include_tree_pids=None):
    logger = logger or logging.getLogger('HomilyMonitor')

    process_memory = _windows_process_memory(os.getpid())
    system_memory = _windows_system_memory()
    details = []

    if process_memory:
        details.append(
            "app_private=%s app_working_set=%s app_peak_working_set=%s"
            % (
                _format_mib(process_memory["private"]),
                _format_mib(process_memory["working_set"]),
                _format_mib(process_memory["peak_working_set"]),
            )
        )

    tree_rows = []
    if include_tree_pids:
        for entry in _process_tree_entries(include_tree_pids):
            memory = _windows_process_memory(entry["pid"])
            if memory:
                tree_rows.append({**entry, **memory})

    if tree_rows:
        tree_private = sum(row["private"] for row in tree_rows)
        tree_working_set = sum(row["working_set"] for row in tree_rows)
        largest = max(tree_rows, key=lambda row: row["working_set"])
        details.append(
            "child_tree_private=%s child_tree_working_set=%s largest_child=%s[%s]=%s"
            % (
                _format_mib(tree_private),
                _format_mib(tree_working_set),
                largest["name"],
                largest["pid"],
                _format_mib(largest["working_set"]),
            )
        )

    if system_memory:
        details.append(
            "system_load=%s%% system_available=%s system_total=%s"
            % (
                system_memory["load"],
                _format_mib(system_memory["available"]),
                _format_mib(system_memory["total"]),
            )
        )

    if details:
        label_text = f" ({label})" if label else ""
        logger.info("Memory snapshot%s: %s", label_text, "; ".join(details))


def cleanup_logs(logger=None):
    logger = logger or logging.getLogger('HomilyMonitor')

    hot_days = _get_int_logging_option("hot_retention_days", DEFAULT_HOT_RETENTION_DAYS)
    archive_days = _get_int_logging_option("archive_retention_days", DEFAULT_ARCHIVE_RETENTION_DAYS)
    if hot_days < 1:
        hot_days = DEFAULT_HOT_RETENTION_DAYS
    if archive_days <= hot_days:
        archive_days = max(DEFAULT_ARCHIVE_RETENTION_DAYS, hot_days + 1)

    hot_cutoff = datetime.now(timezone.utc) - timedelta(days=hot_days)
    archive_cutoff = datetime.now(timezone.utc) - timedelta(days=archive_days)

    scanned_dirs = []
    for candidate in [get_log_dir(), Path(get_base_dir())]:
        if candidate.exists() and candidate not in scanned_dirs:
            scanned_dirs.append(candidate)

    compressed = 0
    deleted = 0

    for directory in scanned_dirs:
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if not _is_managed_log_file(path):
                continue

            modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if path.suffix.lower() == ".zip":
                if modified_at < archive_cutoff:
                    path.unlink(missing_ok=True)
                    deleted += 1
                continue

            if modified_at < archive_cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
                continue

            if modified_at < hot_cutoff:
                if _compress_log_file(path, logger):
                    compressed += 1

    if compressed or deleted:
        logger.info(
            "Log cleanup complete: compressed %s file(s), deleted %s file(s).",
            compressed,
            deleted,
        )


def _is_managed_log_file(path):
    name = path.name
    if name.endswith(".zip"):
        base_name = name[:-4]
        return _is_managed_log_name(base_name)
    return _is_managed_log_name(name)


def _is_managed_log_name(name):
    return (
        name == APP_LOG_FILE
        or name.startswith(APP_LOG_FILE + ".")
        or (name.startswith("batch_") and name.endswith(".log"))
        or name.endswith(".out.log")
        or name.endswith(".err.log")
        or name.endswith(".wrapper.log")
    )


def _compress_log_file(path, logger):
    zip_path = path.with_name(path.name + ".zip")
    try:
        source_stat = path.stat()
        source_timestamp = source_stat.st_mtime

        if zip_path.exists():
            if zip_path.stat().st_mtime >= source_timestamp:
                path.unlink(missing_ok=True)
                return False
            zip_path.unlink()

        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(path, arcname=path.name)

        os.utime(zip_path, (source_timestamp, source_timestamp))
        path.unlink()
        return True
    except PermissionError:
        logger.debug("Skipping log compression for locked file: %s", path)
        return False
    except OSError as exc:
        logger.warning("Failed to compress log file %s: %s", path, exc)
        return False
