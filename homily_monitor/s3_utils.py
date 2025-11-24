# homily_monitor/s3_utils.py

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from datetime import datetime, timedelta, timezone
import logging

from .config_loader import CFG
from .email_utils import send_email_alert

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')
#logging.getLogger("botocore").setLevel(logging.DEBUG)   # very verbose
#logging.getLogger("boto3").setLevel(logging.DEBUG)

S3_ALERT_DELAYS = [
    timedelta(minutes=0),   # immediate
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=1),
]
S3_ALERT_DAILY_DELAY = timedelta(days=1)
S3_ALERT_STATE = {}
S3_ALERT_KEY = "s3"

S3_ENDPOINT = CFG["s3"]["endpoint"]
S3_BUCKET = CFG["s3"]["bucket"]
S3_FOLDER = CFG["s3"]["folder"]
ACCESS_KEY = CFG["s3"]["access_key"]
SECRET_KEY = CFG["s3"]["secret_key"]

# --- S3 CLIENT INIT ---
from botocore.config import Config as BotoConfig

s3_config = BotoConfig(
    region_name="us-east-1",
    signature_version="s3v4",
    s3={
        "addressing_style": "path",
        "payload_signing_enabled": False,  # << use UNSIGNED-PAYLOAD
    },
    retries={"max_attempts": 5, "mode": "standard"},
)

s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=s3_config,
)

def _advance_s3_alert_state(alert_key):
    """Move the alert state to the next delay window."""
    now = datetime.now(timezone.utc)
    state = S3_ALERT_STATE.get(alert_key)
    current_stage = state["stage"] if state else -1
    next_stage = min(current_stage + 1, len(S3_ALERT_DELAYS) - 1)
    if next_stage + 1 < len(S3_ALERT_DELAYS):
        wait_time = S3_ALERT_DELAYS[next_stage + 1]
    else:
        wait_time = S3_ALERT_DAILY_DELAY
    S3_ALERT_STATE[alert_key] = {
        "stage": next_stage,
        "next_allowed": now + wait_time,
    }


def _should_send_s3_alert(alert_key):
    now = datetime.now(timezone.utc)
    state = S3_ALERT_STATE.get(alert_key)
    if state is None or now >= state["next_allowed"]:
        _advance_s3_alert_state(alert_key)
        return True
    logger.debug(
        f"Suppressing S3 alert '{alert_key}' until "
        f"{state['next_allowed'].astimezone(timezone.utc).isoformat()}."
    )
    return False


def reset_s3_alert(alert_key=S3_ALERT_KEY):
    """Clear alert state after a successful S3 operation."""
    if alert_key in S3_ALERT_STATE:
        S3_ALERT_STATE.pop(alert_key, None)


def send_rate_limited_s3_alert(subject, body, alert_key=S3_ALERT_KEY):
    """Send an alert respecting the backoff schedule."""
    if _should_send_s3_alert(alert_key):
        send_email_alert(subject, body)


def list_s3_files():
    files = []
    continuation_token = None
    while True:
        try:
            kwargs = {"Bucket": S3_BUCKET, "Prefix": S3_FOLDER}
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            logger.debug(f"Listing S3 objects in {S3_BUCKET} with prefix {S3_FOLDER}...")
            response = s3_client.list_objects_v2(**kwargs)
            logger.debug("S3 list_objects_v2 response received.")
            if "Contents" in response:
                for obj in response["Contents"]:
                    key = obj["Key"]
                    if key.startswith("Mass-") and key.endswith(".mp3"):
                        files.append({"Key": key, "LastModified": obj["LastModified"]})
            if not response.get("IsTruncated", False):
                logger.debug(f"Completed listing {len(files)} files from {S3_BUCKET}")
                break
            continuation_token = response.get("NextContinuationToken")
            logger.debug(f"Continuing listing with token: {continuation_token}")
        except ClientError as e:
            error_msg = e.response["Error"]["Message"]
            logger.error(f"S3 client error listing files in {S3_BUCKET}: {error_msg}")
            send_rate_limited_s3_alert(
                "S3 Listing Failure",
                f"S3 client error listing bucket {S3_BUCKET}: {e}",
            )
            return []
        except Exception as e:
            logger.error(f"Error listing S3 files in {S3_BUCKET}: {e}")
            send_rate_limited_s3_alert(
                "S3 Listing Failure",
                f"Error listing files in bucket {S3_BUCKET}: {e}",
            )
            return []  # Or raise if you want to stop main loop
    reset_s3_alert()
    return files


def is_file_within_last_48_hours(last_modified):
    now = datetime.now(timezone.utc)
    result = (now - last_modified) <= timedelta(hours=48)
    logger.debug(f"Checking if {last_modified} is within 48 hours: {result}")
    return result


def download_file(s3_key, local_path):
    try:
        logger.info(f"Downloading {s3_key} to {local_path}...")
        s3_client.download_file(S3_BUCKET, s3_key, local_path)
        logger.info("Download successful.")
        reset_s3_alert()
    except ClientError as e:
        error_msg = e.response["Error"]["Message"]
        logger.error(f"S3 client error downloading {s3_key} to {local_path}: {error_msg}")
        send_rate_limited_s3_alert(
            "S3 Download Failure",
            f"S3 client error for {s3_key}: {e}",
        )
    except Exception as e:
        logger.error(f"Unexpected error downloading {s3_key} to {local_path}: {e}")
        send_rate_limited_s3_alert(
            "S3 Download Failure",
            f"Unexpected download error for {s3_key}: {e}",
        )
