from __future__ import annotations

from pathlib import Path


LEGACY_PROFILE = "research"


def validate_profile(profile: str) -> str:
    value = str(profile).strip()
    if value not in {"research", "freqtrade"}:
        raise ValueError(f"Unsupported execution profile: {profile}")
    return value


def profile_results_dir(storage: Path, profile: str) -> Path:
    return storage / "results" / "profiles" / validate_profile(profile)


def artifact_path(storage: Path, profile: str, name: str) -> Path:
    return profile_results_dir(storage, profile) / name


def legacy_artifact_path(storage: Path, name: str) -> Path:
    return storage / "results" / name
