# Как опубликовать ITMO Calendar Local на GitHub

## 1. Перед публикацией

Скачай исходный **GitHub-ready ZIP** или используй подготовленную папку с
`.github`, `itmo_local`, `tests`, `tools`, `scripts`. Это **не** пользовательский ZIP
из Releases (в нём нет тестов и GitHub Actions).

Проверь личные данные: `git status --short`, список добавленных файлов и
содержимое README. Не включай `.env`, `.venv`, SQLite, `.dpapi`, browser profile,
логи, HAR, токены, скриншоты с личным расписанием. `.gitignore` помогает, но не
может исправить секрет, вставленный прямо в исходный `.py` или `.md`.

Также заранее реши, подходит ли **MIT-лицензия**; она разрешает использование,
модификацию и распространение программы, в том числе коммерческое.

Открой **Settings → Security** и включи **Private vulnerability reporting**.

## 2. Создание репозитория

На GitHub создай **пустой** публичный репозиторий, например `itmo-calendar-local`:
не добавляй ещё один README или LICENSE, они уже включены.

В PowerShell в папке исходного репозитория:

```powershell
git init -b main
git add .
git status --short
git commit -m "Initial public beta: ITMO Calendar Local 0.5.1"
git remote add origin https://github.com/<YOUR_USERNAME>/itmo-calendar-local.git
git push -u origin main
```

Замени `<YOUR_USERNAME>` своим логином GitHub. Для HTTPS используй официальную
авторизацию Git Credential Manager или GitHub CLI, **не вставляй токен в URL**.

Альтернативно можно использовать GitHub Desktop и пункт **Publish repository**.

## 3. CI

`.github/workflows/ci.yml` запускает unit-тесты и офлайн-демо на Windows и
Linux, собирает Python wheel и релизный ZIP. Ни GitHub Actions, ни тестам **не
нужны** пароли Apple или доступ к ITMO.ID. Не добавляй эти секреты в настройки
репозитория.

Убедись, что первый CI-run зелёный. Команды для локальной проверки:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m itmo_local demo
.\.venv\Scripts\python.exe tools\build_release.py --output dist\itmo-calendar-local-v0.5.1-windows.zip
```

## 4. GitHub Release

Только когда CI прошёл и инструкции были проверены на чистой Windows-машине:

```powershell
git tag v0.5.1
git push origin v0.5.1
```

`.github/workflows/release.yml` проверяет соответствие тега версии из
`pyproject.toml`, запускает тесты, собирает **Windows ZIP**, Python wheel,
source distribution и `SHA256SUMS.txt`, затем создаёт GitHub Release с
автоматическими заметками. Для этого job запрашивает `contents: write`;
остальные workflows — только чтение.

Пользователям в README и описании релиза лучше давать ссылку именно на
`itmo-calendar-local-v0.5.1-windows.zip`, а не на GitHub **Source code (zip)**,
потому что первый содержит понятные `SETUP.cmd` и `FIRST_RUN.cmd`.

## 5. Перед следующим релизом

- Обнови версию одновременно в `pyproject.toml`, `itmo_local/__init__.py` и
  описание в README/CHANGELOG.
- Проверь изменения зависимости Playwright/CalDAV на тестовом календаре.
- Запусти тесты и офлайн-демо.
- Никогда не добавляй в Issues скопированные личные ответы API или секреты.
- Публикуй новый тег; не переписывай существующие релизы задним числом.

## Честные ограничения первой версии

Это **публичная бета для Windows**, а не готовый подписанный `.exe` или версия
для iOS/macOS/Linux. У пользователя должны быть Python, Edge/Chrome и Apple
Account. Структура my.itmo может измениться; проект не официальный и не
гарантирует неизменность API или разрешённость автоматизации.
