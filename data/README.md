# data/

Данные в git не кладём (хук `check-data-files.sh`). Здесь — только этот README:
откуда данные, команда загрузки, объём, контрольная сумма. Маленькие примеры для
тестов — `tests/fixtures/`. Веса моделей — Git LFS (`models/`).

## catalog/ — каталог направлений (#8)

Команда: `python3 -I scripts/build_catalog.py`. Скрипт сам скачивает файлы в `data/catalog/`,
кладёт ответы Open-Meteo в `data/catalog/openmeteo/` и пишет `backend/src/api/catalog.json`.
Повторный запуск берёт ответы из кэша. Сбор климата на нескольких машинах — флаги `--points`,
`--export`, `--import` (docstring скрипта). Источники и лицензии — `research/data-sources.md`.

| Файл | Байт | sha256 | Загружен |
|---|---|---|---|
| cities500.zip | 13907081 | 54764f97df5aff90af774210ee4be97e5febb23e102549c20dd41928f2c35066 | 2026-10-26 |
| alternateNamesV2.zip | 205167241 | 6caea5c0a248a718d12a1098641217024a15dabcd3a4fccc34e9df10e7a93791 | 2026-10-26 |
| airports.csv | 12752219 | 27c6500d0a742edece15c5aaf960e08c01df0d4356c5c86255897b5d3749ad3b | 2026-10-26 |
| airports.dat | 1127225 | 9387cdb38df5bd664da823f8ccb69fdd9b33a1888f5b7cca09c34a3cd9ff59f9 | 2026-10-26 |

GeoNames обновляется каждый день: новый запуск может дать другие суммы.
