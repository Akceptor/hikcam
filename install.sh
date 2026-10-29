#!/bin/bash
set -euo pipefail

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

if ! [ -e /opt/homebrew/lib/libusb-1.0.dylib ]; then
  echo "installing libusb via Homebrew"
  brew install libusb
fi

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -q --upgrade pip
"$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"

mkdir -p "$BIN_DIR"
ln -sf "$ROOT/bin/hikcam" "$BIN_DIR/hikcam"
chmod +x "$ROOT/bin/hikcam"

echo "installed: $BIN_DIR/hikcam"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: $BIN_DIR is not on PATH" ;;
esac
