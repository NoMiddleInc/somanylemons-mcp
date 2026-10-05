"""Bounded display of canonical saved email clocks; never invent verification."""
import re

CLOCKS = ("email_observed_at", "email_verified_at", "enriched_on")
FIELDS = ("email_source", "email_status", "enrichment_status", *CLOCKS)


def bounded_email_provenance(summary, page=1):
    groups = summary.get("recorded_email_provenance_groups")
    if not isinstance(groups, list):
        return dict(summary)
    calendar = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        display = {key: group.get(key, "not_recorded") for key in FIELDS}
        for key in CLOCKS:
            value = display[key]
            if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", value):
                display[key] = value[:10]
        identity = tuple(display[key] for key in FIELDS)
        count = group.get("people", 0)
        if type(count) is not int or count < 0:
            raise ValueError("Canonical provenance requires nonnegative recorded people counts")
        entry = calendar.setdefault(identity, {**display, "people": 0})
        entry["people"] += count
    projected = dict(summary)
    selected = groups[(page - 1) * 5:page * 5]
    projected["recorded_email_provenance_groups"] = selected
    calendar_groups = list(calendar.values())
    selected_calendar = calendar_groups[(page - 1) * 10:page * 10]
    projected["recorded_email_provenance_calendar_summary"] = selected_calendar
    projected["recorded_email_provenance_people_total"] = sum(group["people"] for group in calendar_groups)
    projected["recorded_email_provenance_calendar_precision"] = "Calendar dates grouped independently for observation, verification and enrichment; not one shared lookup clock. Missing dates remain not_recorded."
    projected["recorded_email_provenance_pagination"] = {
        "page": page, "page_size": 5, "groups_total": len(groups),
        "groups_returned": len(selected), "groups_omitted": len(groups) - len(selected),
        "next_page": page + 1 if page * 5 < len(groups) else None,
        "parameter": "source_page", "exact_timestamps_preserved": True,
        "calendar_groups_total": len(calendar_groups), "calendar_groups_returned": len(selected_calendar),
        "calendar_groups_omitted": len(calendar_groups) - len(selected_calendar), "calendar_page_size": 10,
        "calendar_summary_covers_all_groups": len(selected_calendar) == len(calendar_groups),
    }
    return projected
