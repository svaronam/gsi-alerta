package com.sergio.gsialerta

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.media.AudioAttributes
import android.media.RingtoneManager
import android.net.Uri
import org.json.JSONObject

object Notificador {
    const val CANAL_RESULTADO = "resultado"
    const val CANAL_NOVEDADES = "novedades"
    const val CANAL_VIGILANCIA = "vigilancia"
    const val ID_VIGILANCIA = 1

    fun crearCanales(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        val resultado = NotificationChannel(
            CANAL_RESULTADO, "Notas del 2.º ejercicio", NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "El aviso importante: si apruebas o no"
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 600, 300, 600, 300, 600)
            enableLights(true)
            lightColor = Color.YELLOW
            setSound(
                RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
            )
        }
        val novedades = NotificationChannel(
            CANAL_NOVEDADES, "Novedades de la web", NotificationManager.IMPORTANCE_DEFAULT
        ).apply { description = "PDF nuevos, errores y pruebas" }
        val vigilancia = NotificationChannel(
            CANAL_VIGILANCIA, "Vigilancia en marcha", NotificationManager.IMPORTANCE_MIN
        ).apply {
            description = "Aviso fijo mientras la app escucha a la Pi"
            setShowBadge(false)
        }
        nm.createNotificationChannels(listOf(resultado, novedades, vigilancia))
    }

    private fun abrir(ctx: Context, url: String, codigo: Int): PendingIntent {
        val intent = if (url.isNotBlank()) {
            Intent(Intent.ACTION_VIEW, Uri.parse(url))
        } else {
            Intent(ctx, MainActivity::class.java)
        }.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return PendingIntent.getActivity(
            ctx, codigo, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /** Notifica un aviso que viene de la Pi. */
    fun aviso(ctx: Context, e: JSONObject) {
        crearCanales(ctx)
        val tipo = e.optString("tipo")
        val importante = tipo == "resultado" || tipo == "publicado"
        val id = 1000 + e.optInt("id")
        val texto = e.optString("texto")
        val n = Notification.Builder(ctx, if (importante) CANAL_RESULTADO else CANAL_NOVEDADES)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(e.optString("titulo"))
            .setContentText(texto)
            .setStyle(Notification.BigTextStyle().bigText(texto))
            .setColor(Color.parseColor("#C8102E"))
            .setAutoCancel(true)
            .setWhen((e.optDouble("ts", 0.0) * 1000).toLong().takeIf { it > 0 } ?: System.currentTimeMillis())
            .setShowWhen(true)
            .setCategory(if (importante) Notification.CATEGORY_EVENT else Notification.CATEGORY_STATUS)
            .setContentIntent(abrir(ctx, e.optString("url"), id))
            .build()
        try {
            ctx.getSystemService(NotificationManager::class.java).notify(id, n)
        } catch (ex: SecurityException) {
            // sin permiso de notificaciones: se verá al abrir la app
        }
    }

    /** Notificación fija del servicio en primer plano. */
    fun vigilancia(ctx: Context, texto: String): Notification {
        crearCanales(ctx)
        return Notification.Builder(ctx, CANAL_VIGILANCIA)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("GSI-ALERTA vigilando")
            .setContentText(texto)
            .setOngoing(true)
            .setContentIntent(abrir(ctx, "", 0))
            .build()
    }

    fun actualizarVigilancia(ctx: Context, texto: String) {
        try {
            ctx.getSystemService(NotificationManager::class.java)
                .notify(ID_VIGILANCIA, vigilancia(ctx, texto))
        } catch (ex: SecurityException) {
        }
    }
}
