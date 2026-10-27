#!/usr/bin/env bash
# Run the CI Python jobs in disposable containers, with no writable host mount.
set -euo pipefail
repository=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
temporary=$(mktemp -d "${TMPDIR:-/tmp}/acenix-python-check.XXXXXX")
trap 'rm -rf "$temporary"' EXIT
certificate=${PIP_CERT:-${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}}
mounts=(--mount "type=bind,src=$repository,dst=/source,readonly")
if [[ -f "$certificate" ]]; then
    cp "$certificate" "$temporary/ca.crt"
    mounts+=(--mount "type=bind,src=$temporary/ca.crt,dst=/tmp/acenix-ca.crt,readonly")
    certificate_flags=(-e PIP_CERT=/tmp/acenix-ca.crt -e SSL_CERT_FILE=/tmp/acenix-ca.crt)
else
    certificate_flags=()
fi
versions=("$@")
if [[ ${#versions[@]} == 0 ]]; then versions=(3.11 3.12 3.13); fi
for version in "${versions[@]}"; do
    case "$version" in 3.11|3.12|3.13) ;; *) echo "Unsupported Python test version: $version" >&2; exit 2 ;; esac
    docker run --rm --cpus=1 --memory=2g "${mounts[@]}" "${certificate_flags[@]}" \
        -e HTTPS_PROXY -e HTTP_PROXY -e NO_PROXY "python:$version-slim" \
        sh -eu -c '
          cp -R /source /tmp/project
          cd /tmp/project
          python -m pip install --disable-pip-version-check --no-cache-dir -r requirements-test.txt -e .
          python -m pytest -q -m "not nix"
          ruff check --select F .
          ruff format --check .
          python -m build --outdir /tmp/acenix-dist
          cd /tmp
          python -m venv /tmp/installed
          /tmp/installed/bin/pip install --disable-pip-version-check --no-deps /tmp/acenix-dist/*.whl
          /tmp/installed/bin/acenix --version
          /tmp/installed/bin/acenix --help >/dev/null
        '
done
