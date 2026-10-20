"""Windows CLI; errors are fixed codes and never echo secrets."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .auth import Auth
from .browser_source import BrowserSource, browser_summary
from .common import SafeError, canonical, ensure_private_dir, file_lock
from .config import Settings, load_env_file
from .mirror import ICloudMirror
from .network import HTTP
from .secrets import WindowsSecrets
from .source import Source
from .store import Store
from .sync import Syncer


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Local Windows laptop: personal my.itmo -> iCloud CalDAV')
    commands = p.add_subparsers(dest='command', required=True)
    commands.add_parser('demo', help='Offline synthetic timetable: no ITMO/iCloud access or credentials')
    commands.add_parser('status', help='Read-only local health summary; secrets omitted')
    run = commands.add_parser('run', help='Fetch ITMO, update local SQLite, reconcile iCloud')
    run.add_argument('--skip-source', action='store_true', help='Retry iCloud push using last GOOD local snapshot')
    sync = commands.add_parser('sync-itmo', help='Fetch ITMO into local DB, without touching iCloud')
    sync.add_argument('--approve-digest', default=None,
                      help='Explicitly accept the particular quarantined snapshot checksum')
    browser = commands.add_parser('browser', help='ITMO sign-in using a separate browser profile')
    browser_sub = browser.add_subparsers(dest='browser_action', required=True)
    browser_sub.add_parser('login', help='Manual sign-in at ITMO.ID, then capture site schedule response')
    browser_sub.add_parser('check', help='Read timetable in a headless browser, without writing anything')
    auth = commands.add_parser('auth', help='Configure permitted official ITMO OAuth')
    auth_sub = auth.add_subparsers(dest='auth_action', required=True)
    auth_sub.add_parser('login')
    auth_sub.add_parser('forget')
    auth_sub.add_parser('set-client-secret', help='Only if approved client is confidential')
    icloud = commands.add_parser('icloud', help='Manage Apple CalDAV credentials/binding')
    ic_sub = icloud.add_subparsers(dest='icloud_action', required=True)
    ic_sub.add_parser('set-password')
    ic_sub.add_parser('forget-password')
    ic_sub.add_parser('check', help='Read-only check of chosen calendar')
    ic_sub.add_parser('bind', help='Bind an EMPTY calendar explicitly; never create one automatically')
    ic_sub.add_parser('preview', help='Read-only plan for iCloud changes; needs local real schedule')
    ic_push = ic_sub.add_parser('push', help='Apply only pending local changes to iCloud')
    ic_push.add_argument('--allow-large-deletion', action='store_true', help='Approve >10 confirmed deletions')
    backup = commands.add_parser('backup', help='Consistent SQLite backup; contains personal timetable')
    backup.add_argument('path', type=Path)
    return p


def setup_logging(data_dir: Path) -> None:
    ensure_private_dir(data_dir)
    log = logging.getLogger('itmo_calendar')
    log.setLevel(logging.INFO)
    log.addHandler(RotatingFileHandler(data_dir / 'sync.log', maxBytes=512_000, backupCount=3,
                                       encoding='utf-8'))
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    logging.getLogger('httpcore').setLevel(logging.CRITICAL)
    logging.getLogger('caldav').setLevel(logging.CRITICAL)


def demo() -> None:
    """Exercise only normalizer/state/ICS on disposable fictitious data."""
    from .ical import render_event
    with tempfile.TemporaryDirectory(prefix='itmo-local-demo-') as d:
        settings = Settings(data_dir=Path(d), mode='demo', past_days=1, future_days=3,
                            chunk_days=1, missing_min_seconds=60)
        store = Store(settings.data_dir)
        source = Source(settings, None, None)
        counts = Syncer(settings, store, source).run()
        with store.connect() as connection:
            active = [r for r in store.rows(connection) if r['active']]
        if not active:
            raise SafeError('demo_no_synthetic_events_generated')
        first = render_event(active[0], settings)
        if 'BEGIN:VEVENT' not in first or 'X-ITMO-LOCAL-SYNC:V1' not in first:
            raise SafeError('demo_invalid_ical')
        print('DEMO OK. Только вымышленные занятия; iCloud/my.itmo не вызывались.')
        print(canonical(counts))
        print('UID сохранён в SQLite; в реальном режиме отправка выполняется отдельным этапом.')


def run_source(settings, store, protected, *, approve_digest=None):
    http = HTTP(settings.max_response_bytes)
    if settings.itmo_auth_method == "browser":
        source = BrowserSource(settings)
    else:
        auth = Auth(settings, http, protected)
        source = Source(settings, auth, http)
    return Syncer(settings, store, source).run(approve_digest=approve_digest)


def main() -> int:
    args = make_parser().parse_args()
    try:
        if args.command == 'demo':
            demo()
            return 0
        load_env_file()
        settings = Settings.from_env()
        if os.name != 'nt':
            raise SafeError('windows_required_use_demo_on_other_operating_systems')
        setup_logging(settings.data_dir)
        store = Store(settings.data_dir)
        protected = WindowsSecrets(settings.data_dir)
        http = HTTP(settings.max_response_bytes)
        mirror = ICloudMirror(settings, store, protected)
        if args.command == 'status':
            result = store.status()
            result.update({'mode': settings.mode, 'itmo_auth_method': settings.itmo_auth_method,
                           'authorized_itmo_client_configured': bool(settings.client_id),
                           'itmo_credentials_saved': (bool(protected.get_tokens())
                                                      if settings.itmo_auth_method == 'oauth' else None),
                           'browser_profile_exists':
                               (settings.data_dir / 'itmo-browser-profile').is_dir(),
                           'icloud_credentials_saved': bool(protected.get('icloud_password')),
                           'icloud_apple_id_configured': bool(settings.apple_id),
                           'last_push_counts': store.metadata('last_push_counts')})
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == 'browser':
            if settings.itmo_auth_method != 'browser':
                raise SafeError('set_ITMO_AUTH_METHOD_browser')
            snapshot = BrowserSource(settings).fetch(interactive=args.browser_action == 'login')
            print(canonical(browser_summary(snapshot)))
            print('Проверка браузера успешна. iCloud и локальное расписание не менялись.')
        elif args.command == 'auth':
            if settings.itmo_auth_method != 'oauth':
                raise SafeError('use_browser_login_or_set_ITMO_AUTH_METHOD_oauth')
            auth = Auth(settings, http, protected)
            if args.auth_action == 'login':
                auth.login()
                print('Разрешённая ITMO-сессия сохранена через DPAPI. Проверь sync-itmo.')
            elif args.auth_action == 'forget':
                auth.logout_local()
                print('Локальные ITMO-токены забыты. Дистанционная сессия не отозвана.')
            elif args.auth_action == 'set-client-secret':
                if not sys.stdin.isatty():
                    raise SafeError('interactive_terminal_required')
                secret = getpass.getpass('Разрешённый OAuth client secret (НЕ пароль ИТМО): ')
                protected.set('oauth_client_secret', secret)
                print('OAuth client secret защищён DPAPI.')
        elif args.command == 'icloud':
            if args.icloud_action == 'set-password':
                if not sys.stdin.isatty():
                    raise SafeError('interactive_terminal_required')
                value = getpass.getpass('Apple app-specific password (не основной пароль Apple): ')
                protected.set('icloud_password', value)
                print('App-specific пароль сохранён в Windows DPAPI.')
            elif args.icloud_action == 'forget-password':
                protected.forget('icloud_password')
                print('Локальная копия удалена. При необходимости отзови пароль в Apple Account.')
            elif args.icloud_action == 'check':
                print(canonical(mirror.check()))
            elif args.icloud_action == 'bind':
                mirror.bind_empty()
                print('Привязан конкретный пустой календарь. Другие календари не затрагиваются.')
            elif args.icloud_action == 'preview':
                print(canonical(mirror.push(dry_run=True)))
            elif args.icloud_action == 'push':
                with file_lock(settings.data_dir / 'run.lock', blocking=False):
                    print(canonical(mirror.push(allow_large_deletion=args.allow_large_deletion)))
        elif args.command == 'sync-itmo':
            if settings.mode != 'myitmo':
                raise SafeError('set_MODE_myitmo_to_fetch_personal_timetable')
            with file_lock(settings.data_dir / 'run.lock', blocking=False):
                print(canonical(run_source(settings, store, protected, approve_digest=args.approve_digest)))
        elif args.command == 'run':
            if settings.mode != 'myitmo':
                raise SafeError('set_MODE_myitmo_to_fetch_personal_timetable')
            if not mirror.binding():
                raise SafeError('icloud_calendar_not_bound_run_icloud_bind')
            with file_lock(settings.data_dir / 'run.lock', blocking=False):
                if not args.skip_source:
                    print('Получение и проверка расписания ИТМО...')
                    print('Локально:', canonical(run_source(settings, store, protected)))
                print('Синхронизация с iCloud:', canonical(mirror.push()))
        elif args.command == 'backup':
            path = args.path.expanduser().resolve()
            with file_lock(settings.data_dir / 'run.lock', blocking=False):
                store.backup(path)
                print('Создана копия SQLite с персональным расписанием. Секреты DPAPI не копируются.')
        return 0
    except SafeError as exc:
        print('ОШИБКА: ' + exc.code, file=sys.stderr)
        logging.getLogger('itmo_calendar').warning('failure_code=%s', exc.code)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Upstream exception strings/tracebacks can contain tokens or URLs.
        print('ОШИБКА: unexpected_internal_error (подробности скрыты для защиты данных)', file=sys.stderr)
        logging.getLogger('itmo_calendar').error('failure_code=unexpected_internal_error')
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
