#!/usr/bin/env python3
"""Passt das von „tauri android init“ erzeugte Android-Projekt an (src-tauri/gen/android liegt nicht im Repo):
Rechte, Firebase Messaging (ohne google-services.json – die Daten kommen zur Laufzeit vom Server) und die Brücke zur Chats-Seite."""
import pathlib
import re
import shutil
import sys

APP = pathlib.Path("src-tauri/gen/android/app")
PKG = "com.kulbarts.chats"
SRC = APP / "src/main/java" / PKG.replace(".", "/")


def fail(msg):
    sys.exit(f"patch-android: {msg}")


# ---- Manifest: Rechte, Dienst für FCM, Start-Einrichtung ----
mf = APP / "src/main/AndroidManifest.xml"
m = mf.read_text()
for perm in ("RECORD_AUDIO", "MODIFY_AUDIO_SETTINGS", "CAMERA", "POST_NOTIFICATIONS"):
    if f'android.permission.{perm}"' not in m:
        m = m.replace("<application", f'<uses-permission android:name="android.permission.{perm}" />\n    <application', 1)
if "PushService" not in m:
    if "</application>" not in m:
        fail("kein </application> im Manifest")
    m = m.replace("</application>", f'''    <service android:name="{PKG}.PushService" android:exported="false">
            <intent-filter><action android:name="com.google.firebase.MESSAGING_EVENT" /></intent-filter>
        </service>
        <provider android:name="{PKG}.PushInit" android:authorities="${{applicationId}}.pushinit" android:exported="false" android:initOrder="50" />
    </application>''', 1)
mf.write_text(m)

# ---- Gradle: Firebase Messaging ----
g = APP / "build.gradle.kts"
t = g.read_text()
if "firebase-messaging" not in t:
    if not re.search(r"^dependencies\s*\{", t, re.M):
        fail("kein dependencies-Block in app/build.gradle.kts")
    t = re.sub(r"^dependencies\s*\{", 'dependencies {\n    implementation(platform("com.google.firebase:firebase-bom:33.7.0"))\n    implementation("com.google.firebase:firebase-messaging")', t, count=1, flags=re.M)
    g.write_text(t)
pg = APP / "proguard-rules.pro"
keep = f"-keep class {PKG}.Push** {{ *; }}\n-keep class {PKG}.Bridge {{ *; }}\n"
if pg.exists() and keep not in pg.read_text():
    pg.write_text(pg.read_text() + "\n" + keep)

# ---- App-Symbol: „tauri icon“ nimmt das ganze Bild als Vordergrund, der Launcher schneidet es dann an (nur die Mitte ist sichtbar).
# Deshalb eigene Vordergründe mit Rand (android/icons, aus frontend/chats/icons/maskable-512.png auf 85 % verkleinert). ----
RES = APP / "src/main/res"
icons = sorted(pathlib.Path("icons").glob("mipmap-*/ic_launcher_foreground.png"))
if len(icons) != 5:
    fail("android/icons unvollständig")
for src in icons:
    (RES / src.parent.name).mkdir(parents=True, exist_ok=True)
    shutil.copy(src, RES / src.parent.name / src.name)
(RES / "values").mkdir(parents=True, exist_ok=True)
(RES / "values/ida_icon.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ida_icon_bg">#153321</color>\n</resources>\n')
adaptive = ('<?xml version="1.0" encoding="utf-8"?>\n<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@color/ida_icon_bg" />\n    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n</adaptive-icon>\n')
(RES / "mipmap-anydpi-v26").mkdir(parents=True, exist_ok=True)
(RES / "mipmap-anydpi-v26/ic_launcher.xml").write_text(adaptive)
if (RES / "mipmap-hdpi/ic_launcher_round.png").exists() or (RES / "mipmap-anydpi-v26/ic_launcher_round.xml").exists():
    (RES / "mipmap-anydpi-v26/ic_launcher_round.xml").write_text(adaptive)

# ---- Kotlin: Push/Brücke dazulegen, MainActivity erweitern ----
if not SRC.is_dir():
    fail(f"{SRC} fehlt – anderer Paketname?")
shutil.copy("kotlin/Push.kt", SRC / "Push.kt")
shutil.copy("kotlin/Links.kt", SRC / "Links.kt")

# ---- Links: fremde Seiten nicht in der App laden, sondern in der passenden App/im Browser öffnen (Links.kt) ----
wvc = next(APP.glob("src/main/java/**/RustWebViewClient.kt"), None)
if wvc is None:
    fail("RustWebViewClient.kt nicht gefunden")
w = wvc.read_text()
if "Links.external" not in w:
    w, n = re.subn(r"(\n(\s*)return Rust\.shouldOverride\()", r"\n\2if (com.kulbarts.chats.Links.external(view, request)) return true\1", w, count=1)
    if n != 1:
        fail("shouldOverrideUrlLoading in RustWebViewClient.kt hat sich geändert")
    wvc.write_text(w)
ma = SRC / "MainActivity.kt"
a = ma.read_text()
if "IDAChatsNative" not in a:
    extra = '''
  // --- IDA Chats: Brücke zur Seite und Meldungen (siehe Push.kt) ---
  private var bridge: Bridge? = null

  override fun onWebViewCreate(webView: android.webkit.WebView) {
    super.onWebViewCreate(webView)
    Push.pending = intent?.getStringExtra("chat")?.takeIf { it.isNotEmpty() }
    bridge = Bridge(this, webView).also { webView.addJavascriptInterface(it, "IDAChatsNative") }
  }

  override fun onNewIntent(intent: android.content.Intent) {
    super.onNewIntent(intent)
    intent.getStringExtra("chat")?.takeIf { it.isNotEmpty() }?.let { bridge?.open(it) }
  }

  override fun onResume() { super.onResume(); Push.visible = true }
  override fun onPause() { super.onPause(); Push.visible = false }
'''
    if re.search(r"class MainActivity[^{\n]*\{", a):
        i = a.rstrip().rfind("}")
        a = a[:i] + extra + "}\n"
    else:
        a = re.sub(r"(class MainActivity[^\n]*)", lambda x: x.group(1) + " {" + extra + "}", a, count=1)
    ma.write_text(a)

print(m)
print(a)
print(re.search(r"dependencies\s*\{.*", t, re.S).group(0)[:600])
