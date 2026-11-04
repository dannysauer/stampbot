# Copyright 2026 Danny Sauer
# SPDX-License-Identifier: Apache-2.0

"""Parse and match GitHub user and team lists written in CODEOWNERS style.

An entry is either a login (``renovate[bot]`` or ``@octocat``) or a team
(``@acme/release-team``). GitHub logins can't contain ``/``, so a slash marks
a team. The leading ``@`` is optional. Logins and team references compare
case-insensitively, as GitHub does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

# Each team entry can cost GitHub API calls on every event, so keep lists short.
MAX_PRINCIPALS = 100
# Bound on raw entries before duplicates are removed, so a huge list can't make
# parsing slow. The real limit, MAX_PRINCIPALS, applies after deduplication.
MAX_RAW_PRINCIPALS = MAX_PRINCIPALS * 10
MAX_PRINCIPAL_LENGTH = 100

_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]*"
_LOGIN_RE = re.compile(rf"{_NAME}(?:\[bot\])?", re.IGNORECASE)
_TEAM_RE = re.compile(rf"(?:(?P<org>{_NAME})/)?(?P<slug>{_NAME})")


@dataclass(frozen=True)
class TeamRef:
    """One organization team.

    Attributes:
        org: Organization login, or None for the repository owner's organization.
        slug: Team slug.
        setting: Policy setting the entry came from, used in error messages.
    """

    org: str | None
    slug: str
    setting: str = field(default="allowed_authors", compare=False)

    def key(self) -> tuple[str | None, str]:
        """Return a case-insensitive comparison key."""
        return (self.org.casefold() if self.org else None, self.slug.casefold())

    def display(self, owner_login: str | None = None) -> str:
        """Return the entry in CODEOWNERS form.

        Args:
            owner_login: Organization to show for a team that doesn't name one.
        """
        org = self.org or owner_login
        return f"@{org}/{self.slug}" if org else f"{self.slug} (team)"


@dataclass(frozen=True)
class PrincipalSet:
    """A parsed list of GitHub logins and teams without duplicates.

    Attributes:
        logins: Logins as first written, without a leading ``@``.
        teams: Team references as first written.
    """

    logins: tuple[str, ...] = ()
    teams: tuple[TeamRef, ...] = ()

    @classmethod
    def from_entries(cls, logins: Iterable[str], teams: Iterable[TeamRef]) -> PrincipalSet:
        """Build a set, keeping the first spelling of each duplicate.

        Args:
            logins: Logins in listed order.
            teams: Teams in listed order.

        Returns:
            The entries with case-insensitive duplicates removed.
        """
        unique_logins: dict[str, str] = {}
        for login in logins:
            unique_logins.setdefault(login.casefold(), login)
        unique_teams: dict[tuple[str | None, str], TeamRef] = {}
        for team in teams:
            unique_teams.setdefault(team.key(), team)
        return cls(tuple(unique_logins.values()), tuple(unique_teams.values()))

    def __len__(self) -> int:
        """Return the number of entries; an empty set disables its filter."""
        return len(self.logins) + len(self.teams)

    def has_login(self, login: object) -> bool:
        """Return True when ``login`` is listed directly.

        Args:
            login: GitHub login to look for. Anything but a string never matches.
        """
        if not isinstance(login, str):
            return False
        folded = login.casefold()
        return any(entry.casefold() == folded for entry in self.logins)

    def team_slugs(self) -> list[str]:
        """Return each listed team slug once, in listed order."""
        return list(dict.fromkeys(team.slug.casefold() for team in self.teams))

    def has_any_team(self, member_slugs: list[str]) -> bool:
        """Return True when any slug in ``member_slugs`` is a listed team.

        Args:
            member_slugs: Team slugs the user belongs to.
        """
        listed = set(self.team_slugs())
        return any(slug.casefold() in listed for slug in member_slugs)

    def check_team_owner(self, owner_login: str | None, where: str) -> None:
        """Reject a team that belongs to an organization other than the owner.

        Stampbot looks teams up only in the repository owner's organization, so
        a team in another organization could never match.

        Args:
            owner_login: Repository owner login, or None when unknown.
            where: Plain description of the file or settings holding the entries.

        Raises:
            ValueError: If a team names an organization other than ``owner_login``.
        """
        for team in self.teams:
            if team.org is None:
                continue
            if owner_login is None:
                raise ValueError(
                    f"The {team.setting} entry {team.display()!r} in {where} names a team, "
                    "but GitHub's event didn't say who owns this repository, so Stampbot "
                    "can't check the team. Redeliver the webhook, or replace the team "
                    "with logins."
                )
            if team.org.casefold() != owner_login.casefold():
                raise ValueError(
                    f"The {team.setting} entry {team.display()!r} in {where} names the "
                    f"{team.org!r} organization, but this repository belongs to "
                    f"{owner_login!r}. Stampbot only looks up teams in the repository "
                    f"owner's organization. Change the entry to "
                    f"'@{owner_login}/{team.slug}' or remove it."
                )

    def describe(self, owner_login: str | None = None) -> list[str]:
        """Return every entry in CODEOWNERS form, logins first.

        Args:
            owner_login: Organization to show for a team that doesn't name one.
        """
        return [f"@{login}" for login in self.logins] + [
            team.display(owner_login) for team in self.teams
        ]

    def union(self, other: PrincipalSet) -> PrincipalSet:
        """Return the entries of both sets without duplicates.

        Args:
            other: Set whose entries follow this set's entries.
        """
        return PrincipalSet.from_entries((*self.logins, *other.logins), (*self.teams, *other.teams))


def parse_principals(
    value: object,
    setting: str,
    *,
    allow_teams: bool = True,
    bare_name_is_team: bool = False,
) -> PrincipalSet:
    """Parse one policy setting into a PrincipalSet.

    Args:
        value: Parsed TOML value.
        setting: Setting name used in errors.
        allow_teams: Whether entries may name teams. The deprecated
            ``allowed_users`` setting accepts logins only.
        bare_name_is_team: Treat an entry without ``/`` as a team slug in the
            owner's organization. Only the deprecated ``allowed_teams`` uses this.

    Returns:
        The parsed entries in listed order.

    Raises:
        ValueError: If the value isn't a list of valid entries.
    """
    if not isinstance(value, list):
        raise ValueError(f"{setting} must be a list of strings")
    if len(value) > MAX_RAW_PRINCIPALS:
        raise ValueError(f"{setting} contains too many entries (maximum: {MAX_PRINCIPALS})")

    logins: list[str] = []
    teams: list[TeamRef] = []
    for entry in value:
        if not isinstance(entry, str):
            raise ValueError(f"{setting} must contain only strings")
        if len(entry) > MAX_PRINCIPAL_LENGTH:
            raise ValueError(
                f"{setting} contains an entry longer than {MAX_PRINCIPAL_LENGTH} characters"
            )
        name = entry[1:] if entry.startswith("@") else entry

        if "/" not in name and not bare_name_is_team:
            if _LOGIN_RE.fullmatch(name) is None:
                raise ValueError(f"{setting} entry {entry!r} is not a valid GitHub login")
            logins.append(name)
            continue

        if not allow_teams:
            raise ValueError(
                f"{setting} entry {entry!r} is a team; list teams in allowed_authors instead"
            )
        match = _TEAM_RE.fullmatch(name)
        if match is None or (match["org"] is None and not bare_name_is_team):
            raise ValueError(f"{setting} entry {entry!r} is not a valid team (@org/team-slug)")
        teams.append(TeamRef(org=match["org"], slug=match["slug"], setting=setting))

    parsed = PrincipalSet.from_entries(logins, teams)
    if len(parsed) > MAX_PRINCIPALS:
        raise ValueError(f"{setting} contains too many entries (maximum: {MAX_PRINCIPALS})")
    return parsed
