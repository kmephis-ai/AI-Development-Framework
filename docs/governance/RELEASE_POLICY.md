# Политика Release и Deployment

`MERGED != RELEASED != DEPLOYED != HEALTHY`.

Release исходит только из verified `main`, фиксирует version/tag/SHA, manifest, archive SHA-256, limitations, migration и rollback. External release требует выбранного `LICENSE`, human confirmation и `ADWF_RELEASE_ENABLED=true`. Без этого pipeline fail-closed.

Deployment — отдельная сериализованная операция. DEV допускается только при A3 и свежих exact-SHA gates; production всегда human-gated. После deployment обязательны runtime revision match, smoke и Golden Paths.

