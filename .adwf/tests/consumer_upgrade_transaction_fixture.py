from __future__ import annotations
from pathlib import Path
import json, shutil, tempfile
from unittest.mock import patch

from consumer_upgrade_fixture import A, B, build_framework, seal_inventory
from lib.consumer_profile import apply_consumer_profile
from lib.consumer_upgrade import build_upgrade_compatibility, plan_consumer_upgrade
from lib.managed_surface import plan_adoption
from lib.managed_surface_transaction import apply_adoption


def prepared_transaction(
    canonical_root: Path, *, preserve_shared: bool = False, preserve_agents_router: bool = False, change_profile: bool = True
):
    temp = tempfile.TemporaryDirectory(); base = Path(temp.name)
    source, target, consumer = base / "source", base / "target", base / "consumer"
    source.mkdir(); consumer.mkdir(); build_framework(source, canonical_root)
    # Dedicated removable framework-private fixture path.
    (source / ".adwf/remove-me.txt").write_text("remove-v1\n", encoding="utf-8"); seal_inventory(source)
    shutil.copytree(source, target)
    (consumer / "appsscript.json").write_text('{"timeZone":"Etc/UTC"}\n', encoding="utf-8")
    if preserve_shared:
        (consumer / "README.md").write_text("consumer-owned shared readme\n", encoding="utf-8")
    if preserve_agents_router:
        (consumer / "AGENTS.md").write_text("# Legacy consumer router\nproduct-specific rules remain consumer-owned\n", encoding="utf-8")
    adoption_plan = plan_adoption(source, consumer, source_revision=A)
    with patch("lib.managed_surface_transaction._verify_source_revision", return_value=None):
        adopted = apply_adoption(source, consumer, adoption_plan)
    if adopted["status"] != "COMMITTED": raise AssertionError(adopted)
    profile = apply_consumer_profile(consumer, source, product_name="Upgrade Fixture", default_branch="main", repository_visibility="PRIVATE")
    if profile["status"] != "APPLIED": raise AssertionError(profile)
    snapshot = adopted["snapshot"]

    # B exercises REPLACE + CREATE + REMOVE and forces exact profile transition
    # through a harmless config-file byte change while preserving valid semantics.
    (target / ".adwf/private.txt").write_text("private-v2\n", encoding="utf-8")
    if preserve_agents_router:
        (target / "AGENTS.md").write_text("# Framework package AGENTS revision v2\n", encoding="utf-8")
    if change_profile:
        config = target / ".adwf/config.json"
        value = json.loads(config.read_text(encoding="utf-8"))
        config.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (target / ".adwf/new-target.txt").write_text("new-v2\n", encoding="utf-8")
    (target / ".adwf/remove-me.txt").unlink()
    seal_inventory(target)
    with patch("lib.consumer_upgrade._verify_revision", return_value=None):
        compatibility = build_upgrade_compatibility(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot)
        plan = plan_consumer_upgrade(source, target, consumer, source_revision=A, target_revision=B, snapshot=snapshot)
    if compatibility["status"] != "PASS" or plan["status"] != "READY":
        raise AssertionError((compatibility["status"], plan["status"], compatibility["findings"]))
    return temp, source, target, consumer, snapshot, compatibility, plan
