from __future__ import annotations
from pathlib import Path
import hashlib, json, shutil, tempfile

from lib.consumer_profile import apply_consumer_profile
from lib.managed_surface import load_source_inventory

A = "a" * 40
B = "b" * 40

FRAMEWORK_FILES = (
    ".adwf/config.json",
    ".adwf/managed-surface-policy.json",
    ".adwf/consumer-upgrade-migrations.json",
    ".adwf/packs/apps-script.json",
    ".adwf/schemas/config.schema.json",
    ".adwf/schemas/consumer-profile.schema.json",
    ".adwf/schemas/project-pack.schema.json",
    ".adwf/schemas/managed-surface-policy.schema.json",
    ".adwf/schemas/managed-surface-snapshot.schema.json",
    ".adwf/schemas/consumer-upgrade-migrations.schema.json",
    ".adwf/schemas/consumer-upgrade-compatibility.schema.json",
    ".adwf/schemas/consumer-upgrade-plan.schema.json",
)


def seal_inventory(root: Path) -> None:
    files = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_file() and path.name not in {"MANIFEST.json", "SHA256SUMS.txt"}
    )
    manifest = {
        "framework": "AI Development Framework", "version": "test", "schema_version": 3,
        "scope": "FRAMEWORK_OWNED_TRUST_BOUNDARY", "file_count_excluding_manifests": len(files),
        "total_bytes_excluding_manifests": sum((root / rel).stat().st_size for rel in files), "files": files,
    }
    manifest_path = root / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sums = files + ["MANIFEST.json"]
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256((root / rel).read_bytes()).hexdigest()}  {rel}\n" for rel in sorted(sums)), encoding="utf-8"
    )


def build_framework(root: Path, canonical_root: Path) -> None:
    for rel in FRAMEWORK_FILES:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((canonical_root / rel).read_bytes())
    policy_path = root / ".adwf/managed-surface-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["shared_guarded_paths"] = ["README.md"]
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / ".adwf/private.txt").write_text("private-v1\n", encoding="utf-8")
    (root / "README.md").write_text("shared-v1\n", encoding="utf-8")
    (root / "skills").mkdir(parents=True, exist_ok=True)
    (root / "skills/registry.json").write_text(json.dumps({
        "schema_version": 1, "generated": True, "startup_routers": [],
        "skills": [{"id": "bound-skill", "package_sha256": "1" * 64}],
    }, indent=2) + "\n", encoding="utf-8")
    seal_inventory(root)


def prepared(canonical_root: Path):
    temp = tempfile.TemporaryDirectory()
    base = Path(temp.name)
    source, target, consumer = base / "source", base / "target", base / "consumer"
    source.mkdir(); consumer.mkdir()
    build_framework(source, canonical_root)
    shutil.copytree(source, target)
    (consumer / "appsscript.json").write_text('{"timeZone":"Etc/UTC"}\n', encoding="utf-8")
    inventory = load_source_inventory(source)
    for rel in inventory["files"]:
        dst = consumer / rel; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes((source / rel).read_bytes())
    snapshot = {
        "$schema": ".adwf/schemas/managed-surface-snapshot.schema.json", "schema_version": 1,
        "role": "MANAGED_SURFACE_SNAPSHOT", "source_revision": A,
        "source_manifest_sha256": inventory["manifest_sha256"],
        "entries": [{
            "path": rel, "ownership": "SHARED_GUARDED" if rel in inventory["shared"] else "FRAMEWORK_PRIVATE",
            "installed_sha256": inventory["sums"][rel], "managed_by_adwf": True,
        } for rel in inventory["files"]],
    }
    profile = apply_consumer_profile(consumer, source, product_name="Upgrade Fixture", default_branch="main", repository_visibility="PRIVATE")
    if profile["status"] != "APPLIED":
        raise AssertionError(profile)
    return temp, source, target, consumer, snapshot
