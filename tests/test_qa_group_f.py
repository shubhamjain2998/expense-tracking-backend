"""Tests for QA Group F: input length caps + consistent casefold.

Covers:
  8.1 – backend rejects names longer than 64 chars for categories, tags, persons
  8.3 – casefold (not lower) is applied consistently to categories, tags, persons
"""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SUPABASE_JWT_SECRET", "pytest-placeholder-secret")

from pydantic import ValidationError  # noqa: E402

from app.schemas import (  # noqa: E402
    CategoryCreate,
    CategoryRename,
    PersonCreate,
    TagCreate,
)


# ── Finding 8.1: length caps via Pydantic schema ──────────────────────────────


class TestCategoryNameLength:
    def test_accepts_name_at_max(self):
        CategoryCreate(name="a" * 64)

    def test_rejects_name_over_max(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="a" * 65)

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="")

    def test_rejects_empty_rename(self):
        with pytest.raises(ValidationError):
            CategoryRename(name="")

    def test_rejects_rename_over_max(self):
        with pytest.raises(ValidationError):
            CategoryRename(name="b" * 65)


class TestTagNameLength:
    def test_accepts_name_at_max(self):
        TagCreate(name="t" * 64)

    def test_rejects_name_over_max(self):
        with pytest.raises(ValidationError):
            TagCreate(name="t" * 65)

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            TagCreate(name="")


class TestPersonNameLength:
    def test_accepts_name_at_max(self):
        PersonCreate(name="p" * 64)

    def test_rejects_name_over_max(self):
        with pytest.raises(ValidationError):
            PersonCreate(name="p" * 65)

    def test_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            PersonCreate(name="")


# ── Finding 8.3: casefold applied in routers ─────────────────────────────────


def _read_router(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).parent.parent / "app" / "routers" / f"{name}.py").read_text()


def test_category_create_uses_casefold():
    src = _read_router("categories")
    assert ".casefold()" in src, "categories router must use .casefold() not .lower()"
    assert (
        ".lower()" not in src
    ), "categories router must not use .lower() (use .casefold())"


def test_category_rename_uses_casefold():
    # covered by the same file check above; assert separately for clarity
    src = _read_router("categories")
    assert src.count(".casefold()") >= 2, "both create and rename must call .casefold()"


def test_tag_create_uses_casefold():
    src = _read_router("tags")
    assert ".casefold()" in src, "tags router must use .casefold() not .lower()"
    assert ".lower()" not in src, "tags router must not use .lower() (use .casefold())"


def test_person_create_uses_casefold():
    src = _read_router("persons")
    assert ".casefold()" in src, "persons router must use .casefold()"


def test_casefold_normalises_german_sharp_s():
    """casefold turns ß→ss (str.lower() does not)."""
    assert "Straße".casefold() == "strasse"
    assert "Straße".lower() == "straße"


def test_casefold_passthrough_emoji_and_cjk():
    """Emoji and CJK ideographs are unchanged by casefold."""
    mixed = "🍕 Pizza 한국어"
    result = mixed.casefold()
    assert "🍕" in result
    assert "한국어" in result
    assert "pizza" in result
