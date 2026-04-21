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
