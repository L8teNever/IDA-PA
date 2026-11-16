// Nur für den Start am PC (zum Ausprobieren); auf Android zählt lib.rs.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    ida_chats_lib::run()
}
