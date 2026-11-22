"""Publish a verified APK to a public repository, keeping the release draft until its digest matches."""
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

repo = os.environ["GITHUB_REPOSITORY"]
commit = os.environ["GITHUB_SHA"]
run = os.environ["GITHUB_RUN_ID"]
attempt = os.environ["GITHUB_RUN_ATTEMPT"]
if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit) or not all(re.fullmatch(r"[1-9]\d*", n) for n in (run, attempt)):
    raise ValueError("Unexpected GitHub release coordinates")
tag = f"exact-android-{run}-{attempt}"
api = f"https://api.github.com/repos/{repo}/releases"
filename = "android-production.apk"

def request(url, method="GET", data=None, mime="application/json"):
    allowed = [api, f"https://uploads.github.com/repos/{repo}/releases/"]
    if not any(url.startswith(prefix) for prefix in allowed):
        raise ValueError("Unexpected GitHub Releases endpoint")
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
               "Content-Type": mime, "User-Agent": "disposabl-exact"}
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(url, method=method, data=data, headers=headers), timeout=90) as response:
        return json.load(response)

def prepare_locked():
    state = Path(os.environ["RUNNER_TEMP"]) / "android-draft-release.json"
    if state.is_file():
        return request(f"{api}/{json.loads(state.read_text())['id']}")
    try:
        release = request(api, "POST", {"tag_name": tag, "target_commitish": commit, "name": f"Android build {run}.{attempt}",
                                      "draft": True, "prerelease": True, "make_latest": "false"})
    except urllib.error.HTTPError as error:
        if error.code != 422:
            raise
        release = request(f"{api}/tags/{tag}")
    if release["tag_name"] != tag or release["target_commitish"] != commit:
        raise ValueError("Release tag already belongs to another build")
    tmp = state.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"id":release["id"]}))
    tmp.replace(state)
    return release

def prepare():
    # 'publish' can race a slow draft reservation on first builds.
    lock = Path(os.environ["RUNNER_TEMP"]) / "android-draft-release.lock"
    with lock.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return prepare_locked()

def publish(path):
    release = prepare()
    if release["tag_name"] != tag or release["target_commitish"] != commit:
        raise ValueError("Unexpected release identity")
    payload = Path(path).read_bytes()
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    assets = release["assets"]
    if any(asset["name"] != filename for asset in assets) or len(assets) > 1:
        raise ValueError("Unexpected release assets")
    if assets:
        asset = assets[0]
    elif release["draft"]:
        endpoint = f"https://uploads.github.com/repos/{repo}/releases/{release['id']}/assets?name={filename}"
        asset = request(endpoint, "POST", payload, "application/vnd.android.package-archive")
    else:
        raise ValueError("Published release has no APK")
    url = f"https://github.com/{repo}/releases/download/{tag}/{filename}"
    if (asset["state"], asset["size"], asset["digest"], asset["name"]) != ("uploaded", len(payload), digest, filename):
        raise ValueError("Uploaded APK does not match the verified local APK")
    if release["draft"]:
        updated = request(f"{api}/{release['id']}", "PATCH", {"draft": False, "prerelease": True, "make_latest": "false"})
        if updated["draft"]:
            raise ValueError("Android release is still a draft")
        asset = next(a for a in updated["assets"] if a["id"] == asset["id"])
    if asset["browser_download_url"] != url:
        raise ValueError("Unexpected published APK URL")
    # Exercise the recipient's anonymous URL without downloading the signed binary twice.
    probe = urllib.request.Request(url, headers={"Range": "bytes=0-3"})
    with urllib.request.urlopen(probe, timeout=30) as reply:
        if reply.read(4) != payload[:4]:
            raise ValueError("Public APK download does not match")
    return url

if sys.argv[1:] == ["prepare"]:
    prepare()
elif len(sys.argv) == 3 and sys.argv[1] == "publish":
    for attempt_index in range(3):
        try:
            print(publish(sys.argv[2]))
            break
        except (OSError, TimeoutError) as error:
            if (isinstance(error, urllib.error.HTTPError) and error.code not in (404, 408, 409, 422, 429, 500, 502, 503, 504)) or attempt_index == 2:
                raise
            # Re-read the draft and its assets before repeating any write whose response was lost.
            time.sleep(0.25 * (attempt_index + 1))
else:
    raise ValueError("Expected prepare or publish <verified.apk>")
