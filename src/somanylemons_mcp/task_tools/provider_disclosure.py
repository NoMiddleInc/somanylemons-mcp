"""Tool replies never name the contact-data vendor.

Human instruction on 2026-10-09: customers do not need to know the source, so
nothing the connector says may mention Apollo. The backend keeps the vendor name
in its own records; this mirrors its `insurance/provider_disclosure.py` wording
rules for every reply that leaves the connector.

An employer or person can literally be called Apollo (Apollo Global Management).
In prose, a name followed by a capitalised word is treated as that organisation.
"""

import json
import re

from mcp.types import TextContent

NEUTRAL_NAME = "contact database"

_VENDOR_URL = re.compile(r"(?i)\bhttps?://(?:[\w-]+\.)*apollo\.io[^\s|)\]\"'<>]*")
_IDENTIFIER = re.compile(r"(?<![@/.-])\b\w*[Aa][Pp][Oo][Ll][Ll][Oo]\w*\b")
_VENDOR = re.compile(
    r"(?<![\w@/.-])(?P<name>Apollo|APOLLO|apollo)(?P<site>\.io)?(?P<own>['’]s)?(?![\w@/])(?!\.\w)"
)
# Capitalised words that still describe the vendor, not an organisation's name.
_VENDOR_NOUNS = {
    "account", "accounts", "allowance", "api", "contact", "contacts", "credit", "credits",
    "data", "discovery", "email", "enrichment", "evidence", "fit", "icp", "id", "ids",
    "identity", "limit", "lookup", "match", "matches", "organization", "organizations",
    "people", "person", "profile", "prospect", "prospects", "provider", "query", "record",
    "records", "result", "results", "search", "source", "status", "url", "verification",
}  # fmt: skip
_VERBS = {
    "can", "cannot", "confirmed", "could", "did", "does", "found", "had", "has", "is",
    "lists", "may", "recorded", "records", "rejected", "reported", "reports", "returned",
    "returns", "shows", "was", "will", "would",
}  # fmt: skip
_DETERMINERS = {"a", "an", "the", "this", "that", "our", "your", "no", "one", "each", "any"}
# Values the customer or a publisher wrote: a person, employer or address named Apollo stays.
_IDENTITY_FIELDS = {
    "name", "first_name", "last_name", "company", "company_name", "organization", "employer",
    "agency", "title", "role", "email", "linkedin", "linkedin_url", "website", "website_url",
    "domain", "session_title", "request", "question",
}


def _rename_identifier(match: re.Match) -> str:
    """Rewrite machine names such as apollo_person_id or ApolloBillableBudgetExceeded."""
    token = match.group(0)
    if "_" in token:
        return re.sub(r"(?i)apollo", "provider", token)
    if re.match(r"Apollo[A-Z]", token):
        return "Provider" + token[len("Apollo") :]
    return token


def _is_organisation(text: str, match: re.Match) -> bool:
    """A literal employer or person named Apollo, which must be left as written."""
    if match["site"] or match["own"] or match["name"] != "Apollo":
        return False
    before, after = text[: match.start()], text[match.end() :]
    line_before = before.rsplit("\n", 1)[-1]
    line_after = after.split("\n", 1)[0]
    if line_before.rstrip().endswith("|") and (
        not line_after.strip() or line_after.lstrip().startswith("|")
    ):
        return True
    if re.search(r"(?:\bat|@)\s$", line_before):
        return True
    following = re.match(r"[ -]([A-Z][\w&]*)", after)
    return bool(following) and following[1].casefold() not in _VENDOR_NOUNS


def _neutral(text: str, match: re.Match) -> str:
    before, after = text[: match.start()], text[match.end() :]
    previous = re.search(r"([A-Za-z]+)\s$", before)
    previous_word = previous[1].casefold() if previous else ""
    next_word = re.match(r"\s([A-Za-z]+)", after)
    label = not before.strip() or bool(re.search(r"[:|;(\"']\s*$", before))
    if after.startswith("-"):
        phrase = "provider"
    elif match["own"]:
        phrase = f"{NEUTRAL_NAME}'s" if previous_word in _DETERMINERS else f"the {NEUTRAL_NAME}'s"
    elif previous_word in _DETERMINERS or (label and not next_word):
        phrase = NEUTRAL_NAME
    elif not next_word or next_word[1].casefold() in _VERBS:
        phrase = f"the {NEUTRAL_NAME}"
    else:
        phrase = NEUTRAL_NAME
    starts_sentence = not before.strip() or bool(re.search(r"(?:[.!?:|]\s+|\n\s*)$", before))
    return phrase[0].upper() + phrase[1:] if starts_sentence else phrase


def hide_data_provider(text: str) -> str:
    """Return customer-facing prose with the contact-data vendor's name removed."""
    if not isinstance(text, str) or "apollo" not in text.casefold():
        return text
    text = _VENDOR_URL.sub(f"{NEUTRAL_NAME} record", text)
    text = _IDENTIFIER.sub(_rename_identifier, text)
    parts, cursor = [], 0
    for match in _VENDOR.finditer(text):
        if _is_organisation(text, match):
            continue
        parts.append(text[cursor : match.start()])
        parts.append(_neutral(text, match))
        cursor = match.end()
    parts.append(text[cursor:])
    return re.sub(r"\b[Aa]n (?=contact database)", lambda m: m[0][0] + " ", "".join(parts))


def hide_data_provider_in_reply(value):
    """Rewrite every key, string and text block of a tool reply."""
    if isinstance(value, str):
        return hide_data_provider(value)
    if isinstance(value, (list, tuple)):
        return type(value)(hide_data_provider_in_reply(item) for item in value)
    if isinstance(value, dict):
        return {
            hide_data_provider(key): (
                item
                if isinstance(item, str) and key in _IDENTITY_FIELDS
                else hide_data_provider_in_reply(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, TextContent):
        return value.model_copy(update={"text": _hide_in_text_block(value.text)})
    return value


def _hide_in_text_block(text: str) -> str:
    """A serialized reply keeps its identity fields; any other text is treated as prose."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return hide_data_provider(text)
    if not isinstance(parsed, (dict, list)) or "apollo" not in text.casefold():
        return text
    return json.dumps(hide_data_provider_in_reply(parsed), indent=2, ensure_ascii=False)
