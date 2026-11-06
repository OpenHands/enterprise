# Test-only released-image backport

The main-line source and the tested release are recorded separately in [build-manifest.json](build-manifest.json). The production change consists of a new helper and a small remote-service hunk. The remote-service file shipped in the original 1.79.0 image matched its git tag byte-for-byte (`95c3978e77fd106225c26871e3fdecb206850d03d06078b64aeeb2caa29c6d33`).

From a checkout containing the reviewed source and the `1.79.0` tag, prepare a new temporary Docker context:

```bash
OHE3895_BUILD_DIR=$(mktemp -d)
mkdir -p "$OHE3895_BUILD_DIR/openhands/app_server/sandbox"
git show 1.79.0:openhands/app_server/sandbox/remote_sandbox_service.py > "$OHE3895_BUILD_DIR/openhands/app_server/sandbox/remote_sandbox_service.py"
python - "$OHE3895_BUILD_DIR" <<'PYTHON'
import json, sys
from pathlib import Path
patch = json.loads(Path('.pr/image/backport.patch.json').read_text())['patch']
Path(sys.argv[1], 'backport.patch').write_text(patch)
PYTHON
git apply --unsafe-paths --directory "$OHE3895_BUILD_DIR" "$OHE3895_BUILD_DIR/backport.patch"
cp openhands/app_server/sandbox/final_metrics.py "$OHE3895_BUILD_DIR/openhands/app_server/sandbox/final_metrics.py"
cp .pr/image/Dockerfile "$OHE3895_BUILD_DIR/Dockerfile"
docker build --platform linux/amd64 --provenance=false -t ohe3895-enterprise:local-review "$OHE3895_BUILD_DIR"
```

Check prepared source hashes against the manifest before using the image. The base's dependencies, migrations and entrypoint are untouched. This is an isolated demo verification image, not an upstream release tag. Independent builds need not produce identical image bytes; the captured live digest identifies the image that was actually tested.

For authorized deployment, save the current release's values privately and override only `image.repository` and `image.tag` through Helm. Preserve chart/auth/model/runtime settings. Do not use protected or customer deployments. The recorded EKS auth-lab revision is 11 and personal GKE demo revision is 8; rollback source-image settings are retained privately from revisions 9 and 6 respectively, before this task's image overrides.
