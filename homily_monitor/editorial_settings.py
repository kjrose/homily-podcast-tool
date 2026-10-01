"""Authenticated active-profile reads with a site-bound last-known-good cache."""

import hashlib
import json
import logging
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import requests

from .editorial import default_profile, validate_profile

logger = logging.getLogger("HomilyMonitor")


def state_directory(url, base_dir):
    site_key = hashlib.sha256(url.rstrip("/").encode("utf-8")).hexdigest()[:20]
    return Path(base_dir) / ".homily-studio" / site_key


def store_source_context(config, base_dir, filename, title, description, transcript):
    if not config.get("homily_studio", {}).get("enabled", False):
        return
    directory = state_directory(config["wordpress"]["url"], base_dir) / "sources"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename + ".json")
    path.write_text(json.dumps({"title": title, "description": description,
                               "transcript": transcript or ""}), encoding="utf-8")


class StudioClient:
    def __init__(self, config, base_dir, session=None):
        settings = config.get("homily_studio", {})
        wp = config.get("wordpress", {})
        url = wp.get("url", "").rstrip("/")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Homily Studio requires a WordPress HTTPS URL without embedded credentials")
        self.root = url + "/wp-json/homily-studio/v1"
        self.auth = (os.environ.get("HOMILY_STUDIO_USER", settings.get("user", "")),
                     os.environ.get("HOMILY_STUDIO_APP_PASSWORD", settings.get("app_password", "")))
        if not all(self.auth):
            raise ValueError("Configure the dedicated Homily Studio user and application password")
        self.session = session or requests.Session()
        self.session.trust_env = False  # Do not replace dedicated credentials with netrc values.
        self.site_key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        self.state_dir = state_directory(url, base_dir)

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.root + path, auth=self.auth,
                                        timeout=(10, 60), allow_redirects=False, **kwargs)
        if 300 <= response.status_code < 400:
            raise ValueError("Homily Studio redirects are refused; configure the canonical HTTPS site URL")
        response.raise_for_status()
        return response.json()

    def active_profile(self, fallback):
        cache = self.state_dir / "active.json"
        try:
            profile = validate_profile(self.request("GET", "/settings"))
        except (requests.RequestException, ValueError, TypeError):
            logger.warning("Homily Studio active settings unavailable; using a validated cached or local profile.")
            try:
                return validate_profile(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                return fallback
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_name(uuid.uuid4().hex + ".tmp")
            temporary.write_text(json.dumps(profile), encoding="utf-8")
            temporary.replace(cache)
        except OSError:
            logger.warning("Could not cache the active editorial profile.")
        logger.info("Using Homily Studio editorial revision %s", profile["revision"])
        return profile


def get_editorial_profile():
    # Import only at runtime, so profile validation and preview tests need no production config.
    from .config_loader import CFG, get_base_dir
    fallback = default_profile(CFG)
    if not CFG.get("homily_studio", {}).get("enabled", False):
        return fallback
    return StudioClient(CFG, get_base_dir()).active_profile(fallback)
