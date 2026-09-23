# Архитектура

## Два этапа

```text
index-examples → reference profiles (independent styles)

PPTX + text + audience + instructions
  → ZIP/XML validation
  → template tokens + native source patterns + safe assets
  → sanitized source artwork → LibreOffice → cached template layers for HTML
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
| `native_template.py` | Каталог исходных макетов, перенос графики и фоновых relationships, подготовка кэша слоёв |
| `office.py` | Ограниченный по времени subprocess LibreOffice, отдельный профиль и шрифты документа |
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

`created_at` фиксируется при принятии generation request. `deadline_at=created_at+300`. На POSIX supervisor завершает выделенную группу процессов worker вместе с дочерними LibreOffice; ветки рендера — три потока. Один вызов LibreOffice ограничен 45 секундами. Гарантия завершения дерева процессов на Windows требует отдельного Job Object и пока не проверена. Поздний результат не может заменить `timed_out` на `completed`.

Расширение короткого brief ограничено 45 секундами, модельное планирование — 120 секундами с резервом на export. Один общий запрос описывает три варианта. Если JSON невалиден или запрос не удался, включается явно отмеченный экстрактивный fallback. Это не считается успешной проверкой модели. Текстовый critic получает отдельный ограниченный остаток бюджета. Native exports выполняются в трёх потоках, ограничивая число полных колод в памяти тремя. PDFium-вызовы сериализованы отдельным lock из-за ограничений потокобезопасности.

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

Сохраняются source master/layout/theme, фоновые изображения и безопасная нативная графика выбранного примера. Удаляется старый текст, а не весь shape tree. Для ordinary slides не переносятся непроверенные фотографии, таблицы и charts. Новое содержимое располагается в исходных title/body zones; при необходимости единая body-зона делится на колонки. Выбор учитывает вместимость. Сложные picture-driven макеты не используются как пустые фоторамки.

PPTX повторно открывается и проверяется на редактируемость и отсутствие external relationships. Затем LibreOffice отрисовывает именно этот PPTX в PDF; PNG получаются из PDF. HTML содержит подготовленный растровый фон плюс живое содержимое сцены, а PPTX — нативную графику, не этот растр. Цвет текста оценивается отдельно для каждой исходной зоны. Кэш слоёв защищён SHA-256 в PreparedPackage. При отсутствии LibreOffice используется явно отмеченный резервный PDF сцены и статус needs_review; дизайнерская/VLM-оценка не имитируется.

Планировщик получает required_outline, если исходные «Слайд N» согласуются с заданным числом слайдов. При семантической ошибке предусмотрен один repair-запрос в пределах тех же 120 секунд; причина от локального валидатора записывается в manifest. Ошибки провайдера не отражаются целиком, чтобы не раскрыть payload или ключи. Critic получает обязательную JSON-схему findings.

## Подключённый inference и OCR-расширение

В локальном API-режиме используется NeuralDeep и документированный alias `qwen3.8-27b-noreason`. Провайдерские настройки и ключ остаются на сервере. Structured output ограничивает число слайдов и допустимые fact/table IDs уже в JSON-схеме; локальная семантическая валидация остаётся обязательной. В manifest пишутся usage и длительности вызовов, но не ключи и не reasoning-тексты. Сбой модельной стадии даёт `needs_review`, а не успешный статус только по геометрии.

OCR-транспорт вынесен в `studio/ocr.py`, не активирован в pipeline. Распознавание исходных материалов должно выполняться до фиксации PreparedPackage; проверка выходных слайдов — внутри общего дедлайна. У загрузки и получения результата разные методы, чтобы не повторять списание квоты при сетевой неопределённости. Условия включения, в том числе проверка допустимости OCR-модели, описаны в OCR.md.
