"""Region redaction proposals; automated candidates are never auto-confirmed."""

from dataclasses import dataclass

from .models import Grounding, Region


@dataclass(frozen=True, slots=True)
class RedactionProposal:
    grounding: Grounding
    reason: str
    automatic: bool = True
    confirmed: bool = False


def candidate(region: Region, source_sha256: str, frame_id: str, reason: str) -> RedactionProposal:
    return RedactionProposal(Grounding(source_sha256, frame_id=frame_id, region=region), reason)


def confirm(proposal: RedactionProposal) -> RedactionProposal:
    return RedactionProposal(proposal.grounding, proposal.reason, proposal.automatic, True)
