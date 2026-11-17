"""Domain validation for catalog evidence envelopes."""

from evidence_catalog.validation import (
    ValidationError, boolean, fields, identifier, integer, text,
)

PROJECT = 'recovery-drill-scorecard'


def validate_payload(payload):

    fields(payload, ("start", "end", "approved"))
    integer(payload["start"], "start minute")
    integer(payload["end"], "end minute")
    boolean(payload["approved"], "approved")
    if payload["start"] > payload["end"]:
        raise ValidationError("start must not follow end")


def example_payload():
    return {'start': 1, 'end': 5, 'approved': True}
