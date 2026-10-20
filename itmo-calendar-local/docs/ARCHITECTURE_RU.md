# Архитектура (для разработчиков)

```text
ITMO.ID login (interactive, official website)
           |
Dedicated Playwright Edge/Chrome profile under LOCALAPPDATA
           |
my.itmo personal schedule response + up to four same-host range GETs
           |
Validation: exact origin, JSON schema, full dates, overlap equality, limits
           |
Normalized Lesson / Snapshot -> Syncer -> private local SQLite
           |
Read-only cloud preview -> explicit binding -> CalDAV stable-UID reconciliation
           |
Dedicated iCloud calendar -> Apple Calendar on iPhone
```

## Слои

- `browser_source.py`: первый интерактивный вход или headless-загрузка из официальной страницы. Логи не содержат cookies или HTTP-тел запросов. Дополнительные GET-запросы ограничены точным HTTPS-хостом и маршрутом.
- `models.py`: валидация полей расписания и нормализация времени (Europe/Moscow) с преобразованием в UTC для iCalendar.
- `sync.py` + `store.py`: локальная транзакция SQLite, стабильные UID, дедупликация, карантин опасных снимков, минимум два подтверждения отсутствующей пары с временным интервалом. Отсутствие данных за день НЕ считается отменой.
- `ical.py`: VEVENT, время, предмет, аудитория, преподаватель; `UID` стабилен между обновлениями.
- `mirror.py`: CalDAV чтение/сравнение/запись; разрешён только пустой первоначально привязанный календарь по точному имени, URL и аккаунту. Перед записью выявляются посторонние и неизвестные события. Запись частичных результатов восстанавливается при следующем сравнении.
- `secrets.py`: Apple app-specific пароль через Windows DPAPI; браузерная ITMO-сессия — в отдельном профиле. SQLite и профиль не шифруются приложением целиком.
- `__main__.py`: CLI, контроль блокировок процесса, фиксированные неразглашающие коды ошибок.

## Реалистичные границы

- Интеграция **неофициальная** и зависит от поведения сайта, которому не обещано сохранять недокументированный API.
- Автоматизированные сессии должны соответствовать правилам университета; задача проекта — не обходить авторизацию или ограничения.
- Тесты используют вымышленные данные. Реальные Apple и ITMO учётные записи не подключаются в CI.
- Windows-only runtime. Release ZIP — исходные скрипты с мастер-установкой, **не нативный исполняемый файл** и не подписанный инсталлятор.
