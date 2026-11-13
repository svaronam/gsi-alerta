package com.sergio.gsialerta

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private val fmt = SimpleDateFormat("dd/MM HH:mm:ss", Locale("es", "ES"))

    private lateinit var tvVeredicto: TextView
    private lateinit var tvDetalle: TextView
    private lateinit var tvInfo: TextView
    private lateinit var tvEventos: TextView
    private lateinit var btnPdf: Button
    private lateinit var etServidor: EditText
    private lateinit var etClave: EditText
    private var urlPdf = ""
    private var urlWeb = ""

    private val refresco = object : Runnable {
        override fun run() {
            cargar()
            ui.postDelayed(this, 15000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notificador.crearCanales(this)
        setContentView(construir())
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        if (Prefs.configurado(this)) VigiaService.arrancar(this)
    }

    override fun onResume() {
        super.onResume()
        ui.post(refresco)
    }

    override fun onPause() {
        ui.removeCallbacks(refresco)
        super.onPause()
    }

    // ------------------------------------------------------------------ vista
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun texto(tam: Float, negrita: Boolean = false, color: Int = Color.WHITE) =
        TextView(this).apply {
            textSize = tam
            setTextColor(color)
            if (negrita) setTypeface(typeface, Typeface.BOLD)
            setPadding(0, dp(4), 0, dp(4))
        }

    private fun boton(etiqueta: String, accion: () -> Unit) = Button(this).apply {
        text = etiqueta
        isAllCaps = false
        setOnClickListener { accion() }
    }

    private fun construir(): View {
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(24))
        }
        col.addView(texto(26f, true, Color.parseColor("#FFC400")).apply { text = "GSI-ALERTA" })
        col.addView(texto(13f, color = Color.LTGRAY).apply {
            text = "Notas del 2.º ejercicio · GSI ingreso libre 2025"
        })

        tvVeredicto = texto(24f, true).apply { gravity = Gravity.START; setPadding(0, dp(18), 0, dp(4)) }
        tvDetalle = texto(15f)
        tvInfo = texto(13f, color = Color.LTGRAY)
        col.addView(tvVeredicto)
        col.addView(tvDetalle)
        btnPdf = boton("Abrir PDF del listado") { abrirUrl(urlPdf) }.apply { visibility = View.GONE }
        col.addView(btnPdf)
        col.addView(tvInfo)

        val fila = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        fila.addView(boton("Barrer ahora") { barrer() },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        fila.addView(boton("Probar aviso") { prueba() },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        col.addView(fila)

        col.addView(texto(13f, color = Color.LTGRAY).apply {
            text = "Simulacros: la Pi crea un PDF del 2.º ejercicio inventado, lo lee y te manda el aviso real (tócalo para descargar el PDF)"
            setPadding(0, dp(10), 0, 0)
        })
        val simulacros = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        simulacros.addView(boton("🧪 Simular aprobado") { simular("aprobado") },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        simulacros.addView(boton("🧪 Simular suspenso") { simular("suspenso") },
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        col.addView(simulacros)
        col.addView(boton("Abrir la web del INAP") { abrirUrl(urlWeb) })

        col.addView(texto(18f, true).apply { text = "Avisos"; setPadding(0, dp(18), 0, dp(4)) })
        tvEventos = texto(14f).apply { text = "—" }
        col.addView(tvEventos)

        col.addView(texto(18f, true).apply { text = "Ajustes"; setPadding(0, dp(24), 0, dp(4)) })
        etServidor = EditText(this).apply {
            hint = "http://homelab2:8806"
            setText(Prefs.servidor(this@MainActivity))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        etClave = EditText(this).apply {
            hint = "api_key de config.json"
            setText(Prefs.clave(this@MainActivity))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD
            setSingleLine()
        }
        col.addView(texto(13f, color = Color.LTGRAY).apply { text = "Servidor (Pi por Tailscale)" })
        col.addView(etServidor)
        col.addView(texto(13f, color = Color.LTGRAY).apply { text = "Clave" })
        col.addView(etClave)
        col.addView(boton("Guardar y conectar") { guardar() })
        col.addView(boton("Que Android no la duerma (batería)") {
            try {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            } catch (e: Exception) {
                toast("Ábrelo a mano: Ajustes › Batería")
            }
        })
        col.addView(boton("Detener vigilancia") {
            VigiaService.parar(this)
            toast("Vigilancia detenida (se reanuda al abrir la app)")
        })

        return ScrollView(this).apply { addView(col) }
    }

    // ---------------------------------------------------------------- acciones
    private fun toast(m: String) = Toast.makeText(this, m, Toast.LENGTH_LONG).show()

    private fun abrirUrl(url: String) {
        if (url.isBlank()) return
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (e: Exception) {
            toast("No hay app para abrir $url")
        }
    }

    private fun guardar() {
        Prefs.guardar(this, etServidor.text.toString(), etClave.text.toString())
        VigiaService.parar(this)
        VigiaService.arrancar(this)
        toast("Guardado")
        cargar()
    }

    private fun enSegundoPlano(trabajo: () -> JSONObject, alTerminar: (JSONObject) -> Unit) {
        if (!Prefs.configurado(this)) {
            tvVeredicto.text = "Configura la Pi abajo ↓"
            return
        }
        Thread {
            try {
                val r = trabajo()
                runOnUiThread { alTerminar(r) }
            } catch (e: RespuestaPi) {
                runOnUiThread {
                    tvInfo.text = when (e.codigo) {
                        401 -> "La Pi responde, pero la clave no es correcta. Revísala en Ajustes."
                        404 -> "La Pi responde, pero no conoce esa función: actualiza gsialerta.py en la Pi."
                        else -> "La Pi responde con error ${e.codigo}: ${e.message}"
                    }
                }
            } catch (e: Exception) {
                runOnUiThread {
                    tvInfo.text = "Sin conexión con la Pi: ${e.message}\n¿Tailscale activo en el móvil?"
                }
            }
        }.start()
    }

    private fun cargar() = enSegundoPlano({
        val estado = Api.get(this, "/api/estado")
        val ev = Api.get(this, "/api/eventos?desde=0")
        estado.put("_eventos", ev.optJSONArray("eventos"))
    }) { pintar(it) }

    private fun barrer() {
        tvInfo.text = "Barriendo la web del INAP…"
        enSegundoPlano({ Api.post(this, "/api/check") }) { cargar() }
    }

    private fun simular(caso: String) {
        tvInfo.text = "Simulacro «$caso»: la Pi está generando y leyendo el PDF…"
        enSegundoPlano({ Api.post(this, "/api/simular?caso=$caso") }) {
            toast("Simulacro enviado; el aviso te llega en unos segundos")
            cargar()
        }
    }

    private fun prueba() = enSegundoPlano({ Api.post(this, "/api/prueba") }) {
        toast("Aviso de prueba enviado; debería llegarte en segundos")
    }

    private fun n(x: Double) = String.format(Locale("es", "ES"), "%.2f", x)

    private fun hora(ts: Double) = if (ts > 0) fmt.format(Date((ts * 1000).toLong())) else "—"

    private fun pintar(e: JSONObject) {
        urlWeb = e.optString("url")
        val res = e.optJSONObject("resultado")
        val veredicto = res?.optString("veredicto").orEmpty()
        when {
            veredicto == "aprobado" -> {
                tvVeredicto.text = "✅ ¡APRUEBAS EL 2.º!"
                tvVeredicto.setTextColor(Color.parseColor("#4CAF50"))
                val p = res!!.optJSONObject("puesto")
                tvDetalle.text = if (p != null && p.optBoolean("calculado")) {
                    buildString {
                        append("Nota final ${n(p.optDouble("total"))}  (1.º ${n(p.optDouble("nota1"))} + 2.º ${n(p.optDouble("nota2"))})\n")
                        append("Puesto ${p.optInt("puesto")} de ${p.optInt("aprobados_general")} en acceso general")
                        if (p.optInt("empatados") > 0) append(" (empatado con ${p.optInt("empatados")})")
                        append("\n${p.optInt("plazas_general")} plazas: ")
                        append(if (p.optBoolean("dentro_de_plazas")) "DENTRO ✅" else "FUERA ❌")
                        append("\nPuesto global (con cupo): ${p.optInt("puesto_global")} de ${p.optInt("aprobados_total")}")
                        val avisos = p.optJSONArray("aviso")
                        if (avisos != null && avisos.length() > 0) {
                            append("\n⚠️ " + (0 until avisos.length()).joinToString("; ") { avisos.getString(it) })
                        }
                    }
                } else {
                    "Nota del 2.º: ${res.optString("nota", "?")} · aprueban ${res.optInt("personas")} personas"
                }
            }
            veredicto == "no" -> {
                tvVeredicto.text = "❌ No apareces en el listado"
                tvVeredicto.setTextColor(Color.parseColor("#EF5350"))
                tvDetalle.text = "Aprueban ${res!!.optInt("personas")} personas. Revisa el PDF."
            }
            res != null && res.has("publicado") -> {
                tvVeredicto.text = "📢 ¡Publicado! Leyendo PDF…"
                tvVeredicto.setTextColor(Color.parseColor("#FFC400"))
                tvDetalle.text = "La Pi reintenta leerlo cada minuto."
            }
            else -> {
                tvVeredicto.text = "⏳ Aún sin notas del 2.º"
                tvVeredicto.setTextColor(Color.WHITE)
                val docs = e.optJSONArray("segundo_ejercicio")
                val lista = (0 until (docs?.length() ?: 0)).joinToString(", ") { docs!!.getString(it) }
                tvDetalle.text = "En la web, bajo «Segundo ejercicio»: ${lista.ifBlank { "nada" }}"
            }
        }
        urlPdf = res?.optString("url").orEmpty()
        btnPdf.visibility = if (urlPdf.isNotBlank()) View.VISIBLE else View.GONE

        var nota1 = e.optString("nota1").takeIf { it.isNotBlank() && it != "null" } ?: "—"
        val p1 = e.optJSONObject("puesto1")
        if (p1 != null && p1.has("puesto")) {
            nota1 += " (puesto ${p1.optInt("puesto")} de ${p1.optInt("de")})"
        }
        val errores = e.optInt("errores_seguidos")
        tvInfo.text = buildString {
            append("${e.optString("nombre")} · 1.er ejercicio: $nota1\n")
            append("Última barrida: ${hora(e.optDouble("ultima_barrida", 0.0))} ")
            append("(cada ${e.optInt("intervalo_segundos")} s · ${e.optInt("barridas")} barridas)")
            if (errores > 0) append("\n⚠️ $errores fallos seguidos: ${e.optString("ultimo_error")}")
        }

        val ev = e.optJSONArray("_eventos")
        tvEventos.text = if (ev == null || ev.length() == 0) "Ninguno todavía" else
            (ev.length() - 1 downTo maxOf(0, ev.length() - 20)).joinToString("\n\n") { i ->
                val x = ev.getJSONObject(i)
                "${hora(x.optDouble("ts", 0.0))}  ${x.optString("titulo")}\n${x.optString("texto")}"
            }
    }
}
