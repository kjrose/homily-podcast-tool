# homily_monitor/audio_utils.py

import os
import re
import subprocess
import json
import shutil
from collections import deque
import logging

from .config_loader import CFG
from .email_utils import send_email_alert
from .gpt_utils import VTT_FALLBACK_MODEL, request_text_completion
from pydub import AudioSegment

# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

BATCH_FILE = CFG["paths"]["batch_file"]
_FFMPEG_BINARY = None


def _ffmpeg_candidates():
    configured = CFG.get("paths", {}).get("ffmpeg")
    if configured:
        yield configured

    path_binary = shutil.which("ffmpeg")
    if path_binary:
        yield path_binary

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        yield os.path.join(local_app_data, "Microsoft", "WinGet", "Links", "ffmpeg.exe")


def get_ffmpeg_binary():
    global _FFMPEG_BINARY
    if _FFMPEG_BINARY:
        return _FFMPEG_BINARY

    for candidate in _ffmpeg_candidates():
        if not candidate:
            continue

        resolved = None
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                resolved = candidate
        else:
            resolved = shutil.which(candidate)
            if not resolved and os.path.exists(candidate):
                resolved = os.path.abspath(candidate)

        if resolved and os.path.exists(resolved):
            _FFMPEG_BINARY = resolved
            AudioSegment.converter = resolved
            AudioSegment.ffmpeg = resolved
            logger.debug(f"Resolved FFmpeg executable to {resolved}")
            return _FFMPEG_BINARY

    configured = CFG.get("paths", {}).get("ffmpeg")
    if configured:
        raise FileNotFoundError(f"Configured FFmpeg executable not found: {configured}")
    raise FileNotFoundError(
        "Could not resolve FFmpeg. Add FFmpeg to PATH or set paths.ffmpeg in config.json."
    )


def ensure_parent_dir(path):
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

def normalize_audio(mp3_path, output_path=None):
    """Normalize audio using FFmpeg loudnorm to -23 LUFS."""
    if output_path is None:
        output_path = mp3_path  # Overwrite by default
    try:
        ffmpeg_binary = get_ffmpeg_binary()
        ensure_parent_dir(output_path)
        logger.info(f"Normalizing audio for {mp3_path} to -23 LUFS...")
        
        # First pass to analyze loudness
        first_pass_cmd = [
            ffmpeg_binary,
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
            ffmpeg_binary,
            "-i", mp3_path,
            "-af", f"loudnorm=I=-23:TP=-1:LRA=7:measured_I={measured_i}:measured_LRA={measured_lra}:measured_TP={measured_tp}:measured_thresh={measured_thresh}:linear=true:print_format=summary",
            "-y",
            output_path
        ]
        subprocess.run(second_pass_cmd, check=True, encoding='utf-8')
        logger.info(f"Success: Audio normalized and saved to {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: Audio normalization failed for {mp3_path}: {e.stderr}")
        send_email_alert(mp3_path, f"Audio normalization failed:\n\n{e.stderr}")
        return False
    except Exception as e:
        logger.error(f"Error: Unexpected error normalizing audio for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"Unexpected normalization error:\n\n{e}")
        return False

def is_dead_air(mp3_path, silence_thresh_dB=-40, min_silence_len=1000, silence_ratio_threshold=0.9):
    """Check if MP3 is mostly dead air using FFmpeg silencedetect. Returns True if silent."""
    try:
        ffmpeg_binary = get_ffmpeg_binary()
        logger.debug(f"Running silencedetect on {mp3_path} with thresh {silence_thresh_dB}dB, min_len {min_silence_len/1000}s")
        cmd = [
            ffmpeg_binary,
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

def trim_excess_silence(mp3_path, max_silence_sec=1.0, silence_thresh_dB=-40):
    """Trim excess silence from start and end, leaving at most max_silence_sec of dead air."""
    try:
        ffmpeg_binary = get_ffmpeg_binary()
        logger.info(f"Trimming excess silence for {mp3_path} (max {max_silence_sec}s, thresh {silence_thresh_dB}dB)...")
        
        # Run silencedetect with minimum silence duration = max_silence_sec
        min_silence = max_silence_sec
        cmd = [
            ffmpeg_binary,
            "-i", mp3_path,
            "-af", f"silencedetect=n={silence_thresh_dB}dB:d={min_silence}",
            "-f", "null",
            "-y", "NUL"
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8')
        output = result.stderr
        
        # Parse total duration
        duration_line = next((line for line in output.splitlines() if "Duration:" in line), None)
        if duration_line:
            duration_str = duration_line.split("Duration: ")[1].split(",")[0]
            h, m, s = map(float, duration_str.split(":"))
            total_duration = h * 3600 + m * 60 + s
        else:
            raise ValueError(f"Could not parse duration for {mp3_path}")
        
        # Parse silences
        silences = []
        current_start = None
        for line in output.splitlines():
            if "silence_start:" in line:
                start = float(line.split("silence_start: ")[1].strip())
                current_start = start
            if "silence_end:" in line:
                end_str = line.split("silence_end: ")[1].split(" |")[0]
                end = float(end_str)
                dur = float(line.split("silence_duration: ")[1])
                silences.append((current_start, end, dur))
                current_start = None
        
        # Handle if ends with silence
        if current_start is not None:
            end = total_duration
            dur = end - current_start
            silences.append((current_start, end, dur))
        
        trim_start_amount = 0.0
        trim_end_amount = 0.0
        
        # Leading silence
        if silences and abs(silences[0][0]) < 0.001:  # Starts at ~0
            leading_dur = silences[0][2]
            trim_start_amount = leading_dur - max_silence_sec
        
        # Trailing silence
        if silences and abs(silences[-1][1] - total_duration) < 0.001:  # Ends at total_duration
            trailing_dur = silences[-1][2]
            trim_end_amount = trailing_dur - max_silence_sec
        
        new_start = trim_start_amount
        new_duration = total_duration - trim_start_amount - trim_end_amount
        
        if new_duration <= 0:
            logger.warning(f"Trim would empty the file {mp3_path}, skipping trim")
            return
        
        # Trim with FFmpeg
        trimmed_path = os.path.splitext(mp3_path)[0] + "_trimmed.mp3"
        ensure_parent_dir(trimmed_path)
        trim_cmd = [
            ffmpeg_binary,
            "-y",
            "-i", mp3_path,
            "-ss", str(new_start),
            "-t", str(new_duration),
            "-c", "copy",
            trimmed_path
        ]
        subprocess.run(trim_cmd, check=True, encoding='utf-8')
        
        # Replace original
        os.replace(trimmed_path, mp3_path)
        logger.info(f"Success: Trimmed {mp3_path} (start trim: {trim_start_amount}s, end trim: {trim_end_amount}s)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: Silence trim failed for {mp3_path}: {e.stderr}")
        send_email_alert(mp3_path, f"Silence trim failed:\n\n{e.stderr}")
    except Exception as e:
        logger.error(f"Error: Unexpected error trimming silence for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"Unexpected silence trim error:\n\n{e}")

def run_batch_file(file_path, batch_file=BATCH_FILE):
    """Run the batch file on the given file path, after normalizing audio."""
    # Normalize audio first
    normalized_path = os.path.splitext(file_path)[0] + "_normalized.mp3"
    if not normalize_audio(file_path, normalized_path):
        return False
    if not os.path.exists(normalized_path):
        logger.error(f"Normalization did not produce output for {file_path}")
        return False
    
    if is_dead_air(normalized_path):
        os.remove(normalized_path)  # Clean up if dead air
        return False  # Skip processing
    
    # Replace original with normalized file if successful
    if os.path.exists(normalized_path):
        os.replace(normalized_path, file_path)
        logger.info(f"Success: Replaced {file_path} with normalized version")

    try:
        logger.info(f"Running batch file on {file_path}...")
        # Set env for UTF-8 to avoid encoding issues in child Python processes
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        # Capture output with UTF-8 encoding
        logger.info(f"Executing exact command: \"{batch_file}\" \"{file_path}\"")
        result = subprocess.run(
            f'"{batch_file}" "{file_path}"',
            shell=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            env=env  # Pass the modified env
        )
        logger.debug(f"Batch output: {result.stdout}")
        if result.stderr:
            logger.warning(f"Batch errors: {result.stderr}")
        logger.info(f"Success: Batch file completed.")
        return True
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error: Batch file failed with return code {e.returncode} for {file_path}: {e.stderr}")
        send_email_alert(file_path, f"Batch file execution failed:\n\n{e.stderr}")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure
        return False
    except FileNotFoundError:
        logger.error(f"Error: Batch file {batch_file} not found for {file_path}")
        send_email_alert(file_path, "Batch file is missing.")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure
        return False
    except Exception as e:
        logger.error(f"Error: Unexpected error running batch file for {file_path}: {e}")
        send_email_alert(file_path, f"Unexpected error running batch file:\n\n{e}")
        if os.path.exists(normalized_path):
            os.remove(normalized_path)  # Clean up on failure
        return False

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

def _maybe_send_alert(mp3_path, message, send_alerts):
    if send_alerts:
        send_email_alert(mp3_path, message)


def _load_vtt_lines(mp3_path, send_alerts=True):
    vtt_path = os.path.splitext(mp3_path)[0] + ".vtt"
    if not os.path.exists(vtt_path):
        logger.error(f"Error: No VTT file found for {mp3_path}")
        _maybe_send_alert(mp3_path, "VTT file is missing.", send_alerts)
        return None

    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        logger.debug(f"Loaded VTT file {vtt_path} with {len(lines)} lines")
        return lines
    except FileNotFoundError:  # Safety
        logger.error(f"Error: VTT file {vtt_path} not found (race condition?) for {mp3_path}")
        _maybe_send_alert(mp3_path, "VTT file missing.", send_alerts)
        return None
    except UnicodeDecodeError as e:
        logger.error(f"Error: Encoding error reading VTT {vtt_path} for {mp3_path}: {e}")
        _maybe_send_alert(mp3_path, f"Encoding error in VTT: {e}", send_alerts)
        return None
    except Exception as e:
        logger.error(f"Error: Unexpected error reading VTT {vtt_path} for {mp3_path}: {e}")
        _maybe_send_alert(mp3_path, f"Unexpected error reading VTT: {e}", send_alerts)
        return None


def _parse_vtt_entries(lines, mp3_path, send_alerts=True):
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
        _maybe_send_alert(mp3_path, f"High invalid timestamps in VTT ({invalid_ts_count})", send_alerts)

    if current_time and current_text.strip():
        entries.append({
            "start": current_time[0],
            "end": current_time[1],
            "text": current_text.strip()
        })

    return entries


def _detect_homily_window(entries, lines, mp3_path, send_alerts=True):
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
            recent_texts.clear()

        if homily_start is not None:
            recent_texts.append(entry["text"])
            concat = " ".join(recent_texts).lower()
            if any(marker in concat for marker in end_markers):
                homily_end = entry["start"]
                break

    if homily_start is None:
        logger.warning(f"Warning: Heuristics failed; using GPT fallback for homily start for {mp3_path}")
        full_vtt = "\n".join(lines)

        gpt_prompt = f"""
You are a Catholic liturgy expert. Analyze this VTT transcript of a Mass to find the start timestamp of the homily (sermon after Gospel).

The homily typically starts after "Gospel of the Lord" or "Praise to you, Lord Jesus Christ," often with a pause, then the preacher's opening (e.g., "Brothers and sisters," personal story, or Gospel reflection).

Return ONLY a JSON object with a single field "start_timestamp" containing the start timestamp (e.g., "13:52.380") or "" if undetermined. No explanation, no markdown, no code blocks, no additional text—just the raw JSON.

VTT:
{full_vtt}
        """

        try:
            content = request_text_completion(
                gpt_prompt,
                temperature=0.2,
                model=VTT_FALLBACK_MODEL,
            )
            content = content.replace("```json", "").replace("```", "").strip()
            result = json.loads(content)
            gpt_start = result.get("start_timestamp", "")
            if gpt_start:
                gpt_time = parse_timestamp(gpt_start)
                homily_start = min(
                    (e["start"] for e in entries if e["start"] >= gpt_time),
                    default=entries[-1]["start"] if entries else None,
                )
                logger.info(f"GPT detected homily start: {gpt_start} (adjusted to {homily_start}) for {mp3_path}")
            else:
                raise ValueError("GPT could not determine start")
        except json.JSONDecodeError as e:
            logger.error(f"Error: Invalid JSON from GPT for {mp3_path}: {e} - Content: {content}")
            _maybe_send_alert(mp3_path, "GPT returned invalid JSON for homily detection.", send_alerts)
            return None, None
        except Exception as e:
            logger.error(f"Error: GPT fallback failed for {mp3_path}: {e}")
            _maybe_send_alert(mp3_path, "GPT homily detection failed.", send_alerts)
            return None, None

    if homily_start is not None and homily_end is None:
        recent_texts = deque(maxlen=10)
        end_found = False
        for entry in entries:
            if entry["start"] < homily_start:
                continue
            recent_texts.append(entry["text"])
            concat = " ".join(recent_texts).lower()
            if any(marker in concat for marker in end_markers):
                homily_end = entry["start"]
                end_found = True
                break
        if not end_found and entries:
            homily_end = entries[-1]["end"]

    if homily_start is None or homily_end is None:
        logger.error(f"Warning: Could not locate full homily boundaries for {mp3_path}")
        _maybe_send_alert(mp3_path, "Could not locate homily boundaries in VTT.", send_alerts)
        return None, None

    duration = homily_end - homily_start
    if duration < 60 or duration > 1200:
        logger.warning(f"Warning: Suspicious homily duration: {duration:.2f}s for {mp3_path}")
        _maybe_send_alert(mp3_path, f"Suspicious homily duration extracted: {duration:.2f}s", send_alerts)

    return homily_start, homily_end


def extract_homily_transcript_from_vtt(mp3_path, send_alerts=False):
    lines = _load_vtt_lines(mp3_path, send_alerts=send_alerts)
    if lines is None:
        return None

    entries = _parse_vtt_entries(lines, mp3_path, send_alerts=send_alerts)
    homily_start, homily_end = _detect_homily_window(
        entries,
        lines,
        mp3_path,
        send_alerts=send_alerts,
    )
    if homily_start is None or homily_end is None:
        return None

    transcript_parts = []
    last_text = None
    for entry in entries:
        if entry["end"] <= homily_start or entry["start"] >= homily_end:
            continue

        text = " ".join(entry["text"].split())
        if not text or text == last_text:
            continue

        transcript_parts.append(text)
        last_text = text

    transcript = " ".join(transcript_parts).strip()
    if not transcript:
        logger.warning(f"Warning: No homily transcript text found within detected VTT boundaries for {mp3_path}")
        return None

    logger.debug(f"Recovered homily-only transcript excerpt from VTT for {mp3_path}")
    return transcript


def extract_homily_from_vtt(mp3_path):
    lines = _load_vtt_lines(mp3_path, send_alerts=True)
    if lines is None:
        return False

    entries = _parse_vtt_entries(lines, mp3_path, send_alerts=True)
    homily_start, homily_end = _detect_homily_window(
        entries,
        lines,
        mp3_path,
        send_alerts=True,
    )
    if homily_start is None or homily_end is None:
        return False

    logger.info(f"Extracting homily: {homily_start:.2f}s to {homily_end:.2f}s for {mp3_path}")

    output_path = os.path.splitext(mp3_path)[0].replace("Mass-", "Homily-") + ".mp3"

    try:
        ffmpeg_binary = get_ffmpeg_binary()
        ensure_parent_dir(output_path)
        ffmpeg_cmd = [
            ffmpeg_binary,
            "-y",
            "-i", mp3_path,
            "-ss", str(homily_start),
            "-to", str(homily_end),
            "-c", "copy",
            output_path
        ]
        subprocess.run(ffmpeg_cmd, check=True)
        logger.info(f"Success: Homily saved as: {output_path}")
        
        # Trim excess silence
        trim_excess_silence(output_path)
        
        # Import here to avoid circular import
        from .wordpress_utils import upload_to_wordpress
        
        # Upload to WordPress as draft
        upload_to_wordpress(output_path, mp3_path)
        return True
    except Exception as e:
        logger.error(f"Error: FFmpeg error for {mp3_path}: {e}")
        send_email_alert(mp3_path, f"FFmpeg error while extracting homily:\n\n{e}")
        return False
