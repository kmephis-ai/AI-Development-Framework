# Consumer Installation Record v1

`Consumer Installation Record` — consumer-owned, provider-durable proof that an exact ADWF revision was transactionally adopted into a consumer repository. It lives at `.adwf-consumer/installation.json`, outside framework package inventory and outside ignored `.adwf-runtime`.

The record is emitted only after a committed adoption and a valid sealed Consumer Profile. It binds exact framework commit/tree/package manifest, consumer repository/base identity, adoption transaction and plan, the Managed Surface snapshot semantics, and exact Consumer Profile/Project Pack digests. The record is strict-schema and self-sealed.

A fresh checkout may validate the record without the original runtime journal. Validation rechecks the exact framework source identity, package manifest, reconstructed Managed Surface snapshot contract, Consumer Profile/Project Pack binding and every installed or preserved managed-surface byte. Tamper, substitution, drift, symlink or missing file fails closed.

The record has `mutation_authority=NONE_RECORD_IS_PROOF_ONLY`. It cannot by itself authorize overwrite, removal, detach, migration or upgrade. Existing lifecycle/upgrade planners and transactions remain the only mutation authority and must independently revalidate their own provenance.

For the first future upgrade after a normal provider rematerialization, `UPGRADE-002` may use the committed installation record only as a narrow **provenance rebind fallback** when the exact ignored adoption runtime journal is absent. The executor first resolves the current consumer repository identity, runs full fresh-session installation validation against the exact source framework, reloads the sealed record, reconstructs its Managed Surface snapshot and requires byte-semantic equality with the supplied source snapshot before any upgrade runtime directory or product write is created.

Runtime provenance always wins. If the matching prior UPGRADE-002 journal exists, it remains authoritative. If the matching initial-adoption journal exists, it remains authoritative. Invalid, non-committed, tampered or mismatched runtime provenance is never bypassed by the installation record. An undetectable repository identity, repository substitution, record/source/profile/managed-byte drift or snapshot substitution therefore fails closed before upgrade mutation. This bridge does not change the record's `NONE_RECORD_IS_PROOF_ONLY` contract; it only lets the separately validated upgrade transaction reconstruct the same source authority that a committed adoption journal previously supplied.

Mandatory safety remains `monetary_budget_usd=0` and `secrets=FORBIDDEN`. Credentials, tokens and local absolute paths are not part of the record contract.

This capability proves durable installation provenance on deterministic/adversarial consumers. It does not mean PrihRashOnline-v2 is already ADWF-managed; consumer operating-state/Roadmap and native-gate integration remain separate prerequisites.

## Session-local checkout locator

`managed_surface.consumer_root_sha256` фиксирует locator рабочей директории, существовавшей в момент исходной adoption/projection transaction, и остаётся частью sealed historical record. Он **не является переносимой идентичностью repository**. При fresh-session upgrade rebind ADWF сначала полностью валидирует sealed record, exact source framework, repository identity, profile и каждый managed/preserved byte по историческому snapshot. Только после этого возвращаемая в память копия snapshot получает `consumer_root_sha256` текущего checkout. Сам Installation Record при таком rebind не переписывается и mutation authority не получает.

После успешного connected framework upgrade новый Installation Record строится только из transaction-owned committed B snapshot и exact target framework identity. Старый record не может быть оставлен рядом с B managed files как допустимое publishable состояние; operations/gates proof bindings должны быть атомарно перепривязаны к новому installation/profile hash или вся connected transaction считается незавершённой и rollback/recovery остаётся обязательным.
