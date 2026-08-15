# Contract: adwf-verification-before-completion

Цель: не позволять статусу DONE/PASS опережать фактическое evidence.

Verification должна быть fresh, impact-aware и exact-head-bound. Local PASS не заменяет provider gate; provider CI не заменяет product runtime evidence, если есть product impact. Unknown/ambiguous evidence блокирует positive claim.
