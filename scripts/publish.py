"""Publish only approved Windows installers, never the private source tree."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib import error, parse, request
import zipfile

import yaml

REPOSITORY = "chanwhi123/My-Island-Updates"
CHANNEL = f"https://github.com/{REPOSITORY}/releases/latest/download/"
MAX_ARCHIVE = 1024 * 1024 * 1024
MAX_METADATA = 1024 * 1024
STABLE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
HEX256 = re.compile(r"^[a-fA-F0-9]{64}$")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not STABLE.fullmatch(value):
        raise ValueError("Expected a stable numeric application version")
    return tuple(map(int, value.split(".")))


def validate_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("Invalid artifact URL")
    url = parse.urlsplit(value)
    host = (url.hostname or "").lower()
    allowed = host.endswith(".blob.core.windows.net") or host.endswith(".actions.githubusercontent.com")
    if (url.scheme != "https" or not allowed or url.username or url.password
            or url.fragment or url.port not in (None, 443)):
        raise ValueError("Expected a signed HTTPS GitHub Actions artifact URL")
    return value


class SafeRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, expected_hash: str, destination: Path) -> None:
    validate_url(url)
    if not isinstance(expected_hash, str) or not HEX256.fullmatch(expected_hash):
        raise ValueError("Expected the GitHub artifact SHA256 digest")
    # URL is not part of an env block or shell command. Mask it before any I/O.
    print(f"::add-mask::{url}", flush=True)
    opener = request.build_opener(SafeRedirect())
    digest = hashlib.sha256()
    total = 0
    began = time.monotonic()
    try:
        with opener.open(request.Request(url, headers={"User-Agent": "My-Island-release"}), timeout=30) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_ARCHIVE:
                raise ValueError("Artifact archive is too large")
            with destination.open("wb") as output:
                while True:
                    chunk = response.read1(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE or time.monotonic() - began > 180:
                        raise ValueError("Artifact size or duration limit exceeded")
                    digest.update(chunk)
                    output.write(chunk)
    except error.HTTPError as exc:
        raise ValueError(f"Artifact download failed (HTTP {exc.code}); obtain a fresh signed URL") from None
    except (error.URLError, TimeoutError, OSError):
        raise ValueError("Artifact download failed; obtain a fresh signed URL") from None
    if digest.hexdigest().lower() != expected_hash.lower():
        raise ValueError("Artifact archive SHA256 mismatch")


def selected_entry(archive: zipfile.ZipFile, name: str, limit: int) -> zipfile.ZipInfo:
    entries = [item for item in archive.infolist() if item.filename == name]
    if len(entries) != 1:
        raise ValueError(f"Expected one root archive entry: {name}")
    entry = entries[0]
    if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > limit:
        raise ValueError("Unsafe or oversized installer artifact entry")
    return entry


def verify_and_extract(archive_path: Path, output: Path, expected_version: str, expected_commit: str) -> dict:
    version_tuple(expected_version)
    if not re.fullmatch(r"[a-fA-F0-9]{40}", expected_commit):
        raise ValueError("Expected the validated private source commit")
    installer = f"My-Island-{expected_version}-Windows-x64.exe"
    blockmap = installer + ".blockmap"
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        manifest_info = selected_entry(archive, "latest.yml", MAX_METADATA)
        metadata_info = selected_entry(archive, "update-release.json", MAX_METADATA)
        manifest_bytes = archive.read(manifest_info)
        manifest = yaml.safe_load(manifest_bytes)
        metadata = json.loads(archive.read(metadata_info))
        if not isinstance(manifest, dict) or manifest.get("version") != expected_version:
            raise ValueError("Unexpected update manifest version")
        files = manifest.get("files")
        if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict):
            raise ValueError("Expected exactly one Windows installer")
        file = files[0]
        if file.get("url") != installer:
            raise ValueError("Unsafe or unexpected installer path")
        if manifest.get("path", installer) != installer:
            raise ValueError("Legacy installer path mismatch")
        if (not isinstance(metadata, dict) or metadata.get("version") != expected_version
                or metadata.get("configured") is not True or metadata.get("channelUrl") != CHANNEL
                or metadata.get("installer") != installer
                or metadata.get("sourceCommit") != expected_commit):
            raise ValueError("Artifact is not configured for this approved distribution channel")
        executable_info = selected_entry(archive, installer, MAX_ARCHIVE)
        map_info = selected_entry(archive, blockmap, 64 * 1024 * 1024)
        if executable_info.file_size + map_info.file_size > MAX_ARCHIVE:
            raise ValueError("Expanded installer artifact is too large")
        sha512 = hashlib.sha512()
        count = 0
        with archive.open(executable_info) as source, (output / installer).open("wb") as destination:
            first = source.read(1024 * 1024)
            if first[:2] != b"MZ":
                raise ValueError("Installer is not a Windows executable")
            while first:
                sha512.update(first)
                count += len(first)
                destination.write(first)
                first = source.read(1024 * 1024)
        checksum = base64.b64encode(sha512.digest()).decode("ascii")
        if file.get("sha512") != checksum or manifest.get("sha512", checksum) != checksum:
            raise ValueError("Installer SHA512 mismatch")
        if file.get("size", count) != count or metadata.get("bytes") != count:
            raise ValueError("Installer size mismatch")
        (output / "latest.yml").write_bytes(manifest_bytes)
        with archive.open(map_info) as source, (output / blockmap).open("wb") as destination:
            while chunk := source.read(1024 * 1024):
                destination.write(chunk)
    # Intentionally do not extract update-release.json, source ZIPs, other ZIPs,
    # README files or arbitrary archive members into the public release.
    return {"version": expected_version, "installer": installer, "blockmap": blockmap, "bytes": count}


def gh(*args: str) -> str:
    result = subprocess.run(["gh", *args], check=False, capture_output=True, text=True, timeout=120)
    if result.returncode:
        # Do not reprint stderr: a signed URL must never enter release logs.
        raise ValueError("GitHub release operation failed; inspect repository permissions")
    return result.stdout


def assert_new_release(version: str) -> None:
    current = version_tuple(version)
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{REPOSITORY}/releases?per_page=100"))
    releases = [release for page in pages for release in page]
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        if tag == f"v{version}":
            raise ValueError("This stable release already exists")
        if tag.startswith("v") and STABLE.fullmatch(tag[1:]) and current <= version_tuple(tag[1:]):
            raise ValueError("A new release must be newer than existing stable releases")
    # A draft with the same tag must not accidentally be completed or replaced.
    if any(item.get("tag_name") == f"v{version}" for item in releases):
        raise ValueError("A release with this tag already exists")


def main() -> None:
    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("This workflow is limited to the approved distribution repository")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    inputs = event.get("inputs", {})
    version = inputs.get("expected_version", "")
    version_tuple(version)
    run_id = inputs.get("source_run_id", "")
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise ValueError("Expected a successful private build run ID")
    commit = inputs.get("source_commit", "")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-fA-F0-9]{40}", commit):
        raise ValueError("Expected the validated private source commit")
    assert_new_release(version)
    with tempfile.TemporaryDirectory(prefix="my-island-publish-") as temporary:
        root = Path(temporary)
        archive_path = root / "artifact.zip"
        download(inputs.get("signed_artifact_url", ""), inputs.get("artifact_sha256", ""), archive_path)
        result = verify_and_extract(archive_path, root / "release", version, commit)
        files = [str(root / "release" / name) for name in [result["installer"], result["blockmap"], "latest.yml"]]
        gh("release", "create", f"v{version}", *files, "--repo", REPOSITORY,
           "--title", f"My Island v{version}", "--notes", "Windows installation files and automatic-update metadata. Source remains private.")
        print(f"Published My Island v{version}: Windows installer, block map and update metadata.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile, yaml.YAMLError, subprocess.TimeoutExpired):
        # Keep failure useful but never interpolate exception values or URL query.
        print("Release rejected: verify the fresh signed artifact, expected version, channel and repository access.", file=sys.stderr)
        raise SystemExit(1)
