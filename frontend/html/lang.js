/* ======================================================================
   IDA HTML Editor — Translation Dictionary (lang.js)
   Stores all UI translations for dynamic German/English localization
   ====================================================================== */

window.wbTr = {
    de: {
        greeting: "Hallo!",
        subtitle: "Wähle deine Sprache und lege direkt los.",
        langLabel: "Sprache auswählen",
        optTitle: "WIE MÖCHTEST DU STARTEN?",
        t1: "Letztes Projekt fortsetzen", d1: "Du hast 3 Dateien gespeichert – mach genau dort weiter.",
        t2: "Neues Projekt", d2: "Mit einer Beispiel-Seite starten und sofort loslegen.",
        t3: "Ordner hochladen", d3: "Wähle einen Projektordner — alle Dateien & Unterordner werden importiert.",
        t4: "Live mit Ordner verbinden", d4: "Wähle einen Ordner — IDA HTML speichert deine Änderungen direkt in deinen lokalen Dateien.",
        t5: "Live mit Dateien verbinden", d5: "Wähle einzelne Dateien — IDA HTML bearbeitet sie direkt auf deinem PC.",
        status: "Bereit"
    },
    en: {
        greeting: "Hello!",
        subtitle: "Select your language and get started directly.",
        langLabel: "Select Language",
        optTitle: "HOW DO YOU WANT TO START?",
        t1: "Continue last project", d1: "You have saved 3 files – continue right there.",
        t2: "New project", d2: "Start with a template page and get started instantly.",
        t3: "Upload folder", d3: "Choose a project folder — all files & subfolders will be imported.",
        t4: "Connect live with folder", d4: "Select a folder — IDA HTML saves your changes directly to your local files.",
        t5: "Connect live with files", d5: "Select individual files — IDA HTML edits them directly on your PC.",
        status: "Ready"
    }
};

window.STRINGS = {
    de: {
        welcomeTitle: 'Willkommen bei IDA HTML',
        welcomeSub: 'Dein Editor mit Live-Vorschau im Browser',
        chooseLang: 'Sprache wählen',
        howToStart: 'Wie möchtest du starten?',
        newProject: 'Neues Projekt',
        newProjectDesc: 'Mit einer Beispiel-Seite starten und sofort loslegen.',
        uploadFolder: 'Ordner hochladen',
        uploadFolderDesc: 'Wähle einen Projektordner — alle Dateien & Unterordner werden importiert.',
        files: 'Dateien',
        newFolderTooltip: 'Neuer Ordner',
        importFilesTooltip: 'Dateien importieren',
        importFolderTooltip: 'Ordner importieren',
        importZipTooltip: 'ZIP importieren',
        wbTFiles: 'Dateien importieren',
        wbDFiles: 'Wähle einzelne Dateien — alle Dateien werden deinem Projekt hinzugefügt.',
        ddTitle: 'Dateien hier ablegen',
        ddDesc: 'Ziehe Dateien, Ordner oder ZIP-Archive direkt in das Fenster, um sie zu importieren.',
        ready: 'Bereit',
        noProblems: 'Keine Probleme',
        rename: 'Umbenennen',
        delete: 'Löschen',
        noTitle: '(ohne Titel)',
        captureSnapshot: 'Screenshot erstellen (Teilen)',
        previewRefreshed: 'Vorschau aktualisiert',
        newFileTooltip: 'Neue Datei',
        collapseSidebar: 'Seitenleiste verbergen',
        expandSidebar: 'Seitenleiste einblenden',
        resetProject: 'Projekt zurücksetzen',
        error: 'Fehler',
        warning: 'Warnung',
        warnings: 'Warnungen',
        errors: 'Fehler',
        fileExists: 'Datei existiert bereits.',
        invalidName: 'Ungültiger Dateiname.',
        enterName: 'Bitte einen Dateinamen eingeben.',
        confirmDelete: 'Datei wirklich löschen?',
        keepOne: 'Du musst mindestens eine Datei behalten.',
        resetConfirm: 'Alles auf das Beispiel-Projekt zurücksetzen? Deine Änderungen gehen verloren.',
        importedFiles: 'Dateien importiert',
        loading: 'Lade Dateien…',
        importing: 'Importiere {n} Dateien…',
        skipped: 'übersprungen',
        connecting: 'Verbindung wird hergestellt…',
        nothingToImport: 'Keine unterstützten Dateien im Ordner gefunden.',
        home: 'Startseite',
        explorer: 'Explorer',
        togglePreview: 'Vorschau ein/ausblenden',
        settings: 'Einstellungen',
        liveEdit: 'Live mit Ordner verbinden',
        liveEditDesc: 'Wähle einen Ordner — IDA HTML speichert deine Änderungen direkt in deinen lokalen Dateien.',
        liveEditFiles: 'Live mit Dateien verbinden',
        liveEditFilesDesc: 'Wähle einzelne Dateien — IDA HTML bearbeitet sie direkt auf deinem PC.',
        addLiveFile: 'Weitere Datei live verbinden',
        iframeBlockedTitle: 'Im Vorschau-Fenster blockiert',
        iframeBlockedMsg: 'Die Live-Funktion braucht ein eigenes Browser-Fenster. Öffne IDA HTML in einem neuen Tab und versuche es dort.',
        openNewTab: 'In neuem Tab öffnen',
        browserNotSupportedMsg: 'Diese Funktion braucht Chrome oder Edge.',
        chromeOnly: 'Chrome/Edge',
        liveActive: 'Live',
        liveSaved: 'Datei gespeichert',
        liveSaveFailed: 'Speichern fehlgeschlagen',
        livePermissionDenied: 'Keine Schreibrechte für den Ordner. Lade die Seite neu und stimme zu.',
        browserNotSupported: 'Dein Browser unterstützt das nicht. Benutze Chrome oder Edge.',
        liveDisconnect: 'Live-Verbindung trennen',
        liveConnected: 'Verbunden mit „{name}"',
        newFile: 'Neue Datei erstellen',
        newFolder: 'Neuen Ordner erstellen',
        name: 'Name',
        filePlaceholder: 'z. B. about.html, src/utils.js, theme.css …',
        folderPlaceholder: 'z. B. assets, src/components …',
        fileHint: 'Tipp: schreibe einen Pfad wie <code>src/index.html</code>, um Unterordner zu erstellen.',
        folderHint: 'Du kannst auch verschachtelte Pfade wie <code>src/components</code> eingeben.',
        folderExists: 'Ordner existiert bereits.',
        needExtension: 'Dateiname braucht eine Endung, z. B. <code>.html</code>.',
        newFolderTooltip: 'Neuer Ordner',
        newMenu: 'Neu…',
        refreshPreview: 'Vorschau neu laden',
        desktopView: 'Desktop-Ansicht',
        tabletView: 'Tablet-Ansicht',
        mobileView: 'Handy-Ansicht',
        deleteFileTitle: 'Datei löschen?',
        deleteFileMsg: 'Die Datei wird unwiderruflich entfernt.',
        deleteFolderTitle: 'Ordner löschen?',
        deleteFolderMsg: 'Möchtest du den Ordner und alle darin enthaltenen Dateien löschen?',
        resetTitle: 'Projekt zurücksetzen?',
        resetMsg: 'Alle Dateien und deine Änderungen werden gelöscht und durch das Beispiel-Projekt ersetzt.',
        cancel: 'Abbrechen',
        deleteBtn: 'Löschen',
        resetBtn: 'Zurücksetzen',
        continueLast: 'Letztes Projekt fortsetzen',
        continueLastDesc: 'Du hast {n} Dateien gespeichert – mach genau dort weiter.',
        esTitle: 'Keine Datei geöffnet',
        esDesc: 'Wähle links im Explorer eine Datei aus oder erstelle eine neue Arbeitsfläche.',
        esBtn: 'Neue Datei erstellen',
        ectxGoToSymbol: 'Zum Symbol gehen…',
        ectxRenameSymbol: 'Symbol umbenennen',
        ectxChangeAll: 'Alle Vorkommen ändern',
        ectxFormat: 'Dokument formatieren',
        ectxCut: 'Ausschneiden',
        ectxCopy: 'Kopieren',
        ectxPaste: 'Einfügen',
        ectxCommandPalette: 'Befehlspalette',
        syncing: 'Synchronisiere…',
        syncComplete: 'Synchronisierung abgeschlossen!',
        syncFailed: 'Sync failed',
        noLiveFolder: 'Kein Live-Ordner',
        noLiveFolderMsg: 'Bitte verbinde zuerst einen Ordner mit der Website.',
        syncTooltip: 'Mit PC-Ordner synchronisieren',

        // --- Screenshot / Capture Designer Translations ---
        captureModalTitle: 'Screenshot Designer & Sharing-Vorschau',
        captureCustomize: 'Design anpassen',
        captureBgStyle: 'Hintergrund-Stil',
        captureOptBgGradientDark: 'Cyber Deep (Dunkler Verlauf)',
        captureOptBgGradientSunset: 'Sunset Glow (Orange/Rot)',
        captureOptBgGradientOcean: 'Ocean Breeze (Türkis/Blau)',
        captureOptBgGradientAurora: 'Aurora Glow (Grün/Violett)',
        captureOptBgSolidDark: 'Einfarbig Dunkel (#1e1e1e)',
        captureOptBgSolidLight: 'Einfarbig Hell (#f3f4f6)',
        captureOptBgTransparent: 'Transparent',
        captureLayout: 'Fenster-Anordnung',
        captureOptLayoutSplit: 'Nebeneinander (Code & Vorschau)',
        captureOptLayoutCode: 'Nur Code-Editor',
        captureOptLayoutPreview: 'Nur Live-Vorschau',
        capturePadding: 'Rahmen-Abstand (Padding)',
        captureOptPaddingNone: 'Kein (0px)',
        captureOptPaddingCompact: 'Kompakt (24px)',
        captureOptPaddingNormal: 'Standard (48px)',
        captureOptPaddingLarge: 'Großzügig (72px)',
        captureCodeFontSize: 'Code Schriftgröße',
        captureOptFontSizeSmall: 'Sehr klein (11px)',
        captureOptFontSizeNormal: 'Normal (12.5px)',
        captureOptFontSizeLarge: 'Groß (14px)',
        captureOptBranding: 'Branding-Kopfzeile',
        captureOptTitlebars: 'Fenster-Titelleisten',
        captureAssetsTitle: 'Dateien & Vorschau',
        captureOptLoadAssets: 'Verknüpfte CSS/JS laden',
        captureOptShowAssets: 'Auszuwählende Projektdateien:',
        captureOptNoAssets: 'Keine lokalen Verknüpfungen gefunden',
        captureOptResetPositions: 'Positionen zurücksetzen',
        captureClose: 'Schließen',
        captureExportBtn: 'Bild herunterladen',
        captureNoFileError: 'Bitte öffne zuerst eine Code-Datei, um einen Screenshot zu erstellen.',
        captureLoadingTitle: 'Screenshot Designer wird geladen...',
        captureLoadingDetail: 'Lade Bibliotheken und erstelle Vorschaubilder...',
        captureFailed: 'Screenshot-Erstellung fehlgeschlagen',
        captureExportingTitle: 'Bild wird exportiert...',
        captureExportingDetail: 'Bitte warten...',
        captureExportFailed: 'Export fehlgeschlagen',
        previewBadge: 'Vorschau',

        // --- Status Bar ---
        pwaInstall: 'Als App installieren',
        privacy: 'Datenschutz',

        // --- Settings Panel ---
        settingsTitle: 'Editor-Einstellungen',
        settingsSectionAppearance: 'Darstellung',
        settingsTheme: 'Farbschema',
        settingsThemeVscodeDark: 'VS Code Dunkel',
        settingsThemeMidnightBlue: 'Nachtblau',
        settingsThemeCyberpunk: 'Cyberpunk Neon',
        settingsThemeLight: 'Hell',
        settingsThemeCustom: 'Benutzerdefiniert',
        settingsCustomColors: 'Eigene Farben festlegen',
        settingsColorBg: 'Hintergrund',
        settingsColorSidebar: 'Seitenleiste',
        settingsColorAccent: 'Akzentfarbe',
        settingsColorText: 'Schriftfarbe',
        settingsFontFamily: 'Editor Schriftart',
        settingsFontSystem: 'System Monospace',
        settingsFontSize: 'Editor Schriftgröße',
        settingsLineHeight: 'Editor Zeilenhöhe',
        settingsUiFontSize: 'UI Schriftgröße',
        settingsSectionOptions: 'Editor-Optionen',
        settingsWordWrapTitle: 'Zeilenumbruch',
        settingsWordWrapDesc: 'Lange Zeilen automatisch umbrechen',
        settingsMinimapTitle: 'Minimap',
        settingsMinimapDesc: 'Kleine Codevorschau rechts anzeigen',
        settingsFormatOnSaveTitle: 'Formatieren beim Speichern',
        settingsFormatOnSaveDesc: 'Dokument beim Speichern automatisch formatieren',

        // --- Privacy Modal ---
        privacyTitle: 'Datenschutzerklärung – IDA HTML Editor',
        privacyOkBtn: 'Verstanden',
        privacyBodyHtml: `
            <div class="pm-callout green">
                <strong>Kurzfassung:</strong> IDA HTML ist ein reiner Browser-Editor. Deine Dateien, dein Code und deine Einstellungen verlassen deinen Browser <em>nie</em> ohne dein ausdrückliches Zutun. Es gibt keinen Account, keinen Backend-Server und keine Cloud-Speicherung.
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                    Daten, die ausschließlich lokal in deinem Browser gespeichert werden
                    <span class="pm-badge local">Nur lokal</span>
                </h3>
                <p>Folgende Daten werden über die <code>localStorage</code>-API deines Browsers auf deinem eigenen Gerät gespeichert. Sie verlassen deinen Browser <strong>niemals</strong> und werden nicht an Server übertragen:</p>
                <ul>
                    <li><strong>Deine Dateien &amp; Projekte</strong> – Sämtlicher Code (HTML, CSS, JS, etc.) und Dateinamen, die du im Editor erstellst oder hochlädst, werden ausschließlich im localStorage deines Browsers gespeichert (Schlüssel: <code>codeplay.files</code>). Kein Server sieht diese Daten.</li>
                    <li><strong>Aktiver Tab, geöffnete Tabs, aktive Datei</strong> – Der Zustand deines Editors (welche Datei du gerade bearbeitest, welche Tabs offen sind) wird lokal unter <code>codeplay.state</code> gespeichert.</li>
                    <li><strong>Einstellungen</strong> – Farbthema, Schriftgröße, Schriftfamilie, Zeilenhöhe, Minimap-Einstellung, Wortumbruch, Format-on-Save und UI-Schriftgröße werden lokal gespeichert (Schlüssel: <code>cleo.settings.*</code>).</li>
                    <li><strong>Eigene Farben</strong> – Benutzerdefinierte Akzentfarben, Hintergrundfarben und Textfarben (Schlüssel: <code>cleo.settings.custom*</code>).</li>
                    <li><strong>Onboarding-Status</strong> – Ob du den Willkommensbildschirm bereits gesehen hast (<code>codeplay.onboarded</code>).</li>
                    <li><strong>Sprachauswahl</strong> – Deine gewählte Sprache (Deutsch / Englisch) unter <code>codeplay.lang</code>.</li>
                    <li><strong>Leere Ordner</strong> – Von dir erstellte leere Ordnerstrukturen (<code>codeplay.emptyFolders</code>).</li>
                    <li><strong>Live-Verbindungs-Metadaten</strong> – Name des zuletzt verbundenen lokalen Ordners (bei Verwendung der File System Access API), nur lokal.</li>
                </ul>
                <div class="pm-callout green">
                    Du kannst jederzeit alle gespeicherten Daten löschen, indem du in deinem Browser die Browserdaten für diese Seite löschst (Einstellungen → Datenschutz → Browserdaten löschen → Gespeicherte Daten / localStorage).
                </div>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                    Lokale Ressourcen (DSGVO-konform / Offline-first)
                    <span class="pm-badge local">Lokal / Offline</span>
                </h3>
                <p>IDA HTML wird vollständig lokal in deinem Browser ausgeführt. Sämtliche Ressourcen, Bibliotheken und Schriftarten werden direkt von dieser Webseite geladen. Es werden <strong>keine</strong> Verbindungen zu externen Servern oder CDNs von Drittanbietern hergestellt – deine IP-Adresse wird somit an niemanden übertragen.</p>
                <ul>
                    <li><strong>Monaco Editor (Lokal gehostet)</strong> – Der Code-Editor basiert auf dem Monaco-Editor von Microsoft und wird vollständig lokal aus dem Verzeichnis <code>lib/monaco/</code> geladen.</li>
                    <li><strong>Google Fonts (Lokal gehostet)</strong> – Die Schriftart „JetBrains Mono“ wird lokal gehostet und geladen (100% DSGVO-konform).</li>
                    <li><strong>html2canvas (Lokal gehostet)</strong> – Die Screenshot-Bibliothek wird bei Bedarf lokal geladen und im Browser ausgeführt.</li>
                    <li><strong>Prism.js &amp; Prism-CSS (Lokal gehostet)</strong> – Syntax-Highlighting für Screenshots wird lokal bereitgestellt.</li>
                </ul>
                <div class="pm-callout">
                    <strong>Hinweis zu Cloudflare Tunnel:</strong> Wenn du IDA HTML über einen Cloudflare Tunnel aufrufst (z. B. über eine öffentliche URL statt <code>file://</code>), läuft dein HTTP-Traffic durch Cloudflare's Infrastruktur. Cloudflare kann dabei Verbindungsmetadaten sehen – jedoch <strong>keinen</strong> deiner Dateien oder localStorage-Inhalte.
                </div>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                    Was IDA HTML definitiv NICHT sammelt
                    <span class="pm-badge none">Keine Daten</span>
                </h3>
                <ul>
                    <li>❌ Kein Tracking, keine Analyse-Tools (kein Google Analytics, kein Matomo, kein Plausible).</li>
                    <li>❌ Keine Cookies (weder First-Party noch Third-Party-Cookies von IDA HTML selbst).</li>
                    <li>❌ Kein Account, keine Registrierung, keine Anmeldedaten.</li>
                    <li>❌ Dein Code, deine Dateien oder Dateinamen werden niemals zu einem Server hochgeladen.</li>
                    <li>❌ Keine Telemetriedaten über deine Aktionen im Editor.</li>
                    <li>❌ Keine Weitergabe von Daten an Dritte durch IDA HTML selbst.</li>
                    <li>❌ Kein serverseitiges Session-Tracking.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                    Live-Verbindung mit lokalem Ordner (File System Access API)
                </h3>
                <p>Wenn du die Funktion „Live mit Ordner verbinden" oder „Live mit Dateien verbinden" verwendest (nur in Chrome/Edge verfügbar), greift IDA HTML mit deiner Erlaubnis direkt auf Dateien auf deiner Festplatte zu:</p>
                <ul>
                    <li><strong>Lese-/Schreibzugriff nur auf explizit gewählte Dateien/Ordner</strong> – Der Zugriff endet, sobald du die Seite schließt oder neu lädst.</li>
                    <li><strong>Keine Übertragung ans Netzwerk</strong> – Die Dateiinhalte werden nur lokal im Browser verarbeitet und direkt zurückgeschrieben.</li>
                    <li><strong>Berechtigung durch den Browser</strong> – Der Browser zeigt eine eigene Berechtigungsabfrage an.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                    Screenshot-Designer
                </h3>
                <p>Wenn du den Screenshot-Designer verwendest, werden dynamisch externe Bibliotheken geladen:</p>
                <ul>
                    <li><strong>html2canvas</strong> und <strong>Prism.js</strong> werden von <code>cdnjs.cloudflare.com</code> geladen.</li>
                    <li>Das erzeugte Bild (PNG) wird <strong>ausschließlich lokal</strong> in deinem Browser generiert.</li>
                    <li>Dein Code, der im Screenshot zu sehen ist, bleibt vollständig auf deinem Gerät.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                    Deine Rechte &amp; Datenlöschung
                </h3>
                <p>Da IDA HTML keine personenbezogenen Daten auf Servern speichert, bedarf es keiner formellen Löschanfrage. Du kannst alle gespeicherten Daten selbst löschen:</p>
                <ul>
                    <li><strong>Projektdaten löschen:</strong> Klicke im Editor auf „Projekt zurücksetzen" oder lösche den localStorage deines Browsers für diese Seite.</li>
                    <li><strong>Browser-Einstellungen:</strong> Über Browsereinstellungen → Datenschutz → Browserdaten löschen.</li>
                    <li><strong>Drittanbieter-Datenschutzerklärungen:</strong>
                        <ul>
                            <li>jsDelivr: <em>jsdelivr.com/privacy</em></li>
                            <li>Google Fonts: <em>policies.google.com/privacy</em></li>
                            <li>Cloudflare: <em>cloudflare.com/privacypolicy</em></li>
                        </ul>
                    </li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    Änderungen dieser Erklärung
                </h3>
                <p>Bei wesentlichen Änderungen wird die Versionsnummer in der Statusleiste aktualisiert.</p>
                <p style="color: var(--text-mute); font-size: 11.5px;">Stand: Juni 2025 &nbsp;·&nbsp; IDA HTML v1.0</p>
            </div>
        `
    },
    en: {
        welcomeTitle: 'Welcome to IDA HTML',
        welcomeSub: 'Your browser code editor with live preview',
        chooseLang: 'Choose language',
        howToStart: 'How would you like to start?',
        newProject: 'New project',
        newProjectDesc: 'Start with an example page and get going right away.',
        uploadFolder: 'Upload folder',
        uploadFolderDesc: 'Pick a project folder — all files & subfolders get imported.',
        files: 'Files',
        newFolderTooltip: 'New folder',
        importFilesTooltip: 'Import files',
        importFolderTooltip: 'Import folder',
        importZipTooltip: 'Import ZIP',
        wbTFiles: 'Import Files',
        wbDFiles: 'Choose individual files — all files will be added to your project.',
        ddTitle: 'Drop files here',
        ddDesc: 'Drag and drop files, folders, or ZIP archives directly to import them.',
        ready: 'Ready',
        noProblems: 'No problems',
        rename: 'Rename',
        delete: 'Delete',
        noTitle: '(no title)',
        captureSnapshot: 'Create Screenshot (Share)',
        previewRefreshed: 'Preview refreshed',
        newFileTooltip: 'New file',
        collapseSidebar: 'Hide sidebar',
        expandSidebar: 'Show sidebar',
        resetProject: 'Reset project',
        error: 'Error',
        warning: 'Warning',
        warnings: 'Warnings',
        errors: 'Errors',
        fileExists: 'File already exists.',
        invalidName: 'Invalid file name.',
        enterName: 'Please enter a file name.',
        confirmDelete: 'Really delete this file?',
        keepOne: 'You must keep at least one file.',
        resetConfirm: 'Reset everything to the example project? Your changes will be lost.',
        importedFiles: 'files imported',
        loading: 'Loading files…',
        importing: 'Importing {n} files…',
        skipped: 'skipped',
        connecting: 'Connecting…',
        nothingToImport: 'No supported files found in the folder.',
        home: 'Home',
        explorer: 'Explorer',
        togglePreview: 'Toggle Preview',
        settings: 'Settings',
        liveEdit: 'Live-edit a folder',
        liveEditDesc: 'Pick a folder — IDA HTML writes your changes straight back to your local files.',
        liveEditFiles: 'Live-edit single files',
        liveEditFilesDesc: 'Pick one or more files — IDA HTML edits them directly on your PC.',
        addLiveFile: 'Connect another live file',
        iframeBlockedTitle: 'Blocked inside preview frame',
        iframeBlockedMsg: 'Live-edit needs its own browser window. Open IDA HTML in a new tab and try again there.',
        openNewTab: 'Open in new tab',
        browserNotSupportedMsg: 'This feature requires Chrome or Edge.',
        chromeOnly: 'Chrome/Edge',
        liveActive: 'Live',
        liveSaved: 'File saved',
        liveSaveFailed: 'Save failed',
        livePermissionDenied: 'No write permission for that folder. Reload and grant access.',
        browserNotSupported: 'Your browser does not support this. Use Chrome or Edge.',
        liveDisconnect: 'Disconnect live folder',
        liveConnected: 'Connected to "{name}"',
        newFile: 'Create new file',
        newFolder: 'Create new folder',
        name: 'Name',
        filePlaceholder: 'e.g. about.html, src/utils.js, theme.css …',
        folderPlaceholder: 'e.g. assets, src/components …',
        fileHint: 'Tip: type a path like <code>src/index.html</code> to create subfolders.',
        folderHint: 'You can also type a nested path like <code>src/components</code>.',
        folderExists: 'Folder already exists.',
        needExtension: 'File name needs an extension, e.g. <code>.html</code>.',
        newFolderTooltip: 'New folder',
        newMenu: 'New…',
        refreshPreview: 'Refresh preview',
        desktopView: 'Desktop view',
        tabletView: 'Tablet view',
        mobileView: 'Mobile view',
        deleteFileTitle: 'Delete file?',
        deleteFileMsg: 'The file will be removed permanently.',
        deleteFolderTitle: 'Delete folder?',
        deleteFolderMsg: 'Do you want to delete this folder and all of its files?',
        resetTitle: 'Reset project?',
        resetMsg: 'All your files and changes will be deleted and replaced with the example project.',
        cancel: 'Cancel',
        deleteBtn: 'Delete',
        resetBtn: 'Reset',
        continueLast: 'Continue last project',
        continueLastDesc: 'You have {n} saved files – pick up right where you left off.',
        esTitle: 'No file open',
        esDesc: 'Select a file in the Explorer on the left or create a new one.',
        esBtn: 'Create new file',
        ectxGoToSymbol: 'Go to Symbol…',
        ectxRenameSymbol: 'Rename Symbol',
        ectxChangeAll: 'Change All Occurrences',
        ectxFormat: 'Format Document',
        ectxCut: 'Cut',
        ectxCopy: 'Copy',
        ectxPaste: 'Paste',
        ectxCommandPalette: 'Command Palette',
        syncing: 'Syncing…',
        syncComplete: 'Synchronization complete!',
        syncFailed: 'Sync failed',
        noLiveFolder: 'No Live Folder',
        noLiveFolderMsg: 'Please connect a folder to the website first.',
        syncTooltip: 'Sync with PC folder',

        // --- Screenshot / Capture Designer Translations ---
        captureModalTitle: 'Screenshot Designer & Sharing Preview',
        captureCustomize: 'Customize Design',
        captureBgStyle: 'Background Style',
        captureOptBgGradientDark: 'Cyber Deep (Dark Gradient)',
        captureOptBgGradientSunset: 'Sunset Glow (Orange/Red)',
        captureOptBgGradientOcean: 'Ocean Breeze (Teal/Blue)',
        captureOptBgGradientAurora: 'Aurora Glow (Green/Purple)',
        captureOptBgSolidDark: 'Solid Dark (#1e1e1e)',
        captureOptBgSolidLight: 'Solid Light (#f3f4f6)',
        captureOptBgTransparent: 'Transparent',
        captureLayout: 'Window Layout',
        captureOptLayoutSplit: 'Side by Side (Code & Preview)',
        captureOptLayoutCode: 'Code Editor Only',
        captureOptLayoutPreview: 'Live Preview Only',
        capturePadding: 'Border Padding',
        captureOptPaddingNone: 'None (0px)',
        captureOptPaddingCompact: 'Compact (24px)',
        captureOptPaddingNormal: 'Standard (48px)',
        captureOptPaddingLarge: 'Generous (72px)',
        captureCodeFontSize: 'Code Font Size',
        captureOptFontSizeSmall: 'Very Small (11px)',
        captureOptFontSizeNormal: 'Normal (12.5px)',
        captureOptFontSizeLarge: 'Large (14px)',
        captureOptBranding: 'Branding Header',
        captureOptTitlebars: 'Window Title Bars',
        captureAssetsTitle: 'Files & Preview',
        captureOptLoadAssets: 'Load linked CSS/JS',
        captureOptShowAssets: 'Select project files:',
        captureOptNoAssets: 'No local references found',
        captureOptResetPositions: 'Reset Positions',
        captureClose: 'Close',
        captureExportBtn: 'Download Image',
        captureNoFileError: 'Please open a code file first to create a screenshot.',
        captureLoadingTitle: 'Loading Screenshot Designer...',
        captureLoadingDetail: 'Loading libraries and rendering layout previews...',
        captureFailed: 'Snapshot creation failed',
        captureExportingTitle: 'Exporting PNG image...',
        captureExportingDetail: 'Please wait...',
        captureExportFailed: 'Export failed',
        previewBadge: 'Preview',

        // --- Status Bar ---
        pwaInstall: 'Install as App',
        privacy: 'Privacy',

        // --- Settings Panel ---
        settingsTitle: 'Editor Settings',
        settingsSectionAppearance: 'Appearance',
        settingsTheme: 'Color Theme',
        settingsThemeVscodeDark: 'VS Code Dark',
        settingsThemeMidnightBlue: 'Midnight Blue',
        settingsThemeCyberpunk: 'Cyberpunk Neon',
        settingsThemeLight: 'Light',
        settingsThemeCustom: 'Custom',
        settingsCustomColors: 'Custom Colors',
        settingsColorBg: 'Background',
        settingsColorSidebar: 'Sidebar',
        settingsColorAccent: 'Accent Color',
        settingsColorText: 'Text Color',
        settingsFontFamily: 'Editor Font',
        settingsFontSystem: 'System Monospace',
        settingsFontSize: 'Editor Font Size',
        settingsLineHeight: 'Editor Line Height',
        settingsUiFontSize: 'UI Font Size',
        settingsSectionOptions: 'Editor Options',
        settingsWordWrapTitle: 'Word Wrap',
        settingsWordWrapDesc: 'Wrap long lines automatically',
        settingsMinimapTitle: 'Minimap',
        settingsMinimapDesc: 'Show minimap on the right',
        settingsFormatOnSaveTitle: 'Format on Save',
        settingsFormatOnSaveDesc: 'Format document automatically on save',

        // --- Privacy Modal ---
        privacyTitle: 'Privacy Policy – IDA HTML Editor',
        privacyOkBtn: 'Understood',
        privacyBodyHtml: `
            <div class="pm-callout green">
                <strong>Summary:</strong> IDA HTML is a pure browser-based editor. Your files, code, and settings <em>never</em> leave your browser without your explicit action. There is no account creation, no backend server, and no cloud storage.
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                    Data stored exclusively locally in your browser
                    <span class="pm-badge local">Local only</span>
                </h3>
                <p>The following data is stored on your device using your browser's <code>localStorage</code> API. It <strong>never</strong> leaves your browser and is not transmitted to any server:</p>
                <ul>
                    <li><strong>Your files &amp; projects</strong> – All code (HTML, CSS, JS, etc.) and file names you create or upload in the editor are saved exclusively in your browser's localStorage (key: <code>codeplay.files</code>). No server sees this data.</li>
                    <li><strong>Active tab, open tabs, active file</strong> – The state of your editor (which file you are currently editing, which tabs are open) is saved locally under <code>codeplay.state</code>.</li>
                    <li><strong>Settings</strong> – Color theme, font size, font family, line height, minimap, word wrap, format-on-save, and UI font size are saved locally (keys: <code>cleo.settings.*</code>).</li>
                    <li><strong>Custom colors</strong> – User-defined accent, background, and text colors (keys: <code>cleo.settings.custom*</code>).</li>
                    <li><strong>Onboarding status</strong> – Whether you have already seen the welcome screen (<code>codeplay.onboarded</code>).</li>
                    <li><strong>Language selection</strong> – Your selected language (German / English) under <code>codeplay.lang</code>.</li>
                    <li><strong>Empty folders</strong> – Any empty folder structures created by you (<code>codeplay.emptyFolders</code>).</li>
                    <li><strong>Live connection metadata</strong> – Name of the last connected local folder (when using the File System Access API), purely local.</li>
                </ul>
                <div class="pm-callout green">
                    You can delete all stored data at any time by clearing your browser data for this site (Settings → Privacy → Clear browsing data → Site data / localStorage).
                </div>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                    Local Resources (GDPR compliant / Offline-first)
                    <span class="pm-badge local">Local / Offline</span>
                </h3>
                <p>IDA HTML runs completely locally in your browser. All resources, libraries, and fonts are loaded directly from this website. <strong>No</strong> connections to external third-party servers or CDNs are established – your IP address is not shared with anyone.</p>
                <ul>
                    <li><strong>Monaco Editor (Locally hosted)</strong> – Microsoft's Monaco Editor is loaded locally from <code>lib/monaco/</code>.</li>
                    <li><strong>Google Fonts (Locally hosted)</strong> – The font "JetBrains Mono" is hosted locally (100% GDPR compliant).</li>
                    <li><strong>html2canvas (Locally hosted)</strong> – The screenshot library is loaded locally when needed.</li>
                    <li><strong>Prism.js &amp; Prism-CSS (Locally hosted)</strong> – Syntax highlighting for screenshots is loaded locally.</li>
                </ul>
                <div class="pm-callout">
                    <strong>Note on Cloudflare Tunnels:</strong> If you access IDA HTML through a Cloudflare Tunnel (e.g. via a public URL instead of <code>file://</code>), Cloudflare may see connection metadata – but <strong>none</strong> of your files or localStorage content.
                </div>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                    What IDA HTML definitely does NOT collect
                    <span class="pm-badge none">No data</span>
                </h3>
                <ul>
                    <li>❌ No tracking or analytics tools (no Google Analytics, no Matomo, no Plausible).</li>
                    <li>❌ No cookies (neither first-party nor third-party cookies from IDA HTML itself).</li>
                    <li>❌ No account, no registration, no credentials.</li>
                    <li>❌ Your code, files, or file names are never uploaded to any server.</li>
                    <li>❌ No telemetry about your actions in the editor.</li>
                    <li>❌ No disclosure of data to third parties by IDA HTML.</li>
                    <li>❌ No server-side session tracking.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                    Live connection with local folder (File System Access API)
                </h3>
                <p>If you use the "Live-edit a folder" or "Live-edit single files" feature (available only in Chrome/Edge), IDA HTML accesses files directly on your hard drive with your permission:</p>
                <ul>
                    <li><strong>Read/write access only to explicitly chosen files/folders</strong> – Access ends as soon as you close or reload the page.</li>
                    <li><strong>No network transmission</strong> – File contents are processed locally in the browser and written back directly.</li>
                    <li><strong>Permission by the browser</strong> – The browser shows its own permission prompt.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                    Screenshot Designer
                </h3>
                <p>When you use the Screenshot Designer, external libraries are loaded dynamically:</p>
                <ul>
                    <li><strong>html2canvas</strong> and <strong>Prism.js</strong> are loaded from <code>cdnjs.cloudflare.com</code>.</li>
                    <li>The generated PNG image is created <strong>exclusively locally</strong> in your browser.</li>
                    <li>Your code displayed in the screenshot remains fully on your device.</li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                    Your rights &amp; data deletion
                </h3>
                <p>Since IDA HTML does not store personal data on servers, no formal deletion request is required. You can delete all stored data yourself:</p>
                <ul>
                    <li><strong>Delete project data:</strong> Click "Reset project" in the editor or clear your browser's localStorage for this page.</li>
                    <li><strong>Browser settings:</strong> Browser settings → Privacy → Clear browsing data.</li>
                    <li><strong>Third-party privacy policies:</strong>
                        <ul>
                            <li>jsDelivr: <em>jsdelivr.com/privacy</em></li>
                            <li>Google Fonts: <em>policies.google.com/privacy</em></li>
                            <li>Cloudflare: <em>cloudflare.com/privacypolicy</em></li>
                        </ul>
                    </li>
                </ul>
            </div>

            <div class="pm-divider"></div>

            <div class="pm-section">
                <h3>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                    Changes to this policy
                </h3>
                <p>For major changes, the version number in the status bar will be updated.</p>
                <p style="color: var(--text-mute); font-size: 11.5px;">As of: June 2025 &nbsp;·&nbsp; IDA HTML v1.0</p>
            </div>
        `
    }
};
