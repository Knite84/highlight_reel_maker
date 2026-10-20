"""Conservative, explicit English brief directives; unspecified order is chronological."""

import re


def requested_order(brief: str) -> tuple[str, str | None]:
    result = ("chronological", None)
    for sentence in re.split(r"[.!?;\n]+", brief):
        pattern = (
            r"\breverse[- ]chronological(?:\s+order)?\b|\bnewest (?:capture )?first\b|"
            r"\b(?:narrative|non[- ]chronological|chronological)\s+(?:order|sequenc\w*)\b|"
            r"\bflash\s?backs?\b|\b(?:start|open|begin) with\b.+\bthen\b"
        )
        for match in re.finditer(pattern, sentence, re.IGNORECASE):
            prefix = sentence[: match.start()]
            if re.search(
                r"\b(?:no|not|never|avoid|without|don't|instead of)\s+(?:(?:use|want|any|the|a)\s+)*$",
                prefix,
                re.IGNORECASE,
            ):
                continue
            directive = match.group().lower()
            mode = "narrative"
            if directive.startswith(("reverse", "newest")):
                mode = "reverse_chronological"
            elif directive.startswith("chronological"):
                mode = "chronological"
            result = (mode, sentence.strip())
    return result
