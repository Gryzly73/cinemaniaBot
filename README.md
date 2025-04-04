# cinemaniaBot

Техническое описание Telegram-бота для создания кинорецензий
1. Общие сведения
1.1. Цель проекта  
Создание автоматизированного бота для:  
- Генерации профессиональных и развлекательных кинорецензий с использованием ИИ  
- Публикации контента в Telegram-канале по расписанию или по запросу администратора  
- Упрощения управления контент-стратегией для администраторов  
- Предоставления пользователям доступа к уникальному или малоизвестному киноконтенту  

1.2. Используемые технологии  
- Backend: Python 3.11+  
- Фреймворки* Aiogram 3.x (Telegram API), APScheduler (планировщик)  
- ИИ OpenAI GPT-4 (генерация текста)  
- Базы данных: JSON (для локального хранения истории), OMDB API (поиск фильмов)  
- Дополнительно: aiohttp, dotenv, lru_cache (кэширование)  

1.3. Среда для разворачивания  
- ОС: Linux/Windows (рекомендуется Ubuntu 22.04)  
- Сервер: VPS/VDS с Python 3.11+, 1 ГБ RAM, 20 ГБ HDD  
- Зависимости: Docker (опционально), Redis (для масштабирования)  
2. Основные функции бота
 2.1. Функциональные требования  
2.1.1. Основные функции (для пользователей):  
- 🎬 Поиск фильмов по жанру/названию, публикация по расписанию или по запросу администратора  
- 📚 Просмотр истории опубликованных рецензий  
- ⏰ Получение уведомлений о новых публикациях  
- ℹ️ Интерактивная справка о возможностях бота  

2.1.2. Административные функции:  
- 🎭 Смена жанра для автоматической генерации  
- 🖋 Выбор стиля рецензий (аналитический, юмористический и т.д.)  
- ⏰ Настройка расписания публикаций  
- 🚀 Ручная публикация кастомных рецензий  
- 📊 Просмотр статистики (количество публикаций, история) 

2.2. Ключевые технические детали  
.2.1. Архитектура:  
```plaintext
[Telegram API] ↔ [Aiogram] ↔ [FSM States]  
                    │  
                    ├── [OpenAI GPT-4] → Генерация контента  
                    ├── [OMDB API] → Поиск фильмов  
                    └── [JSON DB] → Локальное хранилище  
```

2.2.2. Структура базы данных:  
- `movies_history.json`:  
  ```json
  {
    "imdb_id": "tt12345678",
    "title": "Название фильма",
    "year": 2024,
    "plot": "Описание сюжета",
    "review": "Текст рецензии",
    "post_date": "2024-01-01T12:00:00"
  }
  
3. Реализация проекта
3.1. Интервью с заказчиком  
- Определение целевой аудитории  
- Уточнение списка жанров и стилей рецензий  
- Настройка интеграции с Telegram-каналом  

3.2. Техническое задание  
- Подробное описание сценариев использования  
- Согласование формата вывода рецензий  
- Выбор моделей ИИ (GPT-4, Claude и т.д.)  

3.3. Разработка и настройка  
- Написание ядра бота (10-12 рабочих дней)  
- Тестовая интеграция с OpenAI API (3-5 дней)  
- Настройка безопасности (валидация ввода, обработка ошибок, 3-5 дней)  
4. Внедрение и передача
4.1. Тестирование и запуск  
- Проверка всех сценариев использования (7 дней)  
- Нагрузочное тестирование   
- Инструкция для администраторов  

4.2. Поддержка (доп. опция)  
- Мониторинг работы бота 24/7  
- Регулярное обновление стилей рецензий  
- Оптимизация запросов к OpenAI  
5. Сроки, стоимость и условия
5.1. Сроки  
- Базовая версия: 25 рабочих дней  
- Расширенная версия (+аналитика): 45 дней  

5.2. Стоимость  
- Разработка: от ХХХХ руб.(базовый функционал)  
- Интеграция с OpenAI: $100/мес (за токены)  
- Техподдержка: ХХХ руб./мес  

5.3. Условия  
- Исходный код передается после полной оплаты  
- Гарантия на код: 3 месяца  
- Возможность переноса на другой сервер  
Дополнительные возможности — по договоренности с заказчиком:
- Интеграция с IMDb/Rotten Tomatoes  
- Система рекомендаций фильмов  
- Голосовой интерфейс для рецензий  
- Партнерская программа для кинокритиков  

Проект может быть адаптирован под конкретные требования заказчика.

Планы по развитию проекта:  
Ниже представлены стратегические направления для масштабирования и улучшения бота. Каждый пункт можно реализовывать поэтапно, в зависимости от потребностей аудитории и бизнес-целей.
1. Улучшение пользовательского опыта 
- Персонализация  
  - Система рекомендаций на основе истории просмотров  
  - Возможность сохранения "Избранного" (фильмы, рецензии)  
  - Настройка персональных уведомлений по жанрам  

- Интерактивность  
  - Голосовой интерфейс для поиска фильмов (Telegram Voice Messages + Whisper API)  
  - Выгрузка трейлеров  
  - Система рейтингов и отзывов пользователей  

- Социальные функции 
  - Комментирование рецензий  
  - Совместные подборки фильмов (коллаборативные плейлисты)  
  - Интеграция с Kinopoisk для синхронизации данных  
2. Технические улучшения
- Производительность 
  - Переход с JSON на SQLite/PostgreSQL для больших объемов данных  
  - Внедрение Redis для кэширования запросов к OpenAI  
  - Оптимизация промптов для снижения стоимости токенов  

- Безопасность  и  масштабируемость
  - Валидация ввода пользователей (защита от SQL-инъекций)     
  - Резервное копирование истории 
  - Контейнеризация через Docker    
3. Расширение контента
- Новые форматы  
  - Подкасты с краткими обзорами (синтез речи через ElevenLabs)  
  - Видео-рецензии с AI-аватарками (D-ID, Synthesia)  
  - Инфографика: сравнительные анализ рейтингов фильмов  

- Экосистема контента  
Еженедельные дайджесты "Топ-5 фильмов недели"
Исторические справки о кинематографе

- Локализация  
  - Поддержка английского/испанского языков  
  - Автоматический перевод рецензий (DeepL API)  
  - Региональные рекомендации (например, "Топ азиатского кино")  
4. Монетизация и реклама
  - Партнерские программы
  - Продвижение стриминговых платформ (Netflix, Amazon Prime)    
  - Спонсорские рубрики ("Фильм дня от <бренда>")  
  - Native-интеграции (например, ссылки на легальные просмотры)  
5. Аналитика и AI
- Сбор метрик  
  - Heatmap популярности жанров  
  - A/B-тестирование стилей рецензий  
  - Анализ вовлеченности (CTR, время чтения)  

- Улучшение ИИ  
  - Fine-tuning GPT-4 под кинотематику  
  - Мультимодальные промпты (анализ постеров через DALL-E)  
  - Прогнозирование рейтингов на основе отзывов  

- Автоматизация  
  - AI-модерация комментариев  
  - Генерация трейлеров через Sora (OpenAI)  
  - Автопостинг в соцсети через Zapier  
Этапы реализации  
1. Short-term (1-3 месяца):  
   - Внедрение SQLite + Redis  
   - Запуск системы рекомендаций  
   - Партнерство с 1-2 стриминговыми платформами  

2. Mid-term (6 месяцев): 
   - Полная локализация на английский, испанский  
   - Интеграция с YouTube для видео-контента  
   - Запуск Premium-подписки  

3. Long-term (12+ месяцев):  
   - Собственное мобильное приложение  
   - AI-кинофестиваль с голосованием  
   - NFT-коллекция культовых киноперсонажей  

Дополнительно
Файл .env:

TELEGRAM_BOT_TOKEN=
OPENAI_API_KEY=
GOOGLE_CX_ID=
GOOGLE_API_KEY=
CHANNEL_ID=
ADMINS=
OMDB_API_KEY=
GENERAL_REVIEW_PROMPT="Напиши профессиональную рецензию, соблюдая структуру: 1 Название фильма, IMDB ID, режиссер 
2 Фабула, сюжет, анализ сюжета 3 режиссерский находки или провалы 4 Оценка актёрской игры 
5 Впечатления от визуального стиля. 
6 При генерации текста избегай многоточий\! или повторяющихся знаков препинания.\*\*\* Не придумывай фильм \! 
Бери только реально существующие фильмы, с IMDB ID. Избегай спойлеров.
Рецензия должна быть на русском языке, размер 130-150 слов. 
Если нет точно по описанию подбери наиболее близкий к описанию предупредив что фильм не совсем соответствует"
