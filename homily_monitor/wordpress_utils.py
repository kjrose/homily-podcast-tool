# homily_monitor/wordpress_utils.py

import os
import time
from datetime import datetime, timedelta
from html import unescape
import logging

import pytz
import requests

from .config_loader import CFG
from .audio_utils import extract_homily_transcript_from_vtt
from .database import get_latest_homily_analysis
from .email_utils import send_email_alert, send_success_email
from .gpt_utils import analyze_transcript_with_gpt, generate_podcast_image
from .helpers import validate_and_get_transcript


# Configure logging (reusing the logger from main.py)
logger = logging.getLogger('HomilyMonitor')

WP_URL = CFG["wordpress"]["url"]
WP_USER = CFG["wordpress"]["user"]
WP_APP_PASS = CFG["wordpress"]["app_password"]
LOCAL_DIR = CFG["paths"]["local_dir"]
CHURCH_CFG = CFG.get("church", {})
NETWORK_CFG = CFG.get("network", {})
WP_SESSION = requests.Session()


def _get_positive_number(name, default, cast=float):
    try:
        value = cast(NETWORK_CFG.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


WP_CONNECT_TIMEOUT_SECONDS = _get_positive_number("wordpress_connect_timeout_seconds", 10.0)
WP_READ_TIMEOUT_SECONDS = _get_positive_number("wordpress_read_timeout_seconds", 60.0)
WP_RETRY_ATTEMPTS = int(_get_positive_number("wordpress_retry_attempts", 2, int))
WP_RETRY_BACKOFF_SECONDS = _get_positive_number("wordpress_retry_backoff_seconds", 2.0)
WP_POST_STATUSES = ("draft", "publish", "future", "pending", "private")
WP_POST_STATUS_QUERY_MODES = ("array", "csv", "any", "omit")
WP_COLLECTION_PAGE_SIZE = 100
WP_COLLECTION_MAX_PAGES = 50


def _get_church_timezone():
    timezone_name = CHURCH_CFG.get("timezone") or CFG["wordpress"].get("timezone")
    if timezone_name:
        try:
            return pytz.timezone(timezone_name)
        except pytz.UnknownTimeZoneError as exc:
            raise ValueError(f"Invalid church timezone configured: {timezone_name}") from exc

    logger.warning(
        "No church timezone configured; defaulting WordPress scheduling to the local system timezone."
    )
    return datetime.now().astimezone().tzinfo or pytz.UTC


def _localize_datetime(naive_dt, tzinfo):
    if hasattr(tzinfo, "localize"):
        return tzinfo.localize(naive_dt)
    return naive_dt.replace(tzinfo=tzinfo)


def _build_publish_dates(original_filename):
    date_time_parts = original_filename.split("Mass-")[1].split(".mp3")[0]
    homily_datetime = datetime.strptime(date_time_parts, "%Y-%m-%d_%H-%M")
    church_timezone = _get_church_timezone()
    publish_date_local = _localize_datetime(homily_datetime, church_timezone)
    publish_date_utc = publish_date_local.astimezone(pytz.UTC)
    return (
        publish_date_local.strftime("%Y-%m-%dT%H:%M:%S"),
        publish_date_utc.strftime("%Y-%m-%dT%H:%M:%S"),
        getattr(church_timezone, "zone", str(church_timezone)),
    )


def _request_with_retries(method, url, description, build_kwargs):
    request_method = getattr(WP_SESSION, method.lower())
    for attempt in range(1, WP_RETRY_ATTEMPTS + 1):
        try:
            kwargs = build_kwargs()
            return request_method(
                url,
                timeout=(WP_CONNECT_TIMEOUT_SECONDS, WP_READ_TIMEOUT_SECONDS),
                **kwargs,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_attempt = attempt == WP_RETRY_ATTEMPTS
            log_message = (
                f"{description} failed due to a connection error "
                f"(attempt {attempt}/{WP_RETRY_ATTEMPTS}): {exc}"
            )
            if last_attempt:
                raise
            logger.warning(log_message)
            time.sleep(WP_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))


def _post_with_retries(url, description, build_kwargs):
    return _request_with_retries("post", url, description, build_kwargs)


def _get_with_retries(url, description, build_kwargs):
    return _request_with_retries("get", url, description, build_kwargs)


def _upload_media_bytes(media_url, auth, filename, file_bytes, content_type, description):
    headers = {'Content-Disposition': f'attachment; filename="{filename}"'}
    return _post_with_retries(
        media_url,
        description,
        lambda: {
            "auth": auth,
            "headers": headers,
            "files": {"file": (filename, file_bytes, content_type)},
        },
    )


def _normalize_wp_datetime(value):
    if not value:
        return None
    return value.strip()[:19]


def _parse_normalized_datetime(value):
    normalized = _normalize_wp_datetime(value)
    if not normalized:
        return None

    try:
        return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def _one_line_text(value, fallback=""):
    if value is None:
        return fallback
    text = " ".join(str(value).split())
    return text if text else fallback


def _parse_recording_datetime(filename, prefix):
    if not filename.startswith(prefix) or not filename.lower().endswith(".mp3"):
        return None

    try:
        return datetime.strptime(filename[len(prefix):-4], "%Y-%m-%d_%H-%M")
    except ValueError:
        return None


def _build_retry_candidate(homily_path):
    homily_filename = os.path.basename(homily_path)
    original_filename = homily_filename.replace("Homily-", "Mass-", 1)
    recorded_at = _parse_recording_datetime(original_filename, "Mass-")
    if recorded_at is None:
        logger.warning(f"Skipping retry candidate with unexpected filename format: {homily_filename}")
        return None

    publish_date_local, publish_date_utc, _ = _build_publish_dates(original_filename)
    legacy_publish_date_utc = recorded_at.replace(tzinfo=pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "recorded_at": recorded_at,
        "homily_path": homily_path,
        "original_filename": original_filename,
        "original_mp3_path": os.path.join(LOCAL_DIR, original_filename),
        "publish_date_local": publish_date_local,
        "publish_date_utc": publish_date_utc,
        "legacy_publish_date_utc": legacy_publish_date_utc,
    }


def _collect_retry_candidates(start_date, end_date):
    if not os.path.isdir(LOCAL_DIR):
        logger.error(f"Local homily directory not found: {LOCAL_DIR}")
        return []

    candidates = []
    for file_name in os.listdir(LOCAL_DIR):
        recorded_at = _parse_recording_datetime(file_name, "Homily-")
        if recorded_at is None or not (start_date <= recorded_at.date() <= end_date):
            continue

        homily_path = os.path.join(LOCAL_DIR, file_name)
        if not os.path.isfile(homily_path):
            continue

        candidate = _build_retry_candidate(homily_path)
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(key=lambda item: item["recorded_at"])
    return candidates


def _build_podcast_collection_params(page, status_mode="array"):
    params = [
        ("context", "edit"),
        ("orderby", "date"),
        ("order", "desc"),
        ("per_page", str(WP_COLLECTION_PAGE_SIZE)),
        ("page", str(page)),
    ]

    if status_mode == "array":
        for status in WP_POST_STATUSES:
            params.append(("status[]", status))
    elif status_mode == "csv":
        params.append(("status", ",".join(WP_POST_STATUSES)))
    elif status_mode == "any":
        params.append(("status", "any"))
    elif status_mode != "omit":
        raise ValueError(f"Unsupported WordPress status query mode: {status_mode}")

    return params


def _fetch_recent_podcast_posts(oldest_date_gmt=None):
    post_url = f"{WP_URL}/wp-json/wp/v2/podcast"
    auth = (WP_USER, WP_APP_PASS)
    posts = []
    total_pages = 0
    status_mode = None
    status_mode_warned = False

    for page in range(1, WP_COLLECTION_MAX_PAGES + 1):
        response = None
        candidate_modes = (status_mode,) if status_mode else WP_POST_STATUS_QUERY_MODES
        for candidate_mode in candidate_modes:
            try:
                current_response = _get_with_retries(
                    post_url,
                    f"WordPress podcast listing page {page}",
                    lambda page=page, candidate_mode=candidate_mode: {
                        "auth": auth,
                        "params": _build_podcast_collection_params(page, candidate_mode),
                    },
                )
            except requests.exceptions.RequestException as e:
                logger.error(f"Podcast listing request failed on page {page}: {e}")
                return None

            if current_response.status_code == 200:
                response = current_response
                status_mode = candidate_mode
                if candidate_mode != "array" and not status_mode_warned:
                    logger.warning(
                        f"WordPress podcast listing fell back to status query mode '{candidate_mode}'."
                    )
                    status_mode_warned = True
                break

            if current_response.status_code != 400:
                logger.error(f"Podcast listing failed on page {page}: {current_response.text}")
                return None

        if response is None:
            logger.error(f"Podcast listing failed on page {page}: no compatible status query mode succeeded.")
            return None

        page_posts = response.json()
        if not page_posts:
            break

        posts.extend(page_posts)

        if not total_pages:
            try:
                total_pages = int(response.headers.get("X-WP-TotalPages", "0"))
            except ValueError:
                total_pages = 0

        page_date_gmt_values = [
            normalized
            for normalized in (
                _normalize_wp_datetime(post.get("date_gmt"))
                for post in page_posts
            )
            if normalized
        ]
        if oldest_date_gmt and page_date_gmt_values and min(page_date_gmt_values) <= oldest_date_gmt:
            break

        if total_pages and page >= total_pages:
            break
        if len(page_posts) < WP_COLLECTION_PAGE_SIZE:
            break
    else:
        logger.warning(
            f"Stopped WordPress listing after {WP_COLLECTION_MAX_PAGES} pages before reaching the end of the podcast archive."
        )

    return posts


def _extract_server_upload_keys(posts):
    server_local_dates = set()
    server_gmt_dates = set()
    for post in posts:
        normalized_local = _normalize_wp_datetime(post.get("date"))
        if normalized_local:
            server_local_dates.add(normalized_local)

        normalized_gmt = _normalize_wp_datetime(post.get("date_gmt"))
        if normalized_gmt:
            server_gmt_dates.add(normalized_gmt)

    return server_local_dates, server_gmt_dates


def _get_server_upload_keys(candidates):
    if not candidates:
        return set(), set()

    oldest_date_gmt = min(
        min(candidate["publish_date_utc"], candidate["legacy_publish_date_utc"])
        for candidate in candidates
    )
    posts = _fetch_recent_podcast_posts(oldest_date_gmt)
    if posts is None:
        return None, None

    return _extract_server_upload_keys(posts)


def _candidate_is_uploaded(candidate, server_local_dates, server_gmt_dates):
    return (
        candidate["publish_date_local"] in server_local_dates
        or candidate["publish_date_utc"] in server_gmt_dates
        or candidate["legacy_publish_date_utc"] in server_gmt_dates
    )


def _get_recent_date_window(days):
    if days < 1:
        logger.error(f"Invalid last-days value '{days}'. Expected a positive integer.")
        return None

    try:
        today = datetime.now(_get_church_timezone()).date()
    except Exception as e:
        logger.error(f"Unable to determine the current church date for homily listing: {e}")
        return None

    start_date = today - timedelta(days=days - 1)
    return start_date, today


def _collect_wordpress_posts_for_window(start_date, end_date):
    try:
        church_timezone = _get_church_timezone()
    except Exception as e:
        return None, f"Unable to determine church timezone: {e}"

    start_local = _localize_datetime(
        datetime.combine(start_date, datetime.min.time()),
        church_timezone,
    )
    oldest_date_gmt = start_local.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%S")
    posts = _fetch_recent_podcast_posts(oldest_date_gmt)
    if posts is None:
        return None, "WordPress query failed."

    filtered_posts = []
    for post in posts:
        local_dt = _parse_normalized_datetime(post.get("date"))
        if local_dt is None:
            continue
        if start_date <= local_dt.date() <= end_date:
            filtered_posts.append(post)

    filtered_posts.sort(
        key=lambda post: _normalize_wp_datetime(post.get("date")) or "",
        reverse=True,
    )
    return filtered_posts, None


def _get_wordpress_post_title(post):
    title = post.get("title", {})
    if isinstance(title, dict):
        title = title.get("rendered", "")
    return _one_line_text(unescape(title), "(untitled)")


def _build_wordpress_report_lines(posts, error_message):
    if error_message:
        return [
            f"WordPress homilies: unavailable",
            f"  Error: {error_message}",
        ]

    lines = [f"WordPress homilies ({len(posts)})"]
    if not posts:
        lines.append("  None")
        return lines

    for post in posts:
        local_dt = _normalize_wp_datetime(post.get("date")) or "unknown"
        local_dt = local_dt.replace("T", " ")
        status = _one_line_text(post.get("status"), "unknown")
        title = _get_wordpress_post_title(post)
        link = _one_line_text(post.get("link"), "")
        lines.append(f"  {local_dt} [{status}] {title}")
        if link:
            lines.append(f"    {link}")

    return lines


def _build_local_homily_report_lines(candidates, server_local_dates=None, server_gmt_dates=None):
    lines = [f"Local homilies on this machine ({len(candidates)})"]
    if not candidates:
        lines.append("  None")
        return lines

    for candidate in sorted(candidates, key=lambda item: item["recorded_at"], reverse=True):
        analysis_row = get_latest_homily_analysis(candidate["original_filename"])
        title = "(analysis not available)"
        liturgy = ""
        special = ""
        analysis_date = ""
        if analysis_row:
            title, _, special, lit_day, lit_year, analysis_date = analysis_row
            title = _one_line_text(title, "(analysis not available)")
            liturgy = _one_line_text(
                " | ".join(part for part in [lit_day, lit_year] if part),
                "",
            )
            special = _one_line_text(special, "")

        upload_status = "unknown"
        if server_local_dates is not None and server_gmt_dates is not None:
            upload_status = "present" if _candidate_is_uploaded(candidate, server_local_dates, server_gmt_dates) else "missing"

        lines.append(
            f"  {candidate['recorded_at'].strftime('%Y-%m-%d %H:%M')} | {os.path.basename(candidate['homily_path'])}"
        )
        lines.append(f"    WordPress match: {upload_status}")
        lines.append(f"    Title: {title}")
        if liturgy:
            lines.append(f"    Liturgy: {liturgy}")
        if special:
            lines.append(f"    Special: {special}")
        if analysis_date:
            lines.append(f"    Analysis date: {analysis_date}")
        lines.append(
            f"    Original MP3: {'present' if os.path.exists(candidate['original_mp3_path']) else 'missing'}"
        )

    return lines


def list_homilies_for_last_days(days):
    window = _get_recent_date_window(days)
    if window is None:
        return None

    start_date, end_date = window
    try:
        candidates = _collect_retry_candidates(start_date, end_date)
    except Exception as e:
        logger.error(f"Unable to prepare local homily listing for {start_date} through {end_date}: {e}")
        return None

    wordpress_posts, wordpress_error = _collect_wordpress_posts_for_window(start_date, end_date)
    server_local_dates = None
    server_gmt_dates = None
    if wordpress_posts is not None:
        server_local_dates, server_gmt_dates = _extract_server_upload_keys(wordpress_posts)

    lines = [
        f"Homily report for {start_date} through {end_date} (last {days} day{'s' if days != 1 else ''})",
        "",
    ]
    lines.extend(_build_wordpress_report_lines(wordpress_posts or [], wordpress_error))
    lines.append("")
    lines.extend(_build_local_homily_report_lines(candidates, server_local_dates, server_gmt_dates))
    return "\n".join(lines)


def _retry_candidates(candidates, selection_label):
    summary = {
        "checked": 0,
        "already_uploaded": 0,
        "retried": 0,
        "failed": 0,
        "aborted": False,
    }

    if not candidates:
        logger.info(f"No local homily files found for {selection_label}.")
        return summary

    server_local_dates, server_gmt_dates = _get_server_upload_keys(candidates)
    if server_local_dates is None or server_gmt_dates is None:
        summary["aborted"] = True
        logger.error(
            f"Retry upload check for {selection_label} aborted because WordPress could not be queried successfully."
        )
        return summary

    for candidate in candidates:
        summary["checked"] += 1
        homily_filename = os.path.basename(candidate["homily_path"])

        if _candidate_is_uploaded(candidate, server_local_dates, server_gmt_dates):
            logger.info(f"Skipping {homily_filename}: already present on WordPress.")
            summary["already_uploaded"] += 1
            continue

        if not os.path.exists(candidate["homily_path"]):
            logger.error(f"Skipping {homily_filename}: homily file no longer exists locally.")
            summary["failed"] += 1
            continue

        analysis_row = get_latest_homily_analysis(candidate["original_filename"])
        if not os.path.exists(candidate["original_mp3_path"]) and not analysis_row:
            logger.error(
                f"Skipping {homily_filename}: original Mass file is missing and no stored analysis was found for "
                f"{candidate['original_filename']}."
            )
            summary["failed"] += 1
            continue

        if not os.path.exists(candidate["original_mp3_path"]):
            logger.warning(
                f"Original Mass file missing for {homily_filename}; relying on stored analysis for retry upload."
            )

        logger.info(f"Retrying WordPress upload for {homily_filename}...")
        if upload_to_wordpress(candidate["homily_path"], candidate["original_mp3_path"]):
            summary["retried"] += 1
        else:
            summary["failed"] += 1

    logger.info(
        f"Retry upload summary for {selection_label}: checked={summary['checked']}, "
        f"already_uploaded={summary['already_uploaded']}, retried={summary['retried']}, failed={summary['failed']}."
    )
    return summary


def upload_to_wordpress(homily_path, original_mp3_path):
    filename = os.path.basename(original_mp3_path)
    logger.info(f"Checking database for analysis of {filename}...")
    row = get_latest_homily_analysis(filename)
    if not row:
        logger.warning(f"No analysis found for {filename}; generating automatically...")
        transcript_path = os.path.splitext(original_mp3_path)[0] + ".txt"
        content = validate_and_get_transcript(transcript_path, original_mp3_path)
        if content:
            logger.info(f"Generating analysis for {filename}...")
            analyze_transcript_with_gpt(original_mp3_path, content, None)
            row = get_latest_homily_analysis(filename)
        if not row:
            logger.error(f"Failed to generate analysis for {filename}")
            send_email_alert(homily_path, "Failed to generate analysis for homily upload.")
            return False

    title, description, special, lit_day, lit_year, date_str = row
    original_filename = os.path.basename(original_mp3_path)

    try:
        publish_date_local, publish_date_utc, timezone_label = _build_publish_dates(original_filename)
    except Exception as e:
        logger.error(f"Failed to determine publish date for {original_filename}: {e}")
        send_email_alert(homily_path, f"Failed to determine publish date for WordPress upload: {e}")
        return False

    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    formatted_date = date_obj.strftime("%B %d, %Y")
    homilist = "**HOMILIST**"
    full_title = f"{formatted_date} – {lit_day or 'Unknown Sunday'} – {lit_year or 'Unknown'} – {homilist} – “{title}”"

    content = description
    if special:
        content += f"\n\nSpecial context: {special}"

    media_url = f"{WP_URL}/wp-json/wp/v2/media"
    post_url = f"{WP_URL}/wp-json/wp/v2/podcast"
    auth = (WP_USER, WP_APP_PASS)

    homily_transcript = None
    if os.path.exists(original_mp3_path):
        homily_transcript = extract_homily_transcript_from_vtt(original_mp3_path, send_alerts=False)
        if homily_transcript:
            logger.info(f"Recovered homily-only transcript excerpt for image generation for {original_filename}.")
        else:
            logger.info(f"No homily-only transcript excerpt available for {original_filename}; using metadata fallback for image generation.")
    else:
        logger.info(f"Original Mass file unavailable for {original_filename}; using metadata fallback for image generation.")

    logger.info(f"Generating podcast image for {full_title}...")
    image_buffer = generate_podcast_image(title, description, homily_text=homily_transcript)
    featured_media_id = None
    cover_image_url = None
    if image_buffer:
        image_bytes = image_buffer.getvalue()
        try:
            response = _upload_media_bytes(
                media_url,
                auth,
                "podcast_cover.png",
                image_bytes,
                "image/png",
                f"WordPress image upload for {full_title}",
            )
            if response.status_code == 201:
                media_data = response.json()
                featured_media_id = media_data['id']
                cover_image_url = media_data['source_url']
                logger.info("Podcast image uploaded.")
            else:
                logger.error(f"Image upload failed for {full_title}: {response.text}")
                send_email_alert(homily_path, f"Image upload to WordPress failed: {response.text}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Image upload request failed for {full_title}: {e}")
            send_email_alert(homily_path, f"Image upload request to WordPress failed: {e}")

    logger.info(f"Uploading audio media for {full_title}...")
    with open(homily_path, 'rb') as f:
        audio_bytes = f.read()

    try:
        response = _upload_media_bytes(
            media_url,
            auth,
            os.path.basename(homily_path),
            audio_bytes,
            "audio/mpeg",
            f"WordPress audio upload for {full_title}",
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Media upload request failed for {full_title}: {e}")
        send_email_alert(homily_path, f"Media upload request to WordPress failed: {e}")
        return False

    if response.status_code != 201:
        logger.error(f"Media upload failed for {full_title}: {response.text}")
        send_email_alert(homily_path, f"Media upload to WordPress failed: {response.text}")
        return False

    media_data = response.json()
    audio_url = media_data['source_url']

    logger.info(
        f"Creating podcast post for {full_title} with local publish date "
        f"{publish_date_local} ({timezone_label}) and GMT {publish_date_utc}..."
    )
    post_data = {
        "title": full_title,
        "content": content,
        "status": "draft",
        "date": publish_date_local,
        "date_gmt": publish_date_utc,
        "meta": {
            "audio_file": audio_url
        }
    }
    if featured_media_id:
        post_data["featured_media"] = featured_media_id
    if cover_image_url and featured_media_id:
        post_data["meta"]["cover_image"] = cover_image_url
        post_data["meta"]["cover_image_id"] = str(featured_media_id)

    try:
        response = _post_with_retries(
            post_url,
            f"WordPress podcast post creation for {full_title}",
            lambda: {"auth": auth, "json": post_data},
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Post creation request failed for {full_title}: {e}")
        send_email_alert(homily_path, f"Podcast post creation request failed: {e}")
        return False

    if response.status_code != 201:
        logger.error(f"Post creation failed for {full_title}: {response.text}")
        send_email_alert(homily_path, f"Podcast post creation failed: {response.text}")
        return False

    response_json = response.json()
    send_success_email(
        "Homily Upload Successful",
        (
            f"Successfully uploaded homily to WordPress as a draft: {full_title}\n\n"
            f"View draft: {response_json['link']}\n\n"
            f"Audio URL: {audio_url}\n\n"
            f"Image URL: {cover_image_url}\n\n"
            f"Description: {description}\n\n"
            f"Publish Post: {WP_URL}/wp-admin/post.php?post={response_json['id']}&action=edit"
        ),
    )
    logger.info(f"Uploaded homily as draft to WordPress: {response_json['link']}")
    return True


def upload_latest_homily():
    logger.info("Searching for the latest homily file...")
    homily_files = [
        os.path.join(LOCAL_DIR, f)
        for f in os.listdir(LOCAL_DIR)
        if f.lower().endswith(".mp3") and f.startswith("Homily-")
    ]
    if not homily_files:
        logger.error("No homily files found.")
        return False

    latest_homily = max(homily_files, key=os.path.getmtime)
    logger.info(f"Uploading latest homily: {latest_homily}")

    original_filename = os.path.basename(latest_homily).replace("Homily-", "Mass-")
    original_mp3_path = os.path.join(LOCAL_DIR, original_filename)

    if not os.path.exists(original_mp3_path):
        logger.error(f"Original MP3 not found for {latest_homily}")
        send_email_alert(latest_homily, "Original MP3 missing for latest homily upload.")
        return False

    logger.info(f"Processing upload for {latest_homily} with original {original_mp3_path}...")
    return upload_to_wordpress(latest_homily, original_mp3_path)


def retry_missing_uploads_for_date(date_str):
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.error(f"Invalid retry date '{date_str}'. Expected format is YYYY-MM-DD.")
        return None

    logger.info(f"Checking WordPress upload status for homilies on {target_date}...")
    try:
        candidates = _collect_retry_candidates(target_date, target_date)
    except Exception as e:
        logger.error(f"Unable to prepare retry candidates for {target_date}: {e}")
        return None

    return _retry_candidates(candidates, f"{target_date}")


def retry_missing_uploads_for_last_days(days):
    window = _get_recent_date_window(days)
    if window is None:
        return None

    start_date, today = window
    logger.info(
        f"Checking WordPress upload status for homilies from {start_date} through {today} "
        f"(last {days} day{'s' if days != 1 else ''})..."
    )

    try:
        candidates = _collect_retry_candidates(start_date, today)
    except Exception as e:
        logger.error(f"Unable to prepare retry candidates for {start_date} through {today}: {e}")
        return None

    return _retry_candidates(candidates, f"{start_date} through {today}")
