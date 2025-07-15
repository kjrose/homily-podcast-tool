# homily_monitor/wordpress_utils.py

import os
import requests
import json

from .config_loader import CFG
from .email_utils import send_email_alert
from .database import get_conn

WP_URL = CFG["wordpress"]["url"]  # e.g., https://example.com
WP_USER = CFG["wordpress"]["user"]
WP_APP_PASS = CFG["wordpress"]["app_password"]


def upload_to_wordpress(homily_path, original_mp3_path):
    # Get analysis from DB (assuming it's already inserted)
    conn = get_conn()  # Add this
    cursor = conn.cursor()  # Update to conn.cursor()
    filename = os.path.basename(original_mp3_path)
    cursor.execute(
        "SELECT title, description, special FROM homilies WHERE filename = ?",
        (filename,),
    )
    row = cursor.fetchone()
    if not row:
        print(f"❌ No analysis found for {filename}")
        send_email_alert(homily_path, "No analysis found for homily upload.")
        return

    title, description, special = row
    content = description
    if special:
        content += f"\n\nSpecial context: {special}"

    # Step 1: Upload media
    media_url = f"{WP_URL}/wp-json/wp/v2/media"
    auth = (WP_USER, WP_APP_PASS)
    headers = {
        "Content-Disposition": f'attachment; filename="{os.path.basename(homily_path)}"'
    }
    with open(homily_path, "rb") as f:
        response = requests.post(
            media_url, auth=auth, headers=headers, files={"file": f}
        )

    if response.status_code != 201:
        print(f"❌ Media upload failed: {response.text}")
        send_email_alert(homily_path, f"Media upload to WP failed: {response.text}")
        return

    media_data = response.json()
    audio_url = media_data["source_url"]

    # Step 2: Create podcast post
    post_url = f"{WP_URL}/wp-json/wp/v2/podcast"
    post_data = {
        "title": title,
        "content": content,
        "status": "draft",
        "meta": {"audio_file": audio_url},
    }
    response = requests.post(post_url, auth=auth, json=post_data)

    if response.status_code != 201:
        print(f"❌ Post creation failed: {response.text}")
        send_email_alert(homily_path, f"Podcast post creation failed: {response.text}")
        return

    print(f"✅ Uploaded homily as draft to WordPress: {response.json()['link']}")
