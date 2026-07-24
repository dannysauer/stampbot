from pathlib import Path

ROOT = Path(__file__).parents[1]
APP_WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
CHART_WORKFLOW = (ROOT / ".github/workflows/chart-release.yml").read_text(encoding="utf-8")


def test_only_orchestrator_has_an_automatic_push_trigger() -> None:
    chart_triggers = CHART_WORKFLOW.split("permissions:", maxsplit=1)[0]

    assert "\n  push:" in APP_WORKFLOW
    assert "\n  push:" not in chart_triggers
    assert "workflow_dispatch:" in chart_triggers
    assert "workflow_call:" in chart_triggers


def test_app_and_chart_publishers_have_distinct_concurrency_groups() -> None:
    assert "group: stampbot-app-release-${{ github.repository }}" in APP_WORKFLOW
    assert "group: stampbot-chart-release-${{ github.repository }}" in CHART_WORKFLOW
    assert "cancel-in-progress: false" in APP_WORKFLOW
    assert "cancel-in-progress: false" in CHART_WORKFLOW


def test_slsa_never_discovers_or_uploads_to_a_release_by_tag() -> None:
    for workflow in (APP_WORKFLOW, CHART_WORKFLOW):
        assert "upload-assets: false" in workflow
        assert "upload-assets: true" not in workflow
        assert "upload-tag-name:" not in workflow
        assert "draft-release:" not in workflow


def test_slsa_callers_allow_the_pinned_generators_declared_permissions() -> None:
    app_provenance = APP_WORKFLOW.split("  app-release-provenance:", maxsplit=1)[1].split(
        "\n  publish-app-release:", maxsplit=1
    )[0]
    chart_provenance = CHART_WORKFLOW.split("  release-chart-provenance:", maxsplit=1)[1].split(
        "\n  publish-chart-release:", maxsplit=1
    )[0]

    # GitHub validates the reusable workflow's nested contents:write job even
    # though upload-assets=false makes that path a no-op at runtime.
    assert "      contents: write" in app_provenance
    assert "      contents: write" in chart_provenance


def test_finalization_uses_captured_numeric_release_identity() -> None:
    for workflow in (APP_WORKFLOW, CHART_WORKFLOW):
        assert "id: create-release" in workflow
        assert (
            "uses: softprops/action-gh-release@3d0d9888cb7fd7b750713d6e236d1fcb99157228" in workflow
        )
        assert "release_id: ${{ steps.create-release.outputs.id }}" in workflow
        assert "release_upload_url: ${{ steps.create-release.outputs.upload_url }}" in workflow
        assert (
            "uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in workflow
        )
        assert "python .github/scripts/finalize_release.py" in workflow
        assert '--release-id "$RELEASE_ID"' in workflow
        assert '--upload-url "$RELEASE_UPLOAD_URL"' in workflow
        assert "gh release edit" not in workflow
        assert "/releases/tags/${TAG}" not in workflow


def test_orchestrator_owns_chart_release_decision() -> None:
    assert "should_release_chart: ${{ steps.version.outputs.should_release_chart }}" in APP_WORKFLOW
    assert "needs.determine-version.outputs.should_release_chart == 'true'" in APP_WORKFLOW
    assert "app_version: ${{ needs.determine-version.outputs.version }}" in APP_WORKFLOW


def test_provenance_artifact_names_are_explicit() -> None:
    assert (
        "name: stampbot-${{ needs.determine-version.outputs.version }}.intoto.jsonl" in APP_WORKFLOW
    )
    assert (
        "name: stampbot-chart-${{ needs.release-chart.outputs.version }}.intoto.jsonl"
        in CHART_WORKFLOW
    )


def test_release_ref_guard_precedes_credentials_and_covers_reusable_chart() -> None:
    for workflow in (APP_WORKFLOW, CHART_WORKFLOW):
        guard = workflow.index("- name: Validate release ref")
        token = workflow.index("- name: Generate app token")

        assert guard < token
        assert 'EXPECTED_REF="refs/heads/${DEFAULT_BRANCH}"' in workflow
        assert 'if [ "$GITHUB_REF" != "$EXPECTED_REF" ]' in workflow

    chart_guard = CHART_WORKFLOW.split("- name: Validate release ref", maxsplit=1)[1].split(
        "- name: Generate app token", maxsplit=1
    )[0]
    assert "if: github.event_name" not in chart_guard


def test_explicit_app_retry_rejects_published_release() -> None:
    retry_validation = APP_WORKFLOW.index("- name: Validate retry release state")
    image_build = APP_WORKFLOW.index("build-and-publish-image:")

    assert retry_validation < image_build
    assert "is already published and cannot be retried" in APP_WORKFLOW
    assert "if: inputs.tag != ''" in APP_WORKFLOW


def test_chart_retry_is_explicit_and_does_not_recreate_the_tag() -> None:
    dispatch_triggers, callable_triggers = CHART_WORKFLOW.split("  workflow_call:", maxsplit=1)

    assert "retry_tag:" in dispatch_triggers
    assert "retry_tag:" not in callable_triggers.split("permissions:", maxsplit=1)[0]
    assert "Chart retry tag must use strict chart-vX.Y.Z semantic versioning" in CHART_WORKFLOW
    assert "does not point to the checked-out commit" in CHART_WORKFLOW
    assert "if: steps.check.outputs.skip != 'true' && inputs.retry_tag == ''" in CHART_WORKFLOW
    assert "Chart release $RETRY_TAG is already published and cannot be retried" in CHART_WORKFLOW


def test_chart_requires_a_published_application_release() -> None:
    app_release_validation = CHART_WORKFLOW.index("- name: Validate application release")
    registry_push = CHART_WORKFLOW.index("- name: Push chart to OCI registry")

    assert app_release_validation < registry_push
    assert "Published application release $APP_TAG was not found" in CHART_WORKFLOW
    assert "Application release $APP_TAG is not published" in CHART_WORKFLOW
