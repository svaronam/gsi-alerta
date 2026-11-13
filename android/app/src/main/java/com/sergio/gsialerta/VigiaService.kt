package com.sergio.gsialerta

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Servicio en primer plano que mantiene un long-poll con la Pi: en cuanto la Pi
 * genera un aviso, la petición vuelve y se notifica al momento (sin esperar a
 * los 15 min de WorkManager).
 */
class VigiaService : Service() {
    @Volatile private var activo = false
    private var hilo: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val tipo = if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0
        ServiceCompat.startForeground(
            this, Notificador.ID_VIGILANCIA,
            Notificador.vigilancia(this, "Conectando con la Pi…"), tipo
        )
        if (hilo?.isAlive != true) {
            activo = true
            hilo = Thread({ bucle() }, "vigia").apply { start() }
        }
        return START_STICKY
    }

    private fun bucle() {
        var fallos = 0
        val fmt = SimpleDateFormat("HH:mm:ss", Locale("es", "ES"))
        while (activo) {
            if (!Prefs.configurado(this)) {
                Notificador.actualizarVigilancia(this, "Falta configurar servidor y clave")
                dormir(30)
                continue
            }
            try {
                val r = Api.recogerAvisos(this, 50)
                fallos = 0
                val ts = r.optDouble("ultima_barrida", 0.0)
                val cuando = if (ts > 0) fmt.format(Date((ts * 1000).toLong())) else "—"
                Notificador.actualizarVigilancia(this, "Última barrida de la Pi: $cuando")
            } catch (e: Exception) {
                fallos++
                Notificador.actualizarVigilancia(
                    this, "Sin conexión con la Pi (${e.message ?: e.javaClass.simpleName}). ¿Tailscale activo?"
                )
                dormir(minOf(60, 5 * fallos))
            }
        }
    }

    private fun dormir(seg: Int) {
        try { Thread.sleep(seg * 1000L) } catch (e: InterruptedException) { }
    }

    override fun onDestroy() {
        activo = false
        hilo?.interrupt()
        super.onDestroy()
    }

    companion object {
        fun arrancar(ctx: Context) {
            try {
                ctx.startForegroundService(Intent(ctx, VigiaService::class.java))
            } catch (e: Exception) {
                // Android no deja arrancarlo desde segundo plano; el worker sigue avisando
            }
            programarWorker(ctx)
        }

        fun parar(ctx: Context) {
            ctx.stopService(Intent(ctx, VigiaService::class.java))
        }

        fun programarWorker(ctx: Context) {
            val req = PeriodicWorkRequestBuilder<CompruebaWorker>(15, TimeUnit.MINUTES).build()
            WorkManager.getInstance(ctx)
                .enqueueUniquePeriodicWork("gsi-avisos", ExistingPeriodicWorkPolicy.KEEP, req)
        }
    }
}

/** Respaldo cada 15 min por si Android mata el servicio. */
class CompruebaWorker(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        if (!Prefs.configurado(applicationContext)) return Result.success()
        return try {
            Api.recogerAvisos(applicationContext, 0)
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }
}
