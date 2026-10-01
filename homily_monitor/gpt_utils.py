# homily_monitor/gpt_utils.py

import json
import os
from datetime import datetime, timedelta, timezone
import openai
from openai import OpenAI
import logging
from io import BytesIO

from .config_loader import CFG
from .email_utils import send_email_alert
from .database import insert_homily  # Use insert_homily
from .speaker_utils import resolve_homilist
from .editorial import finalize_image_prompt
from .editorial_generator import EditorialGenerator
from .editorial_settings import get_editorial_profile

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

client = OpenAI(api_key=CFG["openai_api_key"])

AI_CFG = CFG.get("ai", {})
TEXT_MODEL = AI_CFG.get("text_model", "gpt-5.4")
VTT_FALLBACK_MODEL = AI_CFG.get("vtt_fallback_model", TEXT_MODEL)
DEVIATION_MODEL = AI_CFG.get("deviation_model", TEXT_MODEL)


def _find_homilist_audio_path(path):
    candidate = str(path or "").strip()
    if not candidate:
        return None

    if os.path.isfile(candidate) and candidate.lower().endswith((".mp3", ".wav", ".m4a")):
        return candidate

    root, ext = os.path.splitext(candidate)
    if ext.lower() == ".txt":
        mp3_candidate = f"{root}.mp3"
        if os.path.isfile(mp3_candidate):
            return mp3_candidate

    return None


def request_text_completion(prompt, temperature=0.5, model=None):
    response = client.chat.completions.create(
        model=model or TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


def _build_fallback_image_prompt(title, description, homily_text=None, profile=None):
    profile = profile or get_editorial_profile()
    return finalize_image_prompt(profile, f"Illustrate {title}: {description}", title, description, homily_text)


def _finalize_image_prompt(prompt, title, description, homily_text=None, profile=None):
    profile = profile or get_editorial_profile()
    return finalize_image_prompt(profile, prompt or f"Illustrate {title}: {description}", title, description, homily_text)


def analyze_transcript_with_gpt(mp3_path, transcript_text, last_mod):
    filename = os.path.basename(mp3_path)  # e.g., "Mass-2025-07-14_09-30.mp3"
    homilist_audio_path = _find_homilist_audio_path(mp3_path)
    homilist = resolve_homilist(homilist_audio_path) if homilist_audio_path else {
        "name": "",
        "confidence": None,
        "source": "",
        "fallback_label": "Homilist",
        "threshold": 80,
    }
    confirmed_homilist_name = " ".join(str(homilist.get("name", "")).split()).strip()
    homilist_confidence = homilist.get("confidence")

    if confirmed_homilist_name:
        homilist_prompt_guidance = (
            f"A speaker-identification pass matched the homilist as '{confirmed_homilist_name}' "
            f"with confidence {homilist_confidence} out of 100. Use that name naturally when "
            "referring to the preacher. Do not hedge or rename the person."
        )
    else:
        homilist_prompt_guidance = (
            "No homilist name was confirmed with sufficient confidence. Do not guess a person's name. "
            "If the description needs to refer to the preacher, use neutral terms such as "
            "'the homilist', 'the priest', or 'the deacon' based only on what the transcript itself supports."
        )
    
    profile = get_editorial_profile()

    try:
        logger.info(f"Analyzing transcript for {mp3_path} with GPT...")
        result, _ = EditorialGenerator(client, AI_CFG).metadata(
            profile, filename, transcript_text.strip(), homilist_prompt_guidance,
        )
        logger.info(f"Successfully parsed GPT response for {mp3_path}")
   
        # The filename records the Mass's local time; modification timestamps
        # can instead reflect when an old recording was copied or downloaded.
        try:
            if not filename.startswith("Mass-"):
                raise ValueError("Missing Mass- prefix")
            recorded_at = datetime.strptime(os.path.splitext(filename)[0][5:], "%Y-%m-%d_%H-%M")
        except ValueError:
            if last_mod is None:
                last_mod = datetime.fromtimestamp(os.path.getmtime(mp3_path), tz=timezone.utc)
            recorded_at = last_mod
            logger.warning(f"Cannot read recording date from {filename}; using modification time {recorded_at}")

        date = recorded_at.date()
        hour = recorded_at.hour
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
        insert_homily(
            group_key,
            os.path.basename(mp3_path),
            date_str,
            result["title"],
            result["description"],
            result["special"],
            result["liturgical_day"],
            result["lit_year"],
            homilist_name=confirmed_homilist_name,
            homilist_confidence=homilist_confidence,
            homilist_source=homilist.get("source", ""),
            editorial_profile=profile,
        )
        logger.info(f"Inserted analysis for {mp3_path} into database")
    except openai.OpenAIError as e:
        logger.error(f"OpenAI API error for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed (API error):\n\n{e}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON from GPT for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"GPT response not valid JSON: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in GPT analysis for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed:\n\n{e}")


def generate_podcast_image(title, description, homily_text=None, profile=None):
    """Generate production artwork using the same pipeline as WordPress previews."""
    profile = profile or get_editorial_profile()
    if not profile["images_enabled"]:
        logger.info("Image generation disabled by editorial profile %s", profile["revision"])
        return None
    try:
        generator = EditorialGenerator(client, AI_CFG)
        prompt, _ = generator.image_prompt(profile, title, description, homily_text)
        return BytesIO(generator.image(profile, prompt))
    except Exception as exc:
        logger.error("Podcast image generation failed (%s)", type(exc).__name__)
        return None
