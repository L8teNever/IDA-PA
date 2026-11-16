package com.kulbarts.chats

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebView

/**
 * Links aus den Chats öffnen außerhalb der App – in der passenden App (Instagram, YouTube, Maps …) oder im Browser.
 *
 * Ohne das lud die WebView fremde Seiten selbst: Instagram leitete dann auf instagram:// weiter, und die App zeigte
 * „Webseite nicht verfügbar – ERR_UNKNOWN_URL_SCHEME“. Wird von RustWebViewClient.shouldOverrideUrlLoading aufgerufen
 * (die Zeile setzt android/scripts/patch-android.py beim Bauen ein).
 *
 * In der App bleiben: die eigenen Seiten (*.kulbarts.com), Weiterleitungen und alles, was nicht angetippt wurde – so läuft die
 * Anmeldung über Cloudflare und Google weiter in der App. Eingebettete Player (iframes) sind nicht betroffen, nur die Hauptseite.
 */
object Links {
    private fun own(host: String?) = host != null && (host == "kulbarts.com" || host.endsWith(".kulbarts.com"))

    /** true = erledigt (extern geöffnet), die WebView lädt nichts. */
    fun external(view: WebView, r: WebResourceRequest): Boolean {
        if (!r.isForMainFrame) return false
        val u = r.url
        val scheme = (u.scheme ?: "").lowercase()
        if (scheme in setOf("about", "data", "blob", "javascript", "file")) return false
        if (scheme != "http" && scheme != "https") return open(view.context, u)   // instagram://, intent://, mailto:, tel: …
        if (r.isRedirect || !r.hasGesture() || own(u.host)) return false
        if (!own(Uri.parse(view.url ?: "").host)) return false   // gerade in der Anmeldung (Cloudflare, Google …): dort bleiben
        return open(view.context, u)
    }

    private fun open(ctx: Context, u: Uri): Boolean {
        try {
            val i = if (u.scheme == "intent") Intent.parseUri(u.toString(), Intent.URI_INTENT_SCHEME).apply { component = null; selector = null }
                    else Intent(Intent.ACTION_VIEW, u)
            i.addCategory(Intent.CATEGORY_BROWSABLE).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            try {
                ctx.startActivity(i)
            } catch (e: Exception) {   // keine App dafür: Ersatzadresse aus intent:// (z. B. die Webseite), sonst nichts
                i.getStringExtra("browser_fallback_url")?.let {
                    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(it)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }
        } catch (e: Exception) { /* nicht zu öffnen – dann eben nicht, aber auch keine Fehlerseite */ }
        return true
    }
}
