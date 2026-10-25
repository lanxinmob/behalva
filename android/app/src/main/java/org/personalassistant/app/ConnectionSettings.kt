package org.personalassistant.app

import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Context
import android.text.InputType
import android.widget.*
import org.json.JSONObject
import java.net.URI

internal class ConnectionConfig(val url:String,val token:String,val fingerprint:String) {
    companion object {
        fun validated(url:String,token:String,fingerprint:String):ConnectionConfig {
            val address=url.trim().trimEnd('/')
            val uri=try{URI(address)}catch(_:Exception){throw IllegalArgumentException("地址格式不正确")}
            require(uri.scheme=="https"&&!uri.host.isNullOrBlank()&&uri.rawUserInfo==null&&uri.rawQuery==null&&uri.rawFragment==null&&(uri.port==-1||uri.port in 1..65535)){"请使用完整 HTTPS 地址，不含账号、查询参数或片段"}
            require(uri.rawPath.isNullOrEmpty()||uri.rawPath=="/"){"请填写工作区的 HTTPS 根地址，不含页面路径"}
            val host=uri.host.lowercase().trimEnd('.')
            require(host !in listOf("localhost","::1","[::1]","0.0.0.0","::","[::]")&&!host.startsWith("127.")){"这个地址指向手机自身，请填写电脑的远程地址"}
            require(token.isNotBlank()&&!token.any{it.isWhitespace()}){"设备令牌不能为空或包含空白"}
            val pin=fingerprint.trim().lowercase().replace(":","")
            require(pin.isEmpty()||pin.matches(Regex("[a-f0-9]{64}"))){"证书指纹应为 64 位 SHA-256；可信证书留空"}
            return ConnectionConfig(address,token,pin)
        }
        fun parse(value:String):ConnectionConfig {
            val json=try{JSONObject(value)}catch(_:Exception){throw IllegalArgumentException("配置不是有效的 JSON，请从电脑连接页重新复制")}
            require(json.opt("version") is Number&&json.getDouble("version")==1.0){"不支持此连接配置版本"}
            require(listOf("url","token","fingerprint").all{json.opt(it) is String}){"配置缺少地址、设备令牌或证书字段"}
            return validated(json.getString("url"),json.getString("token"),json.getString("fingerprint"))
        }
    }
}

internal class ConnectionSettings(private val activity:Activity,private val saved:()->Unit) {
    fun transport() {
        val options=arrayOf("内置 Tailscale（与 Clash 并用）","系统网络（已有 VPN 或公网 HTTPS）")
        var selected=if(Tailnet.mode(activity)=="tailnet")0 else 1
        AlertDialog.Builder(activity).setTitle("工作区连接方式").setSingleChoiceItems(options,selected){_,index->selected=index}
            .setNegativeButton("取消",null).setPositiveButton("保存"){_,_->
                Workspace.setTransport(activity,if(selected==0)"tailnet" else "direct");saved()
            }.show()
    }
    private fun review(config:ConnectionConfig) {
        val text="工作区地址：${config.url}\n\n设备令牌：已提供（隐藏）\n\n"+
            if(config.fingerprint.isEmpty())"证书：使用系统信任的 HTTPS 证书；不会跳过验证。" else "证书 SHA-256：\n${config.fingerprint}\n请与电脑连接页核对。"
        AlertDialog.Builder(activity).setTitle("核对连接后保存").setMessage(text+"\n\n切换工作区会隔离原缓存和待发内容。网络是否可达将在保存后同步验证。")
            .setNegativeButton("取消",null).setPositiveButton("确认保存"){_,_->
                try{Workspace.configure(activity,config.url,config.token,config.fingerprint);saved()}
                catch(_:Exception){AlertDialog.Builder(activity).setMessage("连接未保存，请检查设备的安全存储是否可用。").setPositiveButton("关闭",null).show()}
            }.show()
    }
    fun importConfig(){
        val box=LinearLayout(activity).apply{orientation=LinearLayout.VERTICAL;setPadding(UiStyle.dp(activity,20),0,UiStyle.dp(activity,20),0)}
        box.addView(UiStyle.text(activity,"电脑网页 → 连接与授权 → 配对新手机 → 复制连接配置。配置含设备令牌，请通过自己的安全渠道传到此手机。保存后可选择内置 Tailscale 连接。",14f))
        val input=EditText(activity).apply{hint="粘贴连接配置 JSON";contentDescription="连接配置 JSON";minLines=4;maxLines=8;inputType=InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE or InputType.TYPE_TEXT_VARIATION_PASSWORD}
        box.addView(input)
        box.addView(UiStyle.button(activity,"从剪贴板粘贴",false){
            val clipboard=activity.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val value=clipboard.primaryClip?.takeIf{it.itemCount>0}?.getItemAt(0)?.coerceToText(activity)?.toString()
            if(value.isNullOrBlank())input.error="剪贴板没有连接配置" else input.setText(value)
        })
        val dialog=AlertDialog.Builder(activity).setTitle("导入连接配置").setView(ScrollView(activity).apply{addView(box)})
            .setNegativeButton("取消",null).setPositiveButton("核对连接",null).create()
        dialog.setOnShowListener{dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener{
            try{val config=ConnectionConfig.parse(input.text.toString());input.text.clear();dialog.dismiss();review(config)}catch(e:IllegalArgumentException){input.error=e.message}
        }};dialog.show()
    }
    fun manual(){
        val box=LinearLayout(activity).apply{orientation=LinearLayout.VERTICAL;setPadding(UiStyle.dp(activity,20),0,UiStyle.dp(activity,20),0)}
        val inputs=listOf("HTTPS 地址" to Workspace.url(activity),"设备令牌" to "","证书指纹（可信证书留空）" to "").map{(label,value)->
            box.addView(UiStyle.text(activity,label,13f));EditText(activity).apply{contentDescription=label;setText(value);inputType=InputType.TYPE_CLASS_TEXT or if(label=="设备令牌")InputType.TYPE_TEXT_VARIATION_PASSWORD else 0;box.addView(this)}
        }
        val dialog=AlertDialog.Builder(activity).setTitle("手动配置连接").setView(ScrollView(activity).apply{addView(box)}).setNegativeButton("取消",null).setPositiveButton("核对连接",null).create()
        dialog.setOnShowListener{dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener{
            try{val config=ConnectionConfig.validated(inputs[0].text.toString(),inputs[1].text.toString(),inputs[2].text.toString());inputs[1].text.clear();dialog.dismiss();review(config)}catch(e:IllegalArgumentException){inputs[0].error=e.message}
        }};dialog.show()
    }
}
