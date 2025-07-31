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

# Configure ffmpeg path explicitly
AudioSegment.ffmpeg = r"C:\Users\kjrose\AppData\Local\Microsoft\WinGet\Links\ffmpeg.EXE"

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

BATCH_FILE = CFG["paths"]["batch_file"]

def normalize_audio(mp3_path, output_path=None):
    """Normalize audio using FFmpeg loudnorm to -23 LUFS."""
    if output_path is None:
        output_path = mp3_path  # Overwrite by default
    try:
        logger.info(f"Normalizing audio for {mp3_path} to -23 LUFS...")
        
        # First pass to analyze loudness
        first_pass_cmd = [
            "ffmpeg",
            "-i", mp3_path,
            "-af", "loudnorm=I=-23:TP=-1:LRA=7:print_format=json",
            "-f", "null",
            "-y", "NUL"
        ]
        first_pass = subprocess.run(first_pass_cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        first_pass_output = first_pass.stderr
        logger.debug(f"First pass raw output: {first_pass_output}")

        # Extract JSON from output (find the {} block)
        json_match = re.search(r'\{.*?\}', first_pass_output, re.DOTALL | re.MULTILINE)
        if json_match:
            json_str = json_match.group(0)
            logger.debug(f"Extracted JSON string: {json_str}")
            first_pass_data = json.loads(json_str)
            measured_i = first_pass_data["input_i"]
            measured_tp = first_pass_data["input_tp"]
            measured_lra = first_pass_data["input_lra"]
            measured_thresh = first_pass_data["input_thresh"]
            logger.debug(f"Measured values: I={measured_i}, TP={measured_tp}, LRA={measured_lra}, Thresh={measured_thresh}")
        else:
            logger.error(f"Error: No JSON found in first pass output for {mp3_path}")
            send_email_alert(mp3_path, f"FFmpeg normalization failed to parse JSON:\n\n{first_pass_output}")
            return  # Skip normalization on parse failure

        # Second pass to apply normalization
        second_pass_cmd = [
            "ffmpeg",
            "-i", mp3_path,
            "-af", f"loudnorm=I=-23:TP=-1:LRA=7:measured_I={measured_i}:measured_LRA={measured_lra}:measured_TP={measured_tp}:measured_thresh={measured_thresh}:linear=true:print_format=summary",
            "-y",
            output_path
        ]
        subprocess.run(second_pass_cmd, check=True, encoding='utf-8')
        logger.info(f"Success: Audio normalized and saved to {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: Audio normalization failed for {mp3_path}: {e.stderr}")
        send_email_alert(mp3_path, f"Audio normalization failed:\n\n{e.stderr}")
    except Exception as e:
        logger.error(f"Error: Unexpected error normalizing audio for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"Unexpected normalization error:\n\n{e}")

def is_dead_air(mp3_path, silence_thresh_dB=-40, min_silence_len=1000, silence_ratio_threshold=0.9):
    """Check if MP3 is mostly dead air using FFmpeg silencedetect. Returns True if silent."""
    try:
        logger.debug(f"Running silencedetect on {mp3_path} with thresh {silence_thresh_dB}dB, min_len {min_silence_len/1000}s")
        cmd = [
            "ffmpeg",
            "-i", mp3_path,
            "-af", f"silencedetect=n={silence_thresh_dB}dB:d={min_silence_len/1000}",
            "-f", "null",
            "-y", "NUL"  # Windows null output
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        output = result.stderr  # silencedetect logs to stderr
        
        # Parse silence durations from output (e.g., "silence_duration: 1.234")
        silent_durations = []
        for line in output.splitlines():
            if "silence_duration" in line:
                duration_str = line.split("silence_duration: ")[1].split()[0]  # Get first number
                silent_durations.append(float(duration_str))
        
        total_silence_duration = sum(silent_durations)
        
        # Get total duration from ffmpeg output (e.g., "Duration: 00:05:30.00")
        duration_line = next((line for line in output.splitlines() if "Duration:" in line), None)
        if duration_line:
            duration_str = duration_line.split("Duration: ")[1].split(",")[0]
            h, m, s = map(float, duration_str.split(":"))
            total_duration = h * 3600 + m * 60 + s
        else:
            logger.warning(f"Could not parse duration for {mp3_path}, using 1s as fallback")
            total_duration = 1  # Avoid division by zero
        
        silence_ratio = total_silence_duration / total_duration if total_duration > 0 else 1
        logger.debug(f"Silence ratio: {silence_ratio}")
        
        if silence_ratio > silence_ratio_threshold:
            logger.warning(f"File {mp3_path} is mostly dead air (silence ratio: {silence_ratio})")
            send_email_alert(mp3_path, "The file is dead air.")
            return True
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: ffmpeg silencedetect failed for {mp3_path}: {e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Error: Audio analysis failed for {mp3_path}: {e}")
        return False

def run_batch_file(file_path, batch_file=BATCH_FILE):
    """Run the batch file on the given file path, after normalizing audio."""
    # Normalize audio first
    normalized_path = os.path.splitext(file_path)[0] + "_normalized.mp3"
    normalize_audio(file_path, normalized_path)
    
    if is_dead_air(normalized_path):
        os.remove(normalized_path)  # Clean up if dead air
        return  # Skip processing
    
    # Replace original with normalized file if successful
    if os.path.exists(normalized_path):
        os.replace(normalized_path, file_path)
        logger.info(f"Success: Replaced {file_path} with normalized version")

    try:
        logger.info(f"Running batch file on {file_path}...")
        # Capture output with UTF-8 encoding
        result = subprocess.run(
            f'"{batch_file}" "{file_path}"',
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8'
        )
        logger.debug(f"Batch output: {result.stdout}")
        if result.stderr:
            logger.warning(f"Batch errors: {result.stderr}")
        logger.info(f"Success: Batch file completed.")
        
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: Batch file failed with return code {e.returncode} for {file_path}: {e.stderr}")
        send_email_alert(file_path, f"Batch file execution failed:\n\n{e.stderr}")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure
    except FileNotFoundError:
        logger.error(f"Error: Batch file {batch_file} not found for {file_path}")
        send_email_alert(file_path, "Batch file is missing.")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure
    except Exception as e:
        logger.error(f"Error: Unexpected error running batch file for {file_path}: {e}")
        send_email_alert(file_path, f"Unexpected error running batch file:\n\n{e}")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure

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
        logger.error(f"Error: No VTT file found for {mp3_path}")
        send_email_alert(mp3_path, "VTT file is missing.")
        return

    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logger.debug(f"Loaded VTT file {vtt_path} with {len(lines)} lines")
    except FileNotFoundError:  # Safety
        logger.error(f"Error: VTT file {vtt_path} not found (race condition?) for {mp3_path}")
        send_email_alert(mp3_path, "VTT file missing.")
        return
    except UnicodeDecodeError as e:
        logger.error(f"Error: Encoding error reading VTT {vtt_path} for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"Encoding error in VTT: {e}")
        return
    except Exception as e:
        logger.error(f"Error: Unexpected error reading VTT {vtt_path} for {mp3_path}: {e}")
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
                    logger.warning(f"Warning: Invalid timestamp in VTT line '{line}' for {mp3_path}: {e}")
                    invalid_ts_count += 1
            else:
                logger.warning(f"Warning: Unmatched timestamp line in VTT: {line} for {mp3_path}")
        elif current_time:
            current_text += " " + line

    if invalid_ts_count > 5:
        logger.warning(f"Warning: High number of invalid timestamps in VTT for {mp3_path}: {invalid_ts_count}")
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
        logger.warning(f"Warning: Heuristics failed; using GPT fallback for homily start for {mp3_path}")
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
            logger.error(f"Error: Invalid JSON from GPT for {mp3_path}: {e} - Content: {content}")
            send_email_alert(mp3_path, "GPT returned invalid JSON for homily detection.")
            return
        except Exception as e:
            logger.error(f"Error: GPT fallback failed for {mp3_path}: {e}")
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
        logger.error(f"Warning: Could not locate homily start for {mp3_path}")
        send_email_alert(mp3_path, "Could not locate homily start in VTT.")
        return

    duration = homily_end - homily_start
    if duration < 60 or duration > 1200:  # e.g., <1min or >20min
        logger.warning(f"Warning: Suspicious homily duration: {duration:.2f}s for {mp3_path}")
        send_email_alert(mp3_path, f"Suspicious homily duration extracted: {duration:.2f}s")

    logger.info(f"Extracting homily: {homily_start:.2f}s to {homily_end:.2f}s for {mp3_path}")

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
        logger.info(f"Success: Homily saved as: {output_path}")
        
        # Import here to avoid circular import
        from .wordpress_utils import upload_to_wordpress
        
        # Upload to WordPress as draft
        upload_to_wordpress(output_path, mp3_path)
    except Exception as e:
        logger.error(f"Error: FFmpeg error for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"FFmpeg error while extracting homily:\n\n{e}")