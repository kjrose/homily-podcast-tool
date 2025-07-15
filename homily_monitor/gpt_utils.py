# homily_monitor/gpt_utils.py

import json
import os
from datetime import datetime, timedelta, timezone
import openai
from .database import get_conn
from openai import OpenAI

from homily_monitor.config_loader import CFG
from .email_utils import send_email_alert


client = OpenAI(api_key=CFG["openai_api_key"])


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
            last_mod = datetime.fromtimestamp(
                os.path.getmtime(mp3_path), tz=timezone.utc
            )

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
        conn = get_conn()
        cursor = conn.cursor()
        date_str = date.strftime("%Y-%m-%d")
        cursor.execute(
            """
            INSERT INTO homilies (group_key, filename, date, title, description, special)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (
                group_key,
                os.path.basename(mp3_path),
                date_str,
                result["title"],
                result["description"],
                result["special"],
            ),
        )
        conn.commit()

    except (
        openai.OpenAIError
    ) as e:  # Specific to OpenAI issues (import openai if needed)
        print(f"❌ OpenAI API error: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed (API error):\n\n{e}")
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON from GPT response: {e}")
        send_email_alert(
            mp3_path, f"GPT response not valid JSON:\n\n{content}\nError: {e}"
        )
    except Exception as e:
        print(f"❌ Unexpected error in GPT analysis: {e}")
        send_email_alert(mp3_path, f"GPT analysis failed:\n\n{e}")
