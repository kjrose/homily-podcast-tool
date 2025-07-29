# homily_monitor/main.py

import argparse
import time
import os
import logging
from logging.handlers import RotatingFileHandler
import sys

from homily_monitor import (
    config_loader as cfg_mod,
    database,
    s3_utils,
    audio_utils,
    helpers,
    wordpress_utils  # Add this import
)

# Configure logging with UTF-8 encoding
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
if getattr(sys, 'frozen', False):  # PyInstaller check
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(base_dir, 'homily_monitor.log')
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(log_formatter)
logger = logging.getLogger('HomilyMonitor')
logger.setLevel(logging.DEBUG)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
logger.addHandler(console_handler)

try:
    CFG = cfg_mod.CFG
except Exception as e:
    logger.critical(f"Initialization failed due to config error: {e}")
    sys.exit(1)

_ = database.get_conn()

def main():
    logger.info("Starting S3 monitoring...")
    while True:
        try:
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
                s3_utils.download_file(s3_key, local_path)
                logger.info(f"Running batch file on {local_path}...")
                audio_utils.run_batch_file(local_path)
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
    group.add_argument("--test", action="store_true", help="Test")
    group.add_argument("--latest", action="store_true", help="Run batch + GPT analysis on latest .mp3 file")
    group.add_argument("--analyze-latest", action="store_true", help="Analyze the latest transcript file")
    group.add_argument("--extract-latest-homily", action="store_true", help="Extract homily from latest .mp3 + VTT")
    group.add_argument("--upload-latest-homily", action="store_true", help="Upload the latest extracted homily to WordPress as a draft")
    args = parser.parse_args()

    if args.test:
        logger.info("Sending test email...")
        helpers.test_email()
    elif args.analyze_latest:
        logger.info("Analyzing latest transcript...")
        helpers.analyze_latest_transcript()
    elif args.latest:
        logger.info("Running batch + GPT analysis on latest MP3...")
        helpers.run_latest_test()
    elif args.extract_latest_homily:
        logger.info("Extracting latest homily...")
        helpers.extract_latest_homily()
    elif args.upload_latest_homily:
        logger.info("Uploading latest homily to WordPress...")
        wordpress_utils.upload_latest_homily()
    else:
        try:
            main()
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user.")