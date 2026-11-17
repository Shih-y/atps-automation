"""Extract one exact tag's Markdown section from the repository changelog."""
import argparse
from pathlib import Path
import re


VERSION_HEADING = re.compile(r'^## \[([^\]\r\n]+)\](?:[ \t]+-[ \t]+[^\r\n]+)?[ \t]*$')


def extract_notes(changelog, tag):
    sections = []
    current_tag = None
    body = []
    fence = None
    for line in changelog.splitlines(keepends=True):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence is not None:
            body.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = marker[1]
        elif re.match(r'^##(?:[ \t]+|$)', line):
            if current_tag == tag:
                sections.append(''.join(body).strip('\r\n'))
            heading = VERSION_HEADING.fullmatch(line.rstrip('\r\n'))
            current_tag = heading[1] if heading else None
            body = []
            continue
        body.append(line)
    if current_tag == tag:
        sections.append(''.join(body).strip('\r\n'))
    if len(sections) != 1:
        raise ValueError(f'Expected exactly one CHANGELOG.md section for tag {tag!r}; found {len(sections)}')
    if not sections[0].strip():
        raise ValueError(f'CHANGELOG.md section for tag {tag!r} is empty')
    return sections[0] + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--changelog', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        notes = extract_notes(args.changelog.read_text(encoding='utf-8-sig'), args.tag)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    args.output.write_text(notes, encoding='utf-8')


if __name__ == '__main__':
    main()
