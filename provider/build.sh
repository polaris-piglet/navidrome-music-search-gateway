#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
output=${1:?output binary absolute path required}
[[ $output = /* ]] || exit 2
version=v1.1.1-0.20261018010056-7cc4467b4e30
go mod download github.com/guohuiyuan/music-lib@"$version"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cp -R "$(go env GOMODCACHE)/github.com/guohuiyuan/music-lib@$version" "$work/music-lib"
chmod -R u+w "$work/music-lib"
python3 patch-library.py "$work/music-lib"
cp go.mod "$work/build.mod"
cp go.sum "$work/build.sum"
go mod edit -modfile="$work/build.mod" -replace="github.com/guohuiyuan/music-lib=$work/music-lib"
CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH:-amd64}" go build -modfile="$work/build.mod" -trimpath -o "$output" .
