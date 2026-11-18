# IDA Chats

Deine Beeper-Chats im IDA-Stil als eigene App (Dienst `chats`, Image `ida-chats`, PWA): Chats suchen (schwebende Leiste unten im Material-3-Expressive-Stil, 48 px hoch: links Filter Alle / Ungelesen / Archiv, Mitte Einstellungen (eigene Adresse `/einstellungen`: Benachrichtigungen an/aus mit Probemeldung, stumme und automatisch gelesene Chats) Neu laden und – wenn `VOICE_PUBLIC_URL` gesetzt ist – ein Mikrofon, das die Live-App mit `…/?start=1` öffnet und das Gespräch mit Stella direkt startet, rechts Suche mit eigener Adresse `/suche`; keine Kopfzeile mehr),
Unterhaltungen lesen, Nachrichten schreiben, auf Nachrichten antworten, Bilder sehen, Sprachnachrichten als Text anzeigen – und **live**.

```
Browser (chats.kulbarts.com, Cloudflare Access) ──> chats :8000 ──MCP──> Beeper Desktop (beeper.kulbarts.com/v0/mcp)
```

* **Fast kein eigener Speicher.** Chats und Nachrichten liegen in Beeper Desktop; der Dienst merkt sich nur gesehene Nachrichten (siehe „Löschen und gelöschte Nachrichten“) und die Texte umgewandelter Sprachnachrichten (siehe „Sprachnachrichten“). Der Dienst spricht dessen MCP-Adresse an (`BEEPER_MCP_URL`) mit dem Bearer-Token `BEEPER_MCP_TOKEN` an – dem Zugang, den IDA beim Beeper-Anbieter speichert.
  dieselbe wie beim Beeper-Anbieter in IDA).
* **Live, aber sparsam:** Nur solange die App irgendwo offen (und sichtbar) ist, fragt der Dienst alle `LIVE_POLL_S` Sekunden (Standard 5)
  die 30 zuletzt aktiven Chats ab und meldet geänderte per Server-Sent Events (`/api/v1/stream`). Ohne offene App fragt er nichts ab.
* **Medien:** Bilder, Sprachnachrichten und Videos holt der Dienst über `get_attachment_image` (liefert die Datei als Binärteil) und reicht
  sie unter `/api/v1/media` mit Range-Unterstützung (Spulen, iPhone) weiter; bis 60 MB je Datei, im Speicher zwischengehalten (120 MB).
  Nur Bild-, Audio- und Videotypen werden angezeigt, alles andere (HTML, SVG …) nur als Download.
* **Sprachnachrichten:** eigener Player (Wellenform, Tempo 1×/1,5×/2×, Spulen). Den Text liefert Beeper oft schon mit; sonst wandelt ihn je nach Einstellung Beeper
  oder das **lokale Whisper-Modell** (Dienst `whisper`, large-v3-turbo, docs/WHISPER.md) um (`/api/v1/transcript`). Einstellungen → **Sprachnachrichten als Text**: „Umwandeln mit“ (Beeper oder Lokal) und
  „Automatisch umwandeln“ (neue Sprachnachrichten von selbst, sonst erst beim Antippen des Text-Symbols; bei „Automatisch“ steht der Text gleich unter der Nachricht). **Jeder Text wird einmal
  umgewandelt und in `chats.db` gesichert** (Tabelle `transcripts`, Volume `chats-data`; `stt.py`, `store.sync_transcripts()`), bleibt also bei Updates erhalten und gilt auf allen Geräten.
  Texte, die Beeper schon mitliefert, werden beim Laden gleich mitgesichert. Dauert die Umwandlung über 50 s, antwortet `/transcript` mit `pending` und die Seite fragt weiter an (Cloudflare-Limit ~100 s). Die Automatik läuft auf dem Server (auch bei geschlossener App) nur für neue, höchstens einen Tag alte Nachrichten anderer.
* **Bedienung:** Fremde Nachrichten nach rechts, eigene nach links wischen (oder Pfeil am PC) antwortet darauf; langes Drücken am Handy bzw. Rechtsklick am PC öffnet das
  Menü (Antworten, Text kopieren, Datei öffnen); Tippen aufs Bild öffnet die Vollansicht.
* **Adressen für Bild und Video:** Ein geöffnetes Bild bekommt `/chat/<id>/foto/<Nachricht>` (bei mehreren Bildern in einer Nachricht `/<Nr>`),
  ein Video `/chat/<id>/video/<Nachricht>`, ein Profilbild `…/bild`. Beim Durchwischen ändert sich die Adresse mit. Zurück (auch die Taste am Handy)
  schließt das Vollbild; Neuladen oder ein Link öffnen es wieder, sofern die Nachricht im geladenen Verlauf liegt, sonst bleibt nur der Chat offen.
  Die Info-Seite (Tipp auf den Chat-Kopf) hat `/chat/<id>/info`; ein Bild von dort öffnet darüber, Zurück führt Schritt für Schritt zurück.
  Jedes Menü (Long-Press auf eine Nachricht, Chat-Menü in der Liste, Auswahl-Sheets) hängt `/menue` an die aktuelle Adresse; die Zurück-Taste schließt es, statt die Seite zu verlassen. Öffnet ein Menü-Punkt eine neue Ansicht, ersetzt sie den Menü-Eintrag.
* **Vollbild-Bild:** unten dieselbe Leiste wie im Video-Player (Reagieren, Antworten, Weiterleiten, Herunterladen). Neben Bildern und Videos im Verlauf
  gibt es keinen Download-Knopf mehr; heruntergeladen wird nur im Vollbild bzw. Video-Player.
* **Scrollen beim Öffnen:** Der Verlauf springt ans Ende und bleibt dort, solange Bilder, Video-Vorschauen oder Schrift nachladen
  (ResizeObserver auf die Nachrichten, `overflow-anchor: none`). Wer selbst hochscrollt, bleibt an seiner Stelle.
* **Profilbilder:** kommen aus der REST-API von Beeper Desktop (gleiche Adresse ohne `/v0/mcp`, gleicher Token; abweichend über
  `BEEPER_API_URL`): `GET /v1/chats/{id}` liefert `imgURL` von Chat und Teilnehmern (als `file:///…` oder Windows-Pfad), die Bilder holt
  `GET /v1/assets/serve?url=…`. Der Dienst gibt sie unter `/api/v1/avatar/{chat}` (Gruppenbild bzw. andere Person) und
  `/api/v1/avatar/{chat}?user={id}` (Gruppenmitglied) aus, zwischengespeichert; ohne Bild (404) bleibt der farbige Anfangsbuchstabe.
  In Gruppen steht das Bild an der letzten Nachricht einer Folge vom selben Absender. Der Name steht frei über der ersten Blase der Folge (nicht in der Blase), jede Person in eigener Farbe.
* **Lesebestätigung:** Beeper liefert pro Nachricht `seen` ({Nutzer: Zeitpunkt}); die App zeigt nur einmal unter der
  letzten eigenen Nachricht „Gesendet“ (Häkchen) bzw. „Gesehen“ (zwei Häkchen, in Gruppen „Gesehen von …“ mit Namen); an den Blasen selbst steht nur die Uhr beim Senden. Wer wann gelesen hat, zeigt langes Drücken → „Info“ (nicht mehr im Verlauf). Der offene Chat wird alle 4 s neu geholt.
* **Senden von Dateien und Sprachnachrichten:** „+“ öffnet Fotos & Videos, Kamera, Datei (auch Einfügen und Ziehen am PC); vor dem Senden
  eine Vorschau mit Bildunterschrift, bis 10 Dateien, je höchstens 95 MB (Cloudflare-Grenze). Bei leerem Feld wird aus Senden ein Mikrofon:
  Antippen nimmt auf (Wellenform, Zeit, Pause, Verwerfen, höchstens 15 Minuten). `POST /api/v1/chats/{id}/upload` lädt die Datei per
  `POST /v1/assets/upload` zu Beeper und schickt sie mit `POST /v1/chats/{id}/messages` (`attachment.uploadID`, Typ `voice-note` für
  Sprachnachrichten). Aufnahmen wandelt ffmpeg im Image vorher in Ogg/Opus (mono, 32 kbit/s) – das Format, das WhatsApp & Co. als Sprachnachricht erwarten.
  Das geschieht über Dateien statt Pipes (ffmpeg kann so in der Aufnahme springen). Klappt die Umwandlung nicht, schreibt der Dienst den Grund ins Log
  (`Sprachnachricht: ffmpeg fehlgeschlagen …`) und sendet **nichts** – die App meldet „bitte nochmal aufnehmen“, statt dass beim Empfänger (WhatsApp)
  eine Sprachnachricht mit Häkchen ankommt, die sich nicht abspielen lässt. Nur eine Aufnahme, die schon Ogg ist, geht ohne Umwandlung raus.
  Der Typ der Sprachnachricht lautet immer `audio/ogg; codecs=opus` (wie bei Beepers eigenen): Mit nacktem `audio/ogg` kam sie bei WhatsApp mit Häkchen an,
  ließ sich dort aber nicht abspielen (17.11.2026 an einer Nachricht an einen WhatsApp-Kontakt gesehen).
* **Reaktionen:** langes Drücken → Reaktionsleiste; `POST`/`DELETE /api/v1/chats/{id}/messages/{msg}/react` → Beeper `…/reactions`. Anzeige als kleine Pille, die unten über den Rand der Blase ragt (fremde links, eigene rechts).
* **Bearbeiten:** langes Drücken auf eine eigene Textnachricht → „Bearbeiten“; `PUT /api/v1/chats/{id}/messages/{msg}` → Beeper `PUT …/messages/{msg}`.
  Nur Text, keine Nachrichten mit Anhang; ob und wie lange es geht, entscheidet das Netzwerk (sonst kommt die Fehlermeldung von Beeper).
* **Weiterleiten:** langes Drücken → „Weiterleiten“ → Chat aussuchen; `POST /api/v1/chats/{ziel}/forward` mit Text und Anhängen (`src`).
  Die Beeper-API hat kein echtes Weiterleiten: Der Dienst holt die Anhänge bei Beeper und sendet alles im Zielchat neu –
  beim Empfänger steht deshalb kein „Weitergeleitet“.
* **Grenzen von Beeper-MCP** (nicht der App): Kein Werkzeug für Reaktionen, Dateien und Gelesen-Markieren –
  dafür nutzt die App die REST-API. „Als gelesen markieren“ geht über `/v1/chats/{id}/read` mit der ID der neuesten fremden Nachricht (echte Lesebestätigung, auch in Beeper gelesen; bei Snapchat reicht das nicht, dort gilt ein Snap erst als geöffnet, wenn der Chat in Beeper Desktop wirklich aufgeht: der Dienst öffnet ihn deshalb zuerst per `/v1/focus`, solange er noch ungelesen ist, lässt ihn 4 s offen, markiert erst danach als gelesen und springt dann auf den Park-Chat „Note to self“ bzw. `BEEPER_PARK_CHAT`, damit im offenen Chat nicht jede weitere Nachricht von selbst als geöffnet gilt), „Als ungelesen markieren“
  (Knopf oben im Chat, „Ab hier ungelesen“ im Menü einer Nachricht) über `/v1/chats/{id}/unread`. Öffnen und Lesen allein markiert nichts.
  **Sammelchats von Beeper** (mehrere Chats in Beeper zu einem verbunden, REST-Feld `merge.chatIDs`; nicht zu verwechseln mit dem Zusammenführen nur in IDA Chats): Auf dem Sammelchat selbst
  lehnt Beeper `/read` ab (500, ohne Ungelesenes 404). Der Dienst markiert deshalb die Chats dahinter als gelesen (nur die mit Ungelesenem; ein Chat, den Beeper nicht mehr kennt, wird
  übersprungen). Geht einer nicht, kommen die übrigen trotzdem dran und erst danach kommt der Fehler – so auch bei in IDA Chats zusammengeführten Chats. Der Knopf „Als gelesen markieren“ sitzt wie in Beeper als Pille unten über dem Eingabefeld, über der ersten ungelesenen Nachricht steht eine Trennlinie „N ungelesen“. Umfragen sind noch nicht eingebaut.
  **Netzwerk eines Sammelchats:** Er hat selbst keins (Konto `matrix`, in der Liste leer – die App hätte „Chat“ gezeigt). Der Dienst (`beeper_nets`, 1 h gemerkt) setzt bei Chats ohne Netzwerk die Konten der Chats dahinter als `konto1|konto2` ein (Liste, Pins, Chat-Info, Zusammenführungen). Die App nimmt das erste Konto fürs Abzeichen und nennt alle („Instagram + WhatsApp“); App-Regeln („Automatisch als gelesen“, „Auf der Startseite“ pro App) gelten für solche Chats nicht. Chats, die Beeper nicht mehr kennt, bleiben bei „Chat“.
* **Schnell laden:** Nachrichten kommen über die REST-API (`GET /v1/chats/{id}/messages`, ~100 ms), nur wenn die nicht geht über MCP.
  Ältere Seiten hält der Dienst 10 Minuten im Speicher; die Seite holt die nächste ältere Seite schon im Hintergrund („Ältere laden“ ist sofort da)
  und merkt sich pro Chat die letzten 150 Nachrichten (40 Chats, `localStorage`), so steht ein Chat beim Öffnen sofort da und wird dann aufgefrischt.
  Geladene Profilbilder werden beim Neuzeichnen nicht neu eingeblendet.
* **Anpinnen:** Langes Drücken (am PC Rechtsklick) auf einen Chat in der Liste → „Anpinnen“, höchstens 3. Das sind echte Beeper-Pins
  (`PATCH /v1/chats/{id}` mit `isPinned`), sie stehen also auch in Beeper oben. In der Liste erscheinen sie ganz oben als große runde Profilbilder nebeneinander (Name darunter, Zähler am Bild) und nicht mehr als Zeilen. Welche angepinnt sind, liest `GET /api/v1/pins` aus `GET /v1/chats` (60 s gemerkt).
* **Systemhinweise:** Hinweise des Netzwerks erkennt der Dienst als `sys` (`is_system()`): Typ `NOTICE` (Snapstreak gestartet/beendet, Verlauf gesendet …) oder eine Nachricht ganz ohne
  `type` und ohne `isDeleted` (Signal und Snapchat-Gruppen: „… left/joined the chat“, „… was invited …“, „Disappearing messages turned off“; die Kino-News des WhatsApp-Bots haben auch kein
  `type`, aber `isDeleted` – das sind echte Inhalte). `bubble()` zeichnet sie nicht als Blase, sondern als kleine
  graue Zeile in der Mitte mit Symbol (`isSys()`, `sysIcon()`, Klasse `.sysrow`) – ohne Absender, ohne Menü und ohne Wischen zum Antworten. Die Texte bleiben, wie Beeper sie liefert (englisch).
* **Sprachnachrichten verschieben:** Das Wellenbild antippen oder ziehen springt an die Stelle (`seekTo()`, Zeiger-Ereignisse auf `.wave`, wie in WhatsApp). Beim Ziehen zeigen Balken, ein Griff (`.knob`) und die Zeit
  die Stelle; losgelassen wird dorthin gesprungen, eine laufende Nachricht spielt von dort weiter, eine fremde wird geladen und gespielt. Senkrechtes Wischen scrollt weiter den Chat und bricht das Ziehen ab;
  Halten im Wellenbild öffnet kein Nachrichten-Menü.
* **Später senden:** Beeper kann das in seiner API nicht – der Dienst übernimmt es (`app/schedule.py`, Tabelle `scheduled` in `chats.db`, Takt alle 10 s auch bei geschlossener App).
  Senden-Knopf lange drücken (oder Rechtsklick) → Fenster `pickWhen()`: In 1 Stunde, Heute Abend (20:00), Morgen früh (08:00), am Wochenende Montag früh oder eigene Zeit; Schalter
  **„Nur, wenn ich nichts schreibe“** (merkt sich die Wahl auf dem Gerät). Gesendet wird über denselben Weg wie jede Nachricht (`api.send`), mit Antwort-Bezug und Erwähnungen; bei
  zusammengeführten Chats über das gerade gewählte Netzwerk. `POST /api/v1/chats/{id}/schedule`, `GET /api/v1/scheduled`, `PATCH`/`DELETE /api/v1/scheduled/{id}`, `POST …/{id}/send` (jetzt senden).
  **Nur wenn ich nichts schreibe:** Beim Fälligwerden prüft der Takt die neuesten Nachrichten des Chats (bei Zusammenführung aller Chats der Gruppe): Hat der Nutzer seit dem Planen selbst etwas
  geschickt (Reaktionen und Hinweise zählen nicht, ebenso nicht, was der Takt selbst aus Geplantem gesendet hat), wird nicht gesendet (Status `skipped`). Lässt sich das nicht prüfen, wird später
  erneut versucht. **Stati:** `pending` (steht aus), `sent` (jetzt eine normale Nachricht, verschwindet aus der Liste), `skipped`, `missed` (der Dienst war länger als 30 Minuten über die Zeit hinaus aus –
  nicht mehr senden), `failed` (3 Versuche im Abstand von 30 s). Nicht Gesendetes bleibt 3 Tage sichtbar, bis man es verwirft oder neu plant. Im Chat steht Geplantes als gestrichelte Blase ganz unten
  (Antippen: jetzt senden, Zeit ändern, „nur wenn ich nichts schreibe“ umschalten, zurück ins Eingabefeld, abbrechen), in der Liste ein Symbol, in den Einstellungen eine Übersicht. Der Dienst meldet
  Änderungen per SSE (`type: sched`). Nur Text (keine Anhänge), nicht für Ida und WebUntis.
* **Plus-Menü:** Das Plus unter dem Eingabefeld öffnet unter dem Feld eine Leiste (Galerie, Kamera, Dateien) und wird zum Kreuz (`attachMenu()`, `#attpanel`). In der Android-App steht darunter ein
  Raster der letzten 60 Fotos und Videos, die erste Kachel ist die Kamera. Antippen holt die Datei über die Brücke und öffnet die Vorschau mit Bildunterschrift (`pickNative()`, `pickFiles()`).
  Ein Browser darf die Galerie nicht lesen – ohne die Brücke (`NATIVE.mediaList`) gibt es nur die Leiste. Mehrere Dateien auf einmal: Chip „Galerie“. Details der Brücke: `docs/CHATS-ANDROID.md`.
* **Angepinnte Nachrichten:** Beeper hat dafür kein Feld, nur den Hinweis `NOTICE` „Pinned a message“ mit `linkedMessageID` auf die Nachricht. `GET /api/v1/chats/{id}/pins` sucht diese Hinweise
  (`/v1/messages/search`, Wort „pinned“, `dateAfter`) und holt die Nachrichten dazu; die Oberfläche zeigt unter dem Kopf eine Leiste (`pinBar()`), Antippen springt zur Nachricht (lädt bei Bedarf
  den Verlauf nach), bei mehreren der Reihe nach. **Grenze:** Aufheben und Ablaufen eines Pins meldet Beeper nicht (es gibt keinen „Unpinned“-Hinweis) – darum zählen nur Pins der letzten
  `PIN_DAYS` = 7 Tage (WhatsApp-Standard), neueste zuerst, höchstens 3.
* **Geteilte Instagram-Beiträge:** Beeper schickt einen geteilten Beitrag als **eine** Nachricht: das Bild des Beitrags als Datei und als Text `Benutzername Beschreibung` mit dem Link direkt dahinter
  (ohne Leerzeichen) und gleich noch einmal `(Link?is_sponsored=…)` – die Klammer unterscheidet ihn von einem getippten Link. `igShare()` erkennt das, `bubble()` zeichnet daraus **eine Karte** (`igHtml()`, Klasse `igcard`):
  das echte Bild in seinem Seitenverhältnis (Tipp öffnet es groß), darunter Instagram-Symbol, Benutzername (Tipp öffnet den Beitrag bei Instagram) und die Beschreibung (6 Zeilen, Tipp klappt auf), kein Rohtext mit
  zwei Links und nicht mehr das zugeschnittene Vorschaubild der Seite zusätzlich. Ohne Datei (reiner Text-Beitrag) gibt es die Karte mit dem Vorschaubild der Seite, mit mehreren Dateien bleibt es beim alten Bild. Reels als
  reiner Link bleiben die bisherige Linkkarte mit Abspielen (`soleLink()`). Zitate und die Antwortleiste zeigen „Name · Beschreibung“ (`quoteText()`).
* **Menü beim langen Drücken:** Reaktionsleiste, Nachricht und Aktionen stehen so nah an der echten Stelle wie möglich, aber ganz im Bild – oben unterhalb der Statusleiste, unten oberhalb der Gestenleiste
  (`inset()` misst `env(safe-area-inset-*)`; in der Android-App oben mindestens 28 px). Früher las `place()` eine nie gesetzte Variable `--sat`: der Abstand war 0, und bei einer Nachricht ganz oben lag die
  Reaktionsleiste unter der Statusleiste, wo man nicht tippen kann.
* **Smart Copy:** Im Nachrichten-Menü „Text kopieren“ **gedrückt halten** (450 ms, am PC Rechtsklick; ein kleiner Funken am Knopf zeigt es an) → Blatt „Smart Copy“ (`smartCopy()`): oben die ganze Nachricht,
  darunter jeder kopierbare Teil als eigene Zeile mit Symbol und Name – Bestätigungscode, E-Mail-Adresse, Telefonnummer, Adresse, Link, IBAN, Datum, Uhrzeit, Betrag, Nummer (Bestell-, Rechnungs-, Sendungsnummer, Kennungen, Nummern in Blöcken), Sonstiges (Benutzernamen, WLAN …). Bewusst großzügig: alles Sinnvolle, nur kleine Zahlen im Fließtext („3 Katzen“) nicht. Ein Tipp kopiert genau diesen Wert und schließt das Blatt.
  Ablauf: Die einfache Erkennung des Dienstes (`smartcopy.local_items()`, Regeln für Codes mit Stichwort, E-Mail, Telefon, deutsche Straße + PLZ/Ort, Links, IBAN mit Prüfsumme, Datum, Uhrzeit, Betrag, Nummern ab 4 Ziffern oder Buchstaben+Ziffern) zeigt ihre Treffer sofort
  (`POST /api/v1/smartcopy` mit `fast`), während **Claude Haiku** (`cheap_model`, „Claude Haiku 5.5“) die Nachricht liest: der Dienst fragt IDA (`IDA_URL`, `POST /api/v1/smartcopy/haiku`, `backend/app/api/smartcopy.py`,
  bei Limit der nächste Claude-Account) nach einem JSON-Array `{type, label, value}`. **Haikus Werte zählen nur, wenn sie wörtlich in der Nachricht stehen** (`present()`: Codes, Telefon-/sonstige Nummern und IBANs als ganze Zahl,
  nur Leerzeichen/Bindestriche dürfen anders stehen) – Erfundenes fliegt raus. Das Ergebnis ist die Vereinigung beider (Haikus Namen zuerst, gleiche Werte nur einmal, höchstens 20), eine Stunde gemerkt;
  ein Fehler wird nicht gemerkt. Ist Haiku nicht erreichbar oder am Limit, bleibt die einfache Erkennung („Einfache Erkennung – Haiku war gerade nicht erreichbar“). Die Nachricht geht dafür an Claude.
* **Umfragen:** Beeper liefert von einer Umfrage nur die Frage als Text, ohne Typ, ohne Optionen und ohne Stimmen (REST-`Message` hat keine Umfragefelder, der Chat nur die Fähigkeit `poll`;
  die Matrix-Ereignisse sind verschlüsselt und lassen sich Beepers Nachrichten-IDs nicht zuordnen). Eine Umfrage ist darum von einer gewöhnlichen Nachricht nicht zu unterscheiden und erscheint als Text.
* **Zusammenführen:** Langes Drücken auf einen Chat → „Mit anderem Chat zusammenführen“. Das passiert nur in IDA Chats (Tabelle `merges` in
  `chats.db`), Beeper bleibt unberührt. Die Gruppe heißt nach dem ersten Chat, ihre ID ist dessen ID. `GET /api/v1/chats/{gruppe}/messages` liefert
  alle Chats als einen Verlauf; jede Nachricht hat `chat` und `mid` (echte ID), `id` ist `chat~mid`, der Cursor merkt sich pro Chat den Stand.
  Gesendet wird über den Chip im Eingabefeld (Standard: Chat der letzten eingehenden Nachricht), Antworten gehen in den Chat der zitierten Nachricht – der Chip springt dabei auf dessen Netzwerk.
  Eingabefeld und Senden-Knopf tragen die Farbe des Netzwerks, über das gerade gesendet wird.
  **Filter:** In einem zusammengeführten Chat sitzt oben rechts ein Filter-Knopf (`filterBtn()`, `filterMenu()`): „Alle Apps“ oder nur eine App (z. B. nur WhatsApp). `drawMsgs()` zeichnet dann
  nur die Nachrichten dieses Chats (`m.chat`), `S.msgs` bleibt vollständig, ebenso Zwischenspeicher und Nachladen; geschrieben wird mit Filter über dieselbe App (`target()`).
  Wählt man unten im Eingabefeld ein anderes Sendeziel, solange gefiltert wird, wechselt der Filter auf dieses Netzwerk mit.
  **Nachladen und Abgleich rechnen je Chat:** Die neueste Seite eines zusammengeführten Chats besteht aus der neuesten Seite *jedes* Chats der Gruppe, und die reichen verschieden weit zurück. Darum gelten der
  Zeitraum, in dem Fehlendes als gelöscht zählt (`staleTest()` in `loadMsgs()`), und die Frage, ob der Verlauf schon tiefer geladen ist als die neueste Seite (`deeper()`), pro Chat (`m.chat`) – sonst warf der Abgleich alle 5 s
  den nachgeladenen Verlauf des regen Chats heraus und schob beim Hochwischen nach unten. Beim Nachladen älterer Seiten wird nie etwas entfernt.
  Der Knopf ist hervorgehoben, solange gefiltert wird. Einstellungen → „Filter im Chat“ → „Filter merken“ (nur auf diesem Gerät, `localStorage` `chats-filter-keep`/`chats-filter`):
  aus (Standard) = beim Verlassen des Chats springt der Filter auf „Alle Apps“ zurück, an = er bleibt pro Chat, bis man ihn ändert.
  Aufheben bzw. einen Chat herausnehmen fragt vorher nach (eigenes Blatt `sure()`, kein `confirm()` – das zeigt die WebView der APK nicht sicher).
  In der Liste trägt das Profilbild unten rechts das Logo der App (`NET_SVG`, eigene vereinfachte Zeichnungen), Zusammengeführte ein eigenes Zeichen.
* **Vorschläge zum Zusammenführen** (Einstellungen): `GET /api/v1/merges/suggestions` sucht in den neuesten 200 Chats dieselbe Person in
  verschiedenen Apps (`org.suggest`: gleicher Name nach `norm_name`, sehr ähnlicher Name oder gleicher Vorname bei Einzelchats; gleiche Art;
  pro Netzwerk höchstens einer). Antippen: abwählen, weitere dazunehmen, Namen geben, speichern (`POST /merges` + `PATCH`).
  „Nicht vorschlagen“ → `POST /merges/suggestions/dismiss` merkt alle Paare (Tabelle `merge_nope`); Abgewählte werden beim Speichern ebenso gemerkt.
* **Link-Vorschauen:** Doppelte Links von Beeper (kurz/lang, mit/ohne www, gleicher Titel+Bild) werden zu einer Vorschau zusammengefasst;
  besteht der Text nur aus dem Link, steht nur die Vorschau da. Liefert Beeper keine Vorschau (z. B. Snapchat), holt der Dienst sie selbst:
  `GET /api/v1/link?url=` liest og:-Titel/-Text/-Bild der Seite, `GET /api/v1/link/img?u=` reicht das Bild durch. Nur öffentliche Adressen
  (jede Weiterleitung wird geprüft, keine privaten IPs), Größenlimit, 6 h im Speicher; die Seite fragt nur die letzten 40 Nachrichten ab.
  `POST /api/v1/merges`, `DELETE /api/v1/merges/{gruppe}[?chat=…]` (ganze Gruppe bzw. einen Chat herausnehmen).
* **Farben:** Eigene Nachrichten in der Farbe des Netzwerks; eingehende bleiben dunkel mit einem Hauch Netzwerkfarbe (7 %), Akzente (Name, Zitat, Links, Sprachnachricht,
  Antwortleiste) bekommen die Netzwerkfarbe. Der Netzwerkname steht nicht mehr in der Blase, die Farbe zeigt es. Mehrere Konten pro Netzwerk (z. B. ein zweites, lokal verbundenes Signal) erkennt die App am Kontonamen.
* **Tippfehler-Vorschlag:** 1 s nach dem letzten Tippen fragt die App den Dienst `korrektur` (docs/KORREKTUR.md). Hat er etwas korrigiert, steht der
  Vorschlag über dem Eingabefeld, geänderte Wörter markiert, mit eigenem Senden-Knopf. Der normale Knopf schickt dein Original, Antippen des
  Vorschlags übernimmt ihn ins Feld. `POST /api/v1/korrektur` `{text}` → `{changed, text, parts}`; `KORREKTUR_URL` leer = aus.

## Einrichten

1. `.env`: `BEEPER_MCP_URL=https://beeper.kulbarts.com/v0/mcp`, `BEEPER_MCP_TOKEN=…`, `CHATS_PORT=10.7.0.1:8454`, optional `CHATS_PUBLIC_URL`. `docker compose up -d chats korrektur whisper`.
2. Cloudflare: Access-App für `chats.kulbarts.com` (wie IDA), dann Tunnel-Route → `http://10.7.0.1:8454` (über das Dashboard).

## API (für die Oberfläche)

`GET /api/v1/chats?q=&unread=&inbox=&cursor=` · `GET /api/v1/chats/{id}/messages?cursor=&direction=` ·
`POST /api/v1/chats/{id}/send` `{text, reply_to}` · `GET /api/v1/image?src=` · `GET /api/v1/transcript?src=&mime=` · `GET /api/v1/stt/local` · `GET /api/v1/stream` (SSE; auch `{type: "stt"}`, wenn die Automatik einen Text fertig hat)

## Löschen und gelöschte Nachrichten

- **Löschen:** Lange auf eine Nachricht drücken. Eigene: „Für alle löschen“ (auch im Netzwerk, z. B. WhatsApp, wenn es das kann) oder
  „Nur für mich löschen“. Fremde: „Für mich löschen“. Läuft über `DELETE /v1/chats/{id}/messages/{id}?forEveryone=…` der Beeper-REST-API.
  Für mich Gelöschtes blendet der Dienst zusätzlich selbst aus.
- **Gelöschtes bleibt lesbar:** Der Dienst merkt sich jede Nachricht, die er einmal gesehen hat (`chats.db` im Volume `chats-data`,
  `DATA_DIR=/data`). Meldet Beeper eine Nachricht später als gelöscht, steht in der Blase „Diese Nachricht wurde gelöscht“ und darunter
  der frühere Inhalt. Anhänge sind nur noch da, solange Beeper die Datei noch liefert.
- **Bearbeitete Nachrichten:** Ändert sich der Text einer gemerkten Nachricht, legt der Dienst die alte Fassung in `chats.db` (Tabelle `edits`) ab und
  liefert sie als `prev` (älteste zuerst). In der Blase ist „bearbeitet ▾“ antippbar und zeigt die früheren Fassungen durchgestrichen darunter.
  Nur was der Dienst vor der Bearbeitung schon gesehen hatte, ist dabei.
- **Merken im Hintergrund:** Auch bei geschlossener App fragt der Dienst alle `ARCHIVE_POLL_S` Sekunden (Standard 60, 0 = aus) die
  30 zuletzt aktiven Chats ab und lädt geänderte einmal. Was innerhalb dieser Zeit geschickt und wieder gelöscht wird, fehlt.

## Benachrichtigungen und Stummschalten

- **Einschalten:** Zahnrad unten in der Leiste → Einstellungen → Schalter „Benachrichtigungen“ und erlauben. Das meldet dieses Gerät per Web Push an (`chats/app/push.py`,
  Bibliothek `pywebpush`). Die Schlüssel (VAPID) erzeugt der Dienst beim ersten Gebrauch selbst, Schlüssel und Abos liegen in `chats.db`.
- **Akku:** Die App läuft dafür nicht, es gibt keinen Timer und keine offene Verbindung. Der Dienst fragt Beeper alle `PUSH_POLL_S`
  Sekunden (Standard 10, 0 = keine Benachrichtigungen), solange mindestens ein Gerät angemeldet ist. Steigt bei einem Chat die
  Ungelesen-Zahl, geht eine Meldung über den Push-Dienst des Browsers raus (Android/Chrome: Googles FCM, Inhalt Ende-zu-Ende
  verschlüsselt, `Urgency: high`). Das Handy wird nur dann geweckt.
- **Meldung:** Titel ist der Chat bzw. der eigene Name einer Zusammenführung, darunter der Text (in Gruppen „Name: Text“, ohne Text
  „📷 Foto“, „🎤 Sprachnachricht“ …). Als Bild dient das Profilbild der Person bzw. des Chats, im Service Worker rund zugeschnitten.
  Eine Meldung pro Chat (`tag`), neue ersetzen die alte. Tippen öffnet den Chat. Ist die App gerade offen und im Vordergrund,
  kommt keine Meldung, weil dort schon der Hinweis oben erscheint. Öffnest du einen Chat, verschwinden seine Meldungen.
- **Stummschalten:** Chat-Kopf antippen → Info → „Stummschalten“ (oder in der Liste lange drücken): 1 Stunde, 8 Stunden oder bis du
  es aufhebst. Das gilt nur für Benachrichtigungen dieser App, Beeper bleibt unberührt. In Beeper stummgeschaltete Chats bleiben
  ebenfalls still. In der Liste zeigt ein durchgestrichenes Glocken-Symbol stumme Chats.
- **Snaps still (Snapchat):** Einstellungen → „Snaps nicht als neue Nachricht“ (gilt für alle Geräte, gespeichert in `chats.db`, Standard aus).
  Dann zählen bei Snapchat-Chats nur echte Nachrichten: Ein Snap (Bild/Video ohne Text, fremd ungeöffnet ohne Anhang, eigene als
  `SNAP-….jpg`) und Hinweise wie „Your Snapstreak ended“ lösen keine Meldung aus, zählen nicht als ungelesen und schieben den Chat nicht
  nach oben. Dafür liest der Dienst die letzten Nachrichten des Chats (gemerkt, bis sich dort etwas tut) und setzt Zeit, Ungelesen-Zahl
  und Vorschau auf die letzte echte Nachricht. Gibt es auf der ersten Seite keine, steht der Chat hinter dem ältesten geladenen Snap.
  Im Chat selbst bleiben die Snaps sichtbar. API: `GET /prefs`, `PUT /prefs` (`{"snaps_quiet": true}`).
- API: `GET /push` (öffentlicher Schlüssel), `POST /push/subscribe`, `POST /push/unsubscribe`, `GET /mutes`,
  `PUT /chats/{id}/mute` (`{"hours": 1 | 8 | 0}`, 0 = für immer), `DELETE /chats/{id}/mute`.

## Übergang Übersicht ⇄ Chat (Handy)

Beim Öffnen und Schließen eines Chats läuft am Handy (bis 860 px Breite) ein Übergang über die View-Transitions-API des Browsers
(`route()` in `frontend/chats/app.js`, Namen und Tempo am Ende von `app.css`): Das Profilbild wandert aus der Zeile in den Kopf, die
Leiste unten wird zur Eingabe (links Filter → Plus, Mitte → Textfeld, rechts Suche → Senden/Mikrofon) und zurück. Die Leiste bleibt
beim Scrollen stehen. Ohne View Transitions im Browser oder bei „Bewegung reduzieren“ bleibt es beim einfachen Reinschieben.

## Videos und Alben

- **Snapchat-Videostücke:** Snapchat teilt lange Videos in ~10-Sekunden-Stücke. Aufeinanderfolgende reine Videos desselben Absenders
  (höchstens 90 s Abstand) zeigt die App als ein Video – nur bei Snapchat und nur, solange „Video-Stücke zusammenfügen“ in den
  Einstellungen an ist (`snap_join`, Standard an, gilt für alle Geräte). Bei anderen Netzwerken wird nie zusammengefügt.
- **Album:** Mehrere Bilder und/oder Videos hintereinander vom selben Absender (höchstens 3 min Abstand) stehen als Stapel da.
  Tipp aufs oberste Bild/Video öffnet es, Tipp daneben (Rand, Zahl, Kachel-Knopf) öffnet die Übersicht mit allen als Kacheln
  (eigene Adresse `…/chat/<id>/album/<Nachricht>`, Zurück schließt sie). Tipp auf eine Kachel öffnet das Bild bzw. Video.


## Chat „Ida“

In der Liste steht ein eigener Chat **Ida** (nach Zeit einsortiert wie alle anderen). Darin schreibst du direkt mit Ida, dem Haupt-Agenten von IDA
(`chats/app/ida.py`). Dahinter liegt ein normaler IDA-Chat: Er wird beim ersten Senden angelegt, seine ID steht in `chats.db` (kv `ida_chat`).
Wird er in IDA gelöscht, legt das nächste Senden einen neuen an. Die Antwort läuft in IDA weiter, auch wenn die App zu ist; solange sie läuft,
zeigt der Chat drei springende Punkte.

- **Benachrichtigungen:** Alles, was in IDA an dich geht (Erinnerungen, `notify_user`, Antworten anderer Agenten), steht im selben Verlauf.
  Der Dienst schaut alle 60 s (App offen: 20 s) ins IDA-Postfach und meldet Neues am Handy wie eine Chat-Nachricht. Idas Antworten melden sich
  ebenfalls, wenn die App gerade nirgends offen ist. Stummschalten geht wie bei jedem Chat (lange drücken).
- **Gelesen:** Öffnen markiert den Chat gelesen (auch die Meldungen im IDA-Postfach).
- **An Ida schicken:** Nachricht in einem anderen Chat lange drücken → „An Ida schicken“. Sie geht sofort an Ida, mit Chatname, Netzwerk,
  Beeper-Chat-ID und bis zu 8 Nachrichten davor als Kontext (Sprachnachrichten mit Transkript). Ein Tipp auf den Hinweis öffnet den Ida-Chat.
- Ohne `IDA_URL` gibt es den Chat nicht. Anhänge und Sprachnachrichten gehen im Ida-Chat (noch) nicht.

## WebUntis-Nachrichten

Einstellungen → **WebUntis** → „Mit WebUntis anmelden“: Schule suchen (öffentliches Untis-Verzeichnis), Benutzername, Passwort. Der Dienst
meldet sich sofort an und speichert die Daten nur in `chats.db` (kv `untis_login`, nie im Repo; das Passwort geht nie zurück an die Seite).
Code: `chats/app/untis.py`.

- **Pro Lehrkraft ein Chat** (`untis:<name>`, Abzeichen mit Schulhut), nach Zeit zwischen den anderen Chats. Darin stehen ihre Nachrichten
  (Betreff als erste Zeile, ganzer Text, Verlauf der Unterhaltung) und deine an sie. Lehrkräfte ohne Nachrichten findet die Suche – so
  fängst du einen neuen Chat an.
- **Schreiben:** antwortet auf ihre letzte Nachricht („AW: Betreff“). Mit „Antworten“ (Wischen oder Menü) auf eine bestimmte Nachricht.
  Erlaubt sie keine Antworten oder gibt es noch keine Nachricht von ihr, geht eine **neue Nachricht** an sie; Betreff = erste Zeile des Textes.
- **Gelesen:** Öffnen des Chats markiert ihre Nachrichten auch in WebUntis als gelesen (WebUntis tut das beim Abrufen des ganzen Textes).
  Die Liste allein ändert nichts.
- **Meldungen:** alle 2 Minuten (App offen: 30 s) schaut der Dienst ins Postfach; Neues meldet sich am Handy wie eine Chat-Nachricht.
- Anhänge zeigt der Chat nur als Hinweis („in WebUntis öffnen“); senden kann man nur Text.

Schnittstellen: dieselben, die die WebUntis-Webseite nutzt (aus ihrem Code abgelesen, nicht offiziell dokumentiert) – Anmeldung per
JSON-RPC `authenticate`, dann JWT von `/api/token/new`; Postfach `v1/messages`, Gesendet `v1/messages/sent`, Lehrkräfte
`v1/messages/recipients/static/teachers`, neu `POST v2/messages`, Antwort `POST v2/messages/{id}/reply` (multipart, Teil `request`).
Ändert Untis daran etwas, bricht genau dieser Teil – der Rest der App läuft weiter.

## Startseite, Listen und Archiv

Alles gilt auf allen Geräten (gespeichert im Dienst, `chats/app/org.py`, Tabellen `chat_vis`, `net_vis`, `labels`, `label_chats`);
Beeper bleibt unberührt – außer beim Archivieren. Benachrichtigungen ändert nichts davon, dafür gibt es Stummschalten.

- **Pro Chat** (Chat lange drücken oder Kontaktinfo → „Auf der Startseite“): *Immer*, *Nur bei neuen Nachrichten* (taucht nur auf,
  solange etwas ungelesen ist) oder *Ausblenden*. `PUT /chats/{id}/vis` mit `mode` = `show` | `new` | `hidden` | `""`.
- **Pro Netzwerk** (Einstellungen → Startseite, z. B. alle Snapchat-Chats): *Nur bei neuen Nachrichten* oder *Ausblenden*,
  `PUT /nets/{net}/vis`. **Zusammengeführte Chats** zählen nicht dazu: sie bleiben auf der Startseite, darin schreibt man weiter
  auch über das ausgeblendete Netzwerk. Ein einzelner Chat kann die Netzwerk-Regel mit *Immer zeigen* (`show`) überstimmen.
- **Filter „Ausgeblendet“** zeigt alles, was nicht auf der Startseite steht; die Suche findet ausgeblendete Chats immer.
  Ausgeblendete zählen nicht im Titel und am App-Symbol mit.
- **Eigene Listen** (z. B. „Tagesmutter“): im Filter-Menü unten links „Neue Liste …“, Chat lange drücken → Listen. Jede Liste ist ein
  Filter. Titel und Netzwerk werden mitgemerkt, damit die Liste auch Chats zeigt, die weit unten stehen und noch nicht geladen sind.
  `GET /org`, `POST /labels`, `PUT|DELETE /labels/{id}`, `PUT /labels/{id}/chats/{chat}` (`on`, `title`, `network`).
- **Archivieren** (Chat lange drücken): in Beeper archivieren bzw. im Filter „Archiv“ zurückholen, bei Zusammengeführten alle
  (`POST /chats/{id}/archive`, `archived`). Der Hinweis danach macht es mit einem Tipp rückgängig.

## Schneller durch Zwischenspeichern

- **Startseite** (die ersten 80 Chats) liegt im Browser und steht beim Öffnen sofort da; Frisches kommt gleich danach.
- **Verzeichnis** (`chats-dir`, bis 1500 Chats): Name und Netzwerk jedes Chats, der einmal geladen war. Die Suche zeigt daraus
  sofort beim Tippen Treffer (ohne Groß/klein, Akzente und Schnörkel wie „꧁☬…“), Beepers Antwort kommt kurz danach dazu.
  Auch Listen, „Ausgeblendet“ und aus einer Meldung geöffnete Chats haben damit gleich Namen und Bild.
- **Suchergebnisse** merkt sich die Seite für die Sitzung, der Dienst 30 s (nur Suchen, die normale Liste ist immer frisch).
- Nachrichten pro Chat, Profilbilder und die nächste ältere Seite waren schon zwischengespeichert bzw. vorab geladen.

## Gelesen markieren

„Als gelesen markieren“ merkt sich die Zeit der letzten Nachricht und zeigt den Chat sofort gelesen. Das bleibt so, bis Beeper
selbst 0 meldet (dauert ein paar Sekunden, bei Snapchat länger) oder eine neue Nachricht kommt. Früher wurde der Merker nach der
Antwort des Dienstes gelöscht; lieferte Beeper kurz danach noch die alte Zahl, sprang der Chat zurück auf ungelesen. Leiste und
Trennlinie „… ungelesen“ blenden sanft aus.

### Automatisch als gelesen

Pro Chat (Chat öffnen → oben auf den Namen → „Automatisch als gelesen“) und pro App (Einstellungen → „Automatisch als gelesen“,
z. B. alle SMS-Chats). Zwei Stufen: **Beim Öffnen** (die Seite markiert den Chat gelesen, sobald er offen ist) und **Sofort** (der Dienst
markiert neue Nachrichten in Beeper gleich als gelesen, auch ohne offene App – keine Meldung aufs Handy; schon Ungelesenes bleibt, bis
etwas Neues kommt).

- **Reihenfolge:** eigene Einstellung des Chats → Einstellung seiner Gruppe → Regel der App. Am Chat nimmt **Aus** ihn von der App-Regel
  aus (gespeichert als `off`), „Wie alle SMS-Chats“ löscht seine eigene Einstellung.
- **Zusammengeführte Chats** folgen der App-Regel nicht (wie bei „Auf der Startseite“); sie haben nur eine eigene Einstellung.
- **Schnittstellen:** `GET /autoread` → `{items, nets}`, `PUT /chats/{id}/autoread` mit `mode` = `open` | `always` | `off` | `""`,
  `PUT /nets/{app}/autoread` mit `mode` = `open` | `always` | `""` (App = Schlüssel wie `gmessages` für SMS, `whatsapp`, …). Gespeichert in
  `chats.db` (Tabellen `autoread`, `autoread_net`), gilt auf allen Geräten. Der Dienst ordnet Beepers Konto-Text (`gmessages.…`) mit
  `org.net_key` derselben App zu wie die Seite mit `netKey`.

## Geteilte Beiträge und Links

- Instagram schickt einen geteilten Reel als Text „url (url?id=…)“ plus Vorschaubild als Anhang. Das war bisher Bild, Link und
  noch einmal die Link-Vorschau mit demselben Bild. Jetzt ist es **eine Karte**: Bild aus dem Anhang, darunter Titel und Text der Seite.
- **Abspielen:** Tipp aufs Bild spielt Instagram-, YouTube- und TikTok-Videos im eingebetteten Player der jeweiligen Seite ab
  (ohne Anmeldung, eigene Adresse `…/video/<Nachricht>`, Zurück schließt). Oben rechts öffnet es in deren App.
- Links öffnen immer in einem neuen Tab bzw. in der Android-App außerhalb (siehe `docs/CHATS-ANDROID.md`).

## Ältere Nachrichten

Oben im Chat steht statt „Ältere laden“ ein sich drehendes Zeichen; kommt man oben an, lädt die nächste Seite von selbst (meist
schon vorab geholt). Während des Auftritts beim Öffnen eines Chats wartet es, damit nicht gleich beim Öffnen nachgeladen wird.
