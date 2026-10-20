"""Build an importable GitHub-ready repository ZIP, excluding ALL local state."""
from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

if __package__:
    from .build_release import ROOT, release_files
else:
    from build_release import ROOT, release_files

SOURCE_EXTRAS = (
    '.gitignore', '.gitattributes', '.editorconfig',
    'docs/RELEASE_GUIDE_RU.md',
    '.github/workflows/ci.yml', '.github/workflows/release.yml',
    '.github/dependabot.yml',
    '.github/ISSUE_TEMPLATE/bug_report.yml',
    '.github/ISSUE_TEMPLATE/feature_request.yml',
    '.github/ISSUE_TEMPLATE/config.yml',
)


def build_source_zip(destination: Path, root: Path = ROOT) -> tuple[str, ...]:
    items = set(release_files(root))
    items.update(Path(name) for name in SOURCE_EXTRAS)
    for directory in ('tests', 'tools'):
        items.update(p.relative_to(root) for p in (root / directory).glob('*.py'))
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    sorted_paths = sorted(items, key=lambda p: p.as_posix())
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as z:
        for relative in sorted_paths:
            src = root / relative
            if not src.is_file() or src.is_symlink() or not src.resolve().is_relative_to(root.resolve()):
                raise FileNotFoundError(f'Missing or unsafe source file: {relative}')
            info = ZipInfo(f'itmo-calendar-local/{relative.as_posix()}',
                           date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            info.compress_type = ZIP_DEFLATED
            z.writestr(info, src.read_bytes(), compress_type=ZIP_DEFLATED)
    return tuple(p.as_posix() for p in sorted_paths)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    print(f'GitHub-ready source ZIP with {len(build_source_zip(args.output))} allowlisted files: {args.output}')


if __name__ == '__main__':
    main()
