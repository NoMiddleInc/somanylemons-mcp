"""Typed publication excerpts of recorded qualification evidence; never qualify rows."""

PROOF_FIELDS = (
    "employer_geography_proof", "employer_sector_proof", "employer_startup_proof", "eligibility_proof",
)
SOURCE_SCHEMA = {key: ("string", 500) for key in (
    "url", "source_url", "content_hash", "source_hash", "observed_at", "operation_key",
    "source_operation_key", "decision_operation_key",
)}
EVIDENCE_SCHEMA = {"source_url": ("string", 500), "quote": ("string", 1000)}
CLAIM_SCHEMA = {
    "requirement": ("string", 500), "matches": ("boolean",), "reason": ("string", 500), "evidence": ("array_object", 3, EVIDENCE_SCHEMA),
}
PROOF_SCHEMA = {key: ("string", 500) for key in (
    "version", "domain", "company", "company_name", "source_url", "source_hash", "observed_at",
    "basis", "reason", "location_kind", "decision_operation_key", "source_operation_key",
)}
PROOF_SCHEMA.update({
    "quote": ("string", 1000),
    "requested_industries": ("array_string", 20, 500),
    "requested_locations": ("array_string", 20, 500),
    "qualification_requirements": ("array_string", 20, 500),
    "location": ("object_or_string", {key: ("string", 500) for key in ("city", "state", "country")}, 500),
    "sources": ("array_object", 10, SOURCE_SCHEMA),
    "claims": ("array_object", 10, CLAIM_SCHEMA),
})
SCOPE_FIELDS = {"requested_industries", "requested_locations", "qualification_requirements"}


def _object(value, schema):
    result = {}
    truncated = value.get("truncated") is True
    for key, kind in schema.items():
        if key not in value:
            continue
        original = value[key]
        if kind[0] == "string":
            if isinstance(original, str):
                result[key] = original[:kind[1]]
                truncated |= len(original) > kind[1]
            else:
                truncated = True
        elif kind[0] == "boolean":
            if isinstance(original, bool):
                result[key] = original
            else:
                truncated = True
        elif kind[0] in {"object", "object_or_string"}:
            if isinstance(original, dict):
                result[key] = _object(original, kind[1])
                truncated |= result[key]["truncated"]
            elif kind[0] == "object_or_string" and isinstance(original, str):
                result[key] = original[:kind[2]]
                truncated |= len(original) > kind[2]
            else:
                truncated = True
        elif isinstance(original, list):
            selected = []
            for item in original[:kind[1]]:
                if kind[0] == "array_string" and isinstance(item, str):
                    selected.append(item[:kind[2]])
                    truncated |= len(item) > kind[2]
                elif kind[0] == "array_object" and isinstance(item, dict):
                    projected = _object(item, kind[2])
                    selected.append(projected)
                    truncated |= projected["truncated"]
            result[key] = selected
            truncated |= len(selected) != len(original)
        else:
            truncated = True
    result["truncated"] = truncated
    return result


def scope_proof(value):
    if not isinstance(value, dict):
        return None
    # Literal requested scope is safe metadata even when stored alongside a
    # private model input. No other model input or decision is published.
    selected = dict(value)
    decision = value.get("decision_input")
    if isinstance(decision, dict):
        for key in SCOPE_FIELDS:
            if key not in selected and key in decision:
                selected[key] = decision[key]
    return _object(selected, PROOF_SCHEMA)



def saved_scope_proofs(row):
    return {field: scope_proof(row[field]) for field in PROOF_FIELDS
            if isinstance(row.get(field), dict)}
