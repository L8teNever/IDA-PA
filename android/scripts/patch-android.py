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

# ---- Kotlin: Push/Brücke dazulegen, MainActivity erweitern ----
if not SRC.is_dir():
    fail(f"{SRC} fehlt – anderer Paketname?")
shutil.copy("kotlin/Push.kt", SRC / "Push.kt")
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
