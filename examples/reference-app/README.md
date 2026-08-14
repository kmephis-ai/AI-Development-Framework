# ADWF Reference App

Минимальный статический продукт для детерминированной проверки Delivery Plane.
Он не является production-hosting: `REFERENCE_LOCAL` копирует этот artifact в
локальный deployment store, связывает его с exact source SHA и затем независимо
проверяет digest/health marker. Живой cloud deployment остаётся `NOT_VERIFIED`,
пока проект не подключит собственный разрешённый adapter.
