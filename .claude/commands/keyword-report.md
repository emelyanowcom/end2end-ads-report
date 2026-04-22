# /keyword-report — Google Ads Keyword Performance Report

Генерирует еженедельный отчёт по ключевым словам Google Ads и публикует его в Outline.

## Шаг 1 — Проверка доступов

Прочитай файл `.env` в текущей директории. Проверь наличие следующих переменных:

### Обязательные

| Переменная | Назначение |
|---|---|
| `MONDAY_API_KEY` | Monday CRM — классификация лидов (QL/DQL) |
| `OUTLINE_API_TOKEN` | Outline — публикация отчёта и хранение истории |
| `OUTLINE_BASE_URL` | Базовый URL Outline-воркспейса |
| `OUTLINE_DOC_ID` | ID документа с историей ключей (rolling 3-week) |
| `OUTLINE_COLLECTION_ID` | ID коллекции для публикации отчётов |
| `OUTLINE_PARENT_DOC_ID` | ID родительского документа "Google Ads reports" |

### Опциональные (GA4 — сейчас не даёт keyword-level данные, нужно auto-tagging)

| Переменная | Назначение |
|---|---|
| `GA4_PROPERTY_ID` | Google Analytics 4 Property ID |
| `GA4_KEY_FILE` | Путь к Service Account JSON для GA4 |

### Будущие (Google Ads API — когда появится developer token)

| Переменная | Назначение |
|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads API developer token |
| `GOOGLE_ADS_CUSTOMER_ID` | Google Ads Customer ID (без дефисов) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC account ID (если через MCC) |

Если какой-либо **обязательный** ключ отсутствует — спроси пользователя и запиши в `.env` перед продолжением. Опциональные и будущие — не спрашивай, просто упомяни что без них часть данных будет н/д.

## Шаг 2 — Параметры запуска

Спроси пользователя (или прими из аргументов скилла):

1. **Неделя** — ISO-формат, например `2026-W17`. Если не указана — вычисли текущую неделю по сегодняшней дате.
2. **Rick CSV** — путь к файлу выгрузки из Rick.AI. Если не указан — скрипт сам найдёт последний файл `~/Downloads/typhoon.coffee_bq_widget_*.csv`. Скажи пользователю какой файл будет использован.

Если пользователь запустил скилл с аргументом (например `/keyword-report 2026-W17`) — используй его как неделю без вопросов.

## Шаг 3 — Запуск

Выполни команду:

```bash
python3 keyword_report.py --week WEEK [--rick-csv PATH]
```

Покажи прогресс-лог в реальном времени. При ошибке — покажи её полностью и предложи диагностику.

## Шаг 4 — Результат

После успешного запуска выведи:
- Ссылку на опубликованный отчёт в Outline
- Сводку: сколько ключей, лидов (Rick), QL, DQL, 🟢/🔴/🟡/⚪
- Если есть DQL-ключи — перечисли их отдельно

## Заметки по расширению

**Когда появятся Google Ads API credentials:**
- Добавить `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CUSTOMER_ID` в `.env`
- Написать `google_ads_fetcher.py` (аналог `ga4_fetcher.py`) — импрессии/клики/расходы на уровне ключа
- В `keyword_report.py` добавить вызов нового фетчера и переключить `has_ad_data = True`
- Колонки Показы / CTR / Клики / Расход / CPQL появятся автоматически (логика в `render_report` уже готова)

**Когда Rick CSV заменится на API:**
- Заменить `parse_rick_keywords()` и `parse_rick_channel_summary()` на вызов Rick API
- Остальной код не меняется
