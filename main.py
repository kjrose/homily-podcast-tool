# homily_monitor/main.py

import argparse
import time
import os

from homily_monitor import (
    config_loader as cfg_mod,
    database,
    s3_utils,
    audio_utils,
    helpers,
    wordpress_utils  # Add this import
)

CFG = cfg_mod.CFG
_ = database.get_conn()  # Initialize DB early


def main():
    print("📡 Starting S3 monitoring...")
    while True:
        s3_files = s3_utils.list_s3_files()

        for file in s3_files:
            s3_key = file["Key"]
            file_name = os.path.basename(s3_key)
            local_path = os.path.join(CFG["paths"]["local_dir"], file_name)

            if not s3_utils.is_file_within_last_48_hours(file["LastModified"]):
                continue
            if os.path.exists(local_path):
                continue

            s3_utils.download_file(s3_key, local_path)
            audio_utils.run_batch_file(local_path)
            helpers.check_transcript(local_path, file["LastModified"])

        helpers.check_for_completed_weekends()
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mass Downloader and Transcript Checker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true", help="Test")
    group.add_argument("--latest", action="store_true", help="Run batch + GPT analysis on latest .mp3 file")
    group.add_argument("--analyze-latest", action="store_true", help="Analyze the latest transcript file")
    group.add_argument("--extract-latest-homily", action="store_true", help="Extract homily from latest .mp3 + VTT")
    group.add_argument("--upload-latest-homily", action="store_true", help="Upload the latest extracted homily to WordPress as a draft")  # Add this
    args = parser.parse_args()

    if args.test:
        helpers.test_email()
    elif args.analyze_latest:
        helpers.analyze_latest_transcript()
    elif args.latest:
        helpers.run_latest_test()
    elif args.extract_latest_homily:
        helpers.extract_latest_homily()
    elif args.upload_latest_homily:
        wordpress_utils.upload_latest_homily()  # Add this handler
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("🛑 Monitoring stopped by user.")