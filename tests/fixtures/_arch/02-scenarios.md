# Тестовые сценарии

Видимых: 4. В holdout отложено: 2 (не раскрываются).

### S1 — Протечка сверху  [typical]
Input: «Сверху течёт вода по стене в ванной, кв. 45»
Context: рабочий день, 14:00
Expected behaviour: категория «Водоснабжение», срочность «авария», исполнитель — аварийная бригада
Pass criterion: category = water и urgency = emergency
Weight: critical
Holdout: no

### S2 — Дубль обращения  [edge]
Input: то же обращение повторно через 3 минуты
Context: первое уже в работе
Expected behaviour: помечено как дубль, новая заявка не создаётся
Pass criterion: duplicate_of = id первого обращения
Weight: normal
Holdout: no

### S3 — Диспетчер открывает очередь  [typical]
Input: открыть интерфейс, 20 новых обращений
Context: jury-click: открыть очередь → выбрать обращение → принять рекомендацию → следующее
Expected behaviour: срочные сверху, у каждого — объяснение одной строкой
Pass criterion: первая строка очереди — срочная; переход к следующему ≤ 1 клик
Weight: critical
Holdout: no

### S4 — Без сети  [edge]
Input: любое обращение, сеть отключена
Context: стенд жюри (--network=none)
Expected behaviour: ответ из локальной модели
Pass criterion: ответ ≤ 2 с, без исключений в логе
Weight: critical
Holdout: no
