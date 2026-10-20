"""Build a user-installable, allowlist-only ZIP. Never zip the working tree.

Example:
    python tools/build_release.py --output dist/itmo-calendar-local-v0.5.0-windows.zip

Only explicitly enumerated source files can enter the ZIP. No personal state,
.env file, browser profile, .git files, logs, caches, secrets or test data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
ZIP_ROOT = 'itmo-calendar-local'

ROOT_FILES = (
    'README.md', 'README_RU.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
    'CHANGELOG.md', '.env.example', 'pyproject.toml', 'requirements.txt',
    'requirements-dev.txt', 'SETUP.cmd', 'FIRST_RUN.cmd', 'RUN_NOW.cmd', 'STATUS.cmd',
)
DOC_FILES = (
    'ARCHITECTURE_RU.md', 'SETUP_WINDOWS_RU.md', 'FAQ_RU.md',
    'AUTHORIZATION_RU.md', 'BROWSER_LOGIN_RU.md', 'BROWSER_MONTHS_0_4.md',
)
SCRIPTS = (
    'install.ps1', 'first-run.ps1', 'register-task.ps1', 'unregister-task.ps1',
    'run-now.cmd', 'status.cmd',
)


def release_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Enumerate exact filenames; reject symlinks and missing required files."""
    names = (*ROOT_FILES, *(f'docs/{name}' for name in DOC_FILES),
             *(f'scripts/{name}' for name in SCRIPTS),
             *(f'itmo_local/{p.name}' for p in sorted((root / 'itmo_local').glob('*.py'))))
    items: list[Path] = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe release filename')
        candidate = root / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise FileNotFoundError(f'Missing or unsafe release file: {name}')
        if candidate.resolve().parent == root.resolve() and candidate.name == '.env':
            raise ValueError('Refusing secret configuration')
        items.append(relative)
    if not any(p.name == '__main__.py' for p in items):
        raise ValueError('Application code missing')
    return tuple(sorted(set(items), key=lambda p: p.as_posix()))


def build_zip(destination: Path, root: Path = ROOT) -> tuple[str, ...]:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    items = release_files(root)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED, compresslevel=9) as out:
        for relative in items:
            name = f'{ZIP_ROOT}/{relative.as_posix()}'
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            info.compress_type = ZIP_DEFLATED
            out.writestr(info, (root / relative).read_bytes(), compress_type=ZIP_DEFLATED)
    return tuple(p.as_posix() for p in items)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    files = build_zip(args.output)
    print(f'Release ZIP created with {len(files)} allowlisted files: {args.output}')


if __name__ == '__main__':
    main()
