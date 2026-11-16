# IDA Chats für Android (APK)

Die Chats-App als installierbare Android-App. Ordner `android/`, gebaut mit **Tauri 2 (Rust)**: eine schlanke Hülle, die
`https://chats.kulbarts.com` in der System-WebView von Android zeigt. Es gibt nur eine Oberfläche (`frontend/chats`) – jede Änderung
an der Website ist sofort auch in der App, ohne neue APK.

**Strom:** Die App hat keinen Hintergrunddienst, keinen Timer und keine offene Verbindung, solange sie zu ist. Der Live-Abgleich
läuft nur, solange sie sichtbar ist (so macht es die Website selbst).

## Stand

| Schritt | Stand |
| --- | --- |
| 1 Gerüst (Rust/Tauri, WebView auf die Chats-Seite) | fertig |
| 2 Anmeldung (Cloudflare Access in der WebView, Cookie bleibt) | über die WebView; am Gerät prüfen |
| 3 Benachrichtigungen über FCM | offen – braucht ein Firebase-Projekt |
| 4 Antworten / Gelesen direkt aus der Meldung | offen |
| 5 Teilen aus anderen Apps, Zähler am Symbol | offen (Mikrofon- und Kamera-Recht sind schon gesetzt) |
| 7 Bauen, Signieren, Release über GitHub Actions | fertig |
| In-App-Hinweis auf eine neuere Version | offen |

## Herunterladen und installieren

`https://github.com/L8teNever/IDA-PA/releases/latest/download/ida-chats.apk` im Browser am Handy öffnen, Datei antippen und
„Aus dieser Quelle zulassen“ bestätigen. Ein Update wird einfach über die vorhandene App installiert (gleicher Signierschlüssel,
höhere Versionsnummer).

## Bauen

`.github/workflows/android.yml` läuft bei jedem Push auf `main`, der `android/` ändert (oder von Hand über „Run workflow“):

1. Android-SDK 34, Build-Tools, NDK und Rust mit den Android-Zielen (arm64, armv7) einrichten.
2. Version auf `1.0.<Laufnummer>` setzen – Android erlaubt ein Update nur mit höherer Nummer.
3. `tauri icon` (aus `frontend/chats/icons/icon-512.png`) und `tauri android init` erzeugen das Android-Projekt (`src-tauri/gen/android`,
   liegt nicht im Repo). `android/scripts/patch-android.sh` trägt danach die Rechte für Mikrofon und Kamera ins Manifest ein.
4. `tauri android build --apk`, dann `zipalign` und `apksigner`.
5. Die fertige `ida-chats.apk` wird als Release `android-v1.0.<Laufnummer>` abgelegt.

## Signierschlüssel

Liegt als PKCS12 in den Repo-Secrets `ANDROID_KEYSTORE_B64` (Base64) und `ANDROID_KEYSTORE_PASSWORD`, Alias `ida`. Secrets lassen
sich nicht wieder auslesen; eine Sicherung liegt auf dem Server neben den Code-Projekten unter `.android-keys/` (nicht im Repo).
Geht der Schlüssel verloren, lassen sich neue Versionen nur nach Deinstallieren der alten App installieren.

## Einstellungen

- Adresse der Seite, Name, Kennung (`com.kulbarts.chats`): `android/src-tauri/tauri.conf.json`.
- Die WebView meldet sich als normales Chrome am Handy (`userAgent`), weil Google die Anmeldung in eingebetteten Browsern sonst ablehnt.
