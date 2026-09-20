"""Structural checks on the GraphQL documents.

These catch the failures that only surface as a server-side validation error,
which would otherwise mean a broken integration rather than a failing test.
"""

import re

import pytest

from custom_components.fitracking.api import queries

DOCUMENTS = {
    "HOUSEHOLDS": queries.HOUSEHOLDS,
    "PET_DETAIL": queries.PET_DETAIL,
    "SET_DEVICE_OPS": queries.SET_DEVICE_OPS,
    "SET_LED_COLOR": queries.SET_LED_COLOR,
}

DEFINED = re.compile(r"fragment\s+(\w+)\s+on\s+\w+")
SPREAD = re.compile(r"\.\.\.\s*(\w+)")
# Inline fragments read "... on Type" and are not named spreads.
INLINE = re.compile(r"\.\.\.\s*on\s+\w+")


def _spreads(document: str) -> set[str]:
    return {name for name in SPREAD.findall(INLINE.sub("", document))}


@pytest.mark.parametrize("name", DOCUMENTS)
def test_every_spread_is_defined(name):
    document = DOCUMENTS[name]
    assert _spreads(document) <= set(DEFINED.findall(document))


@pytest.mark.parametrize("name", DOCUMENTS)
def test_no_unused_fragments(name):
    """GraphQL rejects a document carrying a fragment it never spreads."""
    document = DOCUMENTS[name]
    assert set(DEFINED.findall(document)) <= _spreads(document)


@pytest.mark.parametrize("name", DOCUMENTS)
def test_braces_balance(name):
    document = DOCUMENTS[name]
    assert document.count("{") == document.count("}")


def test_mutations_do_not_carry_the_pet_fragment():
    """Pulling PetProfile into a device mutation is an unused-fragment error."""
    assert "fragment PetProfile" not in queries.SET_LED_COLOR
    assert "fragment PetProfile" not in queries.SET_DEVICE_OPS


def test_no_user_details_are_requested():
    """pytryfi pulled the account email into every device poll; this must not."""
    for document in DOCUMENTS.values():
        assert "email" not in document
        assert "UserDetails" not in document


def test_pet_detail_declares_its_variables():
    assert "$petId" in queries.PET_DETAIL
    assert "$today" in queries.PET_DETAIL
    assert "query PetDetail($petId: ID!, $today: DateTime!)" in queries.PET_DETAIL


def test_place_is_scoped_to_ongoing_rest():
    """`place` is defined on OngoingRest, not the OngoingActivity interface."""
    rest_block = queries.PET_DETAIL.split("... on OngoingRest")[1]
    assert "place" in rest_block.split("}")[0] or "place" in rest_block[:200]
