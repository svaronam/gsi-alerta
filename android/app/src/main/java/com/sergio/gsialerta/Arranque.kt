package com.sergio.gsialerta

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Vuelve a arrancar la vigilancia al encender el móvil o al actualizar la app. */
class Arranque : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
        if (Prefs.configurado(ctx)) VigiaService.arrancar(ctx)
    }
}
