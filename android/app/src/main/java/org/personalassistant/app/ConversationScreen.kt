package org.personalassistant.app

import android.app.Activity
import android.app.AlertDialog
import android.text.Editable
import android.text.TextWatcher
import android.widget.*
import android.view.Gravity
import org.json.JSONObject
import java.util.UUID

internal class ConversationScreen(private val activity:Activity, private val box:LinearLayout,
    private val state:JSONObject, private val navigate:(String)->Unit,
    private val enqueue:(String,JSONObject)->Unit, private val redraw:()->Unit,
    private val createTask:(String,Int,String)->Unit, private val composer:LinearLayout=box) {
    private fun items(key:String):List<JSONObject> = state.optJSONArray(key)?.let { a ->
        (0 until a.length()).map{a.getJSONObject(it)} } ?: emptyList()
    private fun note(title:String,body:String) {
        box.addView(UiStyle.text(activity,title,16f).apply{UiStyle.heading(this)})
        box.addView(UiStyle.text(activity,body,15f))
    }
    private fun button(title:String,action:()->Unit):Button = UiStyle.button(activity,title,false,action).also{box.addView(it)}
    private fun cache(key:String,value:String)=LocalStore(activity).use{it.cache(key,value)}
    fun guide() {
        note("开始使用个人助手","先连接工作区和模型，再通过聊天把想法变成任务。")
        note("1 · 连接电脑","电脑安装 Tailscale 并启用 HTTPS，在网页“连接与授权”配对手机并复制连接配置。手机设置中粘贴并核对，选择“内置 Tailscale”，点击连接并在浏览器批准 App 节点，然后返回同步。使用电脑的同一 Tailscale 账号；手机 Clash 可保持开启，独立 Tailscale 关闭。可信证书的指纹留空，自签名证书需核对电脑给出的指纹。设备令牌用于访问工作区，不是模型 API Key。")
        button("配置工作区连接"){navigate("设置")}
        note("2 · 接入模型",if(state.optBoolean("model_configured"))"已有配置，实际可用性需要一次真实回复验证。" else "在电脑“连接与授权 → 模型供应商”添加供应商，填写名称、API Key 和请求地址，选择模型后保存。默认自动估算预算，不用填写单价。密钥只保存在电脑，不要发到聊天。")
        note("3 · 授权资料","先导入或索引资料，在资料页批准需要用于云端模型的文件。聊天发送的文字和本对话历史会交给模型，索引文件不等于云端授权。")
        button("查看资料"){navigate("资料")}
        note("4 · 聊天、任务与新功能","在聊天中讨论目标，之后创建调查任务，核对链接和关联资料。新功能讨论可保存为待开发需求；代码启用仍需验证、两名独立 Agent 审查和你的确认。")
        button("开始聊天"){navigate("聊天")}
        note("离线也能保存","查看已同步聊天、编辑草稿并暂存消息；重连后同步。冲突会保留原消息，请查看最新对话后选择是否重新发送。")
    }
    fun chat() {
        val conversations=items("conversations")
        var id=LocalStore(activity).use{it.cached("chat:selected")}
        if(id.isNullOrEmpty()){id=conversations.firstOrNull()?.getString("id")?:UUID.randomUUID().toString().replace("-","");cache("chat:selected",id)}
        val selected=id!!
        val conversation=conversations.firstOrNull{it.getString("id")==selected}
        button(conversation?.optString("title")?.take(40)?.let{"对话：$it"}?:"选择对话"){AlertDialog.Builder(activity).setTitle("历史对话")
            .setItems(conversations.map{it.getString("title").take(50)}.toTypedArray()){_,index->
                cache("chat:selected",conversations[index].getString("id"));redraw()}.setNegativeButton("关闭",null).show()}
        button("＋ 新对话"){cache("chat:selected",UUID.randomUUID().toString().replace("-",""));redraw()}
        if(!state.optBoolean("model_configured"))note("模型尚未接入","消息会保存；配置模型后继续原消息。当前不会产生 AI 回复。")
        val messages=items("messages").filter{it.getString("conversation_id")==selected}.sortedBy{it.getInt("sequence")}
        if(messages.isEmpty())note("有什么想一起推进？","说说你的目标，或聊聊希望助手增加的能力。")
        messages.forEach{m->
            note(if(m.getString("role")=="user")"你" else "助手",m.getString("body"))
            if(m.getString("role")=="assistant"&&m.optString("completion_status") !in listOf("completed","stop"))note("回复可能不完整","可以继续追问。")
        }
        val pending=LocalStore(activity).use{it.queue()}.filter{
            (it.getString("path")=="messages"&&JSONObject(it.getString("body")).optString("conversation_id")==selected)||
                it.getString("path")=="conversations/$selected/tasks"}
        pending.forEach{op->
            val message=JSONObject(op.getString("body"))
            val description=if(op.getString("path")=="messages")message.getString("body") else
                "目标：${message.optString("title")}\n类型：${message.optString("kind")}\n链接：${message.optJSONArray("urls")}\n资料：${message.optJSONArray("source_ids")}"
            note(if(op.getString("status")=="pending")"待同步" else "原内容保留 · 需要处理",description+"\n"+op.optString("error"))
            if(op.getString("status")=="needs_attention")button("核对最新对话后重新发送"){
                AlertDialog.Builder(activity).setTitle("重新提交原内容")
                    .setMessage("请先同步并查看最新对话。确认以当前对话为依据重新提交以下内容？\n\n"+description)
                    .setNegativeButton("保留待处理",null).setPositiveButton("确认重新发送"){_,_->
                        LocalStore(activity).use{it.rebaseConversationRequest(op.getString("id"),conversation?.optInt("sequence")?:0)};redraw()
                    }.show()
            }
        }
        val task=items("tasks").firstOrNull{it.getString("id")==conversation?.optString("last_task_id")}
        val busy=task!=null&&task.optString("state")!="completed"
        if(busy){
            val states=mapOf("queued" to "等待回复","running" to "正在回复","blocked" to "等待配置或接入","waiting_input" to "需要补充","waiting_authorization" to "需要资料授权","failed" to "回复未完成")
            note(states[task!!.optString("state")]?:"等待处理",task.optJSONObject("result")?.optString("message")?:task.optString("notice","消息已保存，等待助手回复…"))
            if(task.optString("state") !in listOf("queued","running")){
                button("继续原消息"){enqueue("tasks/${task.getString("id")}/resume",JSONObject().put("reply",""))}
                button("查看调用与恢复详情"){navigate("任务")}
            }
        }
        val key="chat:draft:$selected"
        val input=EditText(activity).apply {
            tag="chat-input";hint="输入消息…";minLines=2;maxLines=4;textSize=15f;setTextColor(UiStyle.ink);setHintTextColor(UiStyle.muted)
            setPadding(UiStyle.dp(activity,12),UiStyle.dp(activity,10),UiStyle.dp(activity,12),UiStyle.dp(activity,10))
            background=UiStyle.background(activity,UiStyle.surface,true)
            inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(LocalStore(activity).use{it.cached(key)}?:"")
            addTextChangedListener(object:TextWatcher {
                override fun beforeTextChanged(s:CharSequence?,start:Int,count:Int,after:Int){}
                override fun onTextChanged(s:CharSequence?,start:Int,before:Int,count:Int){cache(key,s.toString())}
                override fun afterTextChanged(s:Editable?){}
            })
        }
        val row=LinearLayout(activity).apply{gravity=Gravity.BOTTOM;setPadding(0,UiStyle.dp(activity,8),0,0)}
        row.addView(input,LinearLayout.LayoutParams(0,-2,1f))
        val send=ImageButton(activity).apply{tag="chat-send";contentDescription="发送";setImageResource(android.R.drawable.ic_menu_send);setColorFilter(android.graphics.Color.WHITE);background=UiStyle.background(activity,UiStyle.accent)}
        send.setOnClickListener{
            val body=input.text.toString().trim()
            if(body.isEmpty()){input.error="请先输入消息";return@setOnClickListener}
            // Persist the outgoing message and clear the composer in one SQLite transaction.
            LocalStore(activity).use{it.enqueueChat(JSONObject().put("conversation_id",selected)
                .put("body",body).put("expected_sequence",conversation?.optInt("sequence")?:0),key)}
            redraw()
        };send.isEnabled=!busy&&pending.none{it.getString("path")=="messages"}
        send.alpha=if(send.isEnabled)1f else .45f
        val sendParams=LinearLayout.LayoutParams(UiStyle.dp(activity,48),UiStyle.dp(activity,48));sendParams.setMargins(UiStyle.dp(activity,8),0,0,0);row.addView(send,sendParams);composer.addView(row)
        composer.addView(UiStyle.button(activity,"云端处理与任务说明",false){AlertDialog.Builder(activity).setTitle("云端处理与任务")
            .setMessage("发送的文字及本对话历史会交给配置的云端模型。联网调查请创建任务。任务附带当前对话；功能需求仅进入待开发列表，不会直接改代码或启用程序。")
            .setPositiveButton("知道了",null).show()})
        if(conversation!=null){
            button("创建调查任务"){createTask(selected,conversation.getInt("sequence"),"research")}
            button("保存功能需求"){createTask(selected,conversation.getInt("sequence"),"feature")}
            items("tasks").filter{it.optString("conversation_id")==selected&&it.optString("kind")!="chat"}.forEach{t->note(t.getString("title"),ReadableState.task(t.getString("state")));button("查看关联任务"){navigate("任务")}}
        }
    }
}
