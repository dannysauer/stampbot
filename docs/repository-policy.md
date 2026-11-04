# Repository policy reference

A `stampbot.toml` file decides when Stampbot approves pull requests in a
repository. This page lists every policy key, how the keys combine, and what
happens when the file is missing or invalid.

Service settings such as ports, credentials, and telemetry are in the
[service settings reference](service-settings.md). The `[defaults]` table
described there supplies policy when no file exists.

## Who can cause an approval

Stampbot approves through two paths, and each path checks a different person.

| Path | Who triggers it | What Stampbot checks |
| --- | --- | --- |
| Label approval | Anyone who can add labels to the pull request. GitHub gives that ability to people with triage access or higher. While the label stays on, reopening the pull request or, with `reapprove = true`, pushing a commit can also approve. | The approval label, then every configured filter: `required_labels`, `required_title_patterns`, and `allowed_authors`. |
| ChatOps command | Someone who comments `@stampbot` followed by one of the `approve_commands`. | Only the commenter's repository permission, against `chatops_required_permission`. Filters don't apply. |

`allowed_authors` checks who opened the pull request. It doesn't check who
added the label.

## Policy lookup

For each event, Stampbot checks these locations in order:

1. `stampbot.toml` on the target repository's default branch.
2. `stampbot.toml` in `OWNER/.github` when the owner is an organization.
3. The service-wide [`[defaults]`](service-settings.md#service-wide-policy-defaults).

The first file found wins. Stampbot doesn't merge repository and organization
files. A key that a file leaves out takes its value from the service defaults.
The author filter is the exception: a file that sets `allowed_authors`,
`allowed_users`, or `allowed_teams` replaces all three defaults. That includes
an empty list, so `allowed_teams = []` in a policy file turns off an author
filter that the service defaults set.

Each replica keeps the result, including "no file found", in memory for
`STAMPBOT_REPO_CONFIG_CACHE_SECONDS` (default 300). Within that window, events
for the same installation, repository, and default branch reuse the parsed
policy without reading GitHub. Invalid policy and read failures aren't cached,
so a corrected file applies on the next event.

## Policy keys

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `approval_labels` | list of strings | `["autoapprove", "stamp"]` | Labels that trigger approval. Removing any configured approval label can dismiss active Stampbot approvals. |
| `auto_approve_on_label` | boolean | `true` | Enables label-driven approval and dismissal on label removal. |
| `reapprove` | boolean | `false` | Lets a `synchronize` event approve a new head when an approval label remains. With `false`, a new head is approved only by a later `labeled` or `reopened` event or a ChatOps command. |
| `chatops_enabled` | boolean | `true` | Enables `@stampbot` commands in pull request issue and review comments. |
| `chatops_required_permission` | enum | `maintain` | Minimum permission for approve and unapprove commands: `none`, `read`, `triage`, `write`, `maintain`, or `admin`. |
| `approve_commands` | list of strings | `["approve", "stamp"]` | Words that create approval after `@stampbot`. |
| `unapprove_commands` | list of strings | `["unapprove", "unstamp"]` | Words that dismiss active Stampbot approvals. |
| `required_labels` | list of strings | `[]` | At least one listed label must be present for label approval. |
| `required_title_patterns` | list of regex strings | `[]` | At least one Python-compatible expression must match the title. At most 20 patterns; each is limited to 256 characters. |
| `allowed_authors` | list of logins and teams | `[]` | The pull request author must be listed by login or belong to a listed team. See [Author entries](#author-entries). |
| `allowed_users` | list of logins | `[]` | Deprecated. Merged into `allowed_authors`. |
| `allowed_teams` | list of teams | `[]` | Deprecated. Merged into `allowed_authors`. A bare `team-slug` means a team in the repository owner's organization. |

An empty filter list disables that filter.

Command parsing reads one `\w+` word after `@stampbot`. Custom command words may
contain letters, digits, and underscores; spaces and hyphens end the word.

### Author entries

`allowed_authors` uses CODEOWNERS syntax. A slash marks a team, because GitHub
logins can't contain one.

| Entry | Matches |
| --- | --- |
| `octocat` or `@octocat` | A user whose login is `octocat`. |
| `renovate[bot]` or `@renovate[bot]` | Pull requests opened by the Renovate GitHub App. |
| `@acme/release-team` or `acme/release-team` | Members of the `release-team` team in the `acme` organization. |

Logins and team names compare without regard to case. `allowed_authors`,
`allowed_users`, and `allowed_teams` together hold at most 100 entries after
duplicates are removed, and each entry is at most 100 characters.

GitHub Apps open pull requests under a login ending in `[bot]`, and an App
can't join a team. List each App by its full login. An entry of `renovate`
names a user called `renovate`, not the App.

A team must belong to the organization that owns the repository, because
Stampbot looks up membership only there. An entry such as `@other-org/team`
makes the policy invalid, and the error tells you what to change it to.

Stampbot checks team membership only when the author isn't listed by login,
and stops at the first team that matches. Each team it checks costs two GitHub
API calls.

Two team problems show up only when Stampbot checks a pull request:

| Problem | What Stampbot does |
| --- | --- |
| GitHub can't find a listed team. | If Stampbot reaches that team before finding a match, it stops checking and doesn't approve. GitHub answers the same way when the App lacks read access to organization members, so the comment asks you to check both. |
| The repository belongs to a personal account. | Ignores team entries, because GitHub doesn't support teams there, and logs a warning when it loads the policy. |

When one of these problems is the reason a pull request wasn't approved,
Stampbot comments on the pull request. It comments when someone adds an
approval label or reopens the pull request, not on every push. A missing team
never lets a pull request through; fix or remove the entry to restore approval
for authors who rely on it.

Validation errors and these comments say where the rule lives: `stampbot.toml`
in the repository, the organization policy in `OWNER/.github`, or Stampbot's
service-wide defaults. Only the Stampbot operator can change the defaults, so
problems there tell repository owners to ask the operator, or to set
`allowed_authors` in their own `stampbot.toml` to replace the default rule.
Comments never name teams from the defaults; `@stampbot help` says how many
there are instead.

### Migrating from `allowed_users` and `allowed_teams`

Both older keys still work. Stampbot merges their entries into
`allowed_authors` and logs a warning naming the deprecated key each time it
loads the policy. To migrate, move the entries and add the organization to bare
team slugs:

```toml
# Before
allowed_users = ["renovate[bot]"]
allowed_teams = ["release-team"]

# After
allowed_authors = ["renovate[bot]", "@acme/release-team"]
```

`allowed_users` accepts logins only, so an entry containing `/` there makes the
policy invalid. An `org/` prefix in `allowed_teams` must name the repository
owner, the same as in `allowed_authors`.

## Filter evaluation

Filter categories combine with AND. Values within one category combine with OR.
The author filter is one category, so a login match or a team match passes it.

```toml
approval_labels = ["autoapprove"]
required_labels = ["safe-to-approve", "dependency-update"]
required_title_patterns = ["^fix:", "^chore\\(deps\\):"]
allowed_authors = ["renovate[bot]", "dependabot[bot]", "@acme/platform-automation"]
```

With this policy, a pull request needs the `autoapprove` label and one of the
two required labels. Its title must match one expression. Renovate, Dependabot,
or a member of `platform-automation` must have opened it. The bots are listed
by login because they can't be team members.

These filters apply only to label approval. ChatOps uses the commenter's
repository permission.

### Title-pattern limits

Title patterns use Python `re`-compatible behavior. Stampbot compiles them when
it loads policy and evaluates them in order. Each pattern gets 10 ms against a
title no longer than 256 characters; the first match passes.

Matching runs in a worker thread. A timeout or engine failure rejects approval
for that event with the reason "PR title pattern evaluation could not complete
within the 10 ms safety limit". Rewrite an expensive
expression instead of depending on more time.

## Validation

When Stampbot loads a policy, it checks:

- TOML syntax
- `chatops_required_permission` against the permission list
- that `approve_commands` and `unapprove_commands` are lists of strings
- the type, count, length, and syntax of `required_title_patterns`
- the format, count, and length of every author entry
- that every team belongs to the repository owner's organization

It doesn't reject every unknown key or every wrong TOML type, so use the types
in the key table.

## Missing and invalid policy

An absent policy file moves lookup to the next source. The `OWNER/.github`
repository is optional: when it doesn't exist or the App installation doesn't
include it, GitHub returns a repository-level `404` and Stampbot uses service
defaults.

A failure reading the target repository's policy stops automation for that
event. Once GitHub makes the organization repository available to the App, a
failure reading its policy does too. A readable but invalid policy also stops
automation. Each failure increments
`stampbot_repo_config_loads_total{status="error"}`.

On a newly opened pull request, Stampbot leaves a review comment. Validation
failures name the file and the invalid setting; read failures use a generic
message. When the problem is a value inherited from the service-wide defaults,
the comment only says the defaults are invalid, and Stampbot writes the details
to its logs for the operator, because they can name settings only the operator
should see. Other pull request and ChatOps
events return an error without changing a review.

A title-pattern timeout is different: the policy parsed, but that pull request
is ineligible. Stampbot creates no approval.
