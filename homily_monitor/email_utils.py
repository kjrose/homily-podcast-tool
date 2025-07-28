# homily_monitor/email_utils.py

import smtplib
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


def send_email_alert(mp3_path, reason="The transcript appears to be missing or empty."):
    msg = EmailMessage()
    msg["Subject"] = EMAIL_SUBJECT
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(f"Problem with transcript for:\n\n{mp3_path}\n\nReason: {reason}")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        logger.info(f"📧 Alert email sent for {mp3_path}")
    except Exception as e:
        logger.error(f"❌ Failed to send alert email for {mp3_path}: {e}")


def send_deviation_email(group_key, summary, details):
    msg = EmailMessage()
    msg["Subject"] = f"Homily Deviations for Weekend {group_key}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(f"Deviations detected:\n\n{summary}\n\nDetails:\n{details}")

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        logger.info("📨 Homily deviation summary email sent.")
    except Exception as e:
        logger.error(f"❌ Failed to send deviation summary email for weekend {group_key}: {e}")


def send_success_email(subject, message):
    """
    Send a success notification email.
    
    Args:
        subject (str): The email subject (e.g., "Homily Upload Successful").
        message (str): The body message detailing the success.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(message)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        logger.info(f"✅ Success email sent: {subject}")
    except Exception as e:
        logger.error(f"❌ Failed to send success email for {subject}: {e}")