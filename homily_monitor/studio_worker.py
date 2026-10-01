"""Outbound-only WordPress preview worker; never writes editorial settings."""

import json
import logging
import time
import re

from .editorial_generator import EditorialGenerator
from .editorial_settings import StudioClient

logger = logging.getLogger("HomilyMonitor")


def process_one(client, generator_factory):
    job = client.request("POST", "/jobs/claim", json={})
    if not job:
        return False
    result_path = client.state_dir / (str(int(job["id"])) + ".json")
    client.state_dir.mkdir(parents=True, exist_ok=True)
    # Persist the completed result before delivery. Delivery retries never call the AI again.
    try:
        if job["input"].get("use_local_transcript"):
            filename = job["input"].get("filename", "")
            if not re.fullmatch(r"Mass-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.mp3", filename):
                raise ValueError("Invalid local source reference")
            source = json.loads((client.state_dir / "sources" / (filename + ".json")).read_text(encoding="utf-8"))
            if not source.get("transcript"):
                raise ValueError("No saved local homily transcript; paste a transcript in the preview lab")
            job["input"]["transcript"] = source["transcript"]
        generator = generator_factory()
        result = generator.preview(job)
        payload = {"claim": job["claim"], "result": result}
    except Exception as exc:
        logger.error("Preview job %s failed (%s)", job["id"], type(exc).__name__)
        message = ("No saved local homily context. Paste a transcript in the preview lab."
                   if job["input"].get("use_local_transcript") and isinstance(exc, (OSError, ValueError))
                   else "Generation failed. Check the worker log and API access.")
        payload = {"claim": job["claim"], "error": message}
    temporary = result_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(result_path)
    client.request("POST", f"/jobs/{int(job['id'])}/result", json=payload)
    result_path.unlink()
    return True


def deliver_pending(client):
    for path in client.state_dir.glob("*.json"):
        if not path.stem.isdecimal():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            client.request("POST", f"/jobs/{int(path.stem)}/result", json=payload)
            path.unlink()
        except Exception:
            logger.warning("Preview result %s is awaiting delivery; no image will be regenerated.", path.stem)


def run_worker(once=False):
    from openai import OpenAI
    from .config_loader import CFG, get_base_dir
    if not CFG.get("homily_studio", {}).get("enabled", False):
        raise ValueError("Enable homily_studio in local configuration before starting the worker")
    client = StudioClient(CFG, get_base_dir())
    ai_client = OpenAI(api_key=CFG["openai_api_key"], timeout=600, max_retries=0)
    factory = lambda: EditorialGenerator(ai_client, CFG.get("ai", {}))
    while True:
        try:
            deliver_pending(client)
            worked = process_one(client, factory)
            if once:
                return
            if not worked:
                time.sleep(5)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            logger.error("Preview worker request failed (%s)", type(exc).__name__)
            if once:
                raise
            time.sleep(5)
