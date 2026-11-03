"""Tests for CODEOWNERS-style principal lists."""

import pytest

from stampbot.principals import (
    MAX_PRINCIPALS,
    MAX_RAW_PRINCIPALS,
    PrincipalSet,
    TeamRef,
    parse_principals,
)


def test_parse_principals_splits_logins_and_teams():
    """Test a slash marks a team and anything else is a login."""
    parsed = parse_principals(
        ["renovate[bot]", "@octocat", "@acme/release-team", "acme/deploy_team"], "setting"
    )
    assert parsed.logins == ("renovate[bot]", "octocat")
    assert parsed.teams == (TeamRef("acme", "release-team"), TeamRef("acme", "deploy_team"))
    assert parsed.describe() == [
        "@renovate[bot]",
        "@octocat",
        "@acme/release-team",
        "@acme/deploy_team",
    ]


def test_parse_principals_removes_case_insensitive_duplicates():
    """Test duplicate logins and teams collapse to the first spelling."""
    parsed = parse_principals(
        ["Octocat", "octocat", "@Acme/Release", "acme/release", "release"], "setting"
    )
    assert parsed.logins == ("Octocat", "release")
    assert parsed.teams == (TeamRef("Acme", "Release"),)
    assert len(parsed) == 3


def test_union_keeps_first_spelling():
    """Test union adds only entries the first set lacks."""
    first = PrincipalSet(("octocat",), (TeamRef(None, "ops"),))
    second = PrincipalSet(("OCTOCAT", "hubot"), (TeamRef(None, "OPS"), TeamRef("acme", "ops")))
    merged = first.union(second)
    assert merged.logins == ("octocat", "hubot")
    assert merged.teams == (TeamRef(None, "ops"), TeamRef("acme", "ops"))
    assert merged.team_slugs() == ["ops"]


def test_empty_set_is_false():
    """Test an empty list disables the filter."""
    assert not parse_principals([], "setting")


def test_from_entries_keeps_first_position():
    """Test a later duplicate doesn't move the first entry behind intervening ones."""
    parsed = parse_principals(["@acme/a", "@acme/b", "@ACME/A", "x", "y", "X"], "setting")
    assert parsed.team_slugs() == ["a", "b"]
    assert parsed.logins == ("x", "y")


def test_has_login_rejects_non_strings():
    """Test a malformed webhook login never matches instead of raising."""
    parsed = parse_principals(["octocat"], "setting")
    assert not parsed.has_login(None)
    assert not parsed.has_login(42)


def test_bot_suffix_ignores_case():
    """Test an App login's [bot] suffix may use any case."""
    parsed = parse_principals(["Renovate[BOT]", "dependabot[Bot]"], "setting")
    assert parsed.has_login("renovate[bot]")
    assert parsed.has_login("dependabot[bot]")


def test_limit_applies_after_duplicates_are_removed():
    """Test repeated entries count once toward the limit."""
    parsed = parse_principals(["octocat"] * (MAX_PRINCIPALS + 1), "setting")
    assert parsed.logins == ("octocat",)


def test_raw_list_is_still_bounded():
    """Test a huge list is rejected before deduplication."""
    with pytest.raises(ValueError, match="too many entries"):
        parse_principals(["octocat"] * (MAX_RAW_PRINCIPALS + 1), "setting")
