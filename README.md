# Threading MemcLoad

Задача: нужно переделать однопоточную версию memc_load.py в более производительный вариант. 
Сам скрипт парсит и заливает в мемкеш поминутную выгрузку логов трекера установленных приложений. 
Ключом является тип иидентификатор устройства через двоеточие, значением являет protobuf сообщение.
