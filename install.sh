#!/bin/sh
# Install Kozu-Plugins on this machine: clone (or update) the repo, then `kozu install`.
#   sh install.sh [kozu install flags]     from a clone
#   KOZU_HOME=~/somewhere sh install.sh    clone somewhere other than ~/.kozu-plugins
set -eu
url="${KOZU_REPO:-https://github.com/thekozugroup/Kozu-Plugins.git}"
dest="${KOZU_HOME:-$HOME/.kozu-plugins}"
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v git >/dev/null || { echo "install.sh: git is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "install.sh: python3 is required" >&2; exit 1; }
if [ -f "$here/kozu" ] && [ -f "$here/plugins.json" ]; then
  dest=$here
elif [ -d "$dest/.git" ]; then
  git -C "$dest" pull --ff-only -q
else
  git clone -q "$url" "$dest"
fi
exec python3 "$dest/kozu" install "$@"
