"""Public-release safety: fake credentials must never enter distributable ZIP."""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from zipfile import ZipFile

from tools.build_release import ROOT, build_zip, release_files


def test_version_metadata_and_package_match():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    from itmo_local import __version__

    assert project['version'] == __version__
    assert project['requires-python'] == '>=3.12,<3.14'
    assert project['license'] == 'MIT'


def test_release_zip_allowlist_does_not_include_private_files(tmp_path):
    stage = tmp_path / 'stage'
    for rel in release_files(ROOT):
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)

    # Deliberately plant *synthetic* files in normally secret locations.
    secret_files = {
        '.env': 'APPLE_PASSWORD=FAKE_DO_NOT_USE',
        'state/calendar.sqlite3': 'FAKE_SQLITE_DATA',
        'itmo-browser-profile/Cookies': 'FAKE_COOKIE_DATA',
        'demo.dpapi': 'FAKE_DPAPI_DATA',
        'logs/sync.log': 'FAKE_LOG_DATA',
        'backup.har': 'FAKE_HAR_DATA',
    }
    for name, data in secret_files.items():
        dest = stage / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(data, encoding='utf-8')

    output = tmp_path / 'windows.zip'
    names = build_zip(output, stage)
    with ZipFile(output) as archive:
        contents = archive.namelist()
        assert len(contents) == len(names)
        assert all(x.startswith('itmo-calendar-local/') for x in contents)
        assert 'itmo-calendar-local/SETUP.cmd' in contents
        assert 'itmo-calendar-local/FIRST_RUN.cmd' in contents
        assert 'itmo-calendar-local/itmo_local/browser_source.py' in contents
        assert 'itmo-calendar-local/.env.example' in contents
        assert 'itmo-calendar-local/.env' not in contents
        assert not any(x.endswith(('.sqlite3', '.dpapi', '.har', '.log')) for x in contents)
        data = b''.join(archive.read(name) for name in contents)
        for marker in [b'FAKE_DO_NOT_USE', b'FAKE_COOKIE_DATA', b'FAKE_DPAPI_DATA',
                       b'FAKE_SQLITE_DATA', b'FAKE_LOG_DATA', b'FAKE_HAR_DATA']:
            assert marker not in data


def test_setup_scripts_do_not_copy_secrets_into_env():
    script = (ROOT / 'scripts/first-run.ps1').read_text(encoding='utf-8-sig')
    assert "'BIND'" in script
    assert "'PUBLISH'" in script
    assert "'icloud', 'preview'" in script
    assert script.index("'icloud', 'preview'") < script.index("'icloud', 'push'")
    installer = (ROOT / 'scripts/install.ps1').read_text(encoding='utf-8-sig')
    assert "if (-not (Test-Path $EnvFile))" in installer
    assert "--disable-pip-version-check" in installer
    for path in (ROOT / 'SETUP.cmd', ROOT / 'FIRST_RUN.cmd'):
        assert path.is_file()


def test_source_bundle_includes_ci_but_never_state(tmp_path):
    from tools.build_source_zip import build_source_zip

    path = tmp_path / 'public-repository.zip'
    files = build_source_zip(path)
    assert '.github/workflows/ci.yml' in files
    assert '.github/workflows/release.yml' in files
    assert '.gitignore' in files
    assert 'tests/test_sync.py' in files
    assert not any('.env' == name for name in files)
    assert not any('sqlite3' in name or '.dpapi' in name or 'browser-profile' in name
                   for name in files)
