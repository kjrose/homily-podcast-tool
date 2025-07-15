## Historical Homily Downloader and Analyzer

import json
import os
import re
import subprocess
import time
import smtplib
import argparse
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
import openai
from openai import OpenAI
import boto3
from botocore.client import Config 
from botocore.exceptions import ClientError
import sqlite3
from collections import deque

# --- Load config ---
try:
    with open("config.json", encoding="utf-8") as f:
        cfg = json.load(f)
except FileNotFoundError:
    print("❌ Config file 'config.json' not found.")
    exit(1)  # Or sys.exit(1) if you import sys
except json.JSONDecodeError as e:
    print(f"❌ Invalid JSON in config file: {e}")
    exit(1)
except Exception as e:
    print(f"❌ Unexpected error loading config: {e}")
    exit(1)

# --- Init OpenAI client ---
client = OpenAI(api_key=cfg["openai_api_key"])

# --- S3 Config ---
S3_ENDPOINT = cfg["s3"]["endpoint"]
S3_BUCKET = cfg["s3"]["bucket"]
S3_FOLDER = cfg["s3"]["folder"]
ACCESS_KEY = cfg["s3"]["access_key"]
SECRET_KEY = cfg["s3"]["secret_key"]

# --- Paths ---
DB_PATH = cfg["paths"]["db_path"]
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS homilies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT,
    filename TEXT,
    date TEXT,
    title TEXT,
    description TEXT,
    special TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS compared_groups (
    group_key TEXT PRIMARY KEY,
    compared_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()

# --- Paths ---
LOCAL_DIR = cfg["paths"]["local_dir"]
BATCH_FILE = cfg["paths"]["batch_file"]

# --- Email ---
SMTP_SERVER = cfg["email"]["smtp_server"]
SMTP_PORT = cfg["email"]["smtp_port"]
EMAIL_FROM = cfg["email"]["from"]
EMAIL_TO = cfg["email"]["to"]
SMTP_USER = cfg["email"]["user"]
SMTP_PASS = cfg["email"]["password"]
EMAIL_SUBJECT = cfg["email"]["subject"]

# --- Validate paths ---
if not os.path.exists(LOCAL_DIR):
    print(f"❌ Local directory missing: {LOCAL_DIR}")
    exit(1) 
if not os.path.exists(BATCH_FILE):
    print(f"❌ Batch file missing: {BATCH_FILE}")
    exit(1)
import shutil
if not shutil.which("ffmpeg"):
    print("❌ FFmpeg not found in PATH.")
    exit(1)

# --- EMAIL FUNCTION ---
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

# --- CHECK TRANSCRIPT ---
def validate_and_get_transcript(transcript_path, mp3_path=None):
    if not os.path.exists(transcript_path):
        reason = "Transcript file is missing."
        if mp3_path:
            send_email_alert(mp3_path, reason)
        print(f"❌ {reason} for {transcript_path}")
        return None
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if len(content) < 10:
            reason = "Transcript is blank or too short."
            if mp3_path:
                send_email_alert(mp3_path, reason)
            print(f"⚠️ {reason} for {transcript_path}")
            return None
        return content
    except UnicodeDecodeError as e:
        reason = f"Encoding error in transcript: {e}"
        if mp3_path:
            send_email_alert(mp3_path, reason)
        print(f"❌ {reason} for {transcript_path}")
        return None
    except Exception as e:
        reason = f"Unexpected error reading transcript: {e}"
        if mp3_path:
            send_email_alert(mp3_path, reason)
        print(f"❌ {reason} for {transcript_path}")
        return None

def check_transcript(mp3_path, last_mod=None):
    transcript_path = os.path.splitext(mp3_path)[0] + ".txt"
    content = validate_and_get_transcript(transcript_path, mp3_path)
    if content:
        analyze_transcript_with_gpt(mp3_path, content, last_mod)
        extract_homily_from_vtt(mp3_path)

def analyze_transcript_with_gpt(mp3_path, transcript_text, last_mod):
    prompt = f"""
You are a helpful Catholic Mass assistant.

Read the following transcript of a Catholic homily and respond with the following:

1. Title of the homily (1 short phrase in Title Case)
2. Description of the homily (1–3 sentences)
3. Any special context clues: was it a school Mass, baptism, funeral, etc.?
4. If a title/description cannot be determined, say so clearly.

Transcript:
\"\"\"
{transcript_text.strip()}
\"\"\"
Respond using this JSON format:
{{
  "title": "...",
  "description": "...",
  "special": "..."
}}
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        content = response.choices[0].message.content
        result = json.loads(content)  # Validate JSON

        # Fallback for last_mod if not provided
        if last_mod is None:
            last_mod = datetime.fromtimestamp(os.path.getmtime(mp3_path), tz=timezone.utc)

        date = last_mod.date()
        hour = last_mod.hour
        if date.weekday() == 5:  # Saturday
            if hour >= 15:  # Assume Vigil if 3pm or later
                sunday = date + timedelta(days=1)
            else:
                sunday = date
        elif date.weekday() == 6:  # Sunday
            sunday = date
        else:
            sunday = date  # Default

        group_key = sunday.strftime("%Y-%m-%d")

        # Insert into DB
        date_str = date.strftime("%Y-%m-%d")
        cursor.execute("""
            INSERT INTO homilies (group_key, filename, date, title, description, special)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_key, os.path.basename(mp3_path), date_str, result["title"], result["description"], result["special"]))
        conn.commit()

    except openai.OpenAIError as e:  # Specific to OpenAI issues (import openai if needed)
        print(f"❌ OpenAI API error: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed (API error):\n\n{e}")
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON from GPT response: {e}")
        send_email_alert(mp3_path, f"GPT response not valid JSON:\n\n{content}\nError: {e}")
    except Exception as e:
        print(f"❌ Unexpected error in GPT analysis: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed:\n\n{e}")

# --- S3 HELPERS ---
def list_s3_files():
    files = []
    continuation_token = None
    while True:
        try:
            kwargs = {'Bucket': S3_BUCKET, 'Prefix': S3_FOLDER}
            if continuation_token:
                kwargs['ContinuationToken'] = continuation_token
            response = s3_client.list_objects_v2(**kwargs)
            if 'Contents' in response:
                for obj in response['Contents']:
                    key = obj['Key']
                    if key.startswith("Mass-") and key.endswith(".mp3"):
                        files.append({"Key": key, "LastModified": obj["LastModified"]})
            if not response.get('IsTruncated', False):
                break
            continuation_token = response.get('NextContinuationToken')
        except ClientError as e:
            print(f"❌ S3 client error listing files: {e.response['Error']['Message']}")
            send_email_alert("S3 Listing Failure", f"S3 client error listing bucket {S3_BUCKET}: {e}")
            return []
        except Exception as e:
            print(f"❌ Error listing S3 files: {e}")
            send_email_alert("S3 Listing Failure", f"Error listing files in bucket {S3_BUCKET}: {e}")
            return []  # Or raise if you want to stop main loop
    return files

def is_file_within_last_48_hours(last_modified):
    now = datetime.now(timezone.utc)
    return (now - last_modified) <= timedelta(hours=48)

def download_file(s3_key, local_path):
    try:
        print(f"⬇️ Downloading {s3_key} to {local_path}...")
        s3_client.download_file(S3_BUCKET, s3_key, local_path)
        print("✅ Download successful.")
    except ClientError as e:
        print(f"❌ S3 client error: {e.response['Error']['Message']}")
        send_email_alert(local_path, f"S3 client error for {s3_key}: {e}")
    except Exception as e:
        print(f"❌ Unexpected error downloading {s3_key}: {e}")
        send_email_alert(local_path, f"Unexpected download error for {s3_key}: {e}")

def run_batch_file(file_path):
    try:
        print(f"⚙️ Running batch file on {file_path}...")
        subprocess.run(f'"{BATCH_FILE}" "{file_path}"', shell=True, check=True)
        print("✅ Batch file completed.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Batch file failed with return code {e.returncode}: {e}")
        send_email_alert(file_path, f"Batch file execution failed:\n\n{e}")
    except FileNotFoundError:
        print(f"❌ Batch file {BATCH_FILE} not found.")
        send_email_alert(file_path, "Batch file is missing.")
    except Exception as e:
        print(f"❌ Unexpected error running batch file: {e}")
        send_email_alert(file_path, f"Unexpected error running batch file:\n\n{e}")

def analyze_latest_transcript():
    txt_files = [os.path.join(LOCAL_DIR, f) for f in os.listdir(LOCAL_DIR) if f.lower().endswith(".txt")]
    if not txt_files:
        print("❌ No transcript files found.")
        return
    latest_file = max(txt_files, key=os.path.getmtime)
    print(f"📝 Analyzing latest transcript: {latest_file}")
    content = validate_and_get_transcript(latest_file)
    if content:
        analyze_transcript_with_gpt(latest_file, content, None)
    
def parse_timestamp(ts: str) -> float:
        """Convert VTT/SRT timestamp to seconds."""
        if '.' in ts:
            time_part, ms = ts.split('.')
            h_m_s = time_part.split(':')
            ms = int(ms)
        else:
            h_m_s = ts.split(':')
            ms = 0

        if len(h_m_s) == 3:
            h, m, s = map(int, h_m_s)
        elif len(h_m_s) == 2:
            h = 0
            m, s = map(int, h_m_s)
        else:
            raise ValueError(f"Invalid timestamp: {ts}")

        return h * 3600 + m * 60 + s + ms / 1000.0

def extract_homily_from_vtt(mp3_path):
    vtt_path = os.path.splitext(mp3_path)[0] + ".vtt"
    if not os.path.exists(vtt_path):
        print(f"❌ No VTT file found for {mp3_path}")
        send_email_alert(mp3_path, "VTT file is missing.")
        return

    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:  # Safety
        print(f"❌ VTT file {vtt_path} not found (race condition?).")
        send_email_alert(mp3_path, "VTT file missing.")
        return
    except UnicodeDecodeError as e:
        print(f"❌ Encoding error reading VTT {vtt_path}: {e}")
        send_email_alert(mp3_path, f"Encoding error in VTT: {e}")
        return
    except Exception as e:
        print(f"❌ Unexpected error reading VTT {vtt_path}: {e}")
        send_email_alert(mp3_path, f"Unexpected error reading VTT: {e}")
        return

    entries = []
    current_time = None
    current_text = ""
    invalid_ts_count = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if "-->" in line:
            if current_time and current_text.strip():
                entries.append({
                    "start": current_time[0],
                    "end": current_time[1],
                    "text": current_text.strip()
                })
            match = re.match(r"(\d+:\d{2}(?::\d{2})?\.\d{3})\s-->\s(\d+:\d{2}(?::\d{2})?\.\d{3})", line)

            if match:
                start_str, end_str = match.groups()
                try:
                    start = parse_timestamp(start_str)
                    end = parse_timestamp(end_str)
                    current_time = (start, end)
                    current_text = ""
                except ValueError as e:
                    print(f"⚠️ Invalid timestamp in VTT line '{line}': {e}")
                    invalid_ts_count += 1
            else:
                print(f"⚠️ Unmatched timestamp line in VTT: {line}")
        elif current_time:
            current_text += " " + line

    if invalid_ts_count > 5:
        print(f"⚠️ High number of invalid timestamps in VTT: {invalid_ts_count}")
        send_email_alert(mp3_path, f"High invalid timestamps in VTT ({invalid_ts_count})")

    if current_time and current_text.strip():
        entries.append({
            "start": current_time[0],
            "end": current_time[1],
            "text": current_text.strip()
        })

    # Heuristics to find homily start and end
    found_gospel = False
    homily_start = None
    homily_end = None
    recent_texts = None

    end_markers = [
        "we pray to the lord",
        "lord, hear our prayer",
        "let us offer our prayers",
        "prayers of petition",
        "at the intercession",
        "i believe in one god",
        "prayer of the faithful",
        "prayers of the faithful"
    ]

    for entry in entries:
        text = entry["text"].lower()

        if "the gospel of the lord" in text or "praise to you" in text:
            found_gospel = True
            continue

        if found_gospel:
            if homily_start is None and len(entry["text"].strip()) > 0:
                homily_start = entry["start"]
                recent_texts = deque(maxlen=10)

            if homily_start:
                recent_texts.append(entry["text"])
                concat = " ".join(recent_texts).lower()
                if any(marker in concat for marker in end_markers):
                    homily_end = entry["start"]
                    break

    if homily_start is None:
        print("⚠️ Could not locate homily start.")
        send_email_alert(mp3_path, "Could not locate homily start in VTT.")
        return

    if homily_end is None:
        homily_end = entries[-1]["end"]
    
    duration = homily_end - homily_start
    if duration < 60 or duration > 1200:  # e.g., <1min or >20min
        print(f"⚠️ Suspicious homily duration: {duration:.2f}s")
        send_email_alert(mp3_path, f"Suspicious homily duration extracted: {duration:.2f}s")

    print(f"🎯 Extracting homily: {homily_start:.2f}s to {homily_end:.2f}s")

    output_path = os.path.splitext(mp3_path)[0].replace("Mass-", "Homily-") + ".mp3"
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-i", mp3_path,
        "-ss", str(homily_start),
        "-to", str(homily_end),
        "-c", "copy",
        output_path
    ]

    try:
        subprocess.run(ffmpeg_cmd, check=True)
        print(f"✅ Homily saved as: {output_path}")
    except Exception as e:
        print(f"❌ FFmpeg error: {e}")
        send_email_alert(mp3_path, f"FFmpeg error while extracting homily:\n\n{e}")

def get_latest_mp3(directory):
    try:
        mp3_files = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.lower().endswith(".mp3") and f.startswith("Mass-")
        ]
        if not mp3_files:
            return None
        return max(mp3_files, key=os.path.getmtime)
    except OSError as e:
        print(f"❌ Error accessing directory {directory}: {e}")
        send_email_alert(directory, f"Directory access error: {e}")
        return None

def extract_latest_homily():
    latest = get_latest_mp3(LOCAL_DIR)
    if not latest:
        print("❌ No MP3 files found in directory.")
        return

    print(f"🔍 Extracting homily from latest file: {latest}")
    extract_homily_from_vtt(latest)

def run_latest_test():
    latest = get_latest_mp3(LOCAL_DIR)
    if not latest:
        print("❌ No .mp3 files found.")
        return

    print(f"🎯 Found latest MP3: {latest}")
    run_batch_file(latest)

    transcript_path = os.path.splitext(latest)[0] + ".txt"
    content = validate_and_get_transcript(transcript_path, latest)
    if content:
        analyze_transcript_with_gpt(latest, content, None)

def check_for_completed_weekends():
    now = datetime.now(timezone.utc)
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

                        compare_response = client.chat.completions.create(
                            model="gpt-4-turbo",
                            messages=[{"role": "user", "content": compare_prompt}],
                            temperature=0.1,
                        )
                        compare_content = compare_response.choices[0].message.content
                        compare_result = json.loads(compare_content)

                        if compare_result["status"] == "deviations":
                            send_deviation_email(gk, compare_result["summary"], summaries_str)
                    # Mark as compared
                    cursor.execute("INSERT INTO compared_groups (group_key) VALUES (?)", (gk,))
                    conn.commit()
        except ValueError:
            pass  # Invalid group_key format

# --- S3 CLIENT INIT ---
s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    config=Config(s3={"addressing_style": "path"})
)

# --- MAIN LOOP ---
def main():
    print("📡 Starting S3 monitoring...")
    while True:
        s3_files = list_s3_files()

        for file in s3_files:
            s3_key = file["Key"]
            file_name = os.path.basename(s3_key)
            local_path = os.path.join(LOCAL_DIR, file_name)

            if not is_file_within_last_48_hours(file["LastModified"]):
                continue
            if os.path.exists(local_path):
                continue

            download_file(s3_key, local_path)
            run_batch_file(local_path)
            check_transcript(local_path, file["LastModified"])

        check_for_completed_weekends()
        time.sleep(60)

# --- TEST MODE ---
def test_email():
    print("📤 Sending test alert email...")
    send_email_alert("TEST-Mass.mp3", "This is a test of the transcript alert system.")

# --- CLI HANDLER ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mass Downloader and Transcript Checker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true", help="Send a test email alert")
    group.add_argument("--latest", action="store_true", help="Run batch + GPT analysis on latest .mp3 file")
    group.add_argument("--analyze-latest", action="store_true", help="Analyze the latest transcript file")
    group.add_argument("--extract-latest-homily", action="store_true", help="Extract homily from latest .mp3 + VTT")
    args = parser.parse_args()

    if args.test:
        test_email()
    elif args.analyze_latest:
        analyze_latest_transcript()
    elif args.latest:
        run_latest_test()
    elif args.extract_latest_homily:
        extract_latest_homily()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("🛑 Monitoring stopped by user.")