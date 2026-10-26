# Открытые источники данных: лицензии, свежесть, лимиты

Проверено по документации источников 2026-10-26. Атрибуцию показываем в UI и README:
правила запрещают нарушать права третьих лиц (`task/hackathon-rules.md`, п. 6.5(4–5)). Можно ли класть снапшоты OSM и GeoNames в репозиторий —
вопрос экспертам (`docs/questions-for-organizers.md`).

| Источник | Что берём | Лицензия | Свежесть | Объём, лимиты |
|---|---|---|---|---|
| OurAirports `airports.csv` | аэропорты, `iata_code`, `scheduled_service` | public domain | обновляется каждую ночь | 12,8 МБ; **колонки часового пояса нет** |
| OpenFlights `airports.dat` | часовой пояс аэропорта (tz, Olson) | ODbL, атрибуция OpenFlights | статичный снапшот; DST — «приближение 2009 года» | малый |
| OpenFlights `routes.dat` | есть ли прямой маршрут | ODbL | **с июня 2014**, «только историческая ценность» | 67 663 маршрута; на демо — с оговоркой |
| GeoNames `cities15000.zip` / `cities5000.zip` | города, координаты, tz | CC BY 4.0 | 2026-10-26 | 3,2 / 5,5 МБ |
| GeoNames `alternateNamesV2.zip` | русские и другие варианты названий (TRAP-3) | CC BY 4.0 | 2026-10-26 | 196 МБ; в снапшот — только `ru`/`en` для городов из cities |
| Open-Meteo Historical Weather (ERA5) | климат по месяцам: температура, осадки | CC BY 4.0 | с 1940, задержка ~5 дней | бесплатно < 10 000 вызовов/сутки, < 5 000/час, < 600/мин; только некоммерческое использование |
| Open-Meteo Marine (ERA5-Ocean) | `sea_surface_temperature` — «тёплое море» | CC BY 4.0 | с 1940 | те же лимиты |
| Overpass API (OSM), `tourism=hotel` | отели, звёзды, координаты, теги `wheelchair`, `pets` | ODbL, «© OpenStreetMap contributors» | живые данные | ~10 000 запросов/сутки, < 1 ГБ/сутки; нет слота 15 с → 429; по ресурсам → 504; без `[timeout:]` — 180 с |
| IANA tzdb | `zoneinfo` (Python) | public domain | в образе | — |

Следствия:
- Часовой пояс аэропорта берём из OpenFlights или из ближайшего города GeoNames: в OurAirports его нет.
- Маршруты OpenFlights устарели. Транспорт — синтетика по расстоянию; `routes.dat` — только признак «есть прямой рейс».
- Климат и температуру моря считаем один раз за 10 лет на 1–2 тыс. городов и кладём снапшот в репозиторий (TRAP-6).
- Overpass — снапшот для частых городов, онлайн для прочих, синтетика по координатам при 429/504 (TRAP-2, TRAP-6).

Источники:
- https://ourairports.com/data/, https://ourairports.com/help/data-dictionary.html
- https://openflights.org/data.php
- https://download.geonames.org/export/dump/
- https://open-meteo.com/en/terms, https://open-meteo.com/en/docs/historical-weather-api, https://open-meteo.com/en/docs/marine-weather-api
- https://dev.overpass-api.de/overpass-doc/en/preface/commons.html
