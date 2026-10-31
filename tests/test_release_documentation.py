"""Release-facing documentation and the Admin documentation allowlist agree."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import pytest

from tests.test_readme_documentation import anchors

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative", [
    "docs/INTEGRATION_BACKUPS.md",
    "docs/baselines/golden/v7.7.0-validation-matrix.md",
    "docs/baselines/golden/v7.7.1-validation-matrix.md",
    "docs/OpenAI-Compatible-Transcription.md",
    "docs/HTTP-Tool-Templates.md",
    "docs/Appointment-booking-providers.md",
])
def test_new_release_guides_have_resolvable_local_links(relative):
    source = ROOT / relative
    for target in re.findall(r'\]\(([^\s)]+)', source.read_text()):
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
        assert path.is_file(), target
        if parsed.fragment:
            assert unquote(parsed.fragment) in anchors(path.read_text()), target


def test_new_contributors_have_matching_profile_badges_and_credit():
    readme = (ROOT / "README.md").read_text()
    credits = (ROOT / "CONTRIBUTORS.md").read_text()
    for login in ("pcbeingused333", "lucamancusodev", "abaichuan1017", "Andrew-Engelbrecht"):
        assert f'href="https://github.com/{login}"' in readme
        assert f'src="https://github.com/{login}.png"' in readme
        assert f"(https://github.com/{login})" in credits
    rows = re.findall(r'<td\b', readme)
    credited_rows = re.findall(r'^\| <img ', credits, flags=re.MULTILINE)
    assert len(rows) == len(credited_rows)
    contributor_table = credits.split("| Avatar |", 1)[1].split("<!-- markdownlint-restore -->", 1)[0]
    assert "\n\n|" not in contributor_table
    assert "Reports and Requirements" in credits


def test_upgrade_and_migration_keep_legacy_links_and_explicit_waiver():
    installation = (ROOT / "docs/INSTALLATION.md").read_text()
    for version in ("762", "763", "764", "770", "771"):
        assert f"upgrade-to-v{version}-existing-checkout" in anchors(installation)
    migration = (ROOT / "docs/MIGRATION.md").read_text()
    assert "v764-to-v770" in anchors(migration)
    assert "v770-to-v771" in anchors(migration)
    matrix = (ROOT / "docs/baselines/golden/v7.7.0-validation-matrix.md").read_text()
    assert "WAIVED explicitly for v7.7.0" in matrix
    assert "Merge approval | APPROVED" in matrix
    assert "Tag/date | APPROVED" in matrix
    assert "Authorization is not a claim that publication completed" in matrix
    assert "8c3369511f20e7b378a2c1a5c0ba710ada88d5b3" in matrix


def test_publication_metadata_keeps_one_security_section_and_explicit_support_transition():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    release = changelog.split("## [7.7.0]", 1)[1].split("\n## [", 1)[0]
    assert release.count("### Security") == 1
    current_release = changelog.split("## [7.7.1]", 1)[1].split("\n## [", 1)[0]
    assert current_release.count("### Fixed") == 1
    security = (ROOT / "SECURITY.md").read_text()
    assert "supported trains are 7.7.x and 7.6.x" in security
    assert "7.5.x is\nunsupported" in security


def test_current_release_waiver_is_fresh_and_does_not_authorize_merge_or_tag():
    matrix = (ROOT / "docs/baselines/golden/v7.7.1-validation-matrix.md").read_text()
    assert "WAIVED explicitly for v7.7.1" in matrix
    assert "exact-revision live-call evidence" in matrix
    assert "successful-call golden-baseline requirement" in matrix
    assert "Merge approval | HOLD" in matrix
    assert "Tag/date | HOLD" in matrix
    assert "Supported trains remain 7.7.x and 7.6.x" in matrix


def test_transcription_details_live_in_guide_without_breaking_readme_anchor():
    readme = (ROOT / "README.md").read_text()
    guide = (ROOT / "docs/OpenAI-Compatible-Transcription.md").read_text()
    assert "openai-compatible-transcription" in anchors(readme)
    assert "docs/OpenAI-Compatible-Transcription.md" in readme
    assert "25 MiB" not in readme
    for variable in ("AAVA_SPEECH_API_KEY", "AAVA_SPEECH_API_USERNAME", "AAVA_SPEECH_API_PASSWORD", "AAVA_SPEECH_STT_TIMEOUT_SECONDS"):
        assert variable in guide
        assert variable in (ROOT / "docs/ENVIRONMENT_VARIABLES.md").read_text()
    assert "synthetic" in guide and "no transcription screen" in guide
