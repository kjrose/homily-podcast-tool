import logging
import os

from .config_loader import CFG


logger = logging.getLogger("HomilyMonitor")

SPEAKER_CFG = CFG.get("speaker_identification", {})
SPEAKER_FALLBACK_LABEL = str(
    SPEAKER_CFG.get("fallback_label", "Homilist") or "Homilist"
).strip()
SPEAKER_MATCHING_THRESHOLD = float(SPEAKER_CFG.get("matching_threshold", 80.0) or 80.0)
SPEAKER_MODEL_SOURCE = str(
    SPEAKER_CFG.get("model_source", "speechbrain/spkrec-ecapa-voxceleb") or "speechbrain/spkrec-ecapa-voxceleb"
).strip()
SPEAKER_MODEL_CACHE_DIR = str(SPEAKER_CFG.get("model_cache_dir", "") or "").strip()
SPEAKER_DECISION_THRESHOLD = float(SPEAKER_CFG.get("decision_threshold", 0.25) or 0.25)

_VERIFIER = None


def _one_line_text(value, fallback=""):
    if value is None:
        return fallback
    text = " ".join(str(value).split())
    return text if text else fallback


def _speaker_identification_enabled():
    return bool(SPEAKER_CFG.get("enabled"))


def get_homilist_fallback_label():
    return _one_line_text(SPEAKER_FALLBACK_LABEL, "Homilist")


def _empty_resolution(threshold=None):
    return {
        "name": "",
        "confidence": None,
        "source": "",
        "fallback_label": get_homilist_fallback_label(),
        "threshold": SPEAKER_MATCHING_THRESHOLD if threshold is None else threshold,
    }


def _normalize_score_to_confidence(score):
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        return None

    confidence = ((score_value + 1.0) / 2.0) * 100.0
    if confidence < 0.0:
        confidence = 0.0
    if confidence > 100.0:
        confidence = 100.0
    return round(confidence, 2)


def _load_verifier():
    global _VERIFIER
    if _VERIFIER is not None:
        return _VERIFIER

    from speechbrain.inference.speaker import SpeakerRecognition

    load_kwargs = {"source": SPEAKER_MODEL_SOURCE}
    if SPEAKER_MODEL_CACHE_DIR:
        load_kwargs["savedir"] = SPEAKER_MODEL_CACHE_DIR

    logger.info(f"Loading local SpeechBrain speaker model from {SPEAKER_MODEL_SOURCE}...")
    _VERIFIER = SpeakerRecognition.from_hparams(**load_kwargs)
    return _VERIFIER


def _resolve_sample_path(sample_path):
    candidate = str(sample_path or "").strip()
    if not candidate:
        return None

    if os.path.isabs(candidate):
        return candidate if os.path.isfile(candidate) else None

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    combined = os.path.join(base_dir, candidate)
    return combined if os.path.isfile(combined) else None


def _configured_enrolled_speakers():
    enrolled = []
    for item in SPEAKER_CFG.get("enrolled_speakers", []):
        if not isinstance(item, dict):
            continue

        name = _one_line_text(item.get("name"), "")
        sample_paths = []
        for raw_path in item.get("sample_paths", []):
            resolved = _resolve_sample_path(raw_path)
            if resolved:
                sample_paths.append(resolved)

        if name and sample_paths:
            enrolled.append({
                "name": name,
                "sample_paths": sample_paths,
            })

    return enrolled


def _verify_against_sample(verifier, homily_path, sample_path):
    score, decision = verifier.verify_files(
        homily_path,
        sample_path,
        threshold=SPEAKER_DECISION_THRESHOLD,
    )
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = None

    return {
        "score": score_value,
        "confidence": _normalize_score_to_confidence(score_value),
        "same_speaker": bool(getattr(decision, "item", lambda: decision)()),
        "sample_path": sample_path,
    }


def _pick_best_speaker(verifications):
    ranked = []
    for result in verifications:
        scores = [item["score"] for item in result["samples"] if item["score"] is not None]
        confidences = [item["confidence"] for item in result["samples"] if item["confidence"] is not None]
        if not scores or not confidences:
            continue

        average_score = sum(scores) / len(scores)
        average_confidence = sum(confidences) / len(confidences)
        best_confidence = max(confidences)
        positive_votes = sum(1 for item in result["samples"] if item["same_speaker"])

        ranked.append({
            "name": result["name"],
            "average_score": average_score,
            "average_confidence": round(average_confidence, 2),
            "best_confidence": round(best_confidence, 2),
            "positive_votes": positive_votes,
            "sample_count": len(result["samples"]),
        })

    if not ranked:
        return None

    ranked.sort(
        key=lambda item: (
            item["positive_votes"],
            item["average_confidence"],
            item["best_confidence"],
            item["average_score"],
        ),
        reverse=True,
    )
    return ranked[0]


def resolve_homilist(mp3_path):
    if not _speaker_identification_enabled():
        return _empty_resolution()

    provider = str(SPEAKER_CFG.get("provider", "speechbrain") or "").strip().lower()
    if provider != "speechbrain":
        logger.warning("Speaker identification provider is not supported; using fallback homilist label.")
        return _empty_resolution()

    if not mp3_path or not os.path.isfile(mp3_path):
        logger.warning("Speaker identification skipped because the homily audio file is missing.")
        return _empty_resolution()

    enrolled_speakers = _configured_enrolled_speakers()
    if not enrolled_speakers:
        logger.warning("Speaker identification is enabled but no enrolled speaker samples are configured.")
        return _empty_resolution()

    try:
        verifier = _load_verifier()
        logger.info(f"Resolving homilist identity for {mp3_path} using local SpeechBrain verification...")

        verifications = []
        for speaker in enrolled_speakers:
            sample_results = []
            for sample_path in speaker["sample_paths"]:
                sample_results.append(_verify_against_sample(verifier, mp3_path, sample_path))

            verifications.append({
                "name": speaker["name"],
                "samples": sample_results,
            })

        best_match = _pick_best_speaker(verifications)
        if not best_match:
            return _empty_resolution()

        confidence = best_match["average_confidence"]
        if confidence is None or confidence < SPEAKER_MATCHING_THRESHOLD:
            logger.info(
                f"No homilist match met the configured threshold for {mp3_path} "
                f"(best={confidence}, threshold={SPEAKER_MATCHING_THRESHOLD})."
            )
            return {
                **_empty_resolution(),
                "best_candidate": best_match["name"],
                "best_candidate_confidence": confidence,
            }

        return {
            "name": best_match["name"],
            "confidence": confidence,
            "source": "speechbrain",
            "fallback_label": get_homilist_fallback_label(),
            "threshold": SPEAKER_MATCHING_THRESHOLD,
            "sample_count": best_match["sample_count"],
            "positive_votes": best_match["positive_votes"],
        }
    except ModuleNotFoundError as exc:
        logger.warning(
            "Speaker identification is enabled but the local SpeechBrain dependency is missing: "
            f"{exc.name}"
        )
        return _empty_resolution()
    except Exception as exc:
        logger.warning(f"Homilist identification failed for {mp3_path}: {exc}")
        return _empty_resolution()
