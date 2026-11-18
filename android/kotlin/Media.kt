package com.kulbarts.chats

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import android.util.Size
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.FileInputStream

/**
 * Die letzten Fotos und Videos des Handys für das Plus-Menü der Chats-Seite (Bridge.media…).
 * Nur lesen, und nur Bilder und Videos aus MediaStore: Die Seite nennt eine ID, die Adresse baut dieser Code selbst –
 * es gibt keinen Weg, damit beliebige Dateien oder Pfade zu lesen.
 */
object Media {
    private const val ASK_CODE = 72
    private const val SELECTED = "android.permission.READ_MEDIA_VISUAL_USER_SELECTED"

    private fun wanted(): Array<String> = when {
        Build.VERSION.SDK_INT >= 34 -> arrayOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO, SELECTED)
        Build.VERSION.SDK_INT >= 33 -> arrayOf(Manifest.permission.READ_MEDIA_IMAGES, Manifest.permission.READ_MEDIA_VIDEO)
        else -> arrayOf(Manifest.permission.READ_EXTERNAL_STORAGE)
    }

    private fun has(c: Context, p: String): Boolean = ContextCompat.checkSelfPermission(c, p) == PackageManager.PERMISSION_GRANTED

    /** Darf die App Fotos lesen? Ab Android 14 genügt auch „nur ausgewählte Fotos“. */
    fun allowed(c: Context): Boolean = wanted().any { has(c, it) }

    fun ask(act: MainActivity) = ActivityCompat.requestPermissions(act, wanted(), ASK_CODE)

    private fun base(video: Boolean): Uri =
        if (video) MediaStore.Video.Media.EXTERNAL_CONTENT_URI else MediaStore.Images.Media.EXTERNAL_CONTENT_URI

    /** [{id, video, dur}] der neuesten Fotos und Videos, neueste zuerst. */
    fun list(c: Context, limit: Int): String {
        val out = JSONArray()
        val colId = MediaStore.Files.FileColumns._ID
        val colType = MediaStore.Files.FileColumns.MEDIA_TYPE
        val typeVideo = MediaStore.Files.FileColumns.MEDIA_TYPE_VIDEO
        val cols = arrayOf(colId, colType) + (if (Build.VERSION.SDK_INT >= 29) arrayOf("duration") else emptyArray<String>())
        val sel = "$colType IN (?, ?)"
        val args = arrayOf(MediaStore.Files.FileColumns.MEDIA_TYPE_IMAGE.toString(), typeVideo.toString())
        val order = "${MediaStore.Files.FileColumns.DATE_ADDED} DESC"
        c.contentResolver.query(MediaStore.Files.getContentUri("external"), cols, sel, args, order)?.use { q ->
            val iId = q.getColumnIndexOrThrow(colId)
            val iType = q.getColumnIndexOrThrow(colType)
            val iDur = q.getColumnIndex("duration")
            while (out.length() < limit && q.moveToNext()) {
                out.put(
                    JSONObject()
                        .put("id", q.getLong(iId).toString())
                        .put("video", q.getInt(iType) == typeVideo)
                        .put("dur", if (iDur >= 0) q.getLong(iDur) / 1000 else 0L)
                )
            }
        }
        return out.toString()
    }

    /** Kleines Vorschaubild als JPEG in Base64 (ohne Zeilenumbrüche). */
    fun thumb(c: Context, id: Long, video: Boolean): String {
        val uri = ContentUris.withAppendedId(base(video), id)
        val bmp: Bitmap = (if (Build.VERSION.SDK_INT >= 29) c.contentResolver.loadThumbnail(uri, Size(256, 256), null) else legacy(c, id, video)) ?: return ""
        val bos = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.JPEG, 72, bos)
        return Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP)
    }

    @Suppress("DEPRECATION")
    private fun legacy(c: Context, id: Long, video: Boolean): Bitmap? =
        if (video) MediaStore.Video.Thumbnails.getThumbnail(c.contentResolver, id, MediaStore.Video.Thumbnails.MINI_KIND, null)
        else MediaStore.Images.Thumbnails.getThumbnail(c.contentResolver, id, MediaStore.Images.Thumbnails.MINI_KIND, null)

    /** {name, mime, size} – oder {}, wenn es die Datei nicht (mehr) gibt. */
    fun info(c: Context, id: Long, video: Boolean): String {
        val uri = ContentUris.withAppendedId(base(video), id)
        val cols = arrayOf(MediaStore.MediaColumns.DISPLAY_NAME, MediaStore.MediaColumns.MIME_TYPE, MediaStore.MediaColumns.SIZE)
        c.contentResolver.query(uri, cols, null, null, null)?.use { q ->
            if (q.moveToFirst()) {
                return JSONObject()
                    .put("name", q.getString(0) ?: "datei")
                    .put("mime", q.getString(1) ?: "application/octet-stream")
                    .put("size", q.getLong(2))
                    .toString()
            }
        }
        return "{}"
    }

    /** Ein Stück der Datei (ab off, höchstens len Bytes, höchstens 2 MB) als Base64 – so kommt auch ein Video über die Brücke. */
    fun chunk(c: Context, id: Long, video: Boolean, off: Int, len: Int): String {
        val uri = ContentUris.withAppendedId(base(video), id)
        c.contentResolver.openFileDescriptor(uri, "r")?.use { pfd ->
            FileInputStream(pfd.fileDescriptor).use { s ->
                s.channel.position(off.toLong())
                val buf = ByteArray(len.coerceIn(1, 2 * 1024 * 1024))
                var n = 0
                while (n < buf.size) {
                    val r = s.read(buf, n, buf.size - n)
                    if (r < 0) break
                    n += r
                }
                return Base64.encodeToString(buf, 0, n, Base64.NO_WRAP)
            }
        }
        return ""
    }
}
