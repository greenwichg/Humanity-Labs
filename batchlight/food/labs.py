"""Directory of food-testing laboratories, curated from official accreditation and notification sources."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import LAB_TESTS

TEST_LABELS = {
    "pesticides": "Pesticide residues",
    "microbiology": "Microbiology (bacteria such as Salmonella)",
    "heavy_metals": "Heavy metals (lead, arsenic and others)",
    "mycotoxins": "Mycotoxins (such as aflatoxin)",
    "adulterants": "Common adulterants",
    "allergens": "Allergens",
    "nutrition": "Nutrition and composition",
    "water": "Drinking water",
}
assert set(TEST_LABELS) == set(LAB_TESTS)


class LabInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=3, max_length=300)
    city: str = Field(min_length=2, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    accreditation: str = Field(min_length=3, max_length=200)
    tests: list[str] = Field(default_factory=list)
    accepts_public_samples: bool | None = None
    website: str | None = Field(default=None, max_length=500, pattern=r"^https?://\S+$")
    phone: str | None = Field(default=None, max_length=50)
    source_url: str = Field(max_length=1000, pattern=r"^https://\S+$")
    verified_on: date
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("*", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("tests")
    @classmethod
    def known_tests(cls, value):
        unknown = set(value) - set(TEST_LABELS)
        if unknown:
            raise ValueError(f"Unknown test types: {', '.join(sorted(unknown))}")
        return sorted(set(value))

    @field_validator("verified_on")
    @classmethod
    def not_future(cls, value):
        if value > date.today():
            raise ValueError("The verification date cannot be in the future")
        return value
