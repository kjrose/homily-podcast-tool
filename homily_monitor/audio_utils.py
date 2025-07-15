# homily_monitor/audio_utils.py

import os
import re
import subprocess
from collections import deque

from .config_loader import CFG
from .email_utils import send_email_alert
from .wordpress_utils import upload_to_wordpress

BATCH_FILE = CFG["paths"]["batch_file"]


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
        
        # Upload to WordPress as draft
        # upload_to_wordpress(output_path, mp3_path)
    except Exception as e:
        print(f"❌ FFmpeg error: {e}")
        send_email_alert(mp3_path, f"FFmpeg error while extracting homily:\n\n{e}")