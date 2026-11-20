import re
from typing import Any

from pydantic import BaseModel, Field

from openhands.app_server.event.event_parsing import parse_event_payload
from openhands.sdk import Event


class SequencedEvent(BaseModel):
    seq: int = Field(ge=0, le=2**53 - 1, strict=True)
    event: dict[str, Any]


def parse_stream_entries(
    events: list[SequencedEvent], first: int
) -> list[tuple[int, dict[str, Any], Event]]:
    entries = []
    seen_ids: set[str] = set()
    for seq, entry in enumerate(events, first):
        event_id = entry.event.get('id')
        if entry.seq != seq:
            raise ValueError('Noncontiguous batch')
        if not isinstance(event_id, str) or not re.fullmatch(
            r'[a-zA-Z0-9-]{1,64}', event_id
        ):
            raise ValueError('Invalid event ID')
        normalized_id = event_id.replace('-', '')
        if not normalized_id or normalized_id in seen_ids:
            raise ValueError('Missing or duplicate event ID')
        seen_ids.add(normalized_id)
        entries.append((seq, entry.event, parse_event_payload(entry.event)))
    return entries
