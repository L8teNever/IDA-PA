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
import android.net.Uri
import android.os.Build
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import org.json.JSONObject

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

    fun allowed(ctx: Context) = NotificationManagerCompat.from(ctx).areNotificationsEnabled()

    /** Meldung zeigen: eine pro Chat (neue ersetzt die alte), Tipp öffnet den Chat. */
    fun show(ctx: Context, d: Map<String, String>) {
        if (!allowed(ctx)) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Nachrichten", NotificationManager.IMPORTANCE_HIGH))
        val chat = d["chat"].orEmpty()
        val open = Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP).putExtra("chat", chat)
        val pi = PendingIntent.getActivity(ctx, chat.hashCode(), open, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val body = d["body"].orEmpty()
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(d["title"] ?: "Chats").setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setCategory(NotificationCompat.CATEGORY_MESSAGE).setPriority(NotificationCompat.PRIORITY_HIGH)
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
        Push.setup(act, cfg) { token, error ->
            act.runOnUiThread { event("ida-fcm", JSONObject().put("token", token ?: JSONObject.NULL).put("error", error ?: JSONObject.NULL)) }
        }
    }

    @JavascriptInterface fun pushOff() = act.runOnUiThread { if (own()) Push.off(act) }

    @JavascriptInterface fun pendingChat(): String { val p = Push.pending.orEmpty(); Push.pending = null; return p }

    fun open(chat: String) = act.runOnUiThread { event("ida-open", JSONObject().put("chat", chat)) }
}
