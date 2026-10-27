# Examples for semgrep/rules.yaml, checked by `semgrep --test semgrep/`. Never imported.
# The path-scoped rules (env-read-outside-settings, llm-outside-factory, candidate-text-logged)
# do not apply to this file, so only the unscoped ones have examples here.
import logging

from app.llms.llm_factory import structured_call
from app.prompts import screening_prompts as prompts

logger = logging.getLogger(__name__)


def extract(state, settings, schema):
    # ruleid: upload-not-wrapped-as-document
    structured_call(schema, prompts.RESUME_EXTRACTION, state["resume_text"])
    # ruleid: upload-not-wrapped-as-document
    structured_call(schema, prompts.JOB_EXTRACTION, state.get("job_description", ""))
    # ok: upload-not-wrapped-as-document
    structured_call(schema, prompts.RESUME_EXTRACTION, prompts.as_document(state["resume_text"]))

    # ruleid: secret-value-logged
    logger.info("Using key %s", settings.gemini_api_key.get_secret_value())
    # ruleid: secret-value-logged
    print(f"key={settings.api_key.get_secret_value()}")
    # ok: secret-value-logged
    logger.info("Gemini key set: %s", bool(settings.gemini_api_key))
