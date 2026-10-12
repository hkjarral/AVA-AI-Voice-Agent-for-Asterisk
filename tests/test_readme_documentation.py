"""Protect the landing page's local links, release entry points and attribution."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def anchors(text: str) -> set[str]:
    result = set(re.findall(r'<a\s+(?:id|name)="([^"]+)"', text))
    seen: dict[str, int] = {}
    for heading in re.findall(r'^#{1,6}\s+(.+)$', text, flags=re.MULTILINE):
        heading = re.sub(r'\[([^]]+)\]\([^)]*\)', r'\1', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(slug if not count else f'{slug}-{count}')
    return result


def test_readme_local_links_and_anchors_resolve():
    source = ROOT / 'README.md'
    text = source.read_text()
    targets = re.findall(r'\]\(([^\s)]+)', text)
    targets += re.findall(r'(?:href|src|srcset)="([^"]+)"', text)
    for target in targets:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = (source.parent / unquote(parsed.path)).resolve() if parsed.path else source
        assert path.exists(), target
        if parsed.fragment:
            assert unquote(parsed.fragment) in anchors(path.read_text()), target


def test_contributor_badges_and_operator_boundary_are_preserved():
    readme = (ROOT / 'README.md').read_text()
    credits = (ROOT / 'CONTRIBUTORS.md').read_text()
    for avatar in re.findall(r'<img src="([^"]+)"', credits):
        assert avatar in readme, avatar
    assert 'AVA Core remains MIT-licensed, free, and fully functional on its own.' in readme
    assert readme.count('utm_source=github&utm_medium=readme&utm_campaign=ava_operator_preview&utm_content=core_readme') == 2
    assert 'currently available as an early-access preview' in readme
    assert 'https://youtu.be/fDZ_yMNenJc' in readme
    assert 'https://demo.agent6789.com' in readme
    assert '(925) 736-6718' in readme and '(909) 788-2282' in readme


def test_readme_install_tag_matches_release_metadata():
    readme = (ROOT / 'README.md').read_text()
    latest = re.search(r'^## \[(\d+\.\d+\.\d+)\]', (ROOT / 'CHANGELOG.md').read_text(), re.MULTILINE).group(1)
    assert f'git clone --branch v{latest} ' in readme
    assert f'version-{latest}-blue' in readme
    assert '## Quick Start' in readme
    assert readme.index('## Quick Start') < readme.index('## AVA Operator')
