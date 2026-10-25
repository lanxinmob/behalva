package org.personalassistant.app

import android.app.Activity
import android.graphics.Color
import android.os.Build
import android.view.View
import android.view.WindowInsets
import kotlin.math.roundToInt

internal fun Activity.protectSystemBars(root: View, paddingDp: Int) {
    val padding = (paddingDp * resources.displayMetrics.density).roundToInt()
    window.statusBarColor = Color.TRANSPARENT
    window.navigationBarColor = Color.TRANSPARENT
    window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or
        View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR or
        View.SYSTEM_UI_FLAG_LAYOUT_STABLE or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
        View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
    if (Build.VERSION.SDK_INT >= 30) window.setDecorFitsSystemWindows(false)
    root.setPadding(padding, padding, padding, padding)
    root.setOnApplyWindowInsetsListener { view, insets ->
        if (Build.VERSION.SDK_INT >= 30) {
            val safe = insets.getInsets(WindowInsets.Type.systemBars() or
                WindowInsets.Type.displayCutout() or WindowInsets.Type.ime())
            view.setPadding(padding + safe.left, padding + safe.top,
                padding + safe.right, padding + safe.bottom)
            WindowInsets.CONSUMED
        } else {
            val cutout = insets.displayCutout
            // Recompute from the base padding; repeated dispatch must not add it twice.
            view.setPadding(padding + maxOf(insets.systemWindowInsetLeft, cutout?.safeInsetLeft ?: 0),
                padding + maxOf(insets.systemWindowInsetTop, cutout?.safeInsetTop ?: 0),
                padding + maxOf(insets.systemWindowInsetRight, cutout?.safeInsetRight ?: 0),
                padding + maxOf(insets.systemWindowInsetBottom, cutout?.safeInsetBottom ?: 0))
            insets.consumeSystemWindowInsets().consumeDisplayCutout()
        }
    }
    root.post { root.requestApplyInsets() }
}
