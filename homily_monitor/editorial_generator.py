"""The same generator is used for production and WordPress previews."""

import base64
import json
import logging

from .editorial import (build_analysis_prompt, finalize_image_prompt, image_context,
                       render, validate_profile, voice_instructions)

logger = logging.getLogger("HomilyMonitor")


class EditorialGenerator:
    def __init__(self, client, ai_config):
        self.client = client
        self.ai = ai_config
        self.usage = []

    def text(self, prompt, purpose, temperature):
        model = self.ai.get(purpose, self.ai.get("text_model", "gpt-5.4"))
        response = self.client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], temperature=temperature,
        )
        usage = getattr(response, "usage", None)
        self.usage.append({"purpose": purpose, "model": model,
                           "usage": usage.model_dump() if usage else {}})
        return (response.choices[0].message.content or "").strip()

    def metadata(self, profile, filename, transcript, homilist_guidance=""):
        profile = validate_profile(profile)
        prompt = build_analysis_prompt(profile, filename, transcript, homilist_guidance)
        value = json.loads(self.text(prompt, "analysis_model", 0.5))
        fields = {"title", "description", "liturgical_day", "lit_year", "special"}
        if (not isinstance(value, dict) or set(value) != fields
                or any(not isinstance(value[key], str) or len(value[key]) > 20000 for key in fields)
                or not value["title"].strip() or not value["description"].strip()):
            raise ValueError("Invalid metadata output from the text model")
        return value, prompt

    def image_prompt(self, profile, title, description, transcript=None):
        profile = validate_profile(profile)
        context = image_context(profile, title, description, transcript)
        instructions = render(profile["prompts"]["image_concept"], context)
        craft_prompt = (f"{voice_instructions(profile)}\n{instructions}\n"
                        "Return only a visual image prompt. Do not render an image or include commentary.")
        try:
            concept = self.text(craft_prompt, "image_prompt_model", 0.7)
            if not concept:
                raise ValueError("Empty image concept")
        except Exception:
            logger.warning("Image concept generation failed; preserving the selected style in the fallback.")
            concept = f"Illustrate the preached message of {title}: {description}"
        return finalize_image_prompt(profile, concept, title, description, transcript), craft_prompt

    def image(self, profile, prompt):
        profile = validate_profile(profile)
        response = self.client.images.generate(
            model=self.ai.get("image_model", "gpt-image-1.5"), prompt=prompt,
            size="1024x1024", quality=profile["image_quality"],
        )
        if not response.data or not response.data[0].b64_json:
            raise ValueError("Image API returned no image")
        usage = getattr(response, "usage", None)
        self.usage.append({"purpose": "image_model", "model": self.ai.get("image_model", "gpt-image-1.5"),
                           "usage": usage.model_dump() if usage else {}})
        return base64.b64decode(response.data[0].b64_json, validate=True)

    def preview(self, job):
        profile = validate_profile(job["profile"])
        source = job["input"]
        kind = job["kind"]
        if kind not in ("text", "concept", "image", "full"):
            raise ValueError("Unknown preview kind")
        if kind in ("image", "full") and not profile["images_enabled"]:
            raise ValueError("Image generation is disabled in this profile")
        result = {"revision": profile["revision"], "profile": profile, "input": dict(source), "size": "1024x1024"}
        title, description = source.get("title", ""), source.get("description", "")
        if kind in ("text", "full"):
            metadata, prompt = self.metadata(profile, source["filename"], source["transcript"])
            result.update(metadata=metadata, analysis_prompt=prompt)
            title, description = metadata["title"], metadata["description"]
        if kind in ("concept", "image", "full"):
            if kind == "image" and source.get("reviewed_image_prompt"):
                prompt, craft = source["reviewed_image_prompt"], "Previously reviewed concept; no new concept call."
            else:
                prompt, craft = self.image_prompt(profile, title, description, source.get("transcript"))
            result.update(image_prompt=prompt, concept_prompt=craft)
            if kind in ("image", "full"):
                result["image_base64"] = base64.b64encode(self.image(profile, prompt)).decode("ascii")
        result["usage"] = self.usage
        return result
