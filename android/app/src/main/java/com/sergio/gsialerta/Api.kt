package com.sergio.gsialerta

import android.content.Context
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** Ajustes guardados en el móvil. */
object Prefs {
    private fun p(ctx: Context) = ctx.getSharedPreferences("gsialerta", Context.MODE_PRIVATE)

    fun servidor(ctx: Context): String = p(ctx).getString("servidor", "http://homelab2:8806")!!
    fun clave(ctx: Context): String = p(ctx).getString("clave", "")!!
    fun configurado(ctx: Context) = clave(ctx).isNotBlank()

    fun guardar(ctx: Context, servidor: String, clave: String) {
        p(ctx).edit()
            .putString("servidor", servidor.trim().trimEnd('/'))
            .putString("clave", clave.trim())
            .remove("ultimo_id")
            .apply()
    }

    /** Último aviso ya notificado; -1 = aún no sincronizado con la Pi. */
    fun ultimoId(ctx: Context): Int = p(ctx).getInt("ultimo_id", -1)
    fun setUltimoId(ctx: Context, id: Int) = p(ctx).edit().putInt("ultimo_id", id).apply()
}

/** La Pi ha respondido, pero con un error (clave mala, ruta que no conoce...). */
class RespuestaPi(val codigo: Int, mensaje: String) : IOException(mensaje)

/** Llamadas HTTP a la Pi (bloqueantes: nunca desde el hilo principal). */
object Api {
    fun get(ctx: Context, ruta: String, timeoutMs: Int = 15000): JSONObject =
        llamar(ctx, "GET", ruta, timeoutMs)

    fun post(ctx: Context, ruta: String, timeoutMs: Int = 60000): JSONObject =
        llamar(ctx, "POST", ruta, timeoutMs)

    private fun llamar(ctx: Context, metodo: String, ruta: String, timeoutMs: Int): JSONObject {
        val con = URL(Prefs.servidor(ctx) + ruta).openConnection() as HttpURLConnection
        try {
            con.requestMethod = metodo
            con.connectTimeout = 10000
            con.readTimeout = timeoutMs
            con.setRequestProperty("X-API-Key", Prefs.clave(ctx))
            con.setRequestProperty("Accept", "application/json")
            if (metodo == "POST") {
                con.doOutput = true
                con.setFixedLengthStreamingMode(0)
                con.outputStream.close()
            }
            val codigo = con.responseCode
            val flujo = if (codigo < 400) con.inputStream else con.errorStream
            val cuerpo = flujo?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            val json = try { JSONObject(cuerpo) } catch (e: Exception) { JSONObject() }
            if (codigo >= 400) {
                throw RespuestaPi(codigo, json.optString("error", "HTTP $codigo"))
            }
            return json
        } finally {
            con.disconnect()
        }
    }

    /**
     * Pide los avisos nuevos (long-poll de hasta [espera] s) y los notifica.
     * Lo usan el servicio y el worker; synchronized para no notificar dos veces.
     */
    @Synchronized
    fun recogerAvisos(ctx: Context, espera: Int): JSONObject {
        val desde = Prefs.ultimoId(ctx)
        if (desde < 0) {
            // Primera conexión: no reenvío el histórico; el estado se ve en la app
            val estado = get(ctx, "/api/estado")
            Prefs.setUltimoId(ctx, estado.optInt("ultimo_evento", 0))
            return JSONObject().put("ultima_barrida", estado.opt("ultima_barrida"))
        }
        val r = get(ctx, "/api/eventos?desde=$desde&espera=$espera", (espera + 20) * 1000)
        val eventos = r.optJSONArray("eventos")
        var maximo = desde
        if (eventos != null) {
            for (i in 0 until eventos.length()) {
                val e = eventos.getJSONObject(i)
                val id = e.optInt("id")
                if (id > Prefs.ultimoId(ctx)) Notificador.aviso(ctx, e)
                if (id > maximo) maximo = id
            }
        }
        // si la Pi empezó de cero (borraste estado.json), me resincronizo
        val ultimoServidor = r.optInt("ultimo_id", maximo)
        Prefs.setUltimoId(ctx, if (ultimoServidor < desde) ultimoServidor else maximo)
        return r
    }
}
