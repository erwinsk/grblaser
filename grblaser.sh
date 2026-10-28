#!/usr/bin/env bash
# Jalankan Grblaser dari folder proyek ini: ./grblaser.sh [file ...]
cd "$(dirname "$(readlink -f "$0")")" || exit 1
exec python3 -m grblaser "$@"
