# /keyword-report — Google Ads Keyword Performance Report

Генерирует еженедельный отчёт по ключевым словам Google Ads.

---

## Шаг 1 — Первый запуск: выбор инструментов

Прочитай `.env`. Найди строку вида `# SETUP:` (она записывается после первой настройки).

- **Если строка `# SETUP:` есть** → онбординг уже пройден, **сразу переходи к Шагу 2**. Не задавай вопросов про инструменты.
- **Если строки `# SETUP:` нет** → первый запуск. Задай пользователю три вопроса одним сообщением (ниже).

Задай пользователю три вопроса **одним сообщением**:

---

**Нужно настроить три источника данных. Ответь на вопросы:**

**1. CRM — откуда брать лиды и их квалификацию (QL/DQL)?**
- A) Monday.com _(текущая реализация)_
- B) HubSpot
- C) Salesforce
- D) Pipedrive
- E) Другая CRM — укажи название
- F) Без CRM — только данные из аналитики

**2. Аналитика — источник данных по ключевым словам (сессии, лиды, расходы)?**
- A) Rick.AI CSV-выгрузка _(текущая реализация)_
- B) Rick.AI API
- C) Google Analytics 4 (GA4) + Google Ads API
- D) Только GA4 (без Ads API — нет данных по расходам)
- E) Другой инструмент — укажи название

**3. Куда публиковать отчёт?**
- A) Outline _(текущая реализация)_
- B) Notion
- C) Confluence
- D) Google Docs
- E) Только локальный файл (не публиковать)
- F) Другое — укажи

---

После получения ответов:

1. Запиши выбор в `.env` первой строкой:
   ```
   # SETUP: CRM=monday, ANALYTICS=rick_csv, PUBLISH=outline
   ```
   Эта строка — маркер завершённого онбординга. При следующих запусках скилл увидит её и сразу перейдёт к Шагу 2.

2. Спроси разрешение на поиск: _"Найти инструкции по подключению [выбранных инструментов] в интернете? (да/нет)"_

3. Если пользователь сказал **да** — используй WebSearch и найди:
   - Официальную документацию API для каждого выбранного инструмента
   - Как получить API key / токен
   - Какие права/scopes нужны
   - Ссылки на developer portal
   Покажи краткую инструкцию по каждому инструменту с прямыми ссылками.

4. Перейди к Шагу 2 для запроса недостающих credentials.

---

## Шаг 2 — Проверка credentials

Прочитай `.env`. На основе выбранных инструментов проверь наличие нужных переменных:

### CRM

**Monday.com:**
| Переменная | Как получить |
|---|---|
| `MONDAY_API_KEY` | monday.com → аватар → Developers → My Access Tokens |

**HubSpot:**
| Переменная | Как получить |
|---|---|
| `HUBSPOT_API_KEY` | HubSpot → Settings → Integrations → Private Apps |

**Salesforce:**
| Переменная | Как получить |
|---|---|
| `SALESFORCE_INSTANCE_URL` | URL твоего Salesforce org |
| `SALESFORCE_ACCESS_TOKEN` | Setup → Connected Apps → OAuth |

**Pipedrive:**
| Переменная | Как получить |
|---|---|
| `PIPEDRIVE_API_TOKEN` | Pipedrive → Settings → Personal preferences → API |

### Аналитика

**Rick.AI CSV:** credentials не нужны — только путь к файлу.

**Rick.AI API:**
| Переменная | Как получить |
|---|---|
| `RICK_API_KEY` | Rick.AI → Settings → API |
| `RICK_WIDGET_ID` | ID виджета из URL в Rick |

**GA4 + Google Ads API:**
| Переменная | Как получить |
|---|---|
| `GA4_PROPERTY_ID` | GA4 Admin → Property Settings → Property ID |
| `GA4_KEY_FILE` | Google Cloud → IAM → Service Accounts → JSON key |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads → Tools → API Center |
| `GOOGLE_ADS_CUSTOMER_ID` | Google Ads → номер аккаунта (без дефисов) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC account ID (если работаешь через агентство) |

**GA4 только:**
| Переменная | Как получить |
|---|---|
| `GA4_PROPERTY_ID` | GA4 Admin → Property Settings |
| `GA4_KEY_FILE` | Google Cloud → Service Account JSON |

### Куда публиковать

**Outline:**
| Переменная | Как получить |
|---|---|
| `OUTLINE_API_TOKEN` | Outline → Settings → API |
| `OUTLINE_BASE_URL` | URL твоего Outline instance |
| `OUTLINE_DOC_ID` | ID документа истории ключей |
| `OUTLINE_COLLECTION_ID` | ID коллекции для отчётов |
| `OUTLINE_PARENT_DOC_ID` | ID родительского документа |

**Notion:**
| Переменная | Как получить |
|---|---|
| `NOTION_TOKEN` | notion.so/my-integrations → New integration |
| `NOTION_DATABASE_ID` | URL базы данных (32-символьный ID) |

**Confluence:**
| Переменная | Как получить |
|---|---|
| `CONFLUENCE_URL` | URL твоего Confluence |
| `CONFLUENCE_TOKEN` | Atlassian Account → Security → API tokens |
| `CONFLUENCE_SPACE_KEY` | Ключ пространства (например `MKTG`) |

Если обязательная переменная отсутствует — спроси пользователя и запиши в `.env`. Не продолжай без неё.

---

## Шаг 3 — Параметры запуска

Спроси (или прими из аргументов скилла):

1. **Неделя** — ISO-формат, например `2026-W17`. По умолчанию — текущая неделя.
2. **Путь к CSV** — только если аналитика = Rick.AI CSV. По умолчанию — автопоиск `~/Downloads/typhoon.coffee_bq_widget_*.csv`.

Если пользователь передал аргумент (`/keyword-report 2026-W17`) — используй как неделю без вопросов.

---

## Шаг 4 — Запуск

Выполни:

```bash
python3 keyword_report.py --week WEEK [--rick-csv PATH]
```

Покажи прогресс в реальном времени. При ошибке — покажи её полностью и предложи диагностику.

---

## Шаг 5 — Результат

Выведи:
- Ссылку на опубликованный отчёт
- Сводку: ключей, лидов (Rick), QL, DQL, 🟢/🔴/🟡/⚪
- Если есть DQL-ключи — перечисли отдельно

---

## Точки расширения (для разработчика)

| Инструмент | Что нужно написать |
|---|---|
| Rick.AI API | Заменить `parse_rick_keywords()` и `parse_rick_channel_summary()` на API-клиент |
| Google Ads API | Написать `google_ads_fetcher.py`, переключить `has_ad_data = True` в `keyword_report.py` |
| HubSpot / Pipedrive | Написать аналог `monday_leads_report.py` для нужной CRM |
| Notion / Confluence | Написать аналог `outline_client.py` для нужного инструмента |

Логика классификации (`qualify.py`), принятия решений (`keyword_decisions.py`) и рендеринга (`render_report`) — не зависит от источника данных и не требует изменений.
