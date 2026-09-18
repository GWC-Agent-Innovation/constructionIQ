"""Read a bounded local OKF bundle as advisory context, never executable code."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import yaml

MAX_FILE_BYTES = 32_000
MAX_OKF_CHARS = 8_000
MAX_DOCUMENTS = 10


def read_concept(path: Path) -> tuple[dict, str]:
    with path.open('rb') as stream:
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('Oversized OKF document')
    text = raw.decode('utf-8-sig')
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)(.*)\Z', text, re.S)
    if not match:
        raise ValueError('OKF requires YAML frontmatter')
    metadata = yaml.safe_load(match[1])
    if not isinstance(metadata, dict) or not isinstance(metadata.get('type'), str) or not metadata['type'].strip():
        raise ValueError('OKF requires a nonempty type')
    # Other fields are optional and unknown producer extensions are preserved.
    return metadata, match[2].strip()


def select_okf_context(spec, root: Path, *, max_chars: int = MAX_OKF_CHARS) -> str:
    root = root.resolve()
    if not root.is_dir():
        return ''
    features = {'always', 'bedroom', 'bathroom', 'kitchen', 'living', 'single-floor'}
    if spec.include_dining_room:
        features.add('dining')
    if spec.attached_bathroom_bedrooms:
        features.add('ensuite')
    if any('parking' in name.lower() or 'garage' in name.lower() for name in spec.must_have_rooms):
        features.add('parking')
    if spec.num_bedrooms and spec.num_bedrooms > 1:
        features.add('multiple-bedrooms')
    candidates = []
    try:
        paths = sorted(root.rglob('*.md'))[:100]
    except OSError:
        return ''
    for path in paths:
        if path.name in ('index.md', 'log.md'):
            continue
        try:
            if not path.resolve().is_relative_to(root):
                continue
            metadata, body = read_concept(path)
            if metadata.get('status') == 'deprecated':
                continue
            stale = metadata.get('stale_after')
            if stale:
                expiry = datetime.fromisoformat(str(stale).replace('Z', '+00:00'))
                if expiry.tzinfo is None or datetime.now(timezone.utc) >= expiry:
                    continue
            applies = metadata.get('applies_to', ['always'])
            if not isinstance(applies, list) or not all(isinstance(v, str) for v in applies):
                continue
            if not features.intersection(applies):
                continue
            priority = metadata.get('priority', 0)
            if type(priority) is not int or not 0 <= priority <= 100:
                continue
            if not body:
                continue
            identifier = path.relative_to(root).as_posix()
            # Include provenance and applicability, not just unqualified prose.
            section = f'OKF concept: {identifier}\n' + yaml.safe_dump(metadata, sort_keys=False) + '\n' + body
            candidates.append((-priority, identifier, section))
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError, RecursionError):
            continue
    selected = []
    used = 0
    for _, _, section in sorted(candidates):
        if len(selected) >= MAX_DOCUMENTS:
            break
        if used + len(section) + 2 <= max_chars:
            selected.append(section)
            used += len(section) + 2
    # Links are citation/navigation data. Never fetch URLs, execute code or
    # read linked files outside the bundle based on document instructions.
    return '\n\n'.join(selected)
