#!/usr/bin/env python3
"""Build backend/src/api/catalog.json: the destination catalogue snapshot (#8). Stdlib only.

    python3 -I scripts/build_catalog.py [--data data/catalog] [--out backend/src/api/catalog.json]

Steps (raw files go to --data, outside git; the run can stop and resume, responses are cached):
1. GeoNames cities500 + alternateNamesV2 (CC BY 4.0): coordinates, IANA zone, elevation,
   population, ru/en names. Each curated name below is resolved against GeoNames; a name that
   GeoNames cities500 does not have goes to the Open-Meteo Geocoding API (GeoNames ids).
   No coordinate is typed by hand: a name that resolves nowhere is skipped and printed.
2. OurAirports airports.csv (public domain) + OpenFlights airports.dat (ODbL, zone by IATA):
   airports with scheduled service and an IATA code. The place's airport is the nearest by road
   among the candidates (candidates()); road km are the "roads" snapshot of the previous
   catalog.json (OSRM, TRAP-6: no network). A place without a road to every candidate keeps the
   nearest by straight line. --fetch-roads asks the OSRM demo server (one table request a
   place, 1 a second) for the mountain places with a choice of airports (#223: Домбай is MRV,
   not AER behind the ridge). --airports-only re-picks the airports of the existing --out.
3. Open-Meteo Historical Weather (ERA5, CC BY 4.0): daily max air temperature and
   precipitation 2016–2025 (two 5-year requests) -> monthly means. Open-Meteo Marine (CC BY 4.0): sea surface
   temperature 2023–2025 (the Marine archive has no SST before 2023) for places with a sea cell
   within SEA_KM.
   Free tier: < 600 calls/min, < 5000/hour, < 10000/day; a long request counts as many
   calls (weight = max(1, variables/10 * days/14), open-meteo.com/en/pricing). Ten years for
   450 places is ~25000 calls, so the work is shared between machines (on a 429 the script
   waits and goes on). --cached-only builds from cached responses and skips the places
   without them. Sharing: here --points-out points.json writes the coordinates; a teammate
   on another IP runs --points share.json --export climate.json (no GeoNames download);
   here --only FROM-TO fetches this machine's share, then --cached-only --import climate*.json.
"""

import argparse
import csv
import hashlib
import io
import json
import math
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URLS = {
    "cities500.zip": "https://download.geonames.org/export/dump/cities500.zip",
    "alternateNamesV2.zip": "https://download.geonames.org/export/dump/alternateNamesV2.zip",
    "airports.csv": "https://davidmegginson.github.io/ourairports-data/airports.csv",
    "airports.dat": "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat",
}
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OSRM = "https://router.project-osrm.org/table/v1/driving/"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
START, END = "2016-01-01", "2025-12-31"
AIR_SPANS = (("air16", START, "2020-12-31"), ("air", "2021-01-01", END))  # cache prefix, span
SEA_START = "2023-01-01"  # Marine SST is null before 2023 (checked 2026-10-26)
YEARS = 10
SEA_KM = 30  # a sea cell of the Marine model this close to the centre: the place is on the sea
AIRPORT_KM = 200  # a farther airport is not the place's own, unless the place has no rail
# A road candidate is up to ROAD_SPAN x the straight km of the nearest + ROAD_PLUS_KM: farther
# airports do not win by road unless a ridge stands in the way (Домбай: AER 136, MRV 156 km)
ROAD_SPAN, ROAD_PLUS_KM = 1.5, 30
AIRPORT_SOURCE = (
    "Аэропорт — с регулярными рейсами и кодом IATA "
    f"из OurAirports (public domain), своей страны, если такой есть ближе {AIRPORT_KM} км. "
    f"Кандидаты — аэропорты не дальше {ROAD_SPAN:g} × расстояние до ближайшего по прямой "
    f"+ {ROAD_PLUS_KM} км. Выбран ближайший по дороге (roads — км по дороге, OSRM demo "
    "router.project-osrm.org, данные OpenStreetMap, ODbL; null — дороги нет), тогда у "
    "аэропорта road = true и km — по дороге. Если в roads нет дороги до каждого кандидата, "
    "выбран ближайший по прямой и km — по прямой. Пояс аэропорта — OpenFlights "
    "airports.dat (ODbL), для новых аэропортов — пояс ближайшего города GeoNames; "
    f"airport_km = null, если аэропорт дальше {AIRPORT_KM} км по прямой и есть вокзал "
    "(station_km); без вокзала — расстояние до аэропорта с рейсами. "
)
MOUNTAIN_M = 1000  # GeoNames elevation (DEM) from which a place counts as mountains
DEFAULT_LEVEL = 5500  # api/offers.py DEFAULT_LEVEL
UA = "nedovost-org catalog build (hackathon, github.com/ostrovok-hackathon-2026/nedovost-org)"

# fmt: off
# Curated selection: "<country ru> <ISO2>" header, then "ru|en|tags". Tags: hub (origin city in
# Russia), main (the place a country name resolves to), city, mountains, ski, spa, nature.
# "sea" is never typed: it comes from the Marine data. Optional "|a1=<code>": GeoNames admin1.
SELECTION = """
Россия RU
Москва|Moscow|hub,city
Санкт-Петербург|Saint Petersburg|hub,city
Новосибирск|Novosibirsk|hub,city
Екатеринбург|Yekaterinburg|hub,city
Казань|Kazan|hub,city
Нижний Новгород|Nizhniy Novgorod|hub,city
Челябинск|Chelyabinsk|hub,city
Самара|Samara|hub,city
Омск|Omsk|hub,city
Ростов-на-Дону|Rostov-na-Donu|hub,city
Уфа|Ufa|hub,city
Красноярск|Krasnoyarsk|hub,city
Воронеж|Voronezh|hub,city
Пермь|Perm|hub,city
Волгоград|Volgograd|hub,city
Краснодар|Krasnodar|hub,city
Саратов|Saratov|hub,city
Тюмень|Tyumen|hub,city
Ижевск|Izhevsk|hub,city
Барнаул|Barnaul|hub,city
Ульяновск|Ulyanovsk|hub,city
Иркутск|Irkutsk|hub,city
Хабаровск|Khabarovsk|hub,city
Ярославль|Yaroslavl|hub,city
Владивосток|Vladivostok|hub,city
Махачкала|Makhachkala|hub,city
Томск|Tomsk|hub,city
Оренбург|Orenburg|hub,city
Кемерово|Kemerovo|hub,city
Новокузнецк|Novokuznetsk|hub,city
Астрахань|Astrakhan|hub,city
Набережные Челны|Naberezhnyye Chelny|hub,city
Пенза|Penza|hub,city
Киров|Kirov|hub,city|a1=33
Липецк|Lipetsk|hub,city
Чебоксары|Cheboksary|hub,city
Калининград|Kaliningrad|hub,city
Курск|Kursk|hub,city
Ставрополь|Stavropol|hub,city|a1=70
Улан-Удэ|Ulan-Ude|hub,city
Сочи|Sochi|hub
Белгород|Belgorod|hub,city
Архангельск|Arkhangelsk|hub,city
Мурманск|Murmansk|hub,city
Сургут|Surgut|hub,city
Минеральные Воды|Mineralnye Vody|hub,spa
Якутск|Yakutsk|hub,city
Петрозаводск|Petrozavodsk|hub,city,nature
Магнитогорск|Magnitogorsk|hub,city
Чита|Chita|hub,city
Южно-Сахалинск|Yuzhno-Sakhalinsk|hub,city,nature
Петропавловск-Камчатский|Petropavlovsk-Kamchatsky|hub,nature
Анапа|Anapa|
Геленджик|Gelendzhik|
Новороссийск|Novorossiysk|city
Туапсе|Tuapse|
Лазаревское|Lazarevskoye|
Джубга|Dzhubga|
Кабардинка|Kabardinka|
Витязево|Vityazevo|
Ейск|Yeysk|
Таганрог|Taganrog|city
Дербент|Derbent|city
Каспийск|Kaspiysk|
Светлогорск|Svetlogorsk||a1=23
Зеленоградск|Zelenogradsk|
Балтийск|Baltiysk|
Находка|Nakhodka|
Териберка|Teriberka|nature
Выборг|Vyborg|city
Сестрорецк|Sestroretsk|
Зеленогорск|Zelenogorsk||a1=66
Магадан|Magadan|city
Красная Поляна|Krasnaya Polyana|mountains,ski|a1=38
Шерегеш|Sheregesh|mountains,ski
Таштагол|Tashtagol|mountains,ski
Домбай|Dombay|mountains,ski
Архыз|Arkhyz|mountains,ski
Теберда|Teberda|mountains
Терскол|Terskol|mountains,ski
Эльбрус|Elbrus|mountains,ski
Кировск|Kirovsk|mountains,ski|a1=49
Абзаково|Abzakovo|mountains,ski
Губаха|Gubakha|mountains,ski
Белокуриха|Belokurikha|mountains,spa
Горно-Алтайск|Gorno-Altaysk|mountains,nature
Чемал|Chemal|mountains,nature
Манжерок|Manzherok|mountains,ski
Листвянка|Listvyanka|nature|a1=20
Байкальск|Baykalsk|nature,ski
Слюдянка|Slyudyanka|nature
Хужир|Khuzhir|nature
Северобайкальск|Severobaykalsk|nature
Кисловодск|Kislovodsk|spa
Пятигорск|Pyatigorsk|spa
Ессентуки|Yessentuki|spa
Железноводск|Zheleznovodsk|spa
Нальчик|Nalchik|city
Владикавказ|Vladikavkaz|city,mountains
Грозный|Groznyy|city
Майкоп|Maykop|city
Горячий Ключ|Goryachiy Klyuch|spa
Сортавала|Sortavala|nature
Кондопога|Kondopoga|nature
Великий Новгород|Velikiy Novgorod|city
Псков|Pskov|city
Печоры|Pechory|city
Изборск|Izborsk|city
Старая Русса|Staraya Russa|spa
Вологда|Vologda|city
Кириллов|Kirillov|city
Великий Устюг|Velikiy Ustyug|city
Сыктывкар|Syktyvkar|city
Кострома|Kostroma|city
Иваново|Ivanovo|city
Плёс|Plyos|city
Владимир|Vladimir|city
Суздаль|Suzdal|city
Сергиев Посад|Sergiyev Posad|city
Переславль-Залесский|Pereslavl-Zalesskiy|city
Углич|Uglich|city
Мышкин|Myshkin|city
Тверь|Tver|city
Торжок|Torzhok|city
Калязин|Kalyazin|city
Тула|Tula|city
Рязань|Ryazan|city
Калуга|Kaluga|city
Коломна|Kolomna|city
Зарайск|Zaraysk|city
Звенигород|Zvenigorod|city
Дмитров|Dmitrov|city
Таруса|Tarusa|city
Смоленск|Smolensk|city
Брянск|Bryansk|city
Орёл|Orel|city
Тамбов|Tambov|city
Саранск|Saransk|city
Йошкар-Ола|Yoshkar-Ola|city
Тольятти|Tolyatti|city
Елабуга|Yelabuga|city
Свияжск|Sviyazhsk|city
Болгар|Bolgar|city
Элиста|Elista|city
Тобольск|Tobolsk|city
Ханты-Мансийск|Khanty-Mansiysk|city
Салехард|Salekhard|city
Норильск|Norilsk|city
Благовещенск|Blagoveshchensk|city|a1=05
Кызыл|Kyzyl|city
Абакан|Abakan|city
Курган|Kurgan|city
Златоуст|Zlatoust|city
Миасс|Miass|city
Кунгур|Kungur|city
Кронштадт|Kronshtadt|city
Петергоф|Petergof|city
Гатчина|Gatchina|city
Турция TR
Анталия|Antalya|main
Стамбул|Istanbul|city
Алания|Alanya|
Кемер|Kemer||a1=07
Белек|Belek|
Сиде|Side||a1=07
Манавгат|Manavgat|
Бодрум|Bodrum|
Мармарис|Marmaris|
Фетхие|Fethiye|
Кушадасы|Kusadasi|
Измир|Izmir|city
Даламан|Dalaman|
Каш|Kas|
Чешме|Cesme|
Дидим|Didim|
Анкара|Ankara|city
Гёреме|Goreme|nature
Ургюп|Urgup|nature
Трабзон|Trabzon|city
Денизли|Denizli|spa
Египет EG
Хургада|Hurghada|main
Шарм-эль-Шейх|Sharm el-Sheikh|
Марса-Алам|Marsa Alam|
Дахаб|Dahab|
Эль-Гуна|El Gouna|
Сафага|Safaga|
Каир|Cairo|city
Луксор|Luxor|city
Асуан|Aswan|city
Александрия|Alexandria|city
ОАЭ AE
Дубай|Dubai|main,city
Абу-Даби|Abu Dhabi|city
Шарджа|Sharjah|city
Рас-эль-Хайма|Ras al-Khaimah|
Фуджейра|Fujairah|
Аджман|Ajman|
Таиланд TH
Пхукет|Phuket|main
Бангкок|Bangkok|city
Паттайя|Pattaya|
Краби|Krabi|
Самуи|Ko Samui|
Хуахин|Hua Hin|
Чиангмай|Chiang Mai|city
Чианграй|Chiang Rai|city
Вьетнам VN
Нячанг|Nha Trang|main
Фукуок|Duong Dong|
Дананг|Da Nang|city
Хойан|Hoi An|city
Ханой|Hanoi|city
Хошимин|Ho Chi Minh City|city
Фантьет|Phan Thiet|
Камрань|Cam Ranh|
Халонг|Ha Long|nature
Далат|Da Lat|mountains
Китай CN
Пекин|Beijing|main,city
Шанхай|Shanghai|city
Санья|Sanya|
Гуанчжоу|Guangzhou|city
Шэньчжэнь|Shenzhen|city
Сиань|Xi'an|city
Чэнду|Chengdu|city
Харбин|Harbin|city
Далянь|Dalian|city
Циндао|Qingdao|city
Ханчжоу|Hangzhou|city
Гуйлинь|Guilin|nature
Куньмин|Kunming|city
Урумчи|Urumqi|city
Маньчжурия|Manzhouli|city
Суйфэньхэ|Suifenhe|city
Хэйхэ|Heihe|city
Гонконг HK
Гонконг|Hong Kong|main,city
Макао MO
Макао|Macau|main,city
Грузия GE
Тбилиси|Tbilisi|main,city
Батуми|Batumi|
Кутаиси|Kutaisi|city
Гудаури|Gudauri|mountains,ski
Бакуриани|Bakuriani|mountains,ski
Боржоми|Borjomi|spa
Сигнахи|Sighnaghi|city
Телави|Telavi|city
Мцхета|Mtskheta|city
Кобулети|Kobuleti|
Местиа|Mestia|mountains,ski
Армения AM
Ереван|Yerevan|main,city
Гюмри|Gyumri|city
Дилижан|Dilijan|nature
Цахкадзор|Tsaghkadzor|mountains,ski
Севан|Sevan|nature
Джермук|Jermuk|spa
Азербайджан AZ
Баку|Baku|main,city
Габала|Qabala|mountains
Шеки|Shaki|city
Гянджа|Ganja|city
Губа|Quba|mountains
Узбекистан UZ
Ташкент|Tashkent|main,city
Самарканд|Samarkand|city
Бухара|Bukhara|city
Хива|Khiva|city
Фергана|Fergana|city
Казахстан KZ
Алматы|Almaty|city
Астана|Astana|main,city
Шымкент|Shymkent|city
Туркестан|Turkestan|city
Актау|Aktau|
Щучинск|Shchuchinsk|nature
Караганда|Karaganda|city
Киргизия KG
Бишкек|Bishkek|main,city
Чолпон-Ата|Cholpon-Ata|nature
Каракол|Karakol|mountains,ski
Ош|Osh|city
Таджикистан TJ
Душанбе|Dushanbe|main,city
Худжанд|Khujand|city
Беларусь BY
Минск|Minsk|main,city
Брест|Brest|city
Гродно|Grodno|city
Витебск|Vitebsk|city
Гомель|Gomel|city
Могилёв|Mogilev|city
Несвиж|Nesvizh|city
Молдавия MD
Кишинёв|Chisinau|main,city
Монголия MN
Улан-Батор|Ulaanbaatar|main,city
Сербия RS
Белград|Belgrade|main,city
Нови-Сад|Novi Sad|city
Ниш|Nis|city
Черногория ME
Будва|Budva|main
Котор|Kotor|city
Херцег-Нови|Herceg Novi|
Бар|Bar|
Подгорица|Podgorica|city
Тиват|Tivat|
Ульцинь|Ulcinj|
Италия IT
Рим|Rome|main,city
Милан|Milan|city
Венеция|Venice|city
Флоренция|Florence|city
Неаполь|Naples|city
Римини|Rimini|
Палермо|Palermo|city
Катания|Catania|city
Болонья|Bologna|city
Верона|Verona|city
Франция FR
Париж|Paris|main,city
Ницца|Nice|
Марсель|Marseille|city
Лион|Lyon|city
Канны|Cannes|
Страсбург|Strasbourg|city
Испания ES
Барселона|Barcelona|main,city
Мадрид|Madrid|city
Валенсия|Valencia|city
Аликанте|Alicante|
Санта-Крус-де-Тенерифе|Santa Cruz de Tenerife|
Пальма|Palma|
Севилья|Sevilla|city
Бенидорм|Benidorm|
Салоу|Salou|
Чехия CZ
Прага|Prague|main,city
Карловы Вары|Karlovy Vary|spa
Брно|Brno|city
Австрия AT
Вена|Vienna|main,city
Зальцбург|Salzburg|city
Инсбрук|Innsbruck|mountains,ski
Венгрия HU
Будапешт|Budapest|main,city
Хевиз|Heviz|spa
Германия DE
Берлин|Berlin|main,city
Мюнхен|Munich|city
Франкфурт-на-Майне|Frankfurt am Main|city
Дрезден|Dresden|city
Гамбург|Hamburg|city
Кёльн|Koeln|city
Греция GR
Афины|Athens|main,city
Салоники|Thessaloniki|city
Ираклион|Heraklion|
Ханья|Chania|
Родос|Rhodes|
Керкира|Kerkyra|
Кипр CY
Ларнака|Larnaca|main
Лимасол|Limassol|
Пафос|Paphos|
Айя-Напа|Ayia Napa|
Португалия PT
Лиссабон|Lisbon|main,city
Порту|Porto|city
Фуншал|Funchal|
Нидерланды NL
Амстердам|Amsterdam|main,city
Бельгия BE
Брюссель|Brussels|main,city
Дания DK
Копенгаген|Copenhagen|main,city
Швеция SE
Стокгольм|Stockholm|main,city
Финляндия FI
Хельсинки|Helsinki|main,city
Норвегия NO
Осло|Oslo|main,city
Исландия IS
Рейкьявик|Reykjavik|main,city
Польша PL
Варшава|Warsaw|main,city
Краков|Krakow|city
Гданьск|Gdansk|city
Эстония EE
Таллин|Tallinn|main,city
Латвия LV
Рига|Riga|main,city
Юрмала|Jurmala|
Литва LT
Вильнюс|Vilnius|main,city
Словакия SK
Братислава|Bratislava|main,city
Словения SI
Любляна|Ljubljana|main,city
Хорватия HR
Загреб|Zagreb|city
Дубровник|Dubrovnik|main
Сплит|Split|
Пула|Pula|
Босния и Герцеговина BA
Сараево|Sarajevo|main,city
Мостар|Mostar|city
Албания AL
Тирана|Tirana|city
Дуррес|Durres|main
Саранда|Sarande|
Северная Македония MK
Скопье|Skopje|city
Охрид|Ohrid|main,nature
Болгария BG
София|Sofia|city
Варна|Varna|main
Бургас|Burgas|
Несебр|Nesebar|
Банско|Bansko|mountains,ski
Румыния RO
Бухарест|Bucharest|main,city
Констанца|Constanta|
Мальта MT
Валлетта|Valletta|main,city
Великобритания GB
Лондон|London|main,city
Эдинбург|Edinburgh|city
Ирландия IE
Дублин|Dublin|main,city
Швейцария CH
Женева|Geneva|city
Цюрих|Zurich|main,city
Церматт|Zermatt|mountains,ski
Израиль IL
Тель-Авив|Tel Aviv|main,city
Иерусалим|Jerusalem|city
Эйлат|Eilat|
Иордания JO
Амман|Amman|city
Акаба|Aqaba|main
Катар QA
Доха|Doha|main,city
Оман OM
Маскат|Muscat|main,city
Салала|Salalah|
Бахрейн BH
Манама|Manama|main,city
Саудовская Аравия SA
Джидда|Jeddah|main,city
Мальдивы MV
Мале|Male|main
Шри-Ланка LK
Коломбо|Colombo|city
Хиккадува|Hikkaduwa|main
Бентота|Bentota|
Индия IN
Панаджи|Panaji|main
Дели|Delhi|city
Агра|Agra|city
Джайпур|Jaipur|city
Мумбаи|Mumbai|city
Непал NP
Катманду|Kathmandu|main,city,mountains
Индонезия ID
Денпасар|Denpasar|main
Убуд|Ubud|nature
Малайзия MY
Куала-Лумпур|Kuala Lumpur|main,city
Лангкави|Kuah|
Джорджтаун|George Town|city
Сингапур SG
Сингапур|Singapore|main,city
Филиппины PH
Манила|Manila|city
Себу|Cebu City|main
Южная Корея KR
Сеул|Seoul|main,city
Пусан|Busan|city
Чеджу|Jeju City|
Япония JP
Токио|Tokyo|main,city
Осака|Osaka|city
Киото|Kyoto|city
Саппоро|Sapporo|city,ski
Тайвань TW
Тайбэй|Taipei|main,city
Марокко MA
Марракеш|Marrakesh|main,city
Касабланка|Casablanca|city
Агадир|Agadir|
Тунис TN
Тунис|Tunis|city
Хаммамет|Hammamet|
Сусс|Sousse|
Монастир|Monastir|main
Хумт-Сук|Houmt Souk|
Танзания TZ
Занзибар|Zanzibar|main
Кения KE
Найроби|Nairobi|city
Момбаса|Mombasa|main
ЮАР ZA
Кейптаун|Cape Town|main,city
Маврикий MU
Порт-Луи|Port Louis|main
Сейшелы SC
Виктория|Victoria|main
Куба CU
Гавана|Havana|city
Варадеро|Varadero|main
Мексика MX
Канкун|Cancun|main
Плая-дель-Кармен|Playa del Carmen|
Мехико|Mexico City|city
Доминикана DO
Санто-Доминго|Santo Domingo|city
Пунта-Кана|Punta Cana|main
Аргентина AR
Буэнос-Айрес|Buenos Aires|main,city
Бразилия BR
Рио-де-Жанейро|Rio de Janeiro|main,city
США US
Нью-Йорк|New York City|main,city
Майами|Miami|
Лос-Анджелес|Los Angeles|city
"""
# fmt: on

# Spelling and colloquial names the GeoNames ru/en lists do not have (team list, TRAP-3).
EXTRA_ALIASES = {
    "Москва": ["мск"],
    "Санкт-Петербург": ["питер", "спб", "петербург"],
    "Екатеринбург": ["екб"],
    "Нижний Новгород": ["нижний"],
    "Сочи": ["адлер"],
    "Красная Поляна": ["роза хутор", "поляна"],
    "Кисловодск": ["кавминводы"],
    "Горно-Алтайск": ["алтай", "горный алтай"],
    "Шерегеш": ["горная шория"],
    "Иркутск": ["байкал"],
    "Петропавловск-Камчатский": ["камчатка"],
    "Южно-Сахалинск": ["сахалин"],
    "Петрозаводск": ["карелия"],
    "Великий Новгород": ["новгород"],
    "Терскол": ["приэльбрусье"],
    "Махачкала": ["дагестан"],
    "Ростов-на-Дону": ["ростов"],
    "Гёреме": ["каппадокия", "гереме"],
    "Санья": ["хайнань"],
    "Денпасар": ["бали"],
    "Панаджи": ["гоа"],
    "Шарм-эль-Шейх": ["шарм"],
    "Алматы": ["алма-ата"],
    "Анталия": ["анталья"],
    "Алания": ["аланья"],
    "Фукуок": ["фукуок", "phu quoc"],
    "Самуи": ["koh samui"],
    "Хумт-Сук": ["джерба", "djerba"],
    "Лангкави": ["langkawi"],
    "Джорджтаун": ["пенанг", "penang"],
    "Санта-Крус-де-Тенерифе": ["тенерифе", "tenerife"],
    "Пальма": ["мальорка", "майорка", "mallorca"],
    "Ираклион": ["крит", "crete"],
    "Керкира": ["корфу", "corfu"],
    "Фуншал": ["мадейра", "madeira"],
    "Хиккадува": [],
    "Щучинск": ["боровое", "бурабай"],
    "Хужир": ["ольхон"],
    "Чолпон-Ата": ["иссык-куль"],
    "Денизли": ["памуккале"],
    "Абзаково": ["банное"],
}
# Country names besides the header: «в Эмираты», «в Тай». Matched like place names.
COUNTRY_ALIASES = {
    "ОАЭ": ["эмираты", "объединенные арабские эмираты", "uae"],
    "Таиланд": ["тайланд", "тай"],
    "Беларусь": ["белоруссия", "белорусь"],
    "Киргизия": ["кыргызстан"],
    "Молдавия": ["молдова"],
    "Южная Корея": ["корея"],
    "Великобритания": ["англия", "британия"],
    "США": ["америка", "штаты"],
    "Доминикана": ["доминиканская республика"],
    "Нидерланды": ["голландия"],
    "ЮАР": ["южная африка"],
    "Чехия": ["чехию"],
    "Египет": ["египт"],  # a fleeting vowel: «в Египте», «из Египта»
}
# Russian places without a passenger railway to the rest of the network (team list).
NO_RAIL = {"Петропавловск-Камчатский", "Южно-Сахалинск", "Магадан", "Якутск", "Норильск",
           "Горно-Алтайск", "Чемал", "Манжерок", "Белокуриха", "Шерегеш", "Хужир", "Листвянка",
           "Домбай", "Архыз", "Терскол", "Эльбрус", "Териберка", "Калининград", "Светлогорск",
           "Зеленоградск", "Балтийск", "Геленджик", "Кабардинка", "Джубга", "Витязево",
           "Ханты-Мансийск", "Кызыл", "Плёс", "Мышкин", "Изборск", "Кириллов", "Таруса",
           "Свияжск", "Болгар", "Абзаково"}  # fmt: skip
RAIL_COUNTRIES = {"Россия": 3, "Беларусь": 2}  # station km for a new place, team estimate
# Room-night level for countries with no place in places.json (#9), team estimate.
LEVEL_BY_COUNTRY = {
    "Гонконг": 9000,
    "Макао": 8000,
    "Таджикистан": 4000,
    "Молдавия": 4500,
    "Монголия": 4500,
    "Португалия": 8000,
    "Нидерланды": 11000,
    "Бельгия": 9000,
    "Дания": 12000,
    "Швеция": 11000,
    "Финляндия": 9500,
    "Норвегия": 12000,
    "Исландия": 13000,
    "Польша": 6000,
    "Эстония": 6500,
    "Латвия": 6000,
    "Литва": 6000,
    "Словакия": 6000,
    "Словения": 7500,
    "Хорватия": 8000,
    "Босния и Герцеговина": 5000,
    "Албания": 5000,
    "Северная Македония": 4500,
    "Болгария": 5000,
    "Румыния": 5500,
    "Мальта": 8500,
    "Великобритания": 13000,
    "Ирландия": 12000,
    "Швейцария": 15000,
    "Израиль": 11000,
    "Иордания": 7000,
    "Катар": 10000,
    "Оман": 9000,
    "Бахрейн": 9000,
    "Саудовская Аравия": 9000,
    "Шри-Ланка": 4500,
    "Непал": 3500,
    "Малайзия": 5000,
    "Сингапур": 12000,
    "Филиппины": 5000,
    "Тайвань": 7500,
    "Марокко": 5500,
    "Тунис": 4500,
    "Танзания": 7000,
    "Кения": 7000,
    "ЮАР": 7000,
    "Маврикий": 11000,
    "Сейшелы": 15000,
    "Куба": 6000,
    "Мексика": 8000,
    "Доминикана": 8000,
    "Аргентина": 7000,
    "Бразилия": 7500,
    "США": 14000,
}

# slug, level and station_km of the places of the old places.json (#9, team estimates): offer
# ids keep their slugs.
LEGACY = {
    "Москва": ("mow", 7500, 3), "Санкт-Петербург": ("led", 5000, 2), "Казань": ("kzn", 4500, 2),
    "Нижний Новгород": ("goj", 4500, 3), "Екатеринбург": ("svx", 5000, 2),
    "Новосибирск": ("ovb", 4500, 2), "Сочи": ("aer", 5000, 1), "Красная Поляна": ("krp", 7000, 5),
    "Калининград": ("kgd", 4000, None), "Светлогорск": ("svg", 5000, None),
    "Кисловодск": ("kvk", 4500, 1), "Пятигорск": ("pyt", 4000, 1), "Ессентуки": ("ess", 4000, 1),
    "Минеральные Воды": ("mrv", 3500, 1), "Анапа": ("aaq", 4500, 4),
    "Геленджик": ("gdz", 5000, None), "Краснодар": ("krr", 4000, 2),
    "Ростов-на-Дону": ("rov", 4000, 2), "Волгоград": ("vog", 3800, 1), "Самара": ("kuf", 4000, 2),
    "Уфа": ("ufa", 4000, 3), "Пермь": ("pee", 4000, 3), "Челябинск": ("cek", 3800, 3),
    "Тюмень": ("tjm", 4000, 2), "Омск": ("oms", 3500, 4), "Томск": ("tof", 3800, 3),
    "Барнаул": ("bax", 4000, 2), "Горно-Алтайск": ("rgk", 5000, None),
    "Шерегеш": ("shg", 6000, None), "Красноярск": ("kja", 4500, 2), "Иркутск": ("ikt", 4500, 2),
    "Листвянка": ("lst", 5500, None), "Улан-Удэ": ("uud", 4000, 2),
    "Владивосток": ("vvo", 6000, 1), "Хабаровск": ("khv", 5000, 3),
    "Петропавловск-Камчатский": ("pkc", 7000, None), "Южно-Сахалинск": ("uus", 6000, None),
    "Мурманск": ("mmk", 5000, 1), "Архангельск": ("arh", 4000, 3),
    "Петрозаводск": ("pes", 4000, 1), "Великий Новгород": ("vnv", 3800, 1),
    "Псков": ("pkv", 3500, 2), "Ярославль": ("iar", 4500, 3), "Кострома": ("kmw", 3800, 3),
    "Владимир": ("vld", 4000, 1), "Суздаль": ("szd", 5000, 30), "Тверь": ("kld", 3800, 2),
    "Тула": ("tyl", 3800, 2), "Рязань": ("rzn", 3500, 2), "Калуга": ("klf", 3800, 2),
    "Воронеж": ("voz", 4000, 2), "Астрахань": ("asf", 4000, 2), "Махачкала": ("mcx", 4000, 1),
    "Дербент": ("drb", 3500, 1), "Грозный": ("grv", 4500, 2), "Владикавказ": ("ogz", 4000, 2),
    "Нальчик": ("nal", 3800, 2), "Домбай": ("dmb", 6000, None), "Стамбул": ("ist", 7000, None),
    "Анталия": ("ayt", 6500, None), "Алания": ("aly", 5500, None), "Бодрум": ("bjv", 7000, None),
    "Гёреме": ("nav", 6000, None), "Тбилиси": ("tbs", 4500, None), "Батуми": ("bus", 5000, None),
    "Ереван": ("evn", 4500, None), "Баку": ("gyd", 6000, None), "Минск": ("msq", 4500, 1),
    "Алматы": ("ala", 5500, None), "Астана": ("nqz", 5500, None), "Ташкент": ("tas", 5000, None),
    "Самарканд": ("skd", 4500, None), "Бишкек": ("frs", 4000, None), "Дубай": ("dxb", 9000, None),
    "Абу-Даби": ("auh", 9000, None), "Хургада": ("hrg", 6000, None),
    "Шарм-эль-Шейх": ("ssh", 6000, None), "Каир": ("cai", 5000, None),
    "Пхукет": ("hkt", 5000, None), "Бангкок": ("bkk", 4500, None), "Паттайя": ("pyx", 4000, None),
    "Денпасар": ("dps", 5000, None), "Панаджи": ("goi", 4000, None), "Мале": ("mle", 18000, None),
    "Нячанг": ("cxr", 4000, None), "Санья": ("syx", 6000, None), "Пекин": ("pek", 6000, None),
    "Шанхай": ("pvg", 6500, None), "Сеул": ("icn", 8000, None), "Токио": ("hnd", 10000, None),
    "Белград": ("beg", 6000, None), "Будва": ("tiv", 7000, None), "Рим": ("fco", 10000, None),
    "Париж": ("cdg", 12000, None), "Барселона": ("bcn", 10000, None), "Прага": ("prg", 8000, None),
    "Вена": ("vie", 10000, None), "Будапешт": ("bud", 7500, None), "Берлин": ("ber", 10000, None),
    "Афины": ("ath", 8500, None), "Ларнака": ("lca", 8000, None),
}  # fmt: skip


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower().replace("ё", "е")
    return re.sub(r"[\s\-‐–]+", " ", s)


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def download(data: Path, name: str) -> Path:
    path = data / name
    if not path.exists():
        print(f"download {URLS[name]}", file=sys.stderr)
        req = urllib.request.Request(URLS[name], headers={"User-Agent": UA})
        with (
            urllib.request.urlopen(req, timeout=300) as r,
            open(path.with_name(name + ".part"), "wb") as f,
        ):
            while chunk := r.read(1 << 20):
                f.write(chunk)
        path.with_name(name + ".part").rename(path)
    return path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class Limiter:
    """Weighted Open-Meteo calls: under 500 a minute and 4500 an hour (free tier 600 / 5000)."""

    def __init__(self) -> None:
        self.log: deque[tuple[float, float]] = deque()
        self.total = 0.0

    def wait(self, weight: float) -> None:
        while True:
            now = time.time()
            while self.log and now - self.log[0][0] > 3600:
                self.log.popleft()
            hour = sum(w for _, w in self.log)
            minute = sum(w for t, w in self.log if now - t <= 60)
            if hour + weight <= 4500 and minute + weight <= 500:
                break
            time.sleep(5)
        time.sleep(0.4)
        self.log.append((time.time(), weight))
        self.total += weight


LIMIT = Limiter()
CACHED_ONLY = False  # --cached-only


def get_json(url: str, params: dict, cache: Path | None, weight: float) -> dict:
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if CACHED_ONLY:
        raise LookupError(cache)
    full = f"{url}?{urllib.parse.urlencode(params)}"
    errors = 0
    while True:
        LIMIT.wait(weight)
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            reason = e.read().decode("utf-8", "replace")
            if e.code == 429:  # a limit: wait it out, never give up
                pause = 3600 if "Daily" in reason else 600 if "Hourly" in reason else 60
            elif e.code >= 500 and errors < 6:
                errors += 1
                pause = 30 * 2**errors
            else:
                raise
            print(f"{e.code} {reason[:120]}; пауза {pause} с", file=sys.stderr)
            time.sleep(pause)
        except (urllib.error.URLError, TimeoutError) as e:
            errors += 1
            if errors > 6:
                sys.exit(f"не получилось: {full}: {e}")
            print(f"сеть: {e}; повтор", file=sys.stderr)
            time.sleep(10 * 2**errors)
    if cache:
        cache.write_text(json.dumps(body), encoding="utf-8")
    return body


def selection() -> list[dict]:
    out, country, cc = [], None, None
    for line in SELECTION.strip().splitlines():
        if "|" not in line:
            country, cc = line.rsplit(" ", 1)
            first = True
            continue
        ru, en, tags, *rest = line.split("|") + [""]
        hint = dict(x.split("=") for x in rest if "=" in x)
        tags = [t for t in tags.split(",") if t]
        out.append({"ru": ru, "en": en, "country": country, "cc": cc, "tags": tags,
                    "a1": hint.get("a1"), "first": first})  # fmt: skip
        first = False
    return out


def read_cities(path: Path, ccs: set[str]) -> dict[str, list[list[str]]]:
    by_cc: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(path) as z, z.open("cities500.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            row = line.rstrip("\n").split("\t")
            if row[8] in ccs and row[6] == "P":
                by_cc.setdefault(row[8], []).append(row)
    return by_cc


def match(sel: dict, rows: list[list[str]]) -> tuple[list[str] | None, list[str]]:
    """The most populous GeoNames row named as the selection; warnings on close rivals."""
    keys = {norm(sel["ru"]), norm(sel["en"])}
    hits = []
    for row in rows:
        names = {norm(row[1]), norm(row[2]), *(norm(a) for a in row[3].split(",") if a)}
        if keys & names and (not sel["a1"] or row[10] == sel["a1"]):
            hits.append(row)
    hits.sort(key=lambda r: -int(r[14] or 0))
    warn = []
    if len(hits) > 1:
        best, rival = hits[0], hits[1]
        far = km((float(best[4]), float(best[5])), (float(rival[4]), float(rival[5]))) > 30
        if far and int(rival[14] or 0) >= 0.3 * int(best[14] or 0):
            warn.append(f"{sel['ru']}: соперник {rival[1]} a1={rival[10]} pop={rival[14]} "
                        f"(выбран a1={best[10]} pop={best[14]})")  # fmt: skip
    return (hits[0] if hits else None), warn


def geocode(sel: dict, cache_dir: Path) -> dict | None:
    for q in (sel["ru"], sel["en"]):
        cache = cache_dir / f"geo_{hashlib.sha1((q + sel['cc']).encode()).hexdigest()[:12]}.json"
        try:
            body = get_json(GEOCODE, {"name": q, "count": 10, "language": "ru",
                                      "countryCode": sel["cc"], "format": "json"}, cache, 1)  # fmt: skip
        except LookupError:  # --cached-only and no answer in the cache
            continue
        res = [r for r in body.get("results", []) if r.get("country_code") == sel["cc"]]
        if res:
            return max(res, key=lambda r: r.get("population") or 0)
    return None


def read_alternates(path: Path, ids: set[str]) -> dict[str, list[tuple[str, str, bool]]]:
    out: dict[str, list[tuple[str, str, bool]]] = {}
    with zipfile.ZipFile(path) as z, z.open("alternateNamesV2.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            row = line.rstrip("\n").split("\t")
            if row[1] in ids and row[2] in ("ru", "en") and (len(row) < 8 or row[7] != "1"):
                out.setdefault(row[1], []).append((row[2], row[3], row[4] == "1"))
    return out


def read_airports(csv_path: Path, dat_path: Path, towns: list[tuple]) -> list[list]:
    """towns: ((lat, lon), tz) of GeoNames cities, the first source of a missing zone."""
    tz, zoned = {}, list(towns)
    with open(dat_path, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) > 11 and row[11] not in ("\\N", ""):
                zoned.append(((float(row[6]), float(row[7])), row[11]))
                if row[4] not in ("\\N", ""):
                    tz[row[4]] = row[11]
    out = []
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r["type"] in ("large_airport", "medium_airport") and r["scheduled_service"] == "yes"
                    and re.fullmatch(r"[A-Z]{3}", r["iata_code"] or "")):  # fmt: skip
                at = (round(float(r["latitude_deg"]), 4), round(float(r["longitude_deg"]), 4))
                # OpenFlights stops in 2017: a newer airport (KCY, GSV) gets the zone of the
                # nearest town or older airport; GSV is Europe/Saratov, not Moscow as RTW was
                zone = tz.get(r["iata_code"]) or min(zoned, key=lambda z: km(at, z[0]))[1]
                out.append([r["iata_code"], *at, zone, r["iso_country"]])
    return out


def candidates(r: dict, airports: list[list]) -> list[tuple[float, list]]:
    """(straight km, airport) to compare by road, nearest first: the home ones if one is within
    AIRPORT_KM (Выборг is LED, not LPP), else all; within ROAD_SPAN of the nearest."""
    by_km = sorted(
        ((km((r["lat"], r["lon"]), (a[1], a[2])), a) for a in airports), key=lambda x: x[0]
    )
    home = [x for x in by_km if x[1][4] == r["cc"]]
    pool = home if home and home[0][0] <= AIRPORT_KM else by_km
    return [x for x in pool if x[0] <= ROAD_SPAN * pool[0][0] + ROAD_PLUS_KM]


def fetch_roads(r: dict, near: list[tuple[float, list]], roads: dict) -> None:
    """Road km from the place to every candidate (one OSRM table request; null: no road)."""
    if all(a[0] in roads.get(r["ru"], {}) for _, a in near):
        return
    at = [(r["lat"], r["lon"])] + [(a[1], a[2]) for _, a in near]
    points = ";".join(f"{lon},{lat}" for lat, lon in at)
    print(f"osrm {r['ru']}: {' '.join(a[0] for _, a in near)}", file=sys.stderr)
    time.sleep(1)  # the demo server asks for at most one request a second
    body = get_json(OSRM + points, {"sources": 0, "annotations": "distance"}, None, 0)
    if body.get("code") != "Ok":
        sys.exit(f"osrm {r['ru']}: {body}")
    metres = body["distances"][0][1:]
    roads[r["ru"]] = {a[0]: None if m is None else round(m / 1000)
                      for (_, a), m in zip(near, metres, strict=True)}  # fmt: skip


def airport_fields(r: dict, airports: list[list], roads: dict, station: int | None) -> dict:
    """airport and airport_km of a place: the nearest by road when the roads snapshot has every
    candidate ("road": true, km by road), else the nearest by straight line."""
    near = candidates(r, airports)
    road = roads.get(r["ru"], {})
    ok = [(road[a[0]], a, k) for k, a in near if road.get(a[0]) is not None]
    if ok and all(a[0] in road for _, a in near):
        ap_km, ap_, line = min(ok, key=lambda x: x[0])
        mark = {"road": True}
    else:
        (line, ap_), mark = near[0], {}
        ap_km = round(line)
    # no rail either: the nearest airport with flights however far, else no road at all.
    # Горно-Алтайск: RGK has no scheduled_service in OurAirports, so its road is from elsewhere
    own = line <= AIRPORT_KM or station is None
    return {"airport": {"iata": ap_[0], "km": ap_km, "tz": ap_[3], **mark},
            "airport_km": ap_km if own else None}  # fmt: skip


def monthly(days: list[str], values: list, how: str) -> list[float | None]:
    by_month: dict[int, list[float]] = {}
    for d, v in zip(days, values, strict=True):
        if v is not None:
            by_month.setdefault(int(d[5:7]), []).append(v)
    out = []
    for m in range(1, 13):
        xs = by_month.get(m)
        if not xs:
            out.append(None)
        elif how == "sum":
            out.append(round(sum(xs) / YEARS))
        else:
            out.append(round(statistics.fmean(xs), 1))
    return out


def climate(lat: float, lon: float, cache_dir: Path) -> dict:
    key = f"{lat:.4f}_{lon:.4f}"
    d: dict[str, list] = {"time": [], "temperature_2m_max": [], "precipitation_sum": []}
    for prefix, start, end in AIR_SPANS:
        w = get_json(ARCHIVE, {"latitude": lat, "longitude": lon, "start_date": start,
                               "end_date": end, "daily": "temperature_2m_max,precipitation_sum",
                               "timezone": "auto"},
                     cache_dir / f"{prefix}_{key}.json", 0.2 * 1827 / 14)  # fmt: skip
        for k in d:
            d[k] += w["daily"][k]
    out = {"air": monthly(d["time"], d["temperature_2m_max"], "mean"),
           "rain": monthly(d["time"], d["precipitation_sum"], "sum"), "sea": None}  # fmt: skip
    probe = get_json(MARINE, {"latitude": lat, "longitude": lon, "start_date": "2025-08-01",
                              "end_date": "2025-08-01", "daily": "sea_surface_temperature_mean"},
                     cache_dir / f"seaprobe_{key}.json", 1)  # fmt: skip
    near = km((lat, lon), (probe["latitude"], probe["longitude"])) <= SEA_KM
    if near and probe["daily"]["sea_surface_temperature_mean"][0] is not None:
        s = get_json(MARINE, {"latitude": lat, "longitude": lon, "start_date": SEA_START, "end_date": END,
                              "daily": "sea_surface_temperature_mean"},
                     cache_dir / f"sea_{key}.json", 0.1 * 1096 / 14)  # fmt: skip
        out["sea"] = monthly(s["daily"]["time"], s["daily"]["sea_surface_temperature_mean"], "mean")
    return out


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data" / "catalog")
    ap.add_argument("--out", type=Path, default=ROOT / "backend" / "src" / "api" / "catalog.json")
    ap.add_argument("--cached-only", action="store_true", help="no Open-Meteo calls")
    ap.add_argument("--only", help="call Open-Meteo only for places FROM-TO (numbers of the log), "
                    "the rest from the cache or --import: a teammate fetches a share")  # fmt: skip
    ap.add_argument("--export", type=Path, help="write {name: climate} of the --only places")
    ap.add_argument("--import", dest="imports", type=Path, nargs="*", default=[],
                    help="climate files written by --export on other machines")  # fmt: skip
    ap.add_argument("--points-out", type=Path, help="write [{name, lat, lon}] of all places, stop")
    ap.add_argument("--fetch-roads", action="store_true",
                    help="ask OSRM for the roads of mountain places with a choice of airports")
    ap.add_argument("--airports-only", action="store_true",
                    help="re-pick the airports of the existing --out; needs only airports.csv")
    ap.add_argument("--points", help="file ('-': stdin) with a JSON list of {name, lat, lon}: "
                    "fetch their climate to --export, no GeoNames")  # fmt: skip
    args = ap.parse_args()
    global CACHED_ONLY
    CACHED_ONLY = args.cached_only
    data, om = args.data, args.data / "openmeteo"
    om.mkdir(parents=True, exist_ok=True)
    if args.points:
        text = sys.stdin.read() if args.points == "-" else Path(args.points).read_text("utf-8")
        points = json.loads(text[text.index("[") : text.rindex("]") + 1])  # also an issue body
        share = {}
        for i, p in enumerate(points):
            print(
                f"[{i + 1}/{len(points)}] {p['name']} (вызовов {LIMIT.total:.0f})", file=sys.stderr
            )
            share[p["name"]] = climate(p["lat"], p["lon"], om)
        args.export.write_text(
            json.dumps(share, ensure_ascii=False, separators=(",", ":")), "utf-8"
        )
        print(f"export: {len(share)} places, {args.export.stat().st_size} B", file=sys.stderr)
        return
    old = json.loads(args.out.read_text("utf-8")) if args.out.exists() else {}
    roads = old.get("roads", {})
    if args.airports_only:
        cc = {}
        with open(download(data, "airports.csv"), encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["scheduled_service"] == "yes" and row["iata_code"]:
                    cc[row["iata_code"]] = row["iso_country"]
        airports = [[*a, cc[a[0]]] for a in old["airports"]]
        for p in old["places"]:
            r = {"ru": p["name"], "lat": p["lat"], "lon": p["lon"], "cc": p["cc"]}
            near = candidates(r, airports)
            if args.fetch_roads and "mountains" in p["tags"] and len(near) > 1:
                fetch_roads(r, near, roads)
            p.update(airport_fields(r, airports, roads, p["station_km"]))
        old["roads"] = {k: roads[k] for k in sorted(roads)}
        old["_source"] = re.sub(r"Аэропорт — .*?аэропорта с рейсами\. ", lambda _: AIRPORT_SOURCE,
                                old["_source"], count=1)  # fmt: skip
        write(args.out, old)
        return
    files = {n: download(data, n) for n in URLS}

    sel = selection()
    by_cc = read_cities(files["cities500.zip"], {s["cc"] for s in sel})
    resolved, warnings, missing = [], [], []
    for s in sel:
        row, warn = match(s, by_cc.get(s["cc"], []))
        warnings += warn
        if row:
            g = {"gid": row[0], "lat": float(row[4]), "lon": float(row[5]), "tz": row[17],
                 "pop": int(row[14] or 0), "elev": int(row[16]) if row[16] not in ("", "-9999") else None,
                 "ascii": row[2]}  # fmt: skip
        elif r := geocode(s, om):
            g = {"gid": str(r["id"]), "lat": r["latitude"], "lon": r["longitude"],
                 "tz": r["timezone"], "pop": r.get("population") or 0,
                 "elev": round(r["elevation"]) if r.get("elevation") is not None else None,
                 "ascii": s["en"]}  # fmt: skip
            warnings.append(f"{s['ru']}: нет в cities500, взят геокодер Open-Meteo id={r['id']}")
        else:
            missing.append(s["ru"])
            continue
        resolved.append({**s, **g})

    if args.points_out:
        pts = [
            {"name": r["ru"], "lat": round(r["lat"], 4), "lon": round(r["lon"], 4)}
            for r in resolved
        ]
        args.points_out.write_text(json.dumps(pts, ensure_ascii=False), "utf-8")
        print(f"points: {len(pts)}", file=sys.stderr)
        return

    alts = read_alternates(files["alternateNamesV2.zip"], {r["gid"] for r in resolved})
    towns = [((float(r[4]), float(r[5])), r[17]) for rows in by_cc.values() for r in rows]
    airports = read_airports(files["airports.csv"], files["airports.dat"], towns)

    countries = {}
    for r in resolved:
        countries.setdefault(r["country"], r["cc"])
    country_keys = {norm(c) for c in countries} | {
        norm(a) for v in COUNTRY_ALIASES.values() for a in v
    }
    owners: dict[str, set[str]] = {}
    for r in resolved:
        names = {r["en"]} | {n for lang, n, _ in alts.get(r["gid"], [])}
        r["_aliases"] = {norm(n) for n in names if re.fullmatch(r"[\w .'’\-()]+", n)} - {
            norm(r["ru"])
        }
        for a in r["_aliases"]:
            owners.setdefault(a, set()).add(r["ru"])
    taken_names = {norm(r["ru"]) for r in resolved}

    legacy_levels: dict[str, list[int]] = {}
    for r in resolved:
        if r["ru"] in LEGACY:
            legacy_levels.setdefault(r["country"], []).append(LEGACY[r["ru"]][1])

    imported = {}
    for f in args.imports:
        imported.update(json.loads(f.read_text("utf-8")))
    lo, hi = map(int, args.only.split("-")) if args.only else (1, len(resolved))
    places, slugs = [], {v[0] for v in LEGACY.values()}
    print(f"climate for {len(resolved)} places", file=sys.stderr)
    for i, r in enumerate(resolved):
        aliases = {a for a in r["_aliases"] if len(owners[a]) == 1 and a not in taken_names
                   and a not in country_keys and len(a) >= 3}  # fmt: skip
        aliases |= {norm(a) for a in EXTRA_ALIASES.get(r["ru"], [])}
        aliases.discard(norm(r["ru"]))
        if r["ru"] in LEGACY:
            slug, level, station = LEGACY[r["ru"]]
        else:
            slug = slugify(r["ascii"]) or r["gid"]
            while slug in slugs:
                slug += "x"
            levels = legacy_levels.get(r["country"])
            level = (
                int(statistics.median(levels))
                if levels
                else LEVEL_BY_COUNTRY.get(r["country"], DEFAULT_LEVEL)
            )
            station = None if r["ru"] in NO_RAIL else RAIL_COUNTRIES.get(r["country"])
        slugs.add(slug)
        print(f"[{i + 1}/{len(resolved)}] {r['ru']} (вызовов {LIMIT.total:.0f})", file=sys.stderr)
        CACHED_ONLY = args.cached_only or not lo <= i + 1 <= hi
        try:
            clim = imported.get(r["ru"]) or climate(round(r["lat"], 4), round(r["lon"], 4), om)
        except LookupError:
            continue  # cached only: no climate yet
        sea = clim["sea"] is not None
        tags = [t for t in r["tags"] if t not in ("hub", "main")]
        if sea:
            tags.append("sea")
        if (r["elev"] or 0) >= MOUNTAIN_M and "mountains" not in tags:
            tags.append("mountains")
        if args.fetch_roads and "mountains" in tags and len(near := candidates(r, airports)) > 1:
            fetch_roads(r, near, roads)
        places.append({
            "name": r["ru"], "slug": slug, "country": r["country"], "cc": r["cc"],
            "lat": round(r["lat"], 4), "lon": round(r["lon"], 4), "tz": r["tz"],
            "elev": r["elev"], "pop": r["pop"], "geonameid": int(r["gid"]),
            "hub": "hub" in r["tags"], "main": "main" in r["tags"], "sea": sea, "tags": tags,
            "aliases": sorted(aliases), **airport_fields(r, airports, roads, station),
            "station_km": station, "level": level,
            "climate": clim,
        })  # fmt: skip

    if args.export:
        mine = {r["ru"] for r in resolved[lo - 1 : hi]}
        share = {p["name"]: p["climate"] for p in places if p["name"] in mine}
        args.export.write_text(
            json.dumps(share, ensure_ascii=False, separators=(",", ":")), "utf-8"
        )
        size = args.export.stat().st_size
        print(f"export: {len(share)} places of {lo}-{hi}, {size} B", file=sys.stderr)
    hubs = [p for p in places if p["hub"]]
    snapshot = {
        "_source": (
            f"Сборка {date.today():%Y-%m-%d}, scripts/build_catalog.py (#8). "
            "Координаты, IANA-пояс, высота (DEM), население и варианты названий ru/en — GeoNames "
            "cities500 и alternateNamesV2 (CC BY 4.0, geonames.org); несколько посёлков — "
            "Open-Meteo Geocoding (тоже GeoNames). "
            + AIRPORT_SOURCE
            + "Климат — Open-Meteo Historical Weather (ERA5, CC BY 4.0): air — среднее дневного "
            f"максимума, °C; rain — осадки за месяц, мм; среднее за {START[:4]}–{END[:4]} "
            f"({YEARS} лет). sea — температура поверхности моря, Open-Meteo "
            f"Marine (CC BY 4.0), {SEA_START[:4]}–{END[:4]} (раньше 2023 в архиве нет); есть, если ячейка моря модели ближе {SEA_KM} км; "
            "тогда sea = true. Тег mountains — высота от "
            f"{MOUNTAIN_M} м или список отбора; ski, spa, city, nature, hub (город вылета), "
            "main (куда ведёт название страны) — список отбора команды. level (цена ночи 3★ "
            "на двоих, ₽), station_km и NO_RAIL — оценки команды без источника: для городов "
            "из прежнего places.json (#9) — их значения, для новых — медиана страны или таблица скрипта."
        ),
        "countries": {
            c: sorted({norm(c)} | {norm(a) for a in COUNTRY_ALIASES.get(c, [])})
            for c in sorted({p["country"] for p in places})
        },
        "places": places,
        "airports": [a[:4] for a in airports],
        "roads": {k: roads[k] for k in sorted(roads) if k in {p["name"] for p in places}},
    }
    write(args.out, snapshot)
    print(f"places {len(places)}, hubs {len(hubs)}, sea {sum(p['sea'] for p in places)}, "
          f"airports {len(airports)}, size {args.out.stat().st_size} B, "
          f"Open-Meteo calls {LIMIT.total:.0f}", file=sys.stderr)  # fmt: skip
    for w in warnings:
        print("warn:", w, file=sys.stderr)
    if missing:
        print("missing:", ", ".join(missing), file=sys.stderr)
    for n, p in files.items():
        print(f"{n}\t{p.stat().st_size}\t{sha256(p)}", file=sys.stderr)


def write(out: Path, snapshot: dict) -> None:
    """One place a line: a rebuild diff shows the places that changed."""
    places = snapshot["places"]
    text = (
        "{\n"
        + f' "_source": {json.dumps(snapshot["_source"], ensure_ascii=False)},\n "places": [\n'
    )
    text += ",\n".join(
        "  " + json.dumps(p, ensure_ascii=False, separators=(",", ":")) for p in places
    )
    text += '\n ],\n "countries": ' + json.dumps(
        snapshot["countries"], ensure_ascii=False, separators=(",", ":")
    )
    text += ',\n "airports": ' + json.dumps(snapshot["airports"], separators=(",", ":"))
    roads = json.dumps(snapshot["roads"], ensure_ascii=False, separators=(",", ":"))
    text += ',\n "roads": ' + roads
    out.write_text(text + "\n}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
