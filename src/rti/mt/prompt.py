"""Prompt construction for real-time spoken translation."""
from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a professional simultaneous interpreter. "
    "Translate the user's {source} speech into natural, spoken {target}. "
    "Output ONLY the translation. Do not add explanations, quotes, notes, or the original text. "
    "Preserve meaning, tone, numbers, names and terminology. "
    "If the input is an incomplete fragment, translate the complete part naturally and stop."
)


def build_messages(
    text: str,
    context: list[str] | None = None,
    glossary: dict[str, str] | None = None,
    source: str = "zh-CN",
    target: str = "en-US",
) -> list[dict[str, str]]:
    lines = [SYSTEM_PROMPT.format(source=source, target=target)]
    if glossary:
        terms = "; ".join(f"{source_term} => {target_term}" for source_term, target_term in glossary.items())
        lines.append(f"Terminology (must follow): {terms}")
    if context:
        recent = " | ".join(context[-4:])
        lines.append(f"Recent context (do not translate): {recent}")
    return [
        {"role": "system", "content": "\n".join(lines)},
        {"role": "user", "content": text},
    ]
