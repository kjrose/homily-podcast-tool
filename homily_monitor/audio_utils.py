# homily_monitor/audio_utils.py

import os
import re
import subprocess
import json
from collections import deque
import logging

from .config_loader import CFG
from .email_utils import send_email_alert
from .gpt_utils import client
from pydub import AudioSegment

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

def is_dead_air(mp3_path, silence_thresh_dB=-40, min_silence_len=1000, silence_ratio_threshold=0.9):
    """Check if MP3 is mostly dead air (silent). Returns True if silent."""
    try:
        audio = AudioSegment.from_mp3(mp3_path)
        logger.debug(f"Loaded audio: duration {len(audio)/1000}s")
        silent_ranges = audio.detect_silence(silence_thresh_dB, min_silence_len)
        logger.debug(f"Silent ranges detected: {silent_ranges}")
        
        total_silence_duration = sum(end - start for start, end in silent_ranges) / 1000  # ms to s
        total_duration = len(audio) / 1000
        
        silence_ratio = total_silence_duration / total_duration if total_duration > 0 else 1
        logger.debug(f"Silence ratio: {silence_ratio}")
        
        if silence_ratio > silence_ratio_threshold:
            logger.warning(f"File {mp3_path} is mostly dead air (silence ratio: {silence_ratio})")
            send_email_alert(mp3_path, "The file is dead air.")
            return True
        return False
    except Exception as e:
        logger.error(f"Audio analysis failed for {mp3_path}: {e}")
        return False

def run_batch_file(file_path):
    if is_dead_air(file_path):
        return  # Skip processing
    try:
        logger.info(f"⚙️ Running batch file on {file_path}...")
        subprocess.run(f'"{BATCH_FILE}" "{file_path}"', shell=True, check=True)
        logger.info("✅ Batch file completed.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Batch file failed with return code {e.returncode} for {file_path}: {e}")
        send_email_alert(file_path, f"Batch file execution failed:\n\n{e}")
    except FileNotFoundError:
        logger.error(f"Batch file {BATCH_FILE} not found for {file_path}")
        send_email_alert(file_path, "Batch file is missing.")
    except Exception as e:
        logger.error(f"Unexpected error running batch file for {file_path}: {e}")
        send_email_alert(file_path, f"Unexpected error running batch file:\n\n{e}")

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
        logger.error(f"❌ No VTT file found for {mp3_path}")
        send_email_alert(mp3_path, "VTT file is missing.")
        return

    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logger.debug(f"Loaded VTT file {vtt_path} with {len(lines)} lines")
    except FileNotFoundError:  # Safety
        logger.error(f"❌ VTT file {vtt_path} not found (race condition?) for {mp3_path}")
        send_email_alert(mp3_path, "VTT file missing.")
        return
    except UnicodeDecodeError as e:
        logger.error(f"❌ Encoding error reading VTT {vtt_path} for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"Encoding error in VTT: {e}")
        return
    except Exception as e:
        logger.error(f"❌ Unexpected error reading VTT {vtt_path} for {mp3_path}: {e}")
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
                    logger.warning(f"⚠️ Invalid timestamp in VTT line '{line}' for {mp3_path}: {e}")
                    invalid_ts_count += 1
            else:
                logger.warning(f"⚠️ Unmatched timestamp line in VTT: {line} for {mp3_path}")
        elif current_time:
            current_text += " " + line

    if invalid_ts_count > 5:
        logger.warning(f"⚠️ High number of invalid timestamps in VTT for {mp3_path}: {invalid_ts_count}")
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
    recent_texts = deque(maxlen=10)

    gospel_end_markers = [
        "the gospel of the lord",
        "gospel of the lord",
        "praise to you"
    ]

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

        if any(marker in text for marker in gospel_end_markers):
            found_gospel = True
            continue

        if found_gospel and homily_start is None and len(entry["text"].strip()) > 0:
            homily_start = entry["start"]
            recent_texts.clear()  # Reset for end detection

        if homily_start:
            recent_texts.append(entry["text"])
            concat = " ".join(recent_texts).lower()
            if any(marker in concat for marker in end_markers):
                homily_end = entry["start"]
                break

    # GPT fallback only for start if not found
    if homily_start is None:
        logger.warning("⚠️ Heuristics failed; using GPT fallback for homily start for {mp3_path}")
        # Compile full VTT text for GPT
        full_vtt = "\n".join(lines)  # Raw VTT as string
        
        gpt_prompt = f"""
You are a Catholic liturgy expert. Analyze this VTT transcript of a Mass to find the start timestamp of the homily (sermon after Gospel).

The homily typically starts after "Gospel of the Lord" or "Praise to you, Lord Jesus Christ," often with a pause, then the preacher's opening (e.g., "Brothers and sisters," personal story, or Gospel reflection).

Return ONLY a JSON object with a single field "start_timestamp" containing the start timestamp (e.g., "13:52.380") or "" if undetermined. No explanation, no markdown, no code blocks, no additional text—just the raw JSON.

VTT:
{full_vtt}
"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": gpt_prompt}],
                temperature=0.2,
            )
            content = response.choices[0].message.content.strip()
            # Remove any potential markdown wrappers
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)  # Expects {"start_timestamp": "08:07.140"}
            gpt_start = result.get("start_timestamp", "")
            if gpt_start:
                # Find closest entry start time matching GPT's timestamp
                gpt_time = parse_timestamp(gpt_start)
                # Set homily_start to the closest start >= gpt_time
                homily_start = min((e['start'] for e in entries if e['start'] >= gpt_time), default=entries[-1]['start'] if entries else None)
                logger.info(f"GPT detected homily start: {gpt_start} (adjusted to {homily_start}) for {mp3_path}")
            else:
                raise ValueError("GPT could not determine start")
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON from GPT for {mp3_path}: {e} - Content: {content}")
            send_email_alert(mp3_path, "GPT returned invalid JSON for homily detection.")
            return
        except Exception as e:
            logger.error(f"❌ GPT fallback failed for {mp3_path}: {e}")
            send_email_alert(mp3_path, "GPT homily detection failed.")
            return

    # Now search for end if not found (or re-search from new start)
    if homily_start is not None and homily_end is None:
        recent_texts = deque(maxlen=10)
        end_found = False
        for entry in entries:
            if entry["start"] < homily_start:
                continue  # Skip until homily_start
            text = entry["text"].lower()
            recent_texts.append(entry["text"])
            concat = " ".join(recent_texts).lower()
            if any(marker in concat for marker in end_markers):
                homily_end = entry["start"]
                end_found = True
                break
        if not end_found:
            homily_end = entries[-1]["end"]

    if homily_start is None:
        logger.error("⚠️ Could not locate homily start for {mp3_path}")
        send_email_alert(mp3_path, "Could not locate homily start in VTT.")
        return

    duration = homily_end - homily_start
    if duration < 60 or duration > 1200:  # e.g., <1min or >20min
        logger.warning(f"⚠️ Suspicious homily duration: {duration:.2f}s for {mp3_path}")
        send_email_alert(mp3_path, f"Suspicious homily duration extracted: {duration:.2f}s")

    logger.info(f"🎯 Extracting homily: {homily_start:.2f}s to {homily_end:.2f}s for {mp3_path}")

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
        logger.info(f"✅ Homily saved as: {output_path}")
        
        # Import here to avoid circular import
        from .wordpress_utils import upload_to_wordpress
        
        # Upload to WordPress as draft
        upload_to_wordpress(output_path, mp3_path)
    except Exception as e:
        logger.error(f"❌ FFmpeg error for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"FFmpeg error while extracting homily:\n\n{e}")