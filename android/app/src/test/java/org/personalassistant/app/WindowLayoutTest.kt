package org.personalassistant.app

import android.app.Activity
import android.content.Context
import android.graphics.Insets
import android.graphics.Rect
import android.view.DisplayCutout
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import kotlin.math.roundToInt

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[28])
class WindowLayoutTest {
    @Before fun offline() {
        RuntimeEnvironment.getApplication().getSharedPreferences("connection",Context.MODE_PRIVATE)
            .edit().clear().commit()
    }

    private fun <T: Activity> checkActivity(type: Class<T>, paddingDp: Int, insets: WindowInsets) {
        val controller=Robolectric.buildActivity(type).setup()
        try {
            val activity=controller.get()
            val root=activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0) as ViewGroup
            val padding=(paddingDp * activity.resources.displayMetrics.density).roundToInt()
            repeat(2) { root.dispatchApplyWindowInsets(insets) }
            assertEquals(padding+80,root.paddingTop)
            assertEquals(padding+40,root.paddingLeft)
            assertEquals(padding+16,root.paddingRight)
            assertEquals(padding+48,root.paddingBottom)
            root.measure(View.MeasureSpec.makeMeasureSpec(1080,View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(1920,View.MeasureSpec.EXACTLY))
            root.layout(0,0,1080,1920)
            assertTrue("Header must be below the status bar/cutout",root.getChildAt(0).top>=80)
            assertTrue("Last content must clear the bottom bar",root.getChildAt(root.childCount-1).bottom<=1920-48)
        } finally { controller.pause().stop().destroy() }
    }

    @Test fun legacyBarsAndLandscapeCutout() {
        val cutout=DisplayCutout(Rect(40,80,0,0),listOf(Rect(0,0,40,80)))
        // Android 9 has no public builder; reflect its runtime constructor only in this test.
        val withCutout=WindowInsets::class.java.getConstructor(Rect::class.java,Rect::class.java,
            Rect::class.java,Boolean::class.javaPrimitiveType,Boolean::class.javaPrimitiveType,
            DisplayCutout::class.java).newInstance(Rect(0,60,16,48),null,null,false,false,cutout)
        checkActivity(MainActivity::class.java,12,withCutout)
        checkActivity(BrowserActivity::class.java,8,withCutout)
    }

    @Test @Config(sdk=[35]) fun enforcedEdgeToEdgeAndKeyboard() {
        val insets=WindowInsets.Builder()
            .setInsets(WindowInsets.Type.systemBars(),Insets.of(0,60,16,48))
            .setInsets(WindowInsets.Type.displayCutout(),Insets.of(40,80,0,0))
            .setVisible(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout(),true)
            .build()
        checkActivity(MainActivity::class.java,12,insets)
        checkActivity(BrowserActivity::class.java,8,insets)
        val activity=Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val root=View(activity.get())
            activity.get().setContentView(root)
            activity.get().protectSystemBars(root,12)
            val base=(12 * root.resources.displayMetrics.density).roundToInt()
            root.dispatchApplyWindowInsets(WindowInsets.Builder(insets)
                .setInsets(WindowInsets.Type.ime(),Insets.of(0,0,0,600))
                .setVisible(WindowInsets.Type.ime(),true).build())
            assertEquals(base+600,root.paddingBottom)
            root.dispatchApplyWindowInsets(insets)
            assertEquals(base+48,root.paddingBottom)
        } finally { activity.pause().stop().destroy() }
    }
}
