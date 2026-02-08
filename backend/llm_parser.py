"""LLM-based prompt parser using RouteLLM API.

Takes free-form user instructions and extracts structured
find-and-replace operations via an LLM call.
"""

import json
import logging
from typing import Any

import httpx

from config import ROUTELLM_API_KEY, ROUTELLM_BASE_URL, ROUTELLM_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a precise text replacement instruction parser.
Your job is to take a user's natural language instruction about editing text in a PDF
and convert it into a structured JSON response.

You MUST respond with ONLY valid JSON, no markdown, no explanation. The JSON schema is:
{
  "replacements": {
    "old_text_1": "new_text_1",
    "old_text_2": "new_text_2"
  },
  "case_sensitive": false,
  "notes": "optional notes about limitations or assumptions"
}

Rules:
1. Extract ALL find-and-replace pairs from the user's instruction.
2. Set case_sensitive to true only if the user explicitly requests case-sensitive matching.
3. If the instruction is ambiguous, make your best interpretation and explain in "notes".
4. If the user mentions constraints you cannot express (e.g., "only in headings"), note this in "notes".
5. Handle multiple languages (English, Russian, etc.).
6. For date-related instructions, generate all necessary replacement pairs.

Examples:

User: "Replace 2025 with 2026"
Response: {"replacements": {"2025": "2026"}, "case_sensitive": false, "notes": ""}

User: "Change John Smith to Jane Doe everywhere"
Response: {"replacements": {"John Smith": "Jane Doe"}, "case_sensitive": true, "notes": "Using case-sensitive to preserve name casing"}

User: "Замени слово 'проект' на 'программа'"
Response: {"replacements": {"проект": "программа"}, "case_sensitive": false, "notes": ""}

User: "Update all January dates to March"
Response: {"replacements": {"January": "March", "Jan": "Mar", "Jan.": "Mar."}, "case_sensitive": false, "notes": "Included common abbreviations of January"}

User: "Replace Draft with Final in the title only"
Response: {"replacements": {"Draft": "Final"}, "case_sensitive": true, "notes": "User requested replacement only in titles, but this tool replaces everywhere. Manual review recommended."}
"""


async def parse_prompt(user_prompt: str) -> dict[str, Any]:
    """Parse a natural language editing prompt into structured replacements.

    Args:
        user_prompt: Free-form text instruction from the user.

    Returns:
        Dict with keys: replacements (dict[str,str]), case_sensitive (bool), notes (str).

    Raises:
        ValueError: If the LLM response cannot be parsed.
        httpx.HTTPError: If the API call fails.
    """
    if not ROUTELLM_API_KEY:
        raise ValueError(
            "ROUTELLM_API_KEY is not configured. "
            "Set it in .env or use /api/edit-simple with explicit replacements."
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{ROUTELLM_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {ROUTELLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": ROUTELLM_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": 1024,
            },
        )
        response.raise_for_status()

    data = response.json()

    # Log token usage
    usage = data.get("usage", {})
    if usage:
        logger.info(
            "LLM usage — prompt: %d tokens, completion: %d tokens, total: %d tokens, model: %s",
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            usage.get("total_tokens", 0),
            data.get("model", "unknown"),
        )

    content = data["choices"][0]["message"]["content"].strip()

    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse LLM response: %s\nContent: %s", e, content)
        raise ValueError(f"LLM returned invalid JSON: {content[:200]}")

    # Validate structure
    if "replacements" not in parsed or not isinstance(parsed["replacements"], dict):
        raise ValueError("LLM response missing 'replacements' dict")

    return {
        "replacements": parsed["replacements"],
        "case_sensitive": parsed.get("case_sensitive", False),
        "notes": parsed.get("notes", ""),
    }
