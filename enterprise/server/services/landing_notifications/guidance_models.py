from enum import StrEnum

from pydantic import BaseModel, Field


class GuidanceKind(StrEnum):
    VERIFIED_E2E = 'verified-e2e'
    SUGGESTED = 'suggested'


class GuidanceSource(StrEnum):
    PRIMARY_E2E = 'primary-e2e'
    CHANGED_E2E = 'changed-e2e'
    PR_HOW_TO_TEST = 'pr-how-to-test'
    LINEAR_ACCEPTANCE_CRITERIA = 'linear-acceptance-criteria'
    PR_SUMMARY = 'pr-summary'


class LinearIssueContext(BaseModel):
    identifier: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ''


class TestInstruction(BaseModel):
    kind: GuidanceKind
    source: GuidanceSource
    text: str = Field(min_length=1)
    url: str = Field(min_length=1)


class TestGuidance(BaseModel):
    instructions: tuple[TestInstruction, ...]

    @property
    def has_verified_e2e(self) -> bool:
        return any(
            instruction.kind == GuidanceKind.VERIFIED_E2E
            for instruction in self.instructions
        )
