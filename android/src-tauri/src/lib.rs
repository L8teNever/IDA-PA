//! IDA Chats für Android: eine schlanke Hülle (Tauri 2), die die Chats-Seite in der System-WebView zeigt.
//! Im Hintergrund läuft nichts – kein Dienst, kein Timer, keine offene Verbindung (Akku). Siehe docs/CHATS-ANDROID.md.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("IDA Chats konnte nicht starten");
}
