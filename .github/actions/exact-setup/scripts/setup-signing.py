"""Provision missing mobile update keys with the user's existing GitHub CLI login."""
import argparse
import base64
import json
import os
import secrets
from pathlib import Path
import subprocess
import tempfile

ENVIRONMENT = "android-production"


def gh(*args, payload=None):
    return subprocess.check_output(["gh", *args], text=True,
                                   input=json.dumps(payload) if payload is not None else None).strip()


def generate_for_runner():
    from nacl.public import PublicKey, SealedBox

    destination = Path(os.environ["RUNNER_TEMP"], "sealed")
    destination.mkdir(exist_ok=True)
    for environment, recipient in json.loads(os.environ["RECIPIENTS"]).items():
        if environment != ENVIRONMENT:
            raise ValueError("Unknown signing environment")
        box = SealedBox(PublicKey(base64.b64decode(recipient["key"], validate=True)))
        for name in recipient["names"]:
            if name == "EXACT_UPDATE_SEED":
                plaintext = base64.b64encode(secrets.token_bytes(32))
            elif name in ("ANDROID_APK_SIGNING", "ANDROID_PLAY_UPLOAD_SIGNING"):
                password = secrets.token_hex(32)
                with tempfile.TemporaryDirectory() as directory:
                    keystore = Path(directory, "signing.p12")
                    subprocess.run([
                        "keytool", "-genkeypair", "-noprompt", "-storetype", "PKCS12",
                        "-keystore", str(keystore), "-alias", "android", "-keyalg", "RSA",
                        "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android release",
                        "-storepass:env", "PASS", "-keypass:env", "PASS",
                    ], env=dict(os.environ, PASS=password), check=True)
                    plaintext = json.dumps({
                        "password": password, "keystore": base64.b64encode(keystore.read_bytes()).decode(),
                    }).encode()
            else:
                raise ValueError(f"Unknown signing secret for {environment}")
            (destination / f"{environment}-{name}.json").write_text(json.dumps({
                "key_id": recipient["key_id"], "encrypted_value": base64.b64encode(box.encrypt(plaintext)).decode(),
            }))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--play", action="store_true", help="also provision the optional Play upload key")
parser.add_argument("--generate-on-runner", action="store_true", help=argparse.SUPPRESS)
args = parser.parse_args()
if args.generate_on_runner:
    generate_for_runner()
    raise SystemExit

repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
ref = "main"
sha = gh("api", f"repos/{repo}/commits/{ref}", "--jq", ".sha")

path = f"repos/{repo}/environments/{ENVIRONMENT}/secrets"
existing = gh("api", path, "--paginate", "--jq", ".secrets[].name").splitlines()
wanted = ["EXACT_UPDATE_SEED", "ANDROID_APK_SIGNING"]
if args.play:
    wanted.append("ANDROID_PLAY_UPLOAD_SIGNING")
missing = [name for name in wanted if name not in existing]
if not missing:
    print("All requested signing keys are already configured.")
    raise SystemExit

recipient = {**json.loads(gh("api", path + "/public-key")), "names": missing}
run_url = gh("workflow", "run", "deploy.yml", "--repo", repo, "--ref", ref, "--json",
             payload={"recipients": json.dumps({ENVIRONMENT: recipient})})
run = run_url.rstrip("/").split("/")[-1]
if not run.isdecimal():
    raise ValueError(f"Unexpected GitHub workflow response: {run_url}")
print(f"Generating signing keys in {run_url}", flush=True)
gh("run", "watch", run, "--repo", repo, "--exit-status", "--interval", "5")
if gh("run", "view", run, "--repo", repo, "--json", "headSha,workflowName,event",
      "--jq", '[.headSha,.workflowName,.event] | join(" ")') != f"{sha} Deploy app workflow_dispatch":
    raise ValueError("Signing run does not match the selected workflow and commit")
with tempfile.TemporaryDirectory() as directory:
    gh("run", "download", run, "--repo", repo, "--name", "sealed-keys", "--dir", directory)
    for name in missing:
        sealed = json.loads(Path(directory, f"{ENVIRONMENT}-{name}.json").read_text())
        if sealed["key_id"] != gh("api", path + "/public-key", "--jq", ".key_id"):
            raise ValueError("GitHub environment encryption key changed; run setup again")
        if name in gh("api", path, "--paginate", "--jq", ".secrets[].name").splitlines():
            print(f"Keeping {ENVIRONMENT}/{name}")
            continue
        gh("api", "--method", "PUT", f"{path}/{name}", "--input", "-", payload=sealed)
        print(f"Stored {ENVIRONMENT}/{name}")
