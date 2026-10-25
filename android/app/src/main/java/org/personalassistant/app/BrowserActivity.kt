package org.personalassistant.app

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.graphics.*
import android.view.*
import android.widget.*
import android.text.InputType
import android.util.Base64
import org.json.JSONObject

/** Remote view of the dedicated browser; owner taps are actual browser input. */
class BrowserActivity : Activity() {
    private var snapshot=JSONObject()
    private lateinit var status:TextView
    private lateinit var canvas:BrowserCanvas
    private var pending=false
    private var workspace=""
    override fun onCreate(saved:Bundle?) {
        super.onCreate(saved)
        workspace=Workspace.connectionIdentity(this)
        val root=LinearLayout(this).apply { orientation=LinearLayout.VERTICAL;setBackgroundColor(Color.WHITE) }
        status=UiStyle.text(this,"本机浏览器",16f);root.addView(status)
        root.addView(UiStyle.text(this,"点按会实际操作网页，包括发送和提交。",12f,true))
        fun row(items:List<Pair<String,()->Unit>>) {
            val line=LinearLayout(this)
            items.forEach { (label,action) -> val params=LinearLayout.LayoutParams(0,-2,1f);params.setMargins(UiStyle.dp(this,2),UiStyle.dp(this,3),UiStyle.dp(this,2),UiStyle.dp(this,3));line.addView(UiStyle.button(this,label,false,action),params) }
            root.addView(line)
        }
        row(listOf("刷新画面" to { refresh() },"打开网址" to { input("HTTPS 网址",false){command(JSONObject().put("action","navigate").put("url",it))} },"输入文字" to { input("输入到当前聚焦位置",true){command(JSONObject().put("action","text").put("text",it))} }))
        row(listOf("向上" to { command(JSONObject().put("action","scroll").put("dy",-500)) },"向下" to { command(JSONObject().put("action","scroll").put("dy",500)) },"回车" to { command(JSONObject().put("action","key").put("key","Enter")) },"Tab" to { command(JSONObject().put("action","key").put("key","Tab")) }))
        canvas=BrowserCanvas(this) { x,y -> command(JSONObject().put("action","click").put("x",x).put("y",y)) }
        root.addView(canvas,LinearLayout.LayoutParams(-1,0,1f));setContentView(root);protectSystemBars(root,8);refresh()
    }
    private fun input(title:String,secret:Boolean,accept:(String)->Unit) {
        val edit=EditText(this);if(secret)edit.inputType=InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        AlertDialog.Builder(this).setTitle(title).setView(edit).setNegativeButton("取消",null).setPositiveButton("输入"){_,_->accept(edit.text.toString());edit.setText("")}.show()
    }
    private fun display(result:JSONObject) {
        snapshot=result
        status.text=if(result.optBoolean("connected"))result.optString("url")+"\n"+result.optString("error") else "浏览器未连接，请启动本机浏览器适配进程"
        if(result.optBoolean("pending"))status.append("\n上一操作仍待回执，请勿重复提交。")
        val encoded=result.optString("image_base64")
        if(encoded.isNotEmpty()){val bytes=Base64.decode(encoded,Base64.DEFAULT);canvas.bitmap=BitmapFactory.decodeByteArray(bytes,0,bytes.size)}
    }
    private fun sameWorkspace():Boolean {
        if(workspace==Workspace.connectionIdentity(this))return true
        snapshot=JSONObject();canvas.bitmap=null;status.text="工作区已切换，请关闭并重新打开浏览器接手";return false
    }
    private fun refresh(){if(!sameWorkspace())return;Workspace.execute(this){try{val result=Workspace.request(this,"browser/view");Workspace.onUi(this) { display(result) }}catch(e:Exception){Workspace.onUi(this){status.text=e.message}}}}
    private fun command(body:JSONObject){
        if(!sameWorkspace())return
        if(pending||snapshot.optBoolean("pending")){status.text="上一操作待回执，请刷新确认";return}
        body.put("session_id",snapshot.optString("session_id")).put("generation",snapshot.optLong("generation",-1))
        pending=true;Workspace.execute(this) {try{Workspace.request(this,"browser/command",body);Workspace.onUi(this){status.text="已交给本机会话；正在取得执行后的画面…"};Thread.sleep(1600);val result=Workspace.request(this,"browser/view");Workspace.onUi(this){display(result)}}catch(e:Exception){Workspace.onUi(this){status.text="${e.message}。请刷新确认页面，操作不会自动重复。"}}finally{Workspace.onUi(this){pending=false};body.remove("text")}}
    }
}

private class BrowserCanvas(context:android.content.Context,val click:(Int,Int)->Unit):View(context) {
    var bitmap:Bitmap?=null
        set(value){field?.recycle();field=value;invalidate()}
    private var target=RectF()
    override fun onDraw(canvas:Canvas){super.onDraw(canvas);val b=bitmap?:return;val scale=minOf(width.toFloat()/b.width,height.toFloat()/b.height);target=RectF(0f,0f,b.width*scale,b.height*scale);canvas.drawBitmap(b,null,target,null)}
    override fun onTouchEvent(event:MotionEvent):Boolean {
        if(event.action==MotionEvent.ACTION_UP&&target.contains(event.x,event.y)) {click((event.x/target.width()*1024).toInt(),(event.y/target.height()*768).toInt());performClick()};return true
    }
    override fun performClick():Boolean {super.performClick();return true}
}
