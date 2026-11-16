# Gallica: парсер, скачивание и корпуса газет

Самостоятельный проект для поиска выпусков французской периодики в Gallica/BnF, скачивания найденных газетных страниц и ведения двух корпусов, использованных в исследовании.

Аналитические расчеты для статьи вынесены в отдельный проект `Gallica_article_analysis_project`.

## 1. Состав проекта

```text
Gallica_corpus_parser_project/
├── configs/                    конфигурации поисков
├── data/                       реестры страниц и место для скачанных корпусов
├── scripts/download/           длительные и восстановительные запуски
├── src/gallica_parser/         код парсера
├── tests/                      тесты парсера
├── tools/                      аудит и экспорт реестров корпусов
├── pyproject.toml
└── README.md
```

В проект не включены многогигабайтные изображения, OCR и рабочие SQLite-базы. Они создаются при запуске парсера в `data/` либо в каталогах, заданных в JSON-конфигурации.

## 2. Установка

Требуется Python 3.11 или новее и доступ к `https://gallica.bnf.fr`.

```powershell
Set-Location "<project-directory>/Gallica_corpus_parser_project"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install pytest
```

Парсер использует стандартную библиотеку Python; `pytest` нужен только для тестов.

## 3. Как работает парсер

Используются публичные сервисы BnF/Gallica:

- SRU `searchRetrieve` — обнаружение выпусков по запросам и годам;
- `Issues` — получение выпусков известной газеты;
- `ContentSearch` — поиск в OCR конкретного выпуска;
- `RequestDigitalElement` — получение ALTO XML;
- IIIF — загрузка JPG страницы.

Парсер скачивает не весь выпуск, а страницы, на которых найдено совпадение, прошедшее контекстный фильтр. Состояние записывается в SQLite. При повторном запуске завершенные запросы и страницы пропускаются.

Технический ключ страницы — `issue_ark + page_number`. Если страницу нашло несколько запросов, файл сохраняется один раз, а все совпадения записываются в metadata JSON. Повторные публикации одного текста в разных выпусках не удаляются.

## 4. Конфигурации скачивания

### Отдельная газета

Шаблон: `configs/single_periodical.example.json`.

```powershell
gallica-parser --config configs/single_periodical.example.json --dry-run
gallica-parser --config configs/single_periodical.example.json --max-issues 5 --verbose
gallica-parser --config configs/single_periodical.example.json
```

Основные параметры:

- `periodical_ark` и `periodical_title` — идентификатор и название газеты;
- `date_from`, `date_to` — включительные границы поиска;
- `queries` — запросы к OCR;
- `context_require_any` — допустимые маркеры контекста;
- `output_directory`, `database_path` — корпус и SQLite состояния;
- `request_delay_seconds`, `request_timeout_seconds`, `max_retries` — сетевые параметры;
- `download_images`, `download_alto` — сохраняемые форматы.

### Персонализированный корпус

`configs/alexander_holy_alliance_1812_1825.json` воспроизводит поиск страниц об Александре I и Священном союзе за 1812–1825 годы.

```powershell
gallica-global --config configs/alexander_holy_alliance_1812_1825.json --discover-only
gallica-global --config configs/alexander_holy_alliance_1812_1825.json --process-only --limit 5 --verbose
gallica-global --config configs/alexander_holy_alliance_1812_1825.json
```

В рабочей конфигурации `Journal de la Lozère` исключен из глобального прохода, поскольку был скачан отдельно. Если отдельной подборки нет, удалите `cb327966882` из `exclude_periodical_arks`.

### Событийный корпус

- `configs/events_original_download_1812_1822.json` — точная первоначальная выгрузка, включая позднее исключенные Калишский союз и польскую конституцию;
- `configs/events_final_13_recommended.json` — рекомендуемый поиск по 13 событиям итогового исследования.

```powershell
gallica-global --config configs/events_final_13_recommended.json --discover-only
gallica-global --config configs/events_final_13_recommended.json
```

`search_date_ranges` ограничивает каждый запрос исторически допустимым периодом.

## 5. Длительный запуск и восстановление

```powershell
python scripts/download/run_global_collection.py configs/alexander_holy_alliance_1812_1825.json
python scripts/download/run_event_collection.py configs/events_original_download_1812_1822.json
python scripts/download/run_global_recovery.py configs/alexander_holy_alliance_1812_1825.json
```

Восстановительный режим читает существующую SQLite и повторяет только отсутствующие или ошибочные элементы. Не удаляйте SQLite, если загрузку нужно продолжить.

Блокировка экрана не останавливает Python. Сон, гибернация, выключение компьютера и разрыв сети останавливают процесс; после этого запустите ту же команду. VPN не обязателен. Парсер не обходит CAPTCHA или защитную страницу Gallica.

Для больших выгрузок оставлена задержка 10 секунд. Уменьшение задержки повышает риск ответов `429` и временной блокировки.

## 6. Формат скачанных материалов

```text
data/corpus_name/
└── Название_газеты/
    └── 1821-01-15/
        └── bpt6k.../
            ├── page_2.jpg
            ├── page_2_alto.xml
            ├── page_2_ocr.txt
            ├── page_2_ocr_clean.txt
            └── page_2_metadata.json
```

`ocr_clean.txt` создается из OCR Gallica и восстанавливает явно размеченные в ALTO переносы. Языковая модель для исправления OCR не используется. Цитаты следует сверять с JPG.

## 7. Реестры страниц двух корпусов

- [персонализированный корпус](data/pages_personalized_corpus.csv) — 9 616 уникальных страниц 71 газеты;
- [событийный корпус](data/pages_event_corpus.csv) — 11 320 уникальных страниц 62 газет;
- [сводка](data/corpus_page_manifests_summary.json) — 1 147 общих страниц и 19 789 уникальных страниц в объединении.

В CSV указаны корпус, газета, дата, ARK выпуска, цифровая страница, URL выпуска и прямая ссылка на страницу Gallica. Реестры очищены от журналов и иных публикаций согласно `data/newspaper_type_audit.csv`.

Повторное формирование реестров:

```powershell
python tools/export_corpus_page_manifests.py `
  --event-db "<corpus-data-directory>/state_events_alexander_policy_1812_1822.sqlite3" `
  --personalized-db "<corpus-data-directory>/state_all_newspapers_1812_1825.sqlite3" `
  --extra-personalized "<corpus-data-directory>/corpus_alexandre_sainte_alliance_1812_1825/Journal_de_la_Loz_re"
```

Результаты записываются в `data/`.

Дополнительные средства:

- `tools/audit_gallica_corpus.py` — сверка SQLite и metadata JSON;
- `tools/build_gallica_manifest.py` — создание общего манифеста отдельного корпуса;
- `tools/export_corpus_page_manifests.py` — итоговые реестры двух газетных корпусов.

## 8. Проверка

```powershell
python -m pytest -q
python -m py_compile src/gallica_parser/*.py scripts/download/*.py tools/*.py
```

