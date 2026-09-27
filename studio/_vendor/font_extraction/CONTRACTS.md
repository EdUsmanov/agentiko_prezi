# Контракты входа и выхода

Скрипты в этом каталоге принимают только локальные PPTX/POTX. Все пути
задаются обязательными флагами `--input` и `--output`. Читается OOXML внутри
архива с проверкой числа файлов, размера, степени сжатия и путей.

## `font-model.json` — schemaVersion 2

Машинная схема: [`schemas/font-model.schema.json`](schemas/font-model.schema.json).
Источник схемы — Pydantic `pyapi.domain.font_model.PresentationFontData`.
Необязательные поля в JSON опускаются. Если элемента на слайде нет, нет его
ключа в `elements`; если нет текстовых элементов, нет поля `elements`.

| Поле | Содержание |
| --- | --- |
| `presentation` | Имя входного файла. |
| `slides[]` | Все слайды в порядке презентации; `number` начинается с 1. |
| `slides[].kind` | `title` у слайда 1, `content` у остальных. |
| `slides[].elements` | Присутствующие роли: `title`, `subtitle`, `body`, `table`, `chart`, `diagram`, `footer`, `text`. |
| `usageBySlideKind` | Сводка по виду слайда и роли: семейство, начертание, размеры, номера слайдов. |
| `fontAssets[]` | Найденные файлы шрифтов с `id`, `path`, `sha256`, `bytes`. |
| `unresolved[]` | Сочетания `family`/`weight`/`style` без точного доступного файла. |
| `warnings[]` | Сообщения об ограничениях и ошибках отдельных файлов шрифтов. |

Каждый `FontUse` внутри роли содержит `family` (исходное имя), `script`
(`latin`, `ea`, `cs`, `sym`), `weight` (100–900), `style`
(`normal`/`italic`), `sizesPt` в пунктах, `source`, `evidence`, `shapeIds`,
`status` и `assetId`, если файл найден. `characters` есть у непустого текста.
Если размер не задан ни в тексте, ни в наследуемом стиле, `sizesPt` опускается.
`source` указывает источник свойства шрифта (`slide`, `layout`, `master`,
`presentation`, `theme`, `chart`, `diagram`), а не расположение текста.

`evidence: observed` — непустой текст; `placeholder` — настройка пустого
плейсхолдера. `size-heuristic` и `text-heuristic` — предположение о роли
обычной фигуры. Несколько шрифтов и размеров в одной роли сохраняются.

`status` принимает `embedded`, `installed`, `downloaded`, `missing`.
При `missing` поля `assetId` нет, а начертание есть в `unresolved`.
При наличии файла `assetId` ссылается на `fontAssets[].id`.
`fontAssets[].path` относителен к выходному каталогу и ведёт в `fonts/`.
Каталог `fonts/` управляется скриптом: при повторном запуске старые файлы,
которых нет в новой модели, удаляются после публикации нового `font-model.json`.
У скачанных файлов `fontAssets[].origin` указывает сервис загрузки; у
встроенных и локально установленных файлов поле отсутствует.
`resolvedFamily` может отличаться от исходного семейства, если имя начертания
нормализовано для источника. `fsType`, семейство, вес и стиль проверяются
перед упаковкой скачанного файла.

## `summary.json` — schemaVersion 1

Машинная схема: [`schemas/batch-report.schema.json`](schemas/batch-report.schema.json).
В `counts` указаны `templates`, `succeeded`, `failed`, `slides`,
`resolvedVariants`, `unresolvedVariants`. Начертания суммируются по файлам,
а не считаются уникальными для корпуса.

`presentations[]` содержит имя файла, `status` (`ok`/`error`), счётчики,
относительный `modelPath`, список `unresolved` и строку `error` при ошибке.
`status: ok` означает успешный разбор и сохранение валидной модели;
недоступные шрифты сами по себе не меняют статус. Отчёт обновляется после
каждого файла. В `summary.csv` указаны те же скалярные поля, но нет вложенного
списка `unresolved`.

## Датасет

`presentations.jsonl` имеет `schemaVersion: 1`: в каждом объекте есть
`presentation`, `slideCount`, `slides`, `fontCatalog`. У слайда есть
`fontUses` для непустого текста, `fontHints` для пустых полей,
`recommendedFonts.title/body` и `recommendationBasis`.
`fontCatalog` хранит `usedOnSlides`, `hintedOnSlides`, `declaredOnSlides`,
`embeddedVariants`.

`slide-fonts.csv` содержит `presentation`, `slide`, `kind` (`used`/`hint`),
`family`, `script`, `role`, `source`, `weight`, `italic`, `sizesPt`,
`characters`, `runCount`, `shapeIds`. Массивы в CSV записаны JSON-строками.
Файлы шрифтов и текст презентации в датасет не включаются.
