# homily_monitor/email_utils.py

import smtplib
import time
from email.message import EmailMessage
import logging

from .config_loader import CFG

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

SMTP_SERVER = CFG["email"]["smtp_server"]
SMTP_PORT = CFG["email"]["smtp_port"]
EMAIL_FROM = CFG["email"]["from"]
EMAIL_TO = CFG["email"]["to"]
SMTP_USER = CFG["email"]["user"]
SMTP_PASS = CFG["email"]["password"]
EMAIL_SUBJECT = CFG["email"]["subject"]
NETWORK_CFG = CFG.get("network", {})


def _get_positive_number(name, default, cast=float):
    try:
        value = cast(NETWORK_CFG.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


SMTP_TIMEOUT_SECONDS = _get_positive_number("smtp_timeout_seconds", 30.0)
SMTP_RETRY_ATTEMPTS = int(_get_positive_number("smtp_retry_attempts", 2, int))
SMTP_RETRY_DELAY_SECONDS = _get_positive_number("smtp_retry_delay_seconds", 2.0)


def _send_email(subject, message, success_log_message, failure_log_context):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(message)

    for attempt in range(1, SMTP_RETRY_ATTEMPTS + 1):
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                smtp.login(SMTP_USER, SMTP_PASS)
                smtp.send_message(msg)
            logger.info(success_log_message)
            return True
        except Exception as e:
            last_attempt = attempt == SMTP_RETRY_ATTEMPTS
            log_message = (
                f"Failed to send {failure_log_context} "
                f"(attempt {attempt}/{SMTP_RETRY_ATTEMPTS}): {e}"
            )
            if last_attempt:
                logger.error(log_message)
                return False

            logger.warning(log_message)
            time.sleep(SMTP_RETRY_DELAY_SECONDS * (2 ** (attempt - 1)))


def send_email_alert(mp3_path, reason="The transcript appears to be missing or empty."):
    message = f"Problem with transcript for:\n\n{mp3_path}\n\nReason: {reason}"
    return _send_email(
        EMAIL_SUBJECT,
        message,
        f"Alert email sent for {mp3_path}",
        f"alert email for {mp3_path}",
    )


def send_operational_alert(subject, message):
    return _send_email(
        subject,
        message,
        f"Operational alert email sent: {subject}",
        f"operational alert email for {subject}",
    )


def send_deviation_email(group_key, summary, details):
    return _send_email(
        f"Homily Deviations for Weekend {group_key}",
        f"Deviations detected:\n\n{summary}\n\nDetails:\n{details}",
        "Homily deviation summary email sent.",
        f"deviation summary email for weekend {group_key}",
    )


def send_success_email(subject, message):
    return _send_email(
        subject,
        message,
        f"Success email sent: {subject}",
        f"success email for {subject}",
    )
