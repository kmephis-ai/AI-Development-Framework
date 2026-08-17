# Consumer Installation Record v1

`Consumer Installation Record` — consumer-owned, provider-durable proof that an exact ADWF revision was transactionally adopted into a consumer repository. It lives at `.adwf-consumer/installation.json`, outside framework package inventory and outside ignored `.adwf-runtime`.

The record is emitted only after a committed adoption and a valid sealed Consumer Profile. It binds exact framework commit/tree/package manifest, consumer repository/base identity, adoption transaction and plan, the Managed Surface snapshot semantics, and exact Consumer Profile/Project Pack digests. The record is strict-schema and self-sealed.

A fresh checkout may validate the record without the original runtime journal. Validation rechecks the exact framework source identity, package manifest, reconstructed Managed Surface snapshot contract, Consumer Profile/Project Pack binding and every installed or preserved managed-surface byte. Tamper, substitution, drift, symlink or missing file fails closed.

The record has `mutation_authority=NONE_RECORD_IS_PROOF_ONLY`. It cannot by itself authorize overwrite, removal, detach, migration or upgrade. Existing lifecycle/upgrade planners and transactions remain the only mutation authority and must independently revalidate their own provenance.

Mandatory safety remains `monetary_budget_usd=0` and `secrets=FORBIDDEN`. Credentials, tokens and local absolute paths are not part of the record contract.

This capability proves durable installation provenance on deterministic/adversarial consumers. It does not mean PrihRashOnline-v2 is already ADWF-managed; consumer operating-state/Roadmap and native-gate integration remain separate prerequisites.
