#!/usr/bin/env bash
# Passt das von "tauri android init" erzeugte Android-Projekt an (gen/android liegt nicht im Repo).
set -euo pipefail
M="src-tauri/gen/android/app/src/main/AndroidManifest.xml"
add() { grep -q "android.permission.$1\"" "$M" || sed -i "s#<application#<uses-permission android:name=\"android.permission.$1\" />\n    <application#" "$M"; }
add RECORD_AUDIO            # Sprachnachrichten aufnehmen
add MODIFY_AUDIO_SETTINGS
add CAMERA                  # Foto/Video direkt aufnehmen
grep -n "uses-permission" "$M"
