# musescore-stylus-ci

Build harness for the MuseScore stylus fork on aarch64, for the Boox Note Air 4C.

It contains **no source**. The workflow checks out
`sebastianstupak/MuseScore` at whatever ref you give it and builds an aarch64
AppImage plus the bare `mscore4portabledev` executable.

It exists because GitHub disables Actions on forked repositories behind a
UI-only gate (`422 Actions has been disabled for this repository` from every
API route). This repo is not a fork, so there is no gate.

    gh workflow run build-arm64.yml -f ref=stylus-4.7-ink-damage-rect

Artifacts: `appimage-aarch64` (install with `MS_FORK_APPIMAGE=... ./setup 69`)
and `mscore4portabledev-aarch64` (install in ~30 s with
`scripts/67-fork-devdeploy.sh`, see runbook 13 in the boox repo).
