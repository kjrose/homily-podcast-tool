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
IMAGE_QUALITY = AI_CFG.get("image_quality", "high")

# Optional add-ons from config (default to empty strings if not present)
TITLE_ADDON = CFG.get("gpt_title_addon", "")
DESCRIPTION_ADDON = CFG.get("gpt_description_addon", "")
IMAGE_ADDON = CFG.get("gpt_image_addon", "")

TEXT_FREE_IMAGE_RULES = (
    "The final artwork must contain no visible text of any kind: no title text, "
    "words, letters, numbers, captions, labels, logos, watermarks, signage, "
    "calligraphy, readable scripture, or readable book pages."
)

IMAGE_QUALITY_GUIDANCE = (
    "Create a single cohesive square composition with a clear focal subject and "
    "strong visual storytelling. Prefer premium sacred editorial illustration or "
    "painterly realism, cinematic lighting, rich depth, natural anatomy, expressive "
    "faces and hands, and a clean thumbnail-friendly silhouette. Avoid generic clip art, "
    "busy collages, split panels, poster layouts, awkward anatomy, and clutter."
)

HOMILY_IMAGE_TRANSCRIPT_CHAR_LIMIT = 4000


def request_text_completion(prompt, temperature=0.5, model=None):
    response = client.chat.completions.create(
        model=model or TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def _normalize_image_quality(value):
    allowed = {"low", "medium", "high", "auto"}
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    return "high"


def _normalize_homily_excerpt(homily_text, max_chars=HOMILY_IMAGE_TRANSCRIPT_CHAR_LIMIT):
    cleaned = " ".join(str(homily_text or "").split())
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    truncated = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{truncated or cleaned[:max_chars].strip()} ..."


def _build_fallback_image_prompt(title, description, homily_text=None):
    homily_excerpt = _normalize_homily_excerpt(homily_text)
    if homily_excerpt:
        return (
            "Create a polished square podcast cover image inspired directly by this Catholic homily excerpt: "
            f"{homily_excerpt} "
            f"Use the homily title '{title}' only as secondary thematic guidance, never as text in the image. "
            f"Supplemental homily description: {description[:220]}. "
            "Base the scene on the preached message itself rather than the Mass as a whole. "
            "Show one emotionally resonant sacred scene with refined composition, warm luminous "
            f"color, cinematic light, and meaningful Catholic symbolism only where it fits naturally. "
            f"{IMAGE_QUALITY_GUIDANCE} {TEXT_FREE_IMAGE_RULES}"
        )

    return (
        f"Create a polished square podcast cover image inspired by a Catholic homily about "
        f"'{title}'. Use the title only as thematic guidance, never as text in the image. "
        f"Draw from these homily themes: {description[:220]}. "
        f"Show one emotionally resonant sacred scene with refined composition, warm luminous "
        f"color, cinematic light, and meaningful Catholic symbolism only where it fits naturally. "
        f"{IMAGE_QUALITY_GUIDANCE} {TEXT_FREE_IMAGE_RULES}"
    )


def _finalize_image_prompt(prompt, title, description, homily_text=None):
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        base_prompt = _build_fallback_image_prompt(title, description, homily_text)

    homily_excerpt = _normalize_homily_excerpt(homily_text)
    if homily_excerpt:
        homily_context_rule = (
            f"Use this homily excerpt as the primary thematic source: {homily_excerpt} "
            "Treat the title and description only as supporting metadata. "
            "Align to the preached message rather than the broader Mass setting. "
        )
    else:
        homily_context_rule = (
            "Align to the homily itself rather than the broader Mass setting. "
        )

    return (
        f"{base_prompt}\n\n"
        "Additional non-negotiable requirements: "
        f"{homily_context_rule}"
        f"Use the homily title '{title}' only as thematic context, not as rendered text. "
        f"{IMAGE_QUALITY_GUIDANCE} {TEXT_FREE_IMAGE_RULES}"
    )


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


def generate_podcast_image(title, description, homily_text=None):
    """Generate a square podcast image using GPT Image with homily-first context."""
    homily_excerpt = _normalize_homily_excerpt(homily_text)
    if homily_excerpt:
        homily_context_block = f"""
Primary homily transcript excerpt (use this as the main source for the image concept; truncated if long):
{homily_excerpt}
"""
    else:
        homily_context_block = (
            "No homily transcript excerpt is available. Rely on the title and description only.\n"
        )

    prompt_craft = f"""
You are a creative director specializing in premium podcast cover art for Catholic homilies.

Given the homily title: '{title}'
And description: '{description[:300]}' (truncated if long)
{homily_context_block}

Write a production-ready image prompt for a 1024x1024 square cover image.

Requirements:
- If a homily transcript excerpt is provided, use it as the primary source. The image should reflect the preached message, emotional arc, and concrete imagery of the homily, not the Mass in general.
- Treat the title and description as secondary metadata for clarification only.
- Use the homily title only as thematic context. Never ask for the title, words, letters, typography, captions, logos, watermarks, signage, readable scripture, or any other visible text in the image.
- Focus on one cohesive, emotionally resonant sacred scene that aligns closely with the homily instead of generic church clip art.
- Make the image strong at thumbnail size with a clear focal point, layered depth, rich color, and cinematic light.
- Prefer premium sacred editorial illustration or painterly realism with natural anatomy and expressive faces/hands.
- Avoid busy collages, split layouts, poster design, stock-art feel, or awkward/deformed details.
- If symbolism is used, keep it subtle and directly relevant to the homily.
- Keep the output fully visual and text-free.{IMAGE_ADDON}

Respond ONLY with the raw image prompt string, no additional text.
"""

    try:
        logger.info(f"Crafting GPT Image prompt for {title}...")
        refined_prompt = request_text_completion(
            prompt_craft,
            temperature=0.7,
            model=IMAGE_PROMPT_MODEL,
        )
        refined_prompt = _finalize_image_prompt(refined_prompt, title, description, homily_text)
        logger.debug(f"Refined GPT Image prompt: {refined_prompt}")
    except Exception as e:
        logger.error(f"Failed to craft prompt with GPT for {title}: {e}")
        refined_prompt = _build_fallback_image_prompt(title, description, homily_text)
    
    try:
        logger.info(f"Generating podcast image for {title}...")
        response = client.images.generate(
            model=IMAGE_MODEL,
            prompt=refined_prompt,
            size=IMAGE_SIZE,
            quality=_normalize_image_quality(IMAGE_QUALITY),
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
