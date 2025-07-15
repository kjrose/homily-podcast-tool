# homily_monitor/wordpress_utils.py

import os
import requests
import json

from .config_loader import CFG
from .email_utils import send_email_alert
from .database import get_conn  # Updated to use get_conn
from .helpers import validate_and_get_transcript
from .gpt_utils import analyze_transcript_with_gpt


WP_URL = CFG["wordpress"]["url"]  # e.g., https://example.com
WP_USER = CFG["wordpress"]["user"]
WP_APP_PASS = CFG["wordpress"]["app_password"]
LOCAL_DIR = CFG["paths"]["local_dir"]


def upload_to_wordpress(homily_path, original_mp3_path):
    # Get analysis from DB (assuming it's already inserted)
    conn = get_conn()
    cursor = conn.cursor()
    filename = os.path.basename(original_mp3_path)
    cursor.execute("SELECT title, description, special FROM homilies WHERE filename = ?", (filename,))
    row = cursor.fetchone()
    if not row:
        print(f"⚠️ No analysis found for {filename}; generating automatically...")
        # Generate analysis if missing
        transcript_path = os.path.splitext(original_mp3_path)[0] + ".txt"
        content = validate_and_get_transcript(transcript_path, original_mp3_path)
        if content:
            analyze_transcript_with_gpt(original_mp3_path, content, None)  # None for last_mod to use file mtime
            # Re-query after generation
            cursor.execute("SELECT title, description, special FROM homilies WHERE filename = ?", (filename,))
            row = cursor.fetchone()
        if not row:
            print(f"❌ Failed to generate analysis for {filename}")
            send_email_alert(homily_path, "Failed to generate analysis for homily upload.")
            return

    title, description, special = row
    content = description
    if special:
        content += f"\n\nSpecial context: {special}"

    # Step 1: Upload media
    media_url = f"{WP_URL}/wp-json/wp/v2/media"
    auth = (WP_USER, WP_APP_PASS)
    headers = {'Content-Disposition': f'attachment; filename="{os.path.basename(homily_path)}"'}
    with open(homily_path, 'rb') as f:
        response = requests.post(media_url, auth=auth, headers=headers, files={'file': f})
    
    if response.status_code != 201:
        print(f"❌ Media upload failed: {response.text}")
        send_email_alert(homily_path, f"Media upload to WP failed: {response.text}")
        return

    media_data = response.json()
    audio_url = media_data['source_url']

    # Step 2: Create podcast post
    post_url = f"{WP_URL}/wp-json/wp/v2/podcast"
    post_data = {
        "title": title,
        "content": content,
        "status": "draft",
        "meta": {
            "audio_file": audio_url
        }
    }
    response = requests.post(post_url, auth=auth, json=post_data)
    
    if response.status_code != 201:
        print(f"❌ Post creation failed: {response.text}")
        send_email_alert(homily_path, f"Podcast post creation failed: {response.text}")
        return

    print(f"✅ Uploaded homily as draft to WordPress: {response.json()['link']}")


def upload_latest_homily():
    # Find the latest homily file
    homily_files = [
        os.path.join(LOCAL_DIR, f)
        for f in os.listdir(LOCAL_DIR)
        if f.lower().endswith(".mp3") and f.startswith("Homily-")
    ]
    if not homily_files:
        print("❌ No homily files found.")
        return

    latest_homily = max(homily_files, key=os.path.getmtime)
    print(f"📤 Uploading latest homily: {latest_homily}")

    # Infer original MP3 filename (replace "Homily-" with "Mass-")
    original_filename = os.path.basename(latest_homily).replace("Homily-", "Mass-")
    original_mp3_path = os.path.join(LOCAL_DIR, original_filename)

    # Check if original exists (for DB lookup)
    if not os.path.exists(original_mp3_path):
        print(f"❌ Original MP3 not found for {latest_homily}")
        send_email_alert(latest_homily, "Original MP3 missing for latest homily upload.")
        return

    upload_to_wordpress(latest_homily, original_mp3_path)