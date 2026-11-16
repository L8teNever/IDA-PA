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
| 3 Benachrichtigungen über FCM | läuft (Probemeldung am 16.11.2026 angekommen) |
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
   liegt nicht im Repo). `android/scripts/patch-android.py` trägt danach Rechte (Mikrofon, Kamera, Benachrichtigungen), Firebase
   Messaging, die Brücke zur Seite (`android/kotlin/Push.kt`), das Öffnen von Links (`android/kotlin/Links.kt`) und das
   App-Symbol (`android/icons/`) ein.
4. `tauri android build --apk`, dann `zipalign` und `apksigner`.
5. Die fertige `ida-chats.apk` wird als Release `android-v1.0.<Laufnummer>` abgelegt.

## Name, Symbol und Links

- Die App heißt auf dem Handy nur **„Chats“** (`productName` in `android/src-tauri/tauri.conf.json`).
- **Symbol:** Android zeigt bei adaptiven Symbolen nur die Mitte des Vordergrunds (66 von 108 dp, oft rund zugeschnitten).
  `tauri icon` nimmt das ganze Bild als Vordergrund – dann war die Sprechblase angeschnitten. Deshalb liegen in `android/icons/`
  eigene Vordergründe (aus `frontend/chats/icons/maskable-512.png`, auf 85 % verkleinert) plus dunkelgrüner Hintergrund
  (`#153321`); das Skript ersetzt damit die von Tauri erzeugten.
- **Links** aus den Chats öffnen in der passenden App (Instagram, YouTube …) oder im Browser, nicht in der App selbst
  (`Links.kt`, eingehängt in `RustWebViewClient.shouldOverrideUrlLoading`). Vorher lud die WebView Instagram selbst, das auf
  `instagram://` weiterleitete → „ERR_UNKNOWN_URL_SCHEME“. In der App bleiben eigene Seiten (`*.kulbarts.com`), Weiterleitungen
  und alles ohne Antippen – so läuft die Anmeldung (Cloudflare, Google) weiter in der App. Andere Schemata (`instagram://`,
  `intent://`, `mailto:`, `tel:`) gehen immer an Android.

## Benachrichtigungen (FCM)

Android weckt eine geschlossene App nur über Googles Push-Dienst FCM, und der braucht ein Firebase-Projekt (kostenlos). Weil das Repo
öffentlich ist, steckt **nichts davon im Repo oder in der APK**: Beide Dateien aus Firebase liegen nur im Chats-Dienst (`chats.db`).

**Einrichten (einmal):**

1. https://console.firebase.google.com → Projekt erstellen (Google Analytics aus).
2. Android-App hinzufügen, Paketname genau `com.kulbarts.chats` → `google-services.json` herunterladen.
3. Projekteinstellungen → Dienstkonten → „Neuen privaten Schlüssel generieren“ → JSON-Datei herunterladen.
4. In IDA Chats (Website oder App): Einstellungen → „Android-App“ → beide Dateien hochladen.
5. In der Android-App: Einstellungen → „Benachrichtigungen“ einschalten und erlauben, dann „Probemeldung“.

**So läuft es:**

- Die App fragt nach der Anmeldung `GET /api/v1/fcm/client` (Projekt-ID, App-ID, Absender-Nummer, API-Schlüssel aus der
  `google-services.json`) und richtet Firebase damit zur Laufzeit ein (`Push.init`). Die Daten merkt sie sich, damit Meldungen auch
  nach einem Neustart des Handys ankommen, ohne dass die Seite offen ist.
- Google vergibt ein Geräte-Token; die Seite meldet es mit `POST /fcm/register` am Dienst an.
- Bei einer neuen Nachricht schickt der Dienst (`fcm_send` in `chats/app/push.py`) eine reine Daten-Meldung mit hoher Priorität über
  die FCM-HTTP-v1-API. Dafür meldet er sich mit dem Dienstkonto bei Google an (JWT, Token 50 min gemerkt). Die App baut daraus
  die Benachrichtigung (eine pro Chat, Tipp öffnet den Chat). Ist die App gerade offen, zeigt sie keine – dort erscheint der Hinweis
  der Seite. Stumme Chats und „Snaps nicht als neue Nachricht“ gelten wie bei Web Push, weil derselbe Weg im Dienst sie auslöst.
- Vorn in der Meldung steht das Profilbild des Chats statt des App-Symbols (`Push.avatar`): Die App holt es von `/api/v1/avatar/…`
  mit den Cookies der WebView (Cloudflare-Anmeldung), schneidet es rund und merkt es sich einen Tag. Die Meldung ist dafür eine
  Unterhaltung (MessagingStyle + Kurzbefehl pro Chat). Klappt das Laden nicht, bleibt das App-Symbol. Noch nicht am Handy geprüft.
- Meldung wieder weg: Öffnest du den Chat, nimmt die Seite seine Meldung sofort weg (`IDAChatsNative.dismiss(chat)`, im Browser über
  den Service Worker). Wird er anderswo gelesen (andere Geräte, Beeper), sieht das der Abgleich (`live.check`, Ungelesen fällt auf 0)
  bzw. `POST /chats/{id}/read` und schickt `{"type": "dismiss", "chat": …}` mit normaler Priorität (weckt das Handy nicht, kommt
  spätestens beim Aufwecken an; `push.dismiss`, höchstens einmal pro Minute und Chat). Zusammengeführte Chats erst, wenn keiner der
  Gruppe mehr ungelesen ist. Im Browser geht das nur beim Öffnen: Web Push muss jedes Mal eine sichtbare Meldung zeigen.
- Der Schlüssel des Dienstkontos verlässt den Server nie; `GET /fcm` zeigt nur, ob er da ist.
- API: `GET /fcm` (Stand), `PUT /fcm/client`, `PUT /fcm/account` (Datei als Body), `DELETE /fcm`, `GET /fcm/client`,
  `POST /fcm/register`, `POST /fcm/unregister`, `POST /fcm/test` (jeweils `{"token": …}`). Einstellung `ANDROID_PACKAGE` (Standard
  `com.kulbarts.chats`) wählt die passende App aus der `google-services.json`.
- Brücke Seite ⇄ App: `window.IDAChatsNative` (`pushState()`, `pushSetup(json)`, `pushOff()`, `pendingChat()`, `dismiss(chat)`), Antworten als
  Ereignisse `ida-fcm` (`{token, error}`) und `ida-open` (`{chat}`). Die Brücke reagiert nur auf Seiten unter `kulbarts.com`.

## Signierschlüssel

Liegt als PKCS12 in den Repo-Secrets `ANDROID_KEYSTORE_B64` (Base64) und `ANDROID_KEYSTORE_PASSWORD`, Alias `ida`. Secrets lassen
sich nicht wieder auslesen; eine Sicherung liegt auf dem Server neben den Code-Projekten unter `.android-keys/` (nicht im Repo).
Geht der Schlüssel verloren, lassen sich neue Versionen nur nach Deinstallieren der alten App installieren.

## Einstellungen

- Adresse der Seite, Name, Kennung (`com.kulbarts.chats`): `android/src-tauri/tauri.conf.json`.
- Die WebView meldet sich als normales Chrome am Handy (`userAgent`), weil Google die Anmeldung in eingebetteten Browsern sonst ablehnt.
