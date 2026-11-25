"""Check relative Markdown links in README.md and docs/."""
from functools import cache
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
LINKS = re.compile(
    r'!?\[[^\]\n]+\]\(<?([^\s>]+?)>?(?:\s+"[^"]*")?\)'
    r'|^ {0,3}\[[^\]]+\]:[ \t]*<?([^\s>]+)>?',
    re.M,
)
FENCES = re.compile(r'^ {0,3}(`{3,}|~{3,}).*?(?:\n {0,3}\1[ \t]*$|\Z)', re.M | re.S)


def content(path: Path) -> str:
    return FENCES.sub(lambda match: '\n' * match[0].count('\n'), path.read_text(encoding='utf-8'))


@cache
def anchors(path: Path) -> set[str]:
    body = content(path)
    found = set(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', body))
    counts: dict[str, int] = {}
    for heading in re.findall(r'^ {0,3}#{1,6}\s+(.+)$', body, re.M):
        slug = re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        found.add(f'{slug}-{count}' if count else slug)
        counts[slug] = count + 1
    return found


def main() -> int:
    broken = 0
    for path in [ROOT / 'README.md', *sorted((ROOT / 'docs').rglob('*.md'))]:
        body = content(path)
        for match in LINKS.finditer(body):
            raw = match[1] or match[2]
            if raw.startswith(('/', '<', '`')) or re.match(r'^[\w+.-]+:', raw):
                continue
            url = urlsplit(raw)
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            fragment = unquote(url.fragment).lower()
            if not target.exists() or (
                fragment and target.suffix.lower() == '.md' and fragment not in anchors(target)
            ):
                line = body.count('\n', 0, match.start()) + 1
                print(f'{path.relative_to(ROOT).as_posix()}:{line}: {raw}')
                broken += 1
    print(f'{broken} broken relative links')
    return int(broken > 0)


if __name__ == '__main__':
    raise SystemExit(main())
