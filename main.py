# homily_monitor/main.py

import argparse
import time
import os
import sys
from datetime import datetime, timezone

from homily_monitor import (
    config_loader as cfg_mod,
    log_utils,
)

logger = log_utils.configure_logging()

CFG = cfg_mod.CFG


def _positive_int(value):
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _homily_date(value):
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be in YYYY-MM-DD format") from exc
    return value


def main():
    from homily_monitor import audio_utils, helpers, s3_utils

    os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
    logger.info("Starting S3 monitoring...")
    next_log_cleanup = datetime.now(timezone.utc)
    while True:
        try:
            if datetime.now(timezone.utc) >= next_log_cleanup:
                log_utils.cleanup_logs(logger)
                next_log_cleanup = datetime.now(timezone.utc) + log_utils.get_cleanup_interval()

            s3_files = s3_utils.list_s3_files()
            
            for file in s3_files:
                s3_key = file["Key"]
                file_name = os.path.basename(s3_key)
                local_path = os.path.join(CFG["paths"]["local_dir"], file_name)

                if not s3_utils.is_file_within_last_48_hours(file["LastModified"]):
                    logger.debug(f"Skipping {file_name}: Not within 48 hours or already downloaded.")
                    continue
                if os.path.exists(local_path):
                    logger.debug(f"Skipping {file_name}: Already exists locally.")
                    continue

                logger.info(f"Downloading {s3_key} to {local_path}...")
                if not s3_utils.download_file(s3_key, local_path):
                    logger.warning(f"Skipping downstream processing for {s3_key} because download failed.")
                    continue
                logger.info(f"Running batch file on {local_path}...")
                if not audio_utils.run_batch_file(local_path):
                    logger.warning(f"Skipping transcript processing for {local_path} because batch processing failed.")
                    continue
                logger.info(f"Checking transcript for {local_path}...")
                helpers.check_transcript(local_path, file["LastModified"])

            # Commenting out for now as per your code
            # logger.info("Checking for completed weekends...")
            # helpers.check_for_completed_weekends()
            time.sleep(60)
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mass Downloader and Transcript Checker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true", help="Send the plain-text transcript alert test email")
    group.add_argument(
        "--test-upload-email",
        action="store_true",
        help="Send a styled homily upload preview email using the latest stored homily analysis when available",
    )
    group.add_argument("--latest", action="store_true", help="Run batch + GPT analysis on latest .mp3 file")
    group.add_argument("--analyze-latest", action="store_true", help="Analyze the latest transcript file")
    group.add_argument("--extract-latest", action="store_true", help="Extract homily from latest .mp3 + VTT")
    group.add_argument("--upload-latest", action="store_true", help="Upload the latest extracted homily to WordPress as a draft")
    group.add_argument("--extract", type=str, help="Extract homily for specific Mass-YYYY-MM-DD_HH-MM.mp3 (e.g., --extract 2025-07-20_18-00)")
    group.add_argument("--upload", type=str, help="Upload specific Homily-YYYY-MM-DD_HH-MM.mp3 to WordPress (e.g., --upload 2025-07-20_18-00)")
    group.add_argument(
        "--retry-upload-date",
        type=_homily_date,
        help="Check all homilies for a specific date (YYYY-MM-DD) and upload only the ones missing from WordPress",
    )
    group.add_argument(
        "--retry-upload-last-days",
        type=_positive_int,
        help="Check homilies from the last X days, including today, and upload only the ones missing from WordPress",
    )
    group.add_argument(
        "--list-homilies-last-days",
        type=_positive_int,
        help="List WordPress homilies and local homily files from the last X days, including today",
    )
    parser.add_argument(
        "--email-to",
        type=str,
        help="Override the configured recipient for test email commands",
    )
    args = parser.parse_args()

    if args.test:
        from homily_monitor import helpers

        logger.info("Sending test email...")
        helpers.test_email(email_to=args.email_to)
    elif args.test_upload_email:
        from homily_monitor import wordpress_utils

        logger.info("Sending homily upload preview email...")
        wordpress_utils.send_test_upload_success_email(email_to=args.email_to)
    elif args.analyze_latest:
        from homily_monitor import helpers

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info("Analyzing latest transcript...")
        helpers.analyze_latest_transcript()
    elif args.latest:
        from homily_monitor import helpers

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info("Running batch + GPT analysis on latest MP3...")
        helpers.run_latest_test()
    elif args.extract_latest:
        from homily_monitor import helpers

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info("Extracting latest homily...")
        helpers.extract_latest_homily()
    elif args.upload_latest:
        from homily_monitor import wordpress_utils

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info("Uploading latest homily to WordPress...")
        wordpress_utils.upload_latest_homily()
    elif args.extract:
        from homily_monitor import audio_utils

        date_time_str = args.extract
        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        mass_file = os.path.join(CFG["paths"]["local_dir"], f"Mass-{date_time_str}.mp3")
        if not os.path.exists(mass_file):
            logger.error(f"Mass file not found: {mass_file}")
            sys.exit(1)
        logger.info(f"Extracting homily for {mass_file}...")
        audio_utils.extract_homily_from_vtt(mass_file)
    elif args.upload:
        from homily_monitor import wordpress_utils

        date_time_str = args.upload
        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        homily_file = os.path.join(CFG["paths"]["local_dir"], f"Homily-{date_time_str}.mp3")
        mass_file = os.path.join(CFG["paths"]["local_dir"], f"Mass-{date_time_str}.mp3")
        if not os.path.exists(homily_file) or not os.path.exists(mass_file):
            logger.error(f"Homily or Mass file not found: {homily_file} or {mass_file}")
            sys.exit(1)
        logger.info(f"Uploading homily for {homily_file}...")
        wordpress_utils.upload_to_wordpress(homily_file, mass_file)
    elif args.retry_upload_date:
        from homily_monitor import wordpress_utils

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info(f"Retry-checking WordPress uploads for homilies on {args.retry_upload_date}...")
        wordpress_utils.retry_missing_uploads_for_date(args.retry_upload_date)
    elif args.retry_upload_last_days:
        from homily_monitor import wordpress_utils

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info(
            f"Retry-checking WordPress uploads for homilies from the last {args.retry_upload_last_days} days..."
        )
        wordpress_utils.retry_missing_uploads_for_last_days(args.retry_upload_last_days)
    elif args.list_homilies_last_days:
        from homily_monitor import wordpress_utils

        os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
        log_utils.cleanup_logs(logger)
        logger.info(
            f"Listing WordPress and local homilies from the last {args.list_homilies_last_days} days..."
        )
        report = wordpress_utils.list_homilies_for_last_days(args.list_homilies_last_days)
        if report is None:
            sys.exit(1)
        print(report)
    else:
        try:
            os.makedirs(CFG["paths"]["local_dir"], exist_ok=True)
            log_utils.cleanup_logs(logger)
            main()
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user.")
