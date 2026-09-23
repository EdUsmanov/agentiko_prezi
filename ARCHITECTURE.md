# Архитектура

## Два этапа

```text
index-examples → reference profiles (independent styles)

PPTX + text + audience + instructions
  → ZIP/XML validation
  → template tokens + ordinary-slide zones + safe assets
  → facts + tables + constraints
  → PreparedPackage + DESIGN.md + hashes
  → READY

POST /api/generate (timer starts here)
  → immutable-package validation
  → dedicated worker process
  → optional short-brief proposals, explicitly labelled as drafts
  → three plans, common coverage contract
  → three bounded parallel render branches
  → deterministic audit + one repair pass
  → native PPTX / live HTML / PDF / previews
  → optional text-model critic
  → manifest + ZIP
  → COMPLETED / NEEDS_REVIEW, all within 300 seconds
```

Подготовка не генерирует скрытые готовые колоды. Планирование трёх вариантов и реальная вёрстка выполняются после старта таймера. Второе generation job отклоняется с 409, если первое ещё работает: скрытая очередь не исключается из SLA.

## Модули

| Модуль | Ответственность |
|---|---|
| `security.py` | Bounded ZIP/XML, triage строк с инструкциями |
| `template.py` | Анализ OOXML, tokens, геометрия групп, паттерны и ограниченный asset catalog |
| `content.py` | Декомпозиция текста, Markdown-таблицы, интерпретация числа слайдов |
| `opendesign.py` | Vendored craft + экспорт переносимого design package |
| `gateway.py` | Политика моделей, единственный inference endpoint, timeout, schema context |
| `author.py` | Расширение короткого brief маркированными предложениями без новых чисел |
| `planner.py` | Три варианта, валидация fact/table IDs, контроль чисел и покрытия |
| `composer.py` | Детерминированная геометрия, token composition, безопасные exemplar zones |
| `audit.py` | Проверки и ограниченный repair |
| `render.py` | Общая сцена → PPTX/HTML/PDF/PNG |
| `pipeline.py` | Подготовка, generation workflow, manifest |
| `store.py` | SQLite state machine, атомарная резервация генерации |
| `app.py` | Локальный HTTP API, загрузка, supervisor и интерфейс |
| `worker.py` | Отдельный убиваемый процесс генерации |

## Дедлайн

`created_at` фиксируется при принятии generation request. `deadline_at=created_at+300`. Supervisor прерывает весь worker, поэтому зависший синхронный renderer не может продолжить работу после истечения времени. В worker нет дочерних subprocesses: ветки рендера — потоки одного процесса. Поздний результат не может заменить `timed_out` на `completed`.

Расширение короткого brief ограничено 45 секундами, модельное планирование — 70 секундами с резервом на export. Один общий запрос описывает три варианта. Если JSON невалиден или запрос не удался, включается явно отмеченный экстрактивный fallback. Это не считается успешной проверкой модели. Текстовый critic получает отдельный ограниченный остаток бюджета. Native exports выполняются в трёх потоках, ограничивая число полных колод в памяти тремя. PDFium-вызовы сериализованы отдельным lock из-за ограничений потокобезопасности.

## Состояния

Подготовка: `accepted → running → ready | failed`.

Генерация: `accepted → running → completed | needs_review | failed | timed_out | cancelled`.

`needs_review` означает, что файлы доступны, но есть обязательные проверки с ошибкой. Это не зелёный успех. Неполные результаты после тайм-аута не выдаются через download API. После перезапуска незавершённые задания помечаются прерванными.

## Данные и версии

У каждой подготовки и генерации свой UUID-каталог. В `PreparedPackage` — SHA-256 исходного PPTX, текста, ограничений, версия анализатора и OpenDesign commit. Перед генерацией проверяется неизменность package и PPTX. Изменение пользовательских инструкций создаёт новый пакет, который ссылается на предыдущий; старые результаты сохраняются.

API не выдаёт `package.json` или исходный PPTX. Для скачивания разрешён конечный список имён файлов. База SQLite и загрузки находятся только в `data/`; эта директория не попадает в Git.

## OpenDesign

Используются материалы официального репозитория `nexu-io/open-design`, commit указан в `vendor/opendesign/PROVENANCE.json`. Это интеграция инструкций дизайна и формата design package, а не скрытый вызов закрытой модели через OpenDesign. Craft дополняет template contract; правила исходного PPTX имеют приоритет. Пакет допускает дальнейшее подключение daemon/skill workflow, но в текущем runtime этого нет.

## Рендеринг

Сохраняются реальные source master/layout/theme relationships. Старое содержимое master/layout очищается, чтобы не переносить текст и скрытые активы прошлого отчёта. Проверенные повторяющиеся маленькие брендовые изображения добавляются по исходным координатам. Текст, таблицы и бары диаграмм создаются отдельными объектами. Произвольный декор/сложные градиенты исходника пока не восстанавливаются полностью.

PPTX повторно открывается библиотекой и проверяется на отдельные текстовые объекты и внешние relationships. PDF/HTML/PNG строятся из той же сцены, но не являются визуальным round-trip из самого PPTX. Это ограничение явно отображается в UI и manifest.
