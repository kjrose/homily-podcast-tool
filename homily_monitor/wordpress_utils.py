# homily_monitor/wordpress_utils.py

import os
import requests
import base64
import json
from io import BytesIO
from datetime import datetime
import logging
import pytz  # Add this for timezone handling

from .config_loader import CFG
from .email_utils import send_email_alert, send_success_email
from .database import get_conn  # Updated to use get_conn
from .helpers import validate_and_get_transcript
from .gpt_utils import analyze_transcript_with_gpt, generate_podcast_image


# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

WP_URL = CFG["wordpress"]["url"]  # e.g., https://example.com
WP_USER = CFG["wordpress"]["user"]
WP_APP_PASS = CFG["wordpress"]["app_password"]
LOCAL_DIR = CFG["paths"]["local_dir"]

def upload_to_wordpress(homily_path, original_mp3_path):
    conn = get_conn()
    cursor = conn.cursor()
    filename = os.path.basename(original_mp3_path)
    logger.info(f"Checking database for analysis of {filename}...")
    cursor.execute("SELECT title, description, special, liturgical_day, lit_year, date FROM homilies WHERE filename = ?", (filename,))
    row = cursor.fetchone()
    if not row:
        logger.warning(f"No analysis found for {filename}; generating automatically...")
        # Generate analysis if missing
        transcript_path = os.path.splitext(original_mp3_path)[0] + ".txt"
        content = validate_and_get_transcript(transcript_path, original_mp3_path)
        if content:
            logger.info(f"Generating analysis for {filename}...")
            analyze_transcript_with_gpt(original_mp3_path, content, None)  # None for last_mod to use file mtime
            # Re-query after generation
            cursor.execute("SELECT title, description, special, liturgical_day, lit_year, date FROM homilies WHERE filename = ?", (filename,))
            row = cursor.fetchone()
        if not row:
            logger.error(f"Failed to generate analysis for {filename}")
            send_email_alert(homily_path, "Failed to generate analysis for homily upload.")
            return

    title, description, special, lit_day, lit_year, date_str = row

    # Construct full title and set publish date from filename
    original_filename = os.path.basename(original_mp3_path)  # e.g., "Mass-2025-07-20_18-00.mp3"
    date_time_parts = original_filename.split("Mass-")[1].split(".mp3")[0]  # e.g., "2025-07-20_18-00"
    homily_datetime = datetime.strptime(date_time_parts, "%Y-%m-%d_%H-%M")
    publish_date_utc = homily_datetime.replace(tzinfo=pytz.UTC).isoformat()  # Convert to UTC ISO format for WP API

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    formatted_date = date_obj.strftime("%B %d, %Y")
    homilist = "**HOMILIST**"
    full_title = f"{formatted_date} – {lit_day or 'Unknown Sunday'} – {lit_year or 'Unknown'} – {homilist} – “{title}”"

    content = description
    if special:
        content += f"\n\nSpecial context: {special}"

    # Generate image
    logger.info(f"Generating podcast image for {full_title}...")
    image_buffer = generate_podcast_image(title, description)
    featured_media_id = None
    cover_image_url = None
    if image_buffer:
        # Upload image as media
        media_url = f"{WP_URL}/wp-json/wp/v2/media"
        auth = (WP_USER, WP_APP_PASS)
        headers = {'Content-Disposition': 'attachment; filename="podcast_cover.png"'}
        response = requests.post(media_url, auth=auth, headers=headers, files={'file': ('podcast_cover.png', image_buffer, 'image/png')})
        
        if response.status_code == 201:
            media_data = response.json()
            featured_media_id = media_data['id']
            cover_image_url = media_data['source_url']
            logger.info("Podcast image uploaded.")
        else:
            logger.error(f"Image upload failed for {full_title}: {response.text}")
            send_email_alert(homily_path, f"Image upload to WP failed: {response.text}")

    # Step 1: Upload audio media
    logger.info(f"Uploading audio media for {full_title}...")
    media_url = f"{WP_URL}/wp-json/wp/v2/media"
    auth = (WP_USER, WP_APP_PASS)
    headers = {'Content-Disposition': f'attachment; filename="{os.path.basename(homily_path)}"'}
    with open(homily_path, 'rb') as f:
        response = requests.post(media_url, auth=auth, headers=headers, files={'file': f})
    
    if response.status_code != 201:
        logger.error(f"Media upload failed for {full_title}: {response.text}")
        send_email_alert(homily_path, f"Media upload to WP failed: {response.text}")
        return

    media_data = response.json()
    audio_url = media_data['source_url']

    # Step 2: Create podcast post with publish date
    logger.info(f"Creating podcast post for {full_title} with publish date {publish_date_utc}...")
    post_url = f"{WP_URL}/wp-json/wp/v2/podcast"
    post_data = {
        "title": full_title,
        "content": content,
        "status": "draft",
        "date_gmt": publish_date_utc,  # Set publish date to homily time in UTC
        "meta": {
            "audio_file": audio_url
        }
    }
    if featured_media_id:
        post_data["featured_media"] = featured_media_id
    if cover_image_url and featured_media_id:
        post_data["meta"]["cover_image"] = cover_image_url
        post_data["meta"]["cover_image_id"] = str(featured_media_id)
    response = requests.post(post_url, auth=auth, json=post_data)
    
    if response.status_code != 201:
        logger.error(f"Post creation failed for {full_title}: {response.text}")
        send_email_alert(homily_path, f"Podcast post creation failed: {response.text}")
        return

    send_success_email("Homily Upload Successful", f"Successfully uploaded homily to WordPress as a draft: {full_title} \n\nView draft: {response.json()['link']} \n\nAudio URL: {audio_url} \n\nImage URL: {cover_image_url} \n\n Description: {description} \n\n Publish Post: {WP_URL}/wp-admin/post.php?post={response.json()['id']}&action=edit")
    logger.info(f"Uploaded homily as draft to WordPress: {response.json()['link']}")


def upload_latest_homily():
    # Find the latest homily file
    logger.info("Searching for the latest homily file...")
    homily_files = [
        os.path.join(LOCAL_DIR, f)
        for f in os.listdir(LOCAL_DIR)
        if f.lower().endswith(".mp3") and f.startswith("Homily-")
    ]
    if not homily_files:
        logger.error("No homily files found.")
        return

    latest_homily = max(homily_files, key=os.path.getmtime)
    logger.info(f"Uploading latest homily: {latest_homily}")

    # Infer original MP3 filename (replace "Homily-" with "Mass-")
    original_filename = os.path.basename(latest_homily).replace("Homily-", "Mass-")
    original_mp3_path = os.path.join(LOCAL_DIR, original_filename)

    # Check if original exists (for DB lookup)
    if not os.path.exists(original_mp3_path):
        logger.error(f"Original MP3 not found for {latest_homily}")
        send_email_alert(latest_homily, "Original MP3 missing for latest homily upload.")
        return

    logger.info(f"Processing upload for {latest_homily} with original {original_mp3_path}...")
    upload_to_wordpress(latest_homily, original_mp3_path)