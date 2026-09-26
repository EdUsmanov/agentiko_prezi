# Navy Contours: скрытая подстановка шрифта при экспорте

## Причина

В benchmark 05-main (preparation d11b1c81ca2a481f8a4bd9aa3b443f6e,
generation c68d6c4959ae4c95a0e715b0fddae00c) VLM обоснованно заблокировала
обложку: «Синтетический» разрывалось на «Синтетическ» и «ий».
Ширину composer измерял по установленному Calibri Bold (428.26 pt при 66 pt,
доступно 470.19 pt), но OOXML записывал полный face «Calibri Bold» как семейство.
LibreOffice выбирал Montserrat-Bold. Это подтверждено извлечением реальных
текстовых spans из отдельного native PDF, а не предположением по скриншоту.

## Изменения в обеих ветках

- font_identity.ooxml_face читает legacy family из name ID 1 и B/I из head.
  Typographic family ID 16 не используется: иначе теряются Light/Display.
- render.set_text задаёт canonical family и отдельные bold/italic для абзацев,
  runs и bullets. Метрики, размеры, тексты и шаблон не меняются.
- Нативные chart/legend/axis/data-label fonts используют ту же запись.
- Проверка геометрии реального PPTX учитывает отдельные B/I при выборе
  установленного face для измерения.
- Добавлены portable tests и optional native Calibri/LibreOffice regression.

## Верификация

Отдельный offline скрипт template-benchmark/verify_navy_font.py повторно
экспортирует неизменённые сохранённые scenes, без API/DeepPresenter rerun.
diagnostics/navy-font/before подтверждает Montserrat-Bold и разрыв.
diagnostics/navy-font/after подтверждает Calibri-Bold и две целые строки.
Все пять диагностических PNG осмотрены; исходные benchmark файлы не изменялись.
Это диагностика экспортера, не новый успешный сквозной прогон.

Первый тестовый запуск выявил ошибку новой фикстуры, которая меняла только
Windows name table, оставляя старые Macintosh names. Фикстура исправлена для
всех записей. Итоговые JUnit: ooxml-font-main-final.xml и
ooxml-font-experiment-final.xml в template-benchmark/test-results.
Повторный платный прогон допускается только штатным --retry-case с хэшем
исходного failed result, после успешных регрессий и проверки отсутствия jobs.
