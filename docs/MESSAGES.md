# Nachrichten-App (messages.kulbarts.com)

Eigene kleine PWA für alles, was die KIs **dir** schicken (Nachrichten an `user`: `notify_user`, Erinnerungen,
Tracker, Stella-Gespräche …). Kein Beeper. Läuft im Dienst `app` mit, kein eigener Container.

- Adressen: `https://ida…/m/` oder eine eigene Subdomain, deren erster Teil in `MESSAGES_SUBDOMAINS` steht
  (Standard `messages,nachrichten`). Dort ist `/` die Nachrichten-App, `/api/` und `/static/` bleiben gleich (`MessagesHost` in `app/main.py`).
- Tunnel: `messages.kulbarts.com` → dieselbe Adresse wie `ida.kulbarts.com`, davor Cloudflare Access (nur Simon).
- Push: Web Push mit VAPID + aes128gcm (RFC 8291/8292), nur mit `cryptography` (`app/services/push.py`).
  Der VAPID-Schlüssel entsteht beim ersten Aufruf und liegt in `settings` (`push_vapid`). Jede committete Nachricht an
  `user` geht danach im Hintergrund an alle Geräte (`push_subscriptions`); Geräte mit 404/410 werden gelöscht.
  `PUSH_ENABLED=false` schaltet das Senden ab.
- Sparsam: kein Polling. Die Seite lädt beim Öffnen, beim Wieder-sichtbar-werden (höchstens alle 20 s) und wenn ein Push kommt.
  Schwarzer Hintergrund, keine Webfonts, keine Animationen, kein Framework.
- Design: wie IDA – lädt `css/tokens.css` + `css/components.css` (Schrift, Farben, Material-Icons) und nutzt `md()`/`PALETTE`
  aus IDAs JS. Karten wie unter IDA → Nachrichten: Material-Icon je Nachricht (`icon`) in der Farbe des Absenders (`from_palette`).
- Keine Emojis: Titel und Text kommen in der App und in den Pop-ups ohne Emojis an (`push.no_emoji`, Icon über `push.icon_for`).
- Icon: `frontend/messages/icons/` (Sprechblase mit Idas Augen, lila wie die anderen App-Icons; `badge-96.png` einfarbig für die
  Android-Statusleiste).
- iPhone: Push nur, wenn die Seite über „Zum Home-Bildschirm“ installiert ist (iOS 16.4+).

API: `GET /api/v1/messages/inbox` · `GET /api/v1/push/key` · `POST /api/v1/push/subscribe|unsubscribe|test` · `GET /api/v1/push/devices`
