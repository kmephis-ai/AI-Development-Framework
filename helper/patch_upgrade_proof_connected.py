from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"PATCH_COUNT:{path}:{count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_count(path: str, old: str, new: str, expected: int) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"PATCH_COUNT:{path}:{count}:expected={expected}")
    p.write_text(text.replace(old, new), encoding="utf-8")


# Public durable-installation rebind accepts an explicit expected repository but
# keeps legacy discovery as the default for every existing caller.
replace_once(
    ".adwf/lib/consumer_installation.py",
    '''def rebind_snapshot_for_fresh_session(consumer_root: str | Path, framework_root: str | Path) -> dict[str, Any]:
    """Reconstruct the exact adopted snapshot after revalidating durable installation proof."""
    consumer = Path(consumer_root).resolve()
    framework = Path(framework_root).resolve()
    repository = detect_repository(consumer)
    if repository is None:
        raise ConsumerInstallationError("INSTALLATION_CONSUMER_REPOSITORY_NOT_VERIFIABLE")
    validate_fresh_session(consumer, framework, expected_repository=repository)
    snapshot = _snapshot_from_record(load_record(consumer, framework))
    # The absolute checkout path is session-local, not durable installation identity.
    # Rebind only after every durable proof/managed byte/profile check above passed.
    snapshot["consumer_root_sha256"] = hashlib.sha256(str(consumer).encode("utf-8")).hexdigest()
    return snapshot
''',
    '''def rebind_snapshot_for_fresh_session(
    consumer_root: str | Path,
    framework_root: str | Path,
    *,
    expected_repository: str | None = None,
) -> dict[str, Any]:
    """Reconstruct the exact adopted snapshot after revalidating durable installation proof."""
    consumer = Path(consumer_root).resolve()
    framework = Path(framework_root).resolve()
    if expected_repository is None:
        repository = detect_repository(consumer)
        if repository is None:
            raise ConsumerInstallationError("INSTALLATION_CONSUMER_REPOSITORY_NOT_VERIFIABLE")
    else:
        repository = str(expected_repository)
        if not REPOSITORY.fullmatch(repository):
            raise ConsumerInstallationError("INSTALLATION_CONSUMER_REPOSITORY_INVALID")
    validate_fresh_session(consumer, framework, expected_repository=repository)
    snapshot = _snapshot_from_record(load_record(consumer, framework))
    # The absolute checkout path is session-local, not durable installation identity.
    # Rebind only after every durable proof/managed byte/profile check above passed.
    snapshot["consumer_root_sha256"] = hashlib.sha256(str(consumer).encode("utf-8")).hexdigest()
    return snapshot
''',
)

# Upgrade transaction accepts explicit proof identity only for the durable
# installation fallback. Runtime/adoption journal provenance remains dominant.
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''from .consumer_installation import (
    ConsumerInstallationError, _snapshot_from_record, load_record as load_installation_record,
    validate_fresh_session as validate_installation_fresh_session,
)
''',
    '''from .consumer_installation import (
    REPOSITORY, ConsumerInstallationError, _snapshot_from_record, load_record as load_installation_record,
    validate_fresh_session as validate_installation_fresh_session,
)
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''def _trusted_source_snapshot(source_root: Path, target_root: Path, consumer: Path, snapshot: dict[str, Any]) -> None:
''',
    '''def _trusted_source_snapshot(
    source_root: Path,
    target_root: Path,
    consumer: Path,
    snapshot: dict[str, Any],
    *,
    consumer_repository: str | None = None,
) -> None:
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''    repository = detect_repository(consumer)
    if repository is None:
        raise ConsumerUpgradeError("UPGRADE_APPLY_INSTALLATION_REPOSITORY_NOT_VERIFIABLE")
''',
    '''    if consumer_repository is None:
        repository = detect_repository(consumer)
        if repository is None:
            raise ConsumerUpgradeError("UPGRADE_APPLY_INSTALLATION_REPOSITORY_NOT_VERIFIABLE")
    else:
        repository = str(consumer_repository)
        if not REPOSITORY.fullmatch(repository):
            raise ConsumerUpgradeError("UPGRADE_APPLY_INSTALLATION_REPOSITORY_INVALID")
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''def _validate_static_apply_bindings(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
) -> None:
''',
    '''def _validate_static_apply_bindings(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
    *, consumer_repository: str | None = None,
) -> None:
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''    _trusted_source_snapshot(source_root, target_root, consumer, snapshot)
''',
    '''    _trusted_source_snapshot(
        source_root, target_root, consumer, snapshot,
        consumer_repository=consumer_repository,
    )
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''def _preflight(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    _validate_static_apply_bindings(source_root, target_root, consumer, compatibility, plan, snapshot)
''',
    '''def _preflight(
    source_root: Path, target_root: Path, consumer: Path,
    compatibility: dict[str, Any], plan: dict[str, Any], snapshot: dict[str, Any],
    *, consumer_repository: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    _validate_static_apply_bindings(
        source_root, target_root, consumer, compatibility, plan, snapshot,
        consumer_repository=consumer_repository,
    )
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''def apply_upgrade(
    source_framework_root: str | Path, target_framework_root: str | Path, consumer_root: str | Path,
    compatibility: dict[str, Any], plan: dict[str, Any], source_snapshot: dict[str, Any], *, fault_at: str | None = None,
) -> dict[str, Any]:
    """Apply a READY upgrade plan; no write happens before full exact-state preflight."""
    source_root = Path(source_framework_root).resolve(); target_root = Path(target_framework_root).resolve(); consumer = _safe_consumer(consumer_root)
    _validate_static_apply_bindings(source_root, target_root, consumer, compatibility, plan, source_snapshot)
''',
    '''def apply_upgrade(
    source_framework_root: str | Path, target_framework_root: str | Path, consumer_root: str | Path,
    compatibility: dict[str, Any], plan: dict[str, Any], source_snapshot: dict[str, Any], *,
    fault_at: str | None = None, consumer_repository: str | None = None,
) -> dict[str, Any]:
    """Apply a READY upgrade plan; no write happens before full exact-state preflight."""
    source_root = Path(source_framework_root).resolve(); target_root = Path(target_framework_root).resolve(); consumer = _safe_consumer(consumer_root)
    _validate_static_apply_bindings(
        source_root, target_root, consumer, compatibility, plan, source_snapshot,
        consumer_repository=consumer_repository,
    )
''',
)
replace_once(
    ".adwf/lib/consumer_upgrade_transaction.py",
    '''    _, _, target_profile, profile_payload = _preflight(source_root, target_root, consumer, compatibility, plan, source_snapshot)
''',
    '''    _, _, target_profile, profile_payload = _preflight(
        source_root, target_root, consumer, compatibility, plan, source_snapshot,
        consumer_repository=consumer_repository,
    )
''',
)

# Connected external proof uses sealed installation provenance rather than a
# second adoption. Only ADWF-managed bytes and the controlled profile are
# excluded from the consumer preservation set.
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''from .consumer_profile import apply_consumer_profile, load_consumer_profile
''',
    '''from .consumer_installation import ConsumerInstallationError, RECORD_REL, rebind_snapshot_for_fresh_session
from .consumer_profile import PROFILE_REL, apply_consumer_profile, load_consumer_profile
''',
)
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''def _checkpoint(consumer: Path, baseline: dict[str, str], label: str) -> dict[str, Any]:
''',
    '''def _installation_record_present(consumer: Path) -> bool:
    path = consumer / RECORD_REL
    return path.exists() or path.is_symlink()


def _connected_preservation_baseline(baseline: dict[str, str], snapshot: dict[str, Any]) -> dict[str, str]:
    managed = {
        str(item.get("path") or "")
        for item in snapshot.get("entries") or []
        if item.get("managed_by_adwf") is True
    }
    preserved = {
        rel: digest
        for rel, digest in baseline.items()
        if rel not in managed and rel != PROFILE_REL
    }
    if RECORD_REL not in baseline:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_CONNECTED_INSTALLATION_NOT_TRACKED")
    if not preserved:
        raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_CONNECTED_PRESERVATION_REQUIRED")
    return dict(sorted(preserved.items()))


def _checkpoint(consumer: Path, baseline: dict[str, str], label: str) -> dict[str, Any]:
''',
)
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''    transitions = value.get("transitions") or {}
    if transitions != {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"}:
        errors.append("EXTERNAL_PROOF_TRANSITION_MISMATCH")
    checkpoints = value.get("preservation_checkpoints") or []
    baseline_sha = value.get("preservation_set_sha256")
    if len(checkpoints) != 4 or any(item.get("preservation_sha256") != baseline_sha for item in checkpoints):
        errors.append("EXTERNAL_PROOF_PRESERVATION_BINDING_MISMATCH")
''',
    '''    transitions = value.get("transitions") or {}
    source_transition = transitions.get("adoption")
    if (
        source_transition not in {"COMMITTED", "VERIFIED_EXISTING"}
        or transitions.get("upgrade_b") != "COMMITTED"
        or transitions.get("rollback_a") != "ROLLED_BACK"
        or transitions.get("retry_b") != "COMMITTED"
        or set(transitions) != {"adoption", "upgrade_b", "rollback_a", "retry_b"}
    ):
        errors.append("EXTERNAL_PROOF_TRANSITION_MISMATCH")
    checkpoints = value.get("preservation_checkpoints") or []
    baseline_sha = value.get("preservation_set_sha256")
    expected_first = "CONNECTED_A" if source_transition == "VERIFIED_EXISTING" else "ADOPTION_A"
    labels = [item.get("label") for item in checkpoints]
    if labels != [expected_first, "UPGRADE_B", "ROLLBACK_A", "RETRY_B"]:
        errors.append("EXTERNAL_PROOF_CHECKPOINT_SEQUENCE_MISMATCH")
    if len(checkpoints) != 4 or any(item.get("preservation_sha256") != baseline_sha for item in checkpoints):
        errors.append("EXTERNAL_PROOF_PRESERVATION_BINDING_MISMATCH")
''',
)
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''    baseline = _tracked_regular_files(external)
    baseline_sha = _preservation_sha(baseline)
    with tempfile.TemporaryDirectory(prefix="adwf-external-upgrade-proof-") as tmp:
''',
    '''    baseline = _tracked_regular_files(external)
    with tempfile.TemporaryDirectory(prefix="adwf-external-upgrade-proof-") as tmp:
''',
)
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''        adoption_plan = plan_adoption(source, consumer, source_revision=source_sha)
        if adoption_plan.get("status") != "READY":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_NOT_READY:" + ";".join(adoption_plan.get("blockers") or []))
        adoption = apply_adoption(source, consumer, adoption_plan)
        if adoption.get("status") != "COMMITTED" or not isinstance(adoption.get("snapshot"), dict):
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_FAILED")
        checkpoint_adoption = _checkpoint(consumer, baseline, "ADOPTION_A")

        profile_result = apply_consumer_profile(
            consumer, source, product_name=product_name, default_branch=default_branch,
            repository_visibility=repository_visibility,
        )
        if profile_result.get("status") not in {"APPLIED", "ALREADY_MATERIALIZED"}:
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_PROFILE_FAILED:" + str(profile_result.get("reason") or "UNKNOWN"))
        profile_a = load_consumer_profile(consumer, source, required=True)
        if profile_a is None or profile_a.get("project_packs", {}).get("selected") != "apps-script":
            raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_APPS_SCRIPT_PACK_REQUIRED")

        snapshot_a = adoption["snapshot"]
''',
    '''        if _installation_record_present(consumer):
            try:
                snapshot_a = rebind_snapshot_for_fresh_session(
                    consumer, source, expected_repository=repo,
                )
            except ConsumerInstallationError as exc:
                raise ExternalConsumerUpgradeProofError(
                    "EXTERNAL_PROOF_CONNECTED_INSTALLATION_INVALID:" + str(exc).split(":", 1)[0]
                ) from exc
            profile_a = load_consumer_profile(consumer, source, required=True)
            if profile_a is None or profile_a.get("project_packs", {}).get("selected") != "apps-script":
                raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_APPS_SCRIPT_PACK_REQUIRED")
            preservation = _connected_preservation_baseline(baseline, snapshot_a)
            checkpoint_source = _checkpoint(consumer, preservation, "CONNECTED_A")
            source_transition = "VERIFIED_EXISTING"
        else:
            adoption_plan = plan_adoption(source, consumer, source_revision=source_sha)
            if adoption_plan.get("status") != "READY":
                raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_NOT_READY:" + ";".join(adoption_plan.get("blockers") or []))
            adoption = apply_adoption(source, consumer, adoption_plan)
            if adoption.get("status") != "COMMITTED" or not isinstance(adoption.get("snapshot"), dict):
                raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_ADOPTION_FAILED")
            preservation = baseline
            checkpoint_source = _checkpoint(consumer, preservation, "ADOPTION_A")

            profile_result = apply_consumer_profile(
                consumer, source, product_name=product_name, default_branch=default_branch,
                repository_visibility=repository_visibility,
            )
            if profile_result.get("status") not in {"APPLIED", "ALREADY_MATERIALIZED"}:
                raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_PROFILE_FAILED:" + str(profile_result.get("reason") or "UNKNOWN"))
            profile_a = load_consumer_profile(consumer, source, required=True)
            if profile_a is None or profile_a.get("project_packs", {}).get("selected") != "apps-script":
                raise ExternalConsumerUpgradeProofError("EXTERNAL_PROOF_APPS_SCRIPT_PACK_REQUIRED")
            snapshot_a = adoption["snapshot"]
            source_transition = "COMMITTED"
        baseline_sha = _preservation_sha(preservation)
''',
)
replace_count(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''apply_upgrade(source, target, consumer, compatibility, plan, snapshot_a)''',
    '''apply_upgrade(
            source, target, consumer, compatibility, plan, snapshot_a,
            consumer_repository=repo,
        )''',
    2,
)
replace_count(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''_checkpoint(consumer, baseline, ''',
    '''_checkpoint(consumer, preservation, ''',
    3,
)
replace_once(
    ".adwf/lib/external_consumer_upgrade_proof.py",
    '''            "transitions": {"adoption": "COMMITTED", "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"},
            "preservation_set_sha256": baseline_sha,
            "preservation_checkpoints": [checkpoint_adoption, checkpoint_b, checkpoint_rollback, checkpoint_final],
''',
    '''            "transitions": {"adoption": source_transition, "upgrade_b": "COMMITTED", "rollback_a": "ROLLED_BACK", "retry_b": "COMMITTED"},
            "preservation_set_sha256": baseline_sha,
            "preservation_checkpoints": [checkpoint_source, checkpoint_b, checkpoint_rollback, checkpoint_final],
''',
)

# Backward-compatible schema extension: connected source verification is an
# additional valid source transition/checkpoint, not a relaxed upgrade action.
replace_once(
    ".adwf/schemas/external-consumer-upgrade-proof.schema.json",
    '''"adoption":{"const":"COMMITTED"}''',
    '''"adoption":{"enum":["COMMITTED","VERIFIED_EXISTING"]}''',
)
replace_once(
    ".adwf/schemas/external-consumer-upgrade-proof.schema.json",
    '''"label":{"enum":["ADOPTION_A","UPGRADE_B","ROLLBACK_A","RETRY_B"]}''',
    '''"label":{"enum":["ADOPTION_A","CONNECTED_A","UPGRADE_B","ROLLBACK_A","RETRY_B"]}''',
)

# External proof tests: keep unconnected behavior and add installed-consumer
# success + fail-closed foreign installation coverage.
replace_once(
    ".adwf/tests/test_external_consumer_upgrade_proof.py",
    '''from lib.external_consumer_upgrade_proof import (
''',
    '''from lib.consumer_installation import RECORD_REL, build_record, seal_record, write_record
from lib.external_consumer_upgrade_proof import (
''',
)
replace_once(
    ".adwf/tests/test_external_consumer_upgrade_proof.py",
    '''if __name__ == "__main__":
    unittest.main()
''',
    '''    def _connected_fixture(self):
        fixture = list(self._fixture())
        temp, source, target, consumer, source_sha, source_tree, target_sha, target_tree, consumer_sha, consumer_tree = fixture
        plan = proof_mod.plan_adoption(source, consumer, source_revision=source_sha)
        self.assertEqual(plan["status"], "READY")
        adoption = proof_mod.apply_adoption(source, consumer, plan)
        self.assertEqual(adoption["status"], "COMMITTED")
        profile = proof_mod.apply_consumer_profile(
            consumer, source, product_name="Real Product", default_branch="main",
            repository_visibility="PUBLIC",
        )
        self.assertIn(profile["status"], {"APPLIED", "ALREADY_MATERIALIZED"})
        record = build_record(
            source, consumer, adoption, consumer_repository="owner/real-product",
            consumer_base_sha=consumer_sha, consumer_base_tree=consumer_tree,
        )
        write_record(record, consumer, source)
        runtime = consumer / ".adwf-runtime"
        if runtime.exists():
            shutil.rmtree(runtime)
        self._git(consumer, "add", "-A")
        self._git(consumer, "commit", "-q", "-m", "connected consumer installed A")
        fixture[-2] = self._git(consumer, "rev-parse", "HEAD")
        fixture[-1] = self._git(consumer, "rev-parse", "HEAD^{tree}")
        return tuple(fixture)

    def test_connected_installed_consumer_uses_durable_proof_without_readoption(self):
        fixture = self._connected_fixture()
        try:
            with patch.object(proof_mod, "apply_adoption", side_effect=AssertionError("connected proof must not re-adopt")) as adoption:
                report = self._run(fixture)
            adoption.assert_not_called()
            self.assertEqual(report["transitions"]["adoption"], "VERIFIED_EXISTING")
            self.assertEqual(
                [item["label"] for item in report["preservation_checkpoints"]],
                ["CONNECTED_A", "UPGRADE_B", "ROLLBACK_A", "RETRY_B"],
            )
            self.assertLess(
                report["preservation_checkpoints"][0]["file_count"],
                report["consumer"]["tracked_regular_file_count"],
            )
            self.assertTrue(report["external_source_unchanged"])
            self.assertFalse(report["write_back_performed"])
            self.assertEqual(validate_external_consumer_upgrade_proof(report, fixture[2]), [])
        finally:
            fixture[0].cleanup()

    def test_connected_foreign_installation_blocks_without_readoption(self):
        fixture = list(self._connected_fixture())
        try:
            consumer = fixture[3]
            path = consumer / RECORD_REL
            value = json.loads(path.read_text(encoding="utf-8"))
            value["consumer"]["repository"] = "foreign/consumer"
            value = seal_record(value)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
            self._git(consumer, "add", RECORD_REL)
            self._git(consumer, "commit", "-q", "-m", "foreign installation binding")
            fixture[-2] = self._git(consumer, "rev-parse", "HEAD")
            fixture[-1] = self._git(consumer, "rev-parse", "HEAD^{tree}")
            with patch.object(proof_mod, "apply_adoption") as adoption:
                with self.assertRaisesRegex(
                    ExternalConsumerUpgradeProofError,
                    "CONNECTED_INSTALLATION_INVALID:INSTALLATION_CONSUMER_REPOSITORY_MISMATCH",
                ):
                    self._run(tuple(fixture))
            adoption.assert_not_called()
        finally:
            fixture[0].cleanup()


if __name__ == "__main__":
    unittest.main()
''',
)

# Upgrade transaction tests prove explicit connected repository identity wins
# over unrelated ambient executor identity but still cannot substitute a foreign repo.
replace_once(
    ".adwf/tests/test_consumer_upgrade_transaction.py",
    '''if __name__ == "__main__": unittest.main()
''',
    '''    def test_25_explicit_installation_repository_ignores_unrelated_ambient_executor_repo(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        try:
            self.publish_installation_record(source, consumer, snapshot, repository="example/consumer")
            shutil.rmtree(consumer / ".adwf-runtime")
            with patch("lib.consumer_upgrade_transaction.detect_repository", return_value="executor/adwf"), \\
                 patch("lib.consumer_installation._git") as git:
                git.side_effect = lambda _root, *args: (
                    snapshot["source_revision"] if args == ("rev-parse", "HEAD") else "c" * 40
                )
                result = self.apply(
                    source, target, consumer, compatibility, plan, snapshot,
                    consumer_repository="example/consumer",
                )
            self.assertEqual(result["status"], "COMMITTED")
            self.assert_b(target, consumer)
        finally:
            temp.cleanup()

    def test_26_explicit_foreign_installation_repository_blocks_before_write(self):
        temp, source, target, consumer, snapshot, compatibility, plan = prepared_transaction(ROOT)
        try:
            self.publish_installation_record(source, consumer, snapshot, repository="example/consumer")
            shutil.rmtree(consumer / ".adwf-runtime")
            with patch("lib.consumer_upgrade_transaction.detect_repository", return_value="example/consumer"), \\
                 patch("lib.consumer_installation._git") as git:
                git.side_effect = lambda _root, *args: (
                    snapshot["source_revision"] if args == ("rev-parse", "HEAD") else "c" * 40
                )
                with self.assertRaisesRegex(
                    ConsumerUpgradeError,
                    "UPGRADE_APPLY_SOURCE_INSTALLATION_PROVENANCE_INVALID:INSTALLATION_CONSUMER_REPOSITORY_MISMATCH",
                ):
                    self.apply(
                        source, target, consumer, compatibility, plan, snapshot,
                        consumer_repository="foreign/consumer",
                    )
            self.assertFalse((consumer / ".adwf-runtime/consumer-upgrade").exists())
            self.assert_a(source, consumer)
        finally:
            temp.cleanup()


if __name__ == "__main__": unittest.main()
''',
)

print("UPGRADE_PROOF_CONNECTED semantic patch materialized")
