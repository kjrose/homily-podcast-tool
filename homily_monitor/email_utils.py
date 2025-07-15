# homily_monitor/email_utils.py

import smtplib
from email.message import EmailMessage

from .config_loader import CFG

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
        print(f"📧 Alert email sent for {mp3_path}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


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
        print("📨 Homily deviation summary email sent.")
    except Exception as e:
        print(f"❌ Failed to send deviation summary email: {e}")
