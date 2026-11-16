package com.kulbarts.chats

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.database.Cursor
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Rect
import android.net.Uri
import android.os.Build
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.Person
import androidx.core.content.ContextCompat
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Benachrichtigungen der Android-App über Googles FCM.
 *
 * In der APK steckt KEINE Firebase-Konfiguration (das Repo ist öffentlich): Die Chats-Seite holt die Verbindungsdaten nach der
 * Anmeldung vom eigenen Server und gibt sie über [Bridge.pushSetup] herein. Sie werden hier gemerkt, damit die App auch nach einem
 * Neustart des Handys Meldungen annehmen kann, ohne dass die Seite offen ist ([PushInit]).
 * Im Hintergrund läuft nichts: Google weckt die App nur, wenn eine Nachricht kommt.
 */
object Push {
    private const val PREFS = "ida_push"
    private const val CHANNEL = "chats"
    @Volatile var visible = false          // App gerade im Vordergrund: dann zeigt die Seite selbst den Hinweis
    @Volatile var pending: String? = null   // Chat aus einer angetippten Meldung, den die Seite beim Start öffnen soll

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    fun enabled(ctx: Context) = prefs(ctx).getBoolean("on", false)

    /** Firebase mit den gemerkten Daten einrichten. false = noch nichts gemerkt oder Daten unbrauchbar. */
    fun init(ctx: Context): Boolean {
        val raw = prefs(ctx).getString("cfg", null) ?: return false
        return try {
            val c = JSONObject(raw)
            val opt = FirebaseOptions.Builder()
                .setApiKey(c.getString("api_key")).setApplicationId(c.getString("app_id"))
                .setProjectId(c.getString("project_id")).setGcmSenderId(c.getString("sender_id")).build()
            val app = FirebaseApp.getApps(ctx).firstOrNull { it.name == FirebaseApp.DEFAULT_APP_NAME }
            if (app != null && app.options != opt) app.delete()   // anderes Firebase-Projekt hochgeladen
            if (app == null || app.options != opt) FirebaseApp.initializeApp(ctx.applicationContext, opt)
            true
        } catch (e: Exception) {
            false
        }
    }

    fun setup(act: MainActivity, cfg: String, done: (token: String?, error: String?) -> Unit) {
        prefs(act).edit().putString("cfg", cfg).apply()
        if (!init(act)) return done(null, "Die Firebase-Daten vom Server sind unvollständig")
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(act, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            ActivityCompat.requestPermissions(act, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 71)
        try {
            FirebaseMessaging.getInstance().token.addOnCompleteListener { t ->
                if (t.isSuccessful && t.result != null) { prefs(act).edit().putBoolean("on", true).apply(); done(t.result, null) }
                else done(null, t.exception?.message ?: "Google hat kein Token vergeben")
            }
        } catch (e: Exception) {
            done(null, e.message ?: "Firebase ließ sich nicht starten")
        }
    }

    fun off(ctx: Context) {
        prefs(ctx).edit().putBoolean("on", false).apply()
        try { if (init(ctx)) FirebaseMessaging.getInstance().deleteToken() } catch (e: Exception) { /* egal */ }
    }

    /** Adresse der Chats-Seite merken: von dort holt [avatar] das Profilbild. */
    fun origin(ctx: Context, o: String) = prefs(ctx).edit().putString("origin", o).apply()

    /**
     * Profilbild des Chats, rund zugeschnitten – oder null (dann bleibt es beim App-Symbol). Die Anmeldung (Cloudflare) kommt aus den
     * Cookies der WebView. Das Bild liegt einen Tag im Cache der App, damit nicht jede Meldung das Netz braucht.
     */
    private fun avatar(ctx: Context, av: String): Bitmap? {
        if (av.isEmpty()) return null
        return try {
            val f = File(ctx.cacheDir, "av-" + Integer.toHexString(av.hashCode()))
            if (!f.exists() || System.currentTimeMillis() - f.lastModified() > 86_400_000L) {
                val url = (prefs(ctx).getString("origin", null) ?: "https://chats.kulbarts.com") + "/api/v1/avatar/" + Uri.encode(av)
                val c = URL(url).openConnection() as HttpURLConnection
                try {
                    c.connectTimeout = 4000
                    c.readTimeout = 4000
                    c.instanceFollowRedirects = false   // Umleitung = Anmeldung abgelaufen
                    CookieManager.getInstance().getCookie(url)?.let { c.setRequestProperty("Cookie", it) }
                    if (c.responseCode != 200 || c.contentType?.startsWith("image/") != true) return null
                    f.writeBytes(c.inputStream.use { it.readBytes() })
                } finally {
                    c.disconnect()
                }
            }
            val src = BitmapFactory.decodeFile(f.path) ?: return null
            val n = 192
            val s = minOf(src.width, src.height)
            val out = Bitmap.createBitmap(n, n, Bitmap.Config.ARGB_8888)
            val cv = Canvas(out)
            val p = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
            cv.drawCircle(n / 2f, n / 2f, n / 2f, p)
            p.xfermode = PorterDuffXfermode(PorterDuff.Mode.SRC_IN)
            cv.drawBitmap(src, Rect((src.width - s) / 2, (src.height - s) / 2, (src.width + s) / 2, (src.height + s) / 2), Rect(0, 0, n, n), p)
            out
        } catch (e: Exception) {
            null
        }
    }

    fun allowed(ctx: Context) = NotificationManagerCompat.from(ctx).areNotificationsEnabled()

    /** Meldung zeigen: eine pro Chat (neue ersetzt die alte), mit dem Profilbild des Chats, Tipp öffnet den Chat. */
    fun show(ctx: Context, d: Map<String, String>) {
        if (!allowed(ctx)) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Nachrichten", NotificationManager.IMPORTANCE_HIGH))
        val chat = d["chat"].orEmpty()
        val open = Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra("chat", chat)
        val pi = PendingIntent.getActivity(ctx, chat.hashCode(), open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val body = d["body"].orEmpty()
        val title = d["title"]?.takeIf { it.isNotEmpty() } ?: "Chats"
        val b = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(title).setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
        val pic = avatar(ctx, d["av"].orEmpty())
        if (pic != null && chat.isNotEmpty()) {
            // Als Unterhaltung zeigt Android das Profilbild vorn anstelle des App-Symbols.
            val icon = IconCompat.createWithBitmap(pic)
            val who = Person.Builder().setName(title).setKey(chat).setIcon(icon).build()
            b.setLargeIcon(pic).setStyle(NotificationCompat.MessagingStyle(Person.Builder().setName("Ich").build())
                .addMessage(body, System.currentTimeMillis(), who))
            try {
                val sc = ShortcutInfoCompat.Builder(ctx, "chat-$chat").setShortLabel(title).setLongLived(true).setIcon(icon).setPerson(who)
                    .setIntent(Intent(open).setAction(Intent.ACTION_VIEW)).build()
                if (ShortcutManagerCompat.pushDynamicShortcut(ctx, sc)) b.setShortcutId("chat-$chat")
            } catch (e: Exception) { /* ohne Kurzbefehl bleibt es eine normale Meldung mit Bild */ }
        }
        val n = b.setCategory(NotificationCompat.CATEGORY_MESSAGE).setPriority(NotificationCompat.PRIORITY_HIGH)
            .setNumber(d["unread"]?.toIntOrNull() ?: 0)
            .setAutoCancel(true).setContentIntent(pi).build()
        try { NotificationManagerCompat.from(ctx).notify("chat-$chat", 1, n) } catch (e: SecurityException) { /* Erlaubnis entzogen */ }
    }
}

/** Nimmt die Meldungen von Google an – auch wenn die App zu ist. */
class PushService : FirebaseMessagingService() {
    override fun onMessageReceived(m: RemoteMessage) {
        if (!Push.enabled(this)) return
        if (Push.visible && m.data["test"] != "1") return   // App offen: die Seite zeigt den Hinweis selbst
        Push.show(this, m.data)
    }
    // Neues Token: die Seite meldet es beim nächsten Start der App am Server an (dort liegt die Anmeldung).
    override fun onNewToken(token: String) { }
}

/** Läuft beim Start des App-Prozesses noch vor allem anderen: richtet Firebase mit den gemerkten Daten ein. */
class PushInit : ContentProvider() {
    override fun onCreate(): Boolean { context?.let { Push.init(it) }; return true }
    override fun query(u: Uri, p: Array<String>?, s: String?, a: Array<String>?, o: String?): Cursor? = null
    override fun getType(u: Uri): String? = null
    override fun insert(u: Uri, v: ContentValues?): Uri? = null
    override fun delete(u: Uri, s: String?, a: Array<String>?): Int = 0
    override fun update(u: Uri, v: ContentValues?, s: String?, a: Array<String>?): Int = 0
}

/** Das, was die Chats-Seite von der App nutzen darf (window.IDAChatsNative). Nur auf den eigenen Adressen. */
class Bridge(private val act: MainActivity, private val web: WebView) {
    private fun own(): Boolean {
        val h = Uri.parse(web.url ?: "").host ?: return false
        return h == "kulbarts.com" || h.endsWith(".kulbarts.com")
    }
    private fun event(name: String, detail: JSONObject) =
        web.evaluateJavascript("window.dispatchEvent(new CustomEvent(${JSONObject.quote(name)},{detail:$detail}))", null)

    @JavascriptInterface fun pushState(): String = if (!Push.allowed(act) && Push.enabled(act)) "denied" else if (Push.enabled(act)) "on" else "off"

    @JavascriptInterface fun pushSetup(cfg: String) = act.runOnUiThread {
        if (!own()) return@runOnUiThread
        Uri.parse(web.url ?: "").let { u -> if (u.scheme == "https" && u.host != null) Push.origin(act, "https://${u.host}") }
        Push.setup(act, cfg) { token, error ->
            act.runOnUiThread { event("ida-fcm", JSONObject().put("token", token ?: JSONObject.NULL).put("error", error ?: JSONObject.NULL)) }
        }
    }

    @JavascriptInterface fun pushOff() = act.runOnUiThread { if (own()) Push.off(act) }

    @JavascriptInterface fun pendingChat(): String { val p = Push.pending.orEmpty(); Push.pending = null; return p }

    fun open(chat: String) = act.runOnUiThread { event("ida-open", JSONObject().put("chat", chat)) }
}
