#!/usr/bin/env python3
"""Prevent the public DLL-download workflow from drifting back to a partial build.

GitHub only validates workflow YAML, not this repository's distribution contract:
every push to main must build every ME3-loadable DLL, publish an immutable main
prerelease, and publish numbered releases only after their assets are attached.
This scanner has no YAML dependency because it runs in the local and CI Python
baseline.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
RELEASE_CONFIG = REPO_ROOT / "release-please-config.json"
RELEASE_MANIFEST = REPO_ROOT / ".release-please-manifest.json"


def job_body(text: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", text, re.MULTILINE | re.DOTALL
    )
    if not match:
        raise AssertionError(f"missing workflow job: {name}")
    return match.group(1)


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise AssertionError(f"missing {label}: {needle!r}")


def check(text: str) -> None:
    """Every assertion about the workflow, raising on the first failure.

    Split out from `main` so `--selftest` can feed it a deliberately broken workflow and prove
    each assertion fires. A scanner that passes on the real file tells you nothing about
    whether it would notice the file changing, which is its entire job.
    """
    try:
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
        manifest = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
        assert config["release-type"] == "simple"
        assert re.fullmatch(r"[0-9a-f]{40}", config["bootstrap-sha"])
        assert config["draft"] is True
        assert config["force-tag-creation"] is True
        assert config["include-v-in-tag"] is False
        assert config["packages"] == {".": {}}
        assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["."])

        require(text, "branches: [main]", "main push trigger")

        release_please = job_body(text, "release-please")
        require(release_please, "config-file: release-please-config.json", "release config")
        require(release_please, "manifest-file: .release-please-manifest.json", "release manifest")

        build = job_body(text, "build-attest-publish-main")
        if re.search(r"^    needs:", build, re.MULTILINE):
            raise AssertionError("main build must not depend on Release Please")
        require(build, "bash scripts/er-build-dlls.sh --all", "all-DLL build command")
        require(build, "scripts/me3-dll-list.py --pairs", "dynamic artifact list")
        require(build, "actions/attest@v4", "DLL attestation")
        require(build, "actions/upload-artifact@v4", "versioned-release handoff")
        # The installer is the file an ordinary player downloads, and every way it can go
        # wrong here is silent rather than red.
        #
        # Order matters: it embeds the DLLs, so `build.rs` reads them off disk and they have to
        # exist first. Reordering these two steps is a build error naming the missing files,
        # which is loud -- but only if the order is actually checked, so it is checked.
        dll_build = build.index("bash scripts/er-build-dlls.sh --all")
        # The assignment, not the name. This searched for the bare name until the selftest
        # caught it: the workflow's own comment explaining why the variable matters contains
        # it, so the check passed on prose while the `env:` block could have been deleted.
        installer_build = build.find("ER_INSTALLER_EMBED_DIR: ")
        if installer_build < 0:
            raise AssertionError(
                "the installer is built without ER_INSTALLER_EMBED_DIR, which produces a "
                "working binary that offers every mod and can install none of them"
            )
        if installer_build < dll_build:
            raise AssertionError(
                "the installer is built before the DLLs it embeds; build.rs reads them off disk"
            )
        require(build, "-p er-installer", "installer build")
        require(
            build,
            "cargo xwin build --release --target x86_64-pc-windows-msvc -p er-installer",
            "Windows installer build",
        )
        # The binary is asked whether it carries the payload, rather than the environment being
        # trusted to have been set. This is the only step that can catch a release built
        # without it, and without this line nothing would notice its removal.
        require(build, "er-installer --selfcheck", "installer self-check")
        require(
            build,
            "cp target/x86_64-pc-windows-msvc/release/er-installer.exe dist/",
            "staged Windows installer",
        )
        require(build, "cp target/release/er-installer dist/er-installer", "staged Linux installer")
        require(build, "chmod +x dist/er-installer", "executable bit on the Linux installer")
        for subject in ("dist/er-installer.exe", "dist/er-installer\n"):
            require(build, subject, f"attestation subject {subject.strip()}")
        require(build, "--notes-file", "release notes from a file")

        require(build, 'rolling_tag="main-${GITHUB_SHA}"', "unique rolling tag")
        require(build, 'gh release create "$rolling_tag" dist/*', "immutable prerelease creation")
        if "gh release edit \"$rolling_tag\"" in build or "--clobber" in build:
            raise AssertionError("immutable main prerelease must never be edited or clobbered")

        versioned = job_body(text, "upload-versioned-release")
        require(
            versioned,
            "needs: [release-please, build-attest-publish-main]",
            "versioned-release dependencies",
        )
        require(
            versioned,
            "needs.release-please.outputs.release_created == 'true'",
            "versioned-release condition",
        )
        require(versioned, "actions/download-artifact@v4", "staged-file retrieval")
        require(versioned, 'gh release upload "$tag_name" dist/*', "draft asset upload")
        require(versioned, 'gh release edit "$tag_name" --draft=false', "draft publication")
        if "--clobber" in versioned:
            raise AssertionError("immutable versioned release must not clobber assets")
    except (KeyError, json.JSONDecodeError) as err:
        raise AssertionError(err) from err


# Each entry breaks the workflow one way a careless edit plausibly would, and must be caught.
# The two halves of the installer's contract are here because both fail silently in production:
# a build without the environment variable ships a binary that installs nothing, and a missing
# self-check is what lets that reach a release page.
MUTATIONS: list[tuple[str, str, str]] = [
    (
        "the payload environment variable is dropped",
        "ER_INSTALLER_EMBED_DIR: ",
        "SOMETHING_ELSE: ",
    ),
    ("the self-check is dropped", "er-installer --selfcheck", "true"),
    (
        "the Linux installer stops being staged",
        "cp target/release/er-installer dist/er-installer",
        "true",
    ),
    ("the executable bit is dropped", "chmod +x dist/er-installer", "true"),
    ("the Windows installer leaves the attestation", "dist/er-installer.exe\n", "dist/gone.exe\n"),
    ("the all-DLL build is dropped", "bash scripts/er-build-dlls.sh --all", "true"),
    ("the prerelease stops being immutable", 'gh release create "$rolling_tag"', "gh release edit"),
]


def selftest(text: str) -> int:
    failures = 0
    try:
        check(text)
    except AssertionError as err:
        print(f"SELFTEST FAIL: the real workflow does not pass: {err}", file=sys.stderr)
        return 1

    for description, needle, replacement in MUTATIONS:
        if needle not in text:
            print(f"SELFTEST FAIL: {description!r} cannot be tested -- {needle!r} is not present")
            failures += 1
            continue
        try:
            check(text.replace(needle, replacement))
        except AssertionError:
            continue
        print(f"SELFTEST FAIL: not caught -- {description}")
        failures += 1

    if failures:
        print(f"selftest: {failures} case(s) failed")
        return 1
    print(f"selftest: {len(MUTATIONS)} mutations caught, and the real workflow passes")
    return 0


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")
    if "--selftest" in sys.argv[1:]:
        return selftest(text)
    try:
        check(text)
    except AssertionError as err:
        print(f"[test-release-workflow] FAIL: {err}", file=sys.stderr)
        return 1

    print(
        "[test-release-workflow] ok -- main and numbered releases publish all DLLs immutably, "
        "and both self-contained installers with them"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
