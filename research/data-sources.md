# Открытые источники данных: лицензии, свежесть, лимиты

Проверено по документации источников 2026-10-26. Атрибуцию показываем в UI и README:
правила запрещают нарушать права третьих лиц (`task/hackathon-rules.md`, п. 6.5(4–5)). Можно ли класть снапшоты OSM и GeoNames в репозиторий —
вопрос экспертам (`docs/questions-for-organizers.md`).

| Источник | Что берём | Лицензия | Свежесть | Объём, лимиты |
|---|---|---|---|---|
| OurAirports `airports.csv` | аэропорты, `iata_code`, `scheduled_service` | public domain | обновляется каждую ночь | 12,8 МБ; **колонки часового пояса нет** |
| OpenFlights `airports.dat` | часовой пояс аэропорта (tz, Olson) | ODbL, атрибуция OpenFlights | статичный снапшот; DST — «приближение 2009 года» | малый |
| OpenFlights `routes.dat` | есть ли прямой маршрут | ODbL | **с июня 2014**, «только историческая ценность» | 67 663 маршрута; на демо — с оговоркой |
| GeoNames `cities500.zip` | города, координаты, tz, высота, население | CC BY 4.0 | 2026-10-26 | 13,9 МБ |
| GeoNames `alternateNamesV2.zip` | русские и другие варианты названий (TRAP-3) | CC BY 4.0 | 2026-10-26 | 196 МБ; в снапшот — только `ru`/`en` для городов из cities |
| Open-Meteo Historical Weather (ERA5) | климат по месяцам: температура, осадки | CC BY 4.0 | с 1940, задержка ~5 дней | бесплатно < 10 000 вызовов/сутки, < 5 000/час, < 600/мин; вызов с многими днями стоит больше одного (5 лет по 2 переменным ≈ 26); только некоммерческое использование |
| Open-Meteo Marine | `sea_surface_temperature_mean` — «тёплое море» | CC BY 4.0 | **с 2023**: за 2021–2022 ответ null (проверено 2026-10-26) | те же лимиты |
| Overpass API (OSM), `tourism=hotel` | отели, звёзды, координаты, теги `wheelchair`, `pets` | ODbL, «© OpenStreetMap contributors» | живые данные | ~10 000 запросов/сутки, < 1 ГБ/сутки; нет слота 15 с → 429; по ресурсам → 504; без `[timeout:]` — 180 с |
| 101hotels.com `/main/cities/<slug>`, tutu.ru `/poezda/<A>/<B>/` | калибровка: медиана «от N ₽» 3★ за ночь и доля бесплатной отмены 3★+ по городу (агрегат — `backend/src/api/prices_101hotels.json`); цены поездов (JSON-LD) — только сверка; имён и описаний отелей в git нет | факты, не копия базы (правила п. 6.5(4), 6.9) | 2026-10-27, без дат: `/search` 101hotels запрещён robots.txt | 132 города РФ (~20 отелей на страницу), 92 маршрута из Москвы; пауза 5 с, `scripts/collect_prices.py` |
| IANA tzdb | `zoneinfo` (Python) | public domain | в образе | — |

Следствия:
- Часовой пояс аэропорта берём из OpenFlights или из ближайшего города GeoNames: в OurAirports его нет.
- Маршруты OpenFlights устарели. Транспорт — синтетика по расстоянию; `routes.dat` — только признак «есть прямой рейс».
- Климат считаем один раз: воздух и осадки — среднее за 2016–2025, море — за 2023–2025. Снапшот на 445 мест — `backend/src/api/catalog.json` (TRAP-6). 10 лет — около 25 000 вызовов: сбор поделён на 4 машины с разными IP (#59–#61).
- Overpass — снапшот для частых городов, онлайн для прочих, синтетика по координатам при 429/504 (TRAP-2, TRAP-6).

Источники:
- https://ourairports.com/data/, https://ourairports.com/help/data-dictionary.html
- https://openflights.org/data.php
- https://download.geonames.org/export/dump/
- https://open-meteo.com/en/terms, https://open-meteo.com/en/docs/historical-weather-api, https://open-meteo.com/en/docs/marine-weather-api
- https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
