"""
The ONLY file in this codebase that calls Gemini. Two calls, both listed in
the LLM boundary doc:

1. understand() -- picks a question type from a fixed list, pulls customer
   name mentions out of the sentence. Cannot invent a 6th intent (JSON schema
   with an enum enforces this) and cannot pick a customer (that's entities.py).

2. write() -- composes the final sentences. Called with NO TOOLS attached,
   so even if a WhatsApp message successfully "convinced" the model to do
   something, there is nothing for it to call. It only ever sees {{fN}}
   placeholders (see tools.py) -- it cannot produce a number because it is
   never given one.
"""
import json

from google import genai
from google.genai import types

from app.config.config import settings

_client = genai.Client(api_key=settings.gemini_api_key)
MODEL = "gemini-2.5-flash"

INTENTS = ["daily_list", "one_customer", "explain", "draft", "as_of"]

UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "mentions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "mentions"],
}

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "per_customer": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["customer_id", "line"],
            },
        },
        "draft": {"type": "string"},
        "tone": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "per_customer", "draft", "tone", "notes"],
}


def understand(question: str) -> dict:
    """GEMINI CALL 1 of 2. Returns {"intent": one of INTENTS, "mentions": [...]}."""
    response = _client.models.generate_content(
        model=MODEL,
        contents=(
            "Classify this collections question and extract any customer "
            f"names mentioned.\n\nQuestion: {question}"
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=UNDERSTAND_SCHEMA,
            temperature=0,
        ),
    )
    if response.text is None:
        raise ValueError("understand(): Gemini returned no text (likely blocked or empty response)")
    return json.loads(response.text)


def write(prompt: str) -> dict:
    """
    GEMINI CALL 2 of 2. NO TOOLS in this config -- that is the enforcement
    for "the agent cannot act on a customer message", not a prompt instruction.
    """
    response = _client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=WRITE_SCHEMA,
            temperature=0.2,
        ),
    )
    if response.text is None:
        raise ValueError("write(): Gemini returned no text (likely blocked or empty response)")
    return json.loads(response.text)
