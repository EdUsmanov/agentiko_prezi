# Модель угроз и границы защиты

## Входные форматы PowerPoint

PPTX/POTX проходят общие ZIP/XML-проверки до чтения библиотекой. Проверяется декларация основного OOXML content type, не только расширение: переименованный PPTM/POTM отклоняется, как и макросы/ActiveX/OLE. Для POTX нормализуется только тип основной части в копии в памяти; исходник, его хеш, темы, макеты и встроенные шрифты не переписываются. Сетевого конвертера нет. Исходный POTX, как и PPTX, не отправляется inference-провайдеру.

## Передача изображений для визуальной проверки

VLM отключена по умолчанию; `STUDIO_VLM_ENABLED=true` включает передачу отрисованных PNG настроенному inference-провайдеру после разрешения оператора/пользователя. PNG содержат текст и оформление. Исходный PPTX не передаётся. Режим изображений использует тот же валидированный endpoint/model policy, не произвольный URL модели. Изображения передаются как data URI из локально отрисованных файлов, максимум 10 по 5 МБ на запрос. Ни ссылки из слайдов, ни модельные пути не загружаются. Текст в картинках считается недоверенным. Ответ содержит только типизированные замечания и номера просмотренных слайдов; полнота и принадлежность номеров проверяются. Никакие инструкции, инструменты или код из ответа не выполняются.

Защищаем содержимое документов, server credentials, целостность файлов и соблюдение output contract. Недоверенные источники: пользовательский PPTX, notes и подписи, текст, имена файлов, ответы LLM. Пользовательские поля аудитории/инструкций разрешают менять презентацию, но не модельную политику и не доступы.

## Prompt injection

Основная граница — полномочия, а не regex. Основные LLM-стадии не получают tools. DeepPresenter получает только типизированные операции композиции; shell, filesystem, сетевые fetch-функции и ключ недоступны. Inference вызывается одним серверным адаптером по адресу из конфигурации. Анализатор шаблона получает геометрию и очищенные короткие текстовые образцы (до 480 символов на исходный слайд), а не изображения или PPTX. Образцы остаются недоверенными данными внутри JSON `untrusted_input`. Его ответ ограничен существующими pattern IDs, enum-ролями и плотностью; модель не может изменить координаты, шрифты, палитру или пути. Проверяются полнота и уникальность IDs. Текст контента также передаётся как JSON `untrusted_input` в user message, системные prompts лежат отдельно. План проходит Pydantic (`extra=forbid`), проверку IDs, покрытия, числа слайдов и чисел.

Сканер изолирует очевидные строки «ignore previous instructions», script-теги и запросы секретов. Он сохраняет только SHA-256 строки и позицию в журнале. Это triage с возможными ложными срабатываниями и пропусками, не доказательство семантической безопасности. Даже пропущенная инструкция не даёт модели произвольных инструментов: Design может только выбрать разрешённые IDs, проверить и завершить текущую композицию. Семантическая порча заголовка всё ещё возможна; её снижает contextual critic и проверка источников, но абсолютная защита не заявляется.

## Ограниченный DeepPresenter

Реальные upstream Design/Agent работают с нашим окружением из трёх операций: compose_slides, inspect_variants, finalize. Внешний API возвращает JSON, а не исполняемый код. На ход допускается одна операция; запрещены неизвестные поля, IDs, неполные и повторные назначения. Кандидат плана копируется и принимается атомарно после локального аудита. Финализация проверяет ревизию последней инспекции. Модель не меняет исходные факты, координаты, цвета, шрифты, пути или настройки inference.

Upstream Docker/MCP, Research, eval, HTML/JS и file tools не подключены. Runtime не получает API-ключ как часть конфигурации агента; вызов проходит через Studio ModelGateway. Upstream history/debug с содержимым отключены; в manifest остаются ID версии, имена операций, статусы и длительности. Это ограничение полномочий на уровне приложения, не отдельная OS sandbox. Старый сторонний executor не используется.

## Файлы и рендер

- Только bounded PPTX ZIP: ограничения compressed/uncompressed size, число частей и slides/objects; запрет traversal, symlink, duplicate entry, шифрования, ActiveX/VBA/OLE.
- XML разбирается с defusedxml до передачи python-pptx; DTD/entity expansion/XXE отклоняются.
- PPTX не распаковывается по исходным именам на диск.
- Внешние relationships не загружаются. В выходном файле их наличие приводит к ошибке.
- Только проверенные локальные PNG из исходного изображения; произвольный SVG-код и внешний URL не встраиваются.
- HTML состоит из фиксированного кода и экранированного текста, не из сгенерированного HTML модели. Нет scripts/CDN/remote fonts.
- До LibreOffice PPTX очищается от старого текста и внешних ссылок и повторно проверяется. Вызов не использует shell, получает отдельный временный профиль, ограниченный timeout и environment без LLM/API credentials. Это не OS sandbox. Резервный PDF создаётся Canvas API без интерпретации пользовательских строк как разметки.

## Веб и процессы

Сервер слушает 127.0.0.1, проверяет Host и Origin, запрещает cross-site writes, ограничивает объём request body. HTML предпросмотра отдаётся с CSP sandbox и без доступа к script/network. Download API имеет allowlist. Дедлайн контролируется отдельным процессом-родителем; остановка убивает generation worker с его потоками.

Это не OS sandbox: при уязвимости нативной библиотеки процесс может иметь доступы локального пользователя. Для публичного внедрения требуется контейнер без egress, non-root, read-only filesystem, memory/CPU limits, защищённый inference proxy, authentication и tenant isolation. Это будущая production-работа, а не обязательный многопользовательский режим хакатона.

## Конфиденциальность

В API-режиме текст фактов, параметры макетов и короткие очищенные фрагменты текста шаблона отправляются настроенному inference provider; интерфейс сообщает это до загрузки. Изображения и сам PPTX не отправляются. Смысловой разбор примеров организаторов использует такой же канал и отдельные кэши. Ключ не сохраняется в artifact manifest и не передаётся в model context. Логи приложения не печатают текст запроса или модельный ответ. PPTX notes содержат ссылки и исходный текст фактов: это часть итогового документа, пользователь должен учитывать это при распространении.

Загрузки/история хранятся локально в data. Автоматическая политика TTL и UI удаления не реализованы. Папка data исключена из Git, но не зашифрована. Не публикуйте её вместе с исходным кодом.
# Font resolution (2026-09-24)

Preparation may fetch exact static OFL TrueType files from the fixed official `google/fonts` repository on `raw.githubusercontent.com`. When it lacks a static face, the OFL-verified family can use `fonts.googleapis.com/css2` and `fonts.gstatic.com/s/`; only a static TTF matching the requested binary family, weight and italic flag is accepted. CSS unicode-range subsets are rejected. `STUDIO_DOWNLOAD_OPEN_FONTS=false` disables network lookup. Only sanitized family names enter provider paths: no URLs from uploaded documents, no redirects, no environment proxy credentials. HTTPS hosts are exact-matched; userinfo and explicit ports are forbidden. Requests have timeouts and streamed byte limits; metadata, license text, font format, identity and fsType are validated before atomic cache publication. Font names/variants are disclosed to the provider; slide contents are not. Failures remain missing-font reports, never arbitrary fallback families.

EOT/MTX decoding runs in a separate Node process with a 10-second kill-and-reap timeout, a 128 MiB V8 heap cap, a 16 MiB input/output/per-buffer cap and a 96 MiB cumulative explicit typed-buffer allocation cap. These are resource safeguards, not a general OS sandbox or total RSS limit. No model secrets, NODE_OPTIONS or NODE_PATH are inherited. EOT header restrictions are checked before decoding; decoded TTF restrictions, identity and Unicode coverage are checked again downstream. Encrypted/protected/domain-restricted fonts are not unlocked; fsType is not modified. The slot name regular is not trusted as a numeric weight. JS decoder and Python adapter changes both invalidate the organizer library.

All resolved font assets have SHA-256 pinned in the immutable package and rechecked before generation. `waiting_fonts` preserves private input and technical results; the resume API verifies both input and template hashes. Only the sanitized font report is downloadable during this state. Microsoft Aptos is an explicit operator installation, not an automatic commercial-font download. Its EULA is stricter than its embedding flags: binaries are excluded from the source package, exported PPTX and HTML; local-only-font PDF/HTML exports use rendered pixels. The Aptos package does not authorize commercial software hosting.
