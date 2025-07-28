# homily_monitor/helpers.py

import os
from datetime import datetime, timezone
from collections import Counter
import json
import logging

from homily_monitor.config_loader import CFG
from .email_utils import send_email_alert
from .gpt_utils import analyze_transcript_with_gpt
from .audio_utils import extract_homily_from_vtt, run_batch_file 
from .gpt_utils import client
from .database import get_conn

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

LOCAL_DIR = CFG["paths"]["local_dir"]


def validate_and_get_transcript(transcript_path, mp3_path=None):
    if not os.path.exists(transcript_path):
        reason = "Transcript file is missing."
        if mp3_path:
            logger.error(f"❌ {reason} for {transcript_path}")
            send_email_alert(mp3_path, reason)
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if len(content) < 10:
            reason = "Transcript is blank or too short."
            if mp3_path:
                logger.warning(f"⚠️ {reason} for {transcript_path}")
                send_email_alert(mp3_path, reason)
            return None
        # Garbage (repetitive/low-variety) check
        words = content.lower().split()
        unique_words = set(words)
        if len(words) > 50 and len(unique_words) < 10:  # Long but low diversity
            reason = "Transcript appears to be garbage (highly repetitive)."
            if mp3_path:
                logger.warning(f"⚠️ {reason} for {transcript_path}")
                send_email_alert(mp3_path, reason)
            return None
        
        # More advanced repetition check
        word_counts = Counter(words)
        most_common_count = word_counts.most_common(1)[0][1] if word_counts else 0
        if len(words) > 50 and most_common_count / len(words) > 0.5:  # One word/phrase dominates
            reason = "Transcript appears to be garbage (dominant repetition)."
            if mp3_path:
                logger.warning(f"⚠️ {reason} for {transcript_path}")
                send_email_alert(mp3_path, reason)
            return None
        logger.debug(f"✅ Valid transcript loaded from {transcript_path}")
        return content
    except UnicodeDecodeError as e:
        reason = f"Encoding error in transcript: {e}"
        if mp3_path:
            logger.error(f"❌ {reason} for {transcript_path}")
            send_email_alert(mp3_path, reason)
        return None
    except Exception as e:
        reason = f"Unexpected error reading transcript: {e}"
        if mp3_path:
            logger.error(f"❌ {reason} for {transcript_path}")
            send_email_alert(mp3_path, reason)
        return None


def check_transcript(mp3_path, last_mod=None):
    transcript_path = os.path.splitext(mp3_path)[0] + ".txt"
    logger.info(f"Checking transcript for {mp3_path}...")
    content = validate_and_get_transcript(transcript_path, mp3_path)
    if content:
        logger.info(f"Analyzing transcript for {mp3_path} with GPT...")
        analyze_transcript_with_gpt(mp3_path, content, last_mod)
        logger.info(f"Extracting homily from {mp3_path}...")
        extract_homily_from_vtt(mp3_path)


def analyze_latest_transcript():
    logger.info("Analyzing latest transcript...")
    txt_files = [
        os.path.join(LOCAL_DIR, f)
        for f in os.listdir(LOCAL_DIR)
        if f.lower().endswith(".txt")
    ]
    if not txt_files:
        logger.error("❌ No transcript files found.")
        return
    latest_file = max(txt_files, key=os.path.getmtime)
    logger.info(f"📝 Analyzing latest transcript: {latest_file}")
    content = validate_and_get_transcript(latest_file)
    if content:
        logger.info(f"Processing {latest_file} with GPT...")
        analyze_transcript_with_gpt(latest_file, content, None)


def get_latest_mp3(directory):
    try:
        logger.debug(f"Searching for latest MP3 in {directory}...")
        mp3_files = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.lower().endswith(".mp3") and f.startswith("Mass-")
        ]
        if not mp3_files:
            logger.warning("⚠️ No MP3 files found in directory.")
            return None
        latest = max(mp3_files, key=os.path.getmtime)
        logger.debug(f"Found latest MP3: {latest}")
        return latest
    except OSError as e:
        logger.error(f"❌ Error accessing directory {directory}: {e}")
        send_email_alert(directory, f"Directory access error: {e}")
        return None


def extract_latest_homily():
    logger.info("Extracting latest homily...")
    latest = get_latest_mp3(LOCAL_DIR)
    if not latest:
        logger.error("❌ No MP3 files found in directory.")
        return

    logger.info(f"🔍 Extracting homily from latest file: {latest}")
    extract_homily_from_vtt(latest)


def run_latest_test():
    logger.info("Running latest test...")
    latest = get_latest_mp3(LOCAL_DIR)
    if not latest:
        logger.error("❌ No .mp3 files found.")
        return

    logger.info(f"🎯 Found latest MP3: {latest}")
    run_batch_file(latest)

    transcript_path = os.path.splitext(latest)[0] + ".txt"
    content = validate_and_get_transcript(transcript_path, latest)
    if content:
        logger.info(f"Analyzing {latest} with GPT...")
        analyze_transcript_with_gpt(latest, content, None)


def test_email():
    logger.info("📤 Sending test alert email...")
    send_email_alert("TEST-Mass.mp3", "This is a test of the transcript alert system.")


def check_for_completed_weekends():
    logger.info("Checking for completed weekends...")
    now = datetime.now(timezone.utc)
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT group_key FROM homilies")
    groups = [row[0] for row in cursor.fetchall()]
    for gk in groups:
        try:
            sunday_date = datetime.strptime(gk, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            deadline = sunday_date.replace(hour=21, minute=0, second=0, microsecond=0)
            if now > deadline:
                cursor.execute("SELECT 1 FROM compared_groups WHERE group_key = ?", (gk,))
                if cursor.fetchone() is None:
                    cursor.execute("SELECT COUNT(*) FROM homilies WHERE group_key = ?", (gk,))
                    count = cursor.fetchone()[0]
                    if count >= 2:
                        cursor.execute("SELECT filename, title, description, special FROM homilies WHERE group_key = ?", (gk,))
                        rows = cursor.fetchall()
                        summaries = [
                            f"Filename: {row[0]}\nTitle: {row[1]}\nDescription: {row[2]}\nSpecial: {row[3]}"
                            for row in rows
                        ]
                        summaries_str = "\n\n---\n\n".join(summaries)

                        compare_prompt = f"""
You are a Catholic homily analyst.

Here are summaries of homilies from the same weekend:

{summaries_str}

Determine if they are all essentially the same homily or if there are significant deviations in content, theme, or special contexts.

If all similar, respond with: {{"status": "similar", "summary": "All homilies are consistent."}}

If deviations, respond with: {{"status": "deviations", "summary": "Detailed summary of differences, highlighting which ones deviate and how."}}

Respond in JSON.
"""

                        logger.info(f"Analyzing deviations for group_key {gk}...")
                        compare_response = client.chat.completions.create(
                            model="gpt-4-turbo",
                            messages=[{"role": "user", "content": compare_prompt}],
                            temperature=0.1,
                        )
                        compare_content = compare_response.choices[0].message.content
                        compare_result = json.loads(compare_content)

                        if compare_result["status"] == "deviations":
                            logger.info(f"Deviations detected for {gk}, sending email...")
                            #send_deviation_email(gk, compare_result["summary"], summaries_str)
                    # Mark as compared
                    logger.info(f"Marking {gk} as compared in database...")
                    cursor.execute("INSERT INTO compared_groups (group_key) VALUES (?)", (gk,))
                    conn.commit()
        except ValueError:
            logger.warning(f"⚠️ Invalid group_key format for {gk}")