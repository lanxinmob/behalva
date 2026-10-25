package org.personalassistant.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Insets
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File
import java.util.UUID

@RunWith(org.robolectric.RobolectricTestRunner::class)
@Config(sdk=[35],qualifiers="w390dp-h844dp-xxhdpi")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class DesignLayoutTest {
    private fun descendants(v:View):List<View> = listOf(v)+if(v is ViewGroup)
        (0 until v.childCount).flatMap{descendants(v.getChildAt(it))} else emptyList()

    private fun screenshot(root:View,name:String){
        val output=File("../../build/evidence/ui/android").apply{mkdirs()}
        val image=Bitmap.createBitmap(root.width,root.height,Bitmap.Config.ARGB_8888)
        root.draw(Canvas(image));File(output,name).outputStream().use{image.compress(Bitmap.CompressFormat.PNG,100,it)}
        val colors=(0 until root.width step 30).flatMap{x->(0 until root.height step 30).map{y->image.getPixel(x,y)}}.toSet()
        assertTrue("Rendering must contain real text and controls",colors.size>20);image.recycle()
    }

    @Test fun onboardingApprovalsAndWideTablesRenderAtNormalAndDoubleFont(){
        val app=RuntimeEnvironment.getApplication()
        app.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().clear()
            .putString("database",UUID.randomUUID().toString()+".db").commit()
        val frozen=JSONObject().put("id","frozen-file").put("name","reviewed-plan.pdf").put("size",4182).put("sha256","a".repeat(64))
        fun operation(id:String,withAttachment:Boolean)=JSONObject().put("id",id).put("kind","gmail").put("state","awaiting_approval").put("revision",2).put("payload_hash","fixture-hash")
            .put("payload",JSONObject().put("from","sender@example.invalid").put("to","recipient@example.invalid").put("subject","Review the actual attachment")
                .put("body","Full fixture letter with all content visible. ".repeat(8)).put("attachments",JSONArray().apply{if(withAttachment)put(frozen)}))
        val table=JSONObject().put("title","Wide fixture comparison").put("columns",JSONArray(listOf("Opportunity","Conditions","Deadline","Materials","Cost","Source")))
            .put("rows",JSONArray().put(JSONArray(listOf("Fixture opportunity","A long condition to compare","2027-05-12","Transcript and portfolio","100","https://example.invalid/source"))))
        val fixture=JSONObject().put("operations",JSONArray().put(operation("with",true)).put(operation("without",false)))
            .put("attachments",JSONArray().put(JSONObject(frozen.toString()).put("name","changed-current-file.pdf")))
            .put("tasks",JSONArray().put(JSONObject().put("id","fixture-task").put("title","Compare material requirements").put("kind","research").put("state","completed")
                .put("result",JSONObject().put("body","Material checklist prepared. ".repeat(30)).put("sources",JSONArray().put("https://example.invalid/source")))))
            .put("tables",JSONArray().put(table))
        LocalStore(app).use{it.cache("state",fixture.toString())}
        for(scale in listOf(1f,2f)){
            RuntimeEnvironment.setFontScale(scale)
            val controller=Robolectric.buildActivity(MainActivity::class.java).setup()
            try{
                val activity=controller.get();val root=activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0)
                fun layout(){
                    root.dispatchApplyWindowInsets(WindowInsets.Builder().setInsets(WindowInsets.Type.systemBars(),Insets.of(0,72,0,72)).setVisible(WindowInsets.Type.systemBars(),true).build())
                    root.measure(View.MeasureSpec.makeMeasureSpec(1170,View.MeasureSpec.EXACTLY),View.MeasureSpec.makeMeasureSpec(2532,View.MeasureSpec.EXACTLY));root.layout(0,0,1170,2532)
                    Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
                }
                fun navigate(name:String){
                    MainActivity::class.java.getDeclaredField("current").apply{isAccessible=true}.set(activity,name)
                    MainActivity::class.java.getDeclaredMethod("render",Boolean::class.javaPrimitiveType).apply{isAccessible=true}.invoke(activity,false);layout()
                }
                for((page,name) in listOf("使用引导" to "guide","设置" to "settings","待确认" to "approval","表格" to "table","任务" to "tasks","聊天" to "empty-chat")){
                    navigate(page)
                    val texts=descendants(root).filterIsInstance<TextView>()
                    if(page=="待确认"){
                        assertTrue(texts.any{it.text.contains("reviewed-plan.pdf")});assertFalse(texts.any{it.text.contains("changed-current-file.pdf")})
                        assertTrue(texts.any{it.text.contains("a".repeat(64))});assertTrue(texts.any{it.text.toString()=="无"})
                        assertEquals(2,texts.count{it.text.contains("Full fixture letter")})
                        assertTrue("Full approval body must be measured without clipping",texts.filter{it.text.contains("Full fixture letter")}.all{it.ellipsize==null&&it.layout!=null&&it.height>=it.layout.height+it.paddingTop+it.paddingBottom})
                    }
                    if(page=="表格"){
                        val horizontal=descendants(root).filterIsInstance<HorizontalScrollView>().single()
                        assertTrue("Wide table must scroll independently",horizontal.getChildAt(0).width>horizontal.width)
                        horizontal.scrollTo(horizontal.getChildAt(0).width,0);assertTrue(horizontal.scrollX>0)
                        horizontal.scrollTo(0,0)
                    }
                    if(page=="任务"){
                        assertTrue(texts.any{it.text.startsWith("已完成\nMaterial checklist")})
                        assertFalse(texts.any{it.text.contains("\"body\":")})
                        val full=descendants(root).filterIsInstance<Button>().single{it.text=="查看完整处理记录"}
                        full.performClick()
                        Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
                        val dialog=org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog()
                        assertTrue(descendants(dialog.window!!.decorView).filterIsInstance<TextView>().any{it.text.contains("Material checklist prepared. ".repeat(30))})
                        dialog.dismiss()
                    }
                    if(page=="聊天"){
                        val input=descendants(root).filterIsInstance<EditText>().single();val bounds=android.graphics.Rect()
                        assertTrue(input.getGlobalVisibleRect(bounds));assertTrue(bounds.top>=72&&bounds.bottom<=2460)
                    }
                    assertTrue("Header must clear status bar",texts.first().top>=0&&root.paddingTop>=72)
                    screenshot(root,"$name-${if(scale==2f)"large-text" else "phone"}.png")
                    val nav=descendants(root).filterIsInstance<Button>().filter{it.text.toString() in listOf("首页","聊天","任务","更多")}
                    assertEquals(4,nav.size)
                    assertEquals("Bottom tabs must have equal heights when text wraps",1,nav.map{it.height}.distinct().size)
                    if(page=="待确认"){
                        val scroll=descendants(root).filterIsInstance<ScrollView>().single()
                        scroll.scrollTo(0,scroll.getChildAt(0).height-scroll.height);Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
                        screenshot(root,"approval-without-attachment-${if(scale==2f)"large-text" else "phone"}.png")
                        val none=texts.single{it.text.toString()=="无"};val bounds=android.graphics.Rect()
                        assertTrue("No-attachment disclosure must be reachable",none.getGlobalVisibleRect(bounds))
                        scroll.scrollTo(0,0)
                    }
                }
            }finally{controller.pause().stop().destroy()}
        }
        RuntimeEnvironment.setFontScale(1f)
    }

    @Test fun longOfflineChatComposerClearsKeyboardAndKeepsDraft(){
        val app=RuntimeEnvironment.getApplication()
        app.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().clear()
            .putString("database",UUID.randomUUID().toString()+".db").commit()
        val id="1234567890abcdef1234567890abcdef"
        val messages=JSONArray()
        repeat(30){i->messages.put(JSONObject().put("conversation_id",id).put("sequence",i+1)
            .put("role",if(i%2==0)"user" else "assistant").put("body","Fixture project paragraph ".repeat(14)))}
        LocalStore(app).use{db->db.cache("state",JSONObject().put("model_configured",true)
            .put("conversations",JSONArray().put(JSONObject().put("id",id).put("title","Fixture discussion").put("sequence",30)))
            .put("messages",messages).toString());db.cache("chat:selected",id)}
        val controller=Robolectric.buildActivity(MainActivity::class.java).setup()
        try {
            val a=controller.get();val root=a.findViewById<ViewGroup>(android.R.id.content).getChildAt(0)
            fun layout(keyboard:Int=0){
                root.dispatchApplyWindowInsets(WindowInsets.Builder()
                    .setInsets(WindowInsets.Type.systemBars(),Insets.of(0,72,0,72))
                    .setVisible(WindowInsets.Type.systemBars(),true)
                    .setInsets(WindowInsets.Type.ime(),Insets.of(0,0,0,keyboard))
                    .setVisible(WindowInsets.Type.ime(),keyboard>0).build())
                root.measure(View.MeasureSpec.makeMeasureSpec(1170,View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(2532,View.MeasureSpec.EXACTLY))
                root.layout(0,0,1170,2532);Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            }
            layout();descendants(root).filterIsInstance<Button>().single{it.text=="聊天"}.performClick();layout()
            val input=descendants(root).filterIsInstance<EditText>().single()
            input.setText("Preserve my next question");input.requestFocus();input.setSelection(4,9)
            fun visibleComposer(keyboard:Int){
                layout(keyboard)
                val bounds=android.graphics.Rect();input.getGlobalVisibleRect(bounds)
                assertTrue("Composer must remain visible",bounds.height()>0)
                assertTrue("Composer must clear keyboard",bounds.bottom<=2532-keyboard)
                assertEquals("Preserve my next question",input.text.toString())
                assertEquals(4,input.selectionStart);assertEquals(9,input.selectionEnd)
            }
            visibleComposer(0)
            val output=File("../../build/evidence/ui/android").apply{mkdirs()}
            fun screenshot(name:String){val image=Bitmap.createBitmap(1170,2532,Bitmap.Config.ARGB_8888)
                root.draw(Canvas(image));File(output,name).outputStream().use{image.compress(Bitmap.CompressFormat.PNG,100,it)}
                val colors=(0 until 1170 step 30).flatMap{x->(0 until 2532 step 30).map{y->image.getPixel(x,y)}}.toSet()
                assertTrue("Rendering must contain real text and controls",colors.size>20);image.recycle()}
            screenshot("chat-phone.png");visibleComposer(900);screenshot("chat-keyboard.png")
            val scroll=descendants(root).filterIsInstance<ScrollView>().single()
            scroll.scrollTo(0,300)
            val method=MainActivity::class.java.getDeclaredMethod("render",Boolean::class.javaPrimitiveType)
            method.isAccessible=true;method.invoke(a,true);layout(900)
            val restored=descendants(root).filterIsInstance<EditText>().single()
            assertEquals("Preserve my next question",restored.text.toString())
            assertTrue(restored.hasFocus());assertEquals(4,restored.selectionStart);assertEquals(9,restored.selectionEnd)
            assertEquals(300,scroll.scrollY)
        }finally{controller.pause().stop().destroy()}
    }
}
