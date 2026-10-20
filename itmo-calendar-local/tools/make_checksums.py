"""Generate SHA256SUMS.txt for release files, never for itself."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--directory', type=Path, required=True)
    args = p.parse_args()
    directory = args.directory
    filenames = sorted(p for p in directory.iterdir()
                       if p.is_file() and p.name != 'SHA256SUMS.txt')
    if not filenames:
        raise RuntimeError('No release files')
    digest_lines = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}'
                    for p in filenames]
    (directory / 'SHA256SUMS.txt').write_text('\n'.join(digest_lines) + '\n', encoding='utf-8')
    print(f'Wrote checksums for {len(filenames)} assets')


if __name__ == '__main__':
    main()
