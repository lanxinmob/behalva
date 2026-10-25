package org.personalassistant.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.widget.EditText
import android.widget.TextView
import android.view.View
import android.view.ViewGroup
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowAlertDialog

@RunWith(org.robolectric.RobolectricTestRunner::class)
@Config(sdk=[28])
class ConnectionSettingsTest {
    private fun descendants(view:View):List<View> = listOf(view)+if(view is ViewGroup)(0 until view.childCount).flatMap{descendants(view.getChildAt(it))} else emptyList()
    @Test fun importingRequiresReviewAndNeverDisplaysDeviceSecret(){
        val app=RuntimeEnvironment.getApplication();app.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().clear().commit()
        val controller=Robolectric.buildActivity(Activity::class.java).setup()
        try{
            ConnectionSettings(controller.get()){fail("No save expected before confirmation")}.importConfig()
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            val form=ShadowAlertDialog.getLatestAlertDialog()
            val secret="fixture-device-secret"
            val json=JSONObject().put("version",1).put("url","https://fixture.tailnet.invalid").put("token",secret).put("fingerprint","").toString()
            descendants(form.window!!.decorView).filterIsInstance<EditText>().single().setText(json)
            form.getButton(AlertDialog.BUTTON_POSITIVE).performClick()
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            val review=ShadowAlertDialog.getLatestAlertDialog()
            val text=descendants(review.window!!.decorView).filterIsInstance<TextView>().joinToString("\n"){it.text}
            assertTrue("Review must name the actual destination",text.contains("https://fixture.tailnet.invalid"));assertFalse("Review must mask the device secret",text.contains(secret));assertTrue("Review must state certificate validation",text.contains("系统信任"))
            assertFalse(Workspace.configured(app));review.getButton(AlertDialog.BUTTON_NEGATIVE).performClick();assertFalse(Workspace.configured(app))
        }finally{controller.pause().stop().destroy()}
    }
    @Test fun malformedOrUnsafeImportsCannotConfigureWorkspace(){
        fun json(url:String="https://fixture.invalid",pin:String="",version:Double=1.0)=JSONObject().put("version",version).put("url",url).put("token","fixture-token").put("fingerprint",pin).toString()
        for(value in listOf("not-json",json("http://fixture.invalid"),json("https://127.0.0.1:8765"),json("https://127.1.2.3"),json("https://localhost."),json("https://user:pass@fixture.invalid"),json("https://fixture.invalid?token=x"),json("https://fixture.invalid/settings"),json(pin="abc"),json(version=1.5),json("https://fixture.invalid:0"))){
            try{ConnectionConfig.parse(value);fail("Unsafe configuration accepted")}catch(_:IllegalArgumentException){}
        }
        assertEquals("a".repeat(64),ConnectionConfig.parse(json(pin="AA:".repeat(31)+"AA")).fingerprint)
        assertEquals("",ConnectionConfig.parse(json()).fingerprint)
    }
}
