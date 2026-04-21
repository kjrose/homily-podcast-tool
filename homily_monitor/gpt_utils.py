# homily_monitor/gpt_utils.py

import json
import os
from datetime import datetime, timedelta, timezone
import openai
from openai import OpenAI
import logging
import base64
from io import BytesIO

from .config_loader import CFG
from .email_utils import send_email_alert
from .database import insert_homily  # Use insert_homily

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

client = OpenAI(api_key=CFG["openai_api_key"])

AI_CFG = CFG.get("ai", {})
TEXT_MODEL = AI_CFG.get("text_model", "gpt-5.4")
TRANSCRIPT_ANALYSIS_MODEL = AI_CFG.get("analysis_model", TEXT_MODEL)
IMAGE_PROMPT_MODEL = AI_CFG.get("image_prompt_model", TEXT_MODEL)
VTT_FALLBACK_MODEL = AI_CFG.get("vtt_fallback_model", TEXT_MODEL)
DEVIATION_MODEL = AI_CFG.get("deviation_model", TEXT_MODEL)
IMAGE_MODEL = AI_CFG.get("image_model", "gpt-image-1.5")
IMAGE_SIZE = AI_CFG.get("image_size", "1024x1024")
IMAGE_QUALITY = AI_CFG.get("image_quality", "auto")

# Optional add-ons from config (default to empty strings if not present)
TITLE_ADDON = CFG.get("gpt_title_addon", "")
DESCRIPTION_ADDON = CFG.get("gpt_description_addon", "")
IMAGE_ADDON = CFG.get("gpt_image_addon", "")


def request_text_completion(prompt, temperature=0.5, model=None):
    response = client.chat.completions.create(
        model=model or TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def analyze_transcript_with_gpt(mp3_path, transcript_text, last_mod):
    filename = os.path.basename(mp3_path)  # e.g., "Mass-2025-07-14_09-30.mp3"
    
    prompt = f"""
You are a helpful Catholic Mass assistant.

The filename for the mass is: {filename}. It contains the date and time of the mass (YYYY-MM-DD_HH-MM). Use this date to accurately determine the liturgical day and year cycle.

For liturgical day: Calculate based on the date. If it's a Sunday, find the proper Sunday in Ordinary Time or feast. For weekdays, note the week and any memorials (e.g., "Monday of the 15th Week in Ordinary Time" or "Memorial of Saint Kateri Tekakwitha").

For liturgical year cycle: Sundays/solemnities use A/B/C (Year A if year % 3 == 2, B if 0, C if 1; but adjust for liturgical year starting in Advent previous year). Weekdays use Cycle I (odd calendar years) or II (even).

Cross-reference with transcript content like readings to confirm.

Read the following transcript of a Catholic homily and respond with the following:

1. Liturgical day (e.g., "14th Sunday in Ordinary Time" or "Memorial of Saint Kateri Tekakwitha" – infer precisely from date and transcript)
2. Liturgical year cycle (A, B, or C for Sundays; I or II for weekdays – infer from date and content)
3. A podcast title for the homily (1 short phrase in Title Case){TITLE_ADDON}
4. A description of the homily appropriate for a podcast (3-5 sentences){DESCRIPTION_ADDON}
5. Any special context clues: was it a school Mass, baptism, funeral, etc.? (use "" if none)
6. Respond ONLY with the raw JSON object, without any markdown, code blocks, wrappers, or additional text like ```json. Start directly with {{ and end with }}.

Transcript:
\"\"\"
{transcript_text.strip()}
\"\"\"
Respond using this JSON format:
{{
  "liturgical_day": "...",
  "lit_year": "...",
  "title": "...",
  "description": "...",
  "special": "..."
}}
"""
   
    try:
        logger.info(f"Analyzing transcript for {mp3_path} with GPT...")
        content = request_text_completion(
            prompt,
            temperature=0.5,
            model=TRANSCRIPT_ANALYSIS_MODEL,
        )
        logger.debug(f"GPT response content: {content}")
        result = json.loads(content)  # Validate JSON
        logger.info(f"Successfully parsed GPT response for {mp3_path}")
   
        # Fallback for last_mod if not provided
        if last_mod is None:
            last_mod = datetime.fromtimestamp(os.path.getmtime(mp3_path), tz=timezone.utc)
            logger.debug(f"Using file mtime {last_mod} as last_mod for {mp3_path}")

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
        logger.info(f"Inserting analysis for {mp3_path} into database with group_key {group_key}")
        insert_homily(group_key, os.path.basename(mp3_path), date_str, result["title"], result["description"], result["special"], result["liturgical_day"], result["lit_year"])
        logger.info(f"Inserted analysis for {mp3_path} into database")
    except openai.OpenAIError as e:
        logger.error(f"OpenAI API error for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed (API error):\n\n{e}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from GPT for {mp3_path}: {e} - Content: {content}")
        send_email_alert(mp3_path, f"GPT response not valid JSON:\n\n{content}\nError: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in GPT analysis for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed:\n\n{e}")


def generate_podcast_image(title, description):
    """Generate a square podcast image using GPT Image with a GPT-crafted prompt."""
    # Step 1: Use GPT to create an optimized GPT Image prompt
    prompt_craft = f"""
You are a creative AI artist specializing in podcast cover art for Catholic homilies.

Given the homily title: '{title}'
And description: '{description[:300]}' (truncated if long)

Craft a highly detailed, effective GPT Image prompt (50-100 words) for a 1024x1024 square image. Include:
- Vivid, engaging visual themes inspired by the homily (e.g., religious symbols, serene landscapes).
- Warm, inviting colors with high contrast for podcast thumbnails.
- Overlay the title '{title}' in elegant, readable font.
- Style: Realistic or illustrative, cinematic lighting, high detail.
- Avoid boring/generic; make it dynamic and thematic.{IMAGE_ADDON}

Respond ONLY with the raw image prompt string, no additional text.
"""

    try:
        logger.info(f"Crafting GPT Image prompt for {title}...")
        refined_prompt = request_text_completion(
            prompt_craft,
            temperature=0.7,
            model=IMAGE_PROMPT_MODEL,
        )
        logger.debug(f"Refined GPT Image prompt: {refined_prompt}")
    except Exception as e:
        logger.error(f"Failed to craft prompt with GPT for {title}: {e}")
        refined_prompt = f"A serene, inspirational square podcast cover for a Catholic homily titled '{title}'. Incorporate subtle religious symbols like a cross or Bible, with themes from: {description[:200]}. Use warm, inviting colors; overlay title in elegant font."  # Fallback
    
    try:
        logger.info(f"Generating podcast image for {title}...")
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=refined_prompt,
            size=IMAGE_SIZE,
            quality=IMAGE_QUALITY,
        )

        if response.data:
            image_base64 = response.data[0].b64_json
            if not image_base64:
                logger.warning(f"No image data available for {title}")
                return None
            image_bytes = base64.b64decode(image_base64)
            logger.debug(f"Generated image data for {title}")
            return BytesIO(image_bytes)
        logger.warning(f"No image data available for {title}")
        return None
    except Exception as e:
        logger.error(f"Failed to generate image for {title}: {e}")
        return None
