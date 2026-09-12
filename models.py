"""Pydantic v2 schemas for lead-enrichment-agent — TWO-MODEL split.

ExtractionPayload: the ONLY shape the LLM ever emits. Every field required,
additionalProperties false at every nesting level — its model_json_schema()
goes verbatim into Groq strict json_schema mode.
CompanyRecord: final output = payload fields + Python-computed confidence_score
+ errors. The LLM never emits these two fields.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeadershipEntry(_StrictModel):
    name: str
    title: str
    # No default: Groq strict mode requires EVERY field in `required`.
    # The LLM must always emit the key (empty string when unknown).
    linkedin_url: str


class ExtractionPayload(_StrictModel):
    company_overview: str
    target_audience: str
    # No defaults: all four keys must appear in `required` for strict mode.
    contact_points: list[str]
    leadership: list[LeadershipEntry]


class CompanyRecord(_StrictModel):
    company_overview: str
    target_audience: str
    contact_points: list[str] = Field(default_factory=list)
    leadership: list[LeadershipEntry] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)
    errors: list[str] = Field(default_factory=list)
    domain: str = ""
    leadership_sources: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        payload: ExtractionPayload,
        confidence_score: float,
        errors: list[str] | None = None,
        domain: str = "",
    ) -> "CompanyRecord":
        return cls(
            **payload.model_dump(),
            confidence_score=confidence_score,
            errors=errors or [],
            domain=domain,
        )


class RunSummary(_StrictModel):
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_runtime_s: float = 0.0
    domains_failed: int = 0


__all__ = [
    "LeadershipEntry",
    "ExtractionPayload",
    "CompanyRecord",
    "RunSummary",
    "ValidationError",
]
