package org.personalassistant.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.View
import android.widget.LinearLayout

internal class TailnetSettings(private val activity:Activity,private val box:LinearLayout) {
    private val handler=Handler(Looper.getMainLooper())
    private val status=UiStyle.text(activity,"",14f)
    private val login=UiStyle.button(activity,"打开 Tailscale 登录页",true){
        val link=Tailnet.state().optString("auth_url")
        if(Tailnet.validLogin(link))runCatching{activity.startActivity(Intent(Intent.ACTION_VIEW,Uri.parse(link)))}
            .onFailure{status.text="未找到浏览器，请安装浏览器后重试"}
    }
    private val poll=object:Runnable {
        override fun run() {
            if(!box.isAttachedToWindow||activity.isDestroyed)return
            Tailnet.refresh(activity){activity.runOnUiThread{if(box.isAttachedToWindow&&!activity.isDestroyed){display();handler.postDelayed(this,1500)}}}
        }
    }
    fun install() {
        box.addView(status)
        box.addView(login)
        box.addView(UiStyle.button(activity,"连接／重新连接",false){
            try{Tailnet.connect(activity);status.text="正在建立连接…"}catch(e:Exception){status.text=e.message}
        })
        box.addView(UiStyle.button(activity,"断开连接",false){Tailnet.stop(activity);display()})
        box.addView(UiStyle.button(activity,"退出本机登录",false){
            AlertDialog.Builder(activity).setTitle("退出 Tailscale 登录？")
                .setMessage("清除此工作区在手机上的节点身份，保留离线资料和待同步内容。账号中的旧设备可在 Tailscale 管理后台删除。")
                .setNegativeButton("取消",null).setPositiveButton("退出"){_,_->
                    Tailnet.stop(activity,true){error->activity.runOnUiThread{if(!activity.isDestroyed){display();if(error!=null)status.text=error}}};display()
                }.show()
        })
        box.addOnAttachStateChangeListener(object:View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v:View){handler.post(poll)}
            override fun onViewDetachedFromWindow(v:View){handler.removeCallbacks(poll)}
        })
        display()
        if(box.isAttachedToWindow)handler.post(poll)
    }
    private fun display() {
        val value=Tailnet.state()
        status.text=when(value.optString("state")) {
            "Starting"->"正在建立私有连接…"
            "NeedsLogin"->if(value.optString("auth_url").isBlank())"正在取得登录链接…" else "等待网页登录授权"
            "NeedsMachineAuth"->"等待管理员批准此设备"
            "Running"->"私有网络已连接；工作区状态请点击右上角同步核验"
            "LoggedOut"->"已退出本机登录"
            "LoggingOut"->"正在清除本机登录状态…"
            "Error"->value.optString("detail").ifBlank{"内置连接失败，请重新连接"}
            else->"连接已停止"
        }
        login.visibility=if(Tailnet.validLogin(value.optString("auth_url")))View.VISIBLE else View.GONE
    }
}
