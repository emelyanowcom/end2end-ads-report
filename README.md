# Google Ads Keyword Report

**EN** | [RU](#ru)

Automated weekly report on Google Ads keyword performance. Pulls lead data from your CRM, attribution data from your analytics tool, classifies leads as QL/DQL, and publishes a structured report to your knowledge base.

## What it does

- **Per-keyword decisions**: Amplify 🟢 / Reduce 🟡 / Disable 🔴 / Watch ⚪ — based on QL count and 3-week rolling history
- **Lead qualification**: QL (qualified) vs DQL (disqualified) — SLA-aware, detects lead replies via chat, email, WhatsApp, phone notes
- **Channel summary**: leads by source (Google Ads / Direct / Organic / etc.) from your analytics tool
- **Recommendations tracking**: compares last week's decisions with current results
- **DQL keyword table**: highlights which keywords attract non-converting traffic
- **Publishes to Outline** (Notion / Confluence support is on the roadmap)

## Stack

| Layer | Current implementation | Replaceable with |
|---|---|---|
| CRM | Monday.com | HubSpot, Salesforce, Pipedrive |
| Attribution data | Rick.AI CSV export | Rick.AI API, GA4, Google Ads API |
| Report destination | Outline | Notion, Confluence, Google Docs |
| Decision engine | `keyword_decisions.py` | — (core logic, not a connector) |
| Lead qualification | `qualify.py` | — (core logic, not a connector) |

## Quick start

```bash
# 1. Clone
git clone https://github.com/emelyanowcom/typhoon-ads-report
cd typhoon-ads-report

# 2. Install dependencies
pip install requests google-analytics-data google-auth

# 3. Configure
cp .env.example .env
# Fill in your API keys

# 4. Run
python3 keyword_report.py --week 2026-W17
# or use the Claude Code skill:
# /keyword-report 2026-W17
```

## Files

```
keyword_report.py         — CLI orchestrator
qualify.py                — Lead qualification logic (QL / DQL / SKIP)
keyword_decisions.py      — 3-week rolling decision engine
outline_client.py         — Outline API client (history read/write)
ga4_fetcher.py            — Google Analytics 4 Data API client
monday_leads_report.py    — Monday.com GraphQL client
weekly_leads_framework.py — Weekly leads report framework
report.py                 — Markdown report renderer
.env.example              — Environment variables template
.claude/commands/
  keyword-report.md       — /keyword-report Claude Code skill
```

## Lead qualification logic

A lead is **QL** (qualified) if any of:
- Reached KSF stage (product presentation / agreed to buy)
- Manager actively took lead into work
- Sales missed SLA deadline (auto-QL — can't blame the channel)
- Lead replied: via chat, pasted email (`message:` prefix), WhatsApp inbound, phone/verbal (`said that...`)

A lead is **DQL** if: SLA was met by sales AND lead never responded.

A lead is **SKIP** (excluded from counts) if: marked as duplicate (`to_lost_leads3 = Duplicated`).

## Keyword decision rules

| Decision | Condition |
|---|---|
| 🟢 Amplify | QL ≥ 1 OR KSF signal this week |
| 🔴 Disable | 3 consecutive weeks: clicks > 5, QL = 0, no KSF |
| 🟡 Reduce | QL = 0, no KSF, < 3 weeks of data |
| ⚪ Watch | Traffic < 5 clicks/sessions — not enough data |

Lead count per keyword comes from your analytics tool (source of truth). QL count comes from CRM.

## Environment variables

See [`.env.example`](.env.example) for the full list with descriptions.

## Claude Code skill

The `/keyword-report` skill (`.claude/commands/keyword-report.md`) guides you through:
1. Choosing your CRM, analytics tool, and publish destination
2. Fetching connection instructions from official docs (with your permission)
3. Configuring credentials
4. Running the report

---

<a name="ru"></a>

# Google Ads Keyword Report [RU]

Автоматический еженедельный отчёт по ключевым словам Google Ads. Забирает данные о лидах из CRM, атрибуцию из аналитики, классифицирует лиды как QL/DQL и публикует структурированный отчёт в базу знаний.

## Что делает

- **Решение по каждому ключу**: Усилить 🟢 / Ослабить 🟡 / Отключить 🔴 / Наблюдать ⚪ — на основе QL и 3-недельной истории
- **Квалификация лидов**: QL (квалифицирован) vs DQL (дисквалифицирован) — учитывает SLA, ответы через чат, email, WhatsApp, телефонные заметки менеджеров
- **Сводка по каналам**: лиды по источникам (Google Ads / Direct / Organic / прочие) из аналитики
- **Сверка рекомендаций**: сравнивает решения прошлой недели с текущими результатами
- **Таблица DQL-ключей**: видно какие ключи привлекают нерелевантный трафик
- **Публикация в Outline** (Notion / Confluence — в роадмапе)

## Стек

| Слой | Текущая реализация | Можно заменить на |
|---|---|---|
| CRM | Monday.com | HubSpot, Salesforce, Pipedrive |
| Данные атрибуции | Rick.AI CSV-выгрузка | Rick.AI API, GA4, Google Ads API |
| Публикация отчёта | Outline | Notion, Confluence, Google Docs |
| Движок решений | `keyword_decisions.py` | — (ядро, не коннектор) |
| Квалификация лидов | `qualify.py` | — (ядро, не коннектор) |

## Быстрый старт

```bash
# 1. Клонировать
git clone https://github.com/emelyanowcom/typhoon-ads-report
cd typhoon-ads-report

# 2. Установить зависимости
pip install requests google-analytics-data google-auth

# 3. Настроить
cp .env.example .env
# Заполнить API-ключи

# 4. Запустить
python3 keyword_report.py --week 2026-W17
# или через скилл Claude Code:
# /keyword-report 2026-W17
```

## Структура файлов

```
keyword_report.py         — CLI-оркестратор
qualify.py                — Логика квалификации лидов (QL / DQL / SKIP)
keyword_decisions.py      — 3-недельный rolling движок решений
outline_client.py         — Outline API клиент (история ключей)
ga4_fetcher.py            — Google Analytics 4 Data API клиент
monday_leads_report.py    — Monday.com GraphQL клиент
weekly_leads_framework.py — Фреймворк еженедельных отчётов по лидам
report.py                 — Рендерер Markdown-отчётов
.env.example              — Шаблон переменных окружения
.claude/commands/
  keyword-report.md       — Скилл /keyword-report для Claude Code
```

## Логика квалификации лидов

Лид **QL** (квалифицирован) если выполнено хотя бы одно:
- Достиг KSF-стадии (презентация продукта / согласился купить)
- Менеджер взял лида в работу
- Менеджер нарушил SLA (авто-QL — нельзя винить канал)
- Лид ответил: в чате, вставленный email (`message:` префикс), входящее WA-сообщение, телефонный звонок (`said that...` в заметках)

Лид **DQL** если: SLA соблюдён И лид не ответил ни разу.

Лид **SKIP** (исключается из счётчиков) если: отмечен как дубликат (`to_lost_leads3 = Duplicated`).

## Правила решений по ключам

| Решение | Условие |
|---|---|
| 🟢 Усилить | QL ≥ 1 ИЛИ KSF-сигнал в текущей неделе |
| 🔴 Отключить | 3 недели подряд: кликов > 5, QL = 0, нет KSF |
| 🟡 Ослабить | QL = 0, нет KSF, история < 3 недель |
| ⚪ Наблюдать | Трафик < 5 кликов/сессий — недостаточно данных |

Количество лидов по ключу — из аналитики (источник истины). Количество QL — из CRM.

## Переменные окружения

Полный список с описанием — в [`.env.example`](.env.example).

## Скилл Claude Code

Скилл `/keyword-report` (`.claude/commands/keyword-report.md`) ведёт тебя через:
1. Выбор CRM, аналитики и места публикации
2. Поиск инструкций по подключению в официальной документации (с твоего разрешения)
3. Настройку credentials
4. Запуск отчёта
