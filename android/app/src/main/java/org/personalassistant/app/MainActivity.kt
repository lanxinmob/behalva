package org.personalassistant.app

import android.Manifest
import android.app.*
import android.content.*
import android.graphics.Color
import android.net.Uri
import android.os.*
import android.provider.OpenableColumns
import android.text.InputType
import android.view.*
import android.widget.*
import android.util.Base64
import org.json.*
import java.util.UUID

class MainActivity:Activity(){
    private lateinit var root:LinearLayout
    private lateinit var content:LinearLayout
    private lateinit var status:TextView
    private lateinit var pageTitle:TextView
    private lateinit var bodyScroll:ScrollView
    private lateinit var composerHost:LinearLayout
    private lateinit var bottomNav:LinearLayout
    private var current="工作台"
    private var state=JSONObject()
    private val tabs=listOf("工作台","聊天","使用引导","任务","资料","草稿","待确认","目标","定期跟进","规则","表格","设置")
    private val chatHandler=Handler(Looper.getMainLooper())
    private var syncingConnection:String?=null
    private var syncing:Boolean
        get()=syncingConnection==Workspace.connectionIdentity(this)
        set(value){syncingConnection=if(value)Workspace.connectionIdentity(this) else null}
    private val chatPoll=object:Runnable{override fun run(){
        val pending=LocalStore(this@MainActivity).use{it.queue().any{op->op.getString("status")=="pending"}}
        val waiting=items("tasks").any{it.optString("kind")=="chat"&&it.optString("state") in listOf("queued","running")}
        if(current=="聊天"&&(pending||waiting)&&!syncing&&Workspace.configured(this@MainActivity))sync()
        chatHandler.postDelayed(this,5000)
    }}
    override fun onResume(){super.onResume();chatHandler.postDelayed(chatPoll,5000)}
    override fun onPause(){chatHandler.removeCallbacks(chatPoll);super.onPause()}
    override fun onCreate(saved:Bundle?){super.onCreate(saved)
        if(Build.VERSION.SDK_INT>=33)requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS),1)
        current=saved?.getString("page")?:"工作台"
        root=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setBackgroundColor(Color.WHITE)};setContentView(root);protectSystemBars(root,12)
        val header=LinearLayout(this).apply{gravity=Gravity.CENTER_VERTICAL}
        val titleBox=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        pageTitle=text(current,22f).apply{UiStyle.heading(this)};titleBox.addView(pageTitle)
        status=UiStyle.text(this,"离线工作区",12f,true).apply{maxLines=2};titleBox.addView(status)
        header.addView(titleBox,LinearLayout.LayoutParams(0,-2,1f))
        header.addView(ImageButton(this).apply{setImageResource(android.R.drawable.ic_popup_sync);setColorFilter(UiStyle.ink);contentDescription="同步工作区";background=UiStyle.background(this@MainActivity,UiStyle.surface);setOnClickListener{sync()}},LinearLayout.LayoutParams(UiStyle.dp(this,48),UiStyle.dp(this,48)))
        root.addView(header)
        bodyScroll=ScrollView(this);content=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL};bodyScroll.addView(content);root.addView(bodyScroll,LinearLayout.LayoutParams(-1,0,1f))
        composerHost=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL};root.addView(composerHost)
        bottomNav=LinearLayout(this).apply{setPadding(0,UiStyle.dp(this@MainActivity,8),0,0)};root.addView(bottomNav)
        load();handleShare(intent);if(Workspace.configured(this)){Workspace.schedule(this);sync()}
    }
    override fun onNewIntent(intent:Intent){super.onNewIntent(intent);setIntent(intent);handleShare(intent)}
    override fun onSaveInstanceState(saved:Bundle){saved.putString("page",current);super.onSaveInstanceState(saved)}
    private fun text(value:String,size:Float=15f)=UiStyle.text(this,value,size)
    private fun button(label:String,fn:()->Unit)=UiStyle.button(this,label,false,fn)
    private fun items(name:String):List<JSONObject>{val a=state.optJSONArray(name)?:JSONArray();return (0 until a.length()).map{a.getJSONObject(it)}}
    private fun load(){LocalStore(this).use{db->state=db.cached("state")?.let{JSONObject(it)}?:JSONObject()};render(current=="聊天")}
    private fun sync(){
        if(!Workspace.configured(this)){current="设置";render();return}
        if(syncing)return
        syncing=true;status.text="正在同步…"
        fun identity()=Workspace.url(this)+"|"+getSharedPreferences("connection",MODE_PRIVATE).getString("database","workspace.db")
        val connection=identity()
        val previousQueue=LocalStore(this).use{it.queue().toString()}
        Workspace.execute(this){try{val result=Workspace.sync(this);Workspace.onUi(this){
            syncing=false
            if(!isDestroyed&&identity()==connection){
                val changed=result.optLong("cursor",-1)!=state.optLong("cursor",-2)
                state=result;status.text="已同步 · 离线修改会保留在此手机"
                if(current!="聊天"||changed||previousQueue!=LocalStore(this).use{it.queue().toString()})render(current=="聊天")
            }
        }}catch(e:Exception){Workspace.onUi(this){syncing=false;if(!isDestroyed)status.text="离线或服务不可达：${e.message}"}}}
    }
    private fun queue(path:String,body:JSONObject,method:String="POST"){LocalStore(this).use{it.enqueue(path,body,method)};status.text="已保存到手机待同步队列";load();if(Workspace.configured(this))sync()}
    private fun panel(title:String,body:String="",fn:(LinearLayout)->Unit={}){val p=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setPadding(UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12));background=UiStyle.background(this@MainActivity,Color.WHITE,true)};p.addView(text(title,17f).apply{UiStyle.heading(this)});if(body.isNotBlank())p.addView(text(body));fn(p);val params=LinearLayout.LayoutParams(-1,-2);params.setMargins(0,UiStyle.dp(this,10),0,UiStyle.dp(this,6));content.addView(p,params)}
    private fun form(title:String,fields:List<Pair<String,String>>,multiline:Boolean=false,save:(List<String>)->Unit){val view=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setPadding(UiStyle.dp(this@MainActivity,20),UiStyle.dp(this@MainActivity,8),UiStyle.dp(this@MainActivity,20),UiStyle.dp(this@MainActivity,8))};val edits=fields.map{(label,value)->view.addView(text(label,13f));EditText(this).apply{contentDescription=label;setText(value);inputType=InputType.TYPE_CLASS_TEXT or if(label.contains("令牌"))InputType.TYPE_TEXT_VARIATION_PASSWORD else if(multiline)InputType.TYPE_TEXT_FLAG_MULTI_LINE else 0;minLines=if(multiline)3 else 1;view.addView(this)}};AlertDialog.Builder(this).setTitle(title).setView(ScrollView(this).apply{addView(view)}).setNegativeButton("取消",null).setPositiveButton("保存"){_,_->save(edits.map{it.text.toString()})}.show()}
    private fun online(path:String,body:JSONObject,done:(JSONObject)->Unit={}){Workspace.execute(this){try{val response=Workspace.request(this,path,body);Workspace.onUi(this){done(response);sync()}}catch(e:Exception){Workspace.onUi(this){status.text=e.message}}}}
    private fun render(preserveChat:Boolean=false){if(!::content.isInitialized)return
        val previousInput=composerHost.findViewWithTag<EditText>("chat-input")
        val wasFocused=preserveChat&&previousInput?.hasFocus()==true
        val selection=previousInput?.selectionStart?:0
        val selectionEnd=previousInput?.selectionEnd?:selection
        val previousScroll=bodyScroll.scrollY
        val atEnd=content.height-bodyScroll.height-previousScroll<UiStyle.dp(this,48)
        content.removeAllViews();composerHost.removeAllViews();bottomNav.removeAllViews();pageTitle.text=current
        for(title in listOf("工作台","聊天","任务","更多")){
            val selected=if(title=="更多")current !in listOf("工作台","聊天","任务") else current==title
            val b=button(if(title=="工作台")"首页" else title){if(title=="更多")AlertDialog.Builder(this).setTitle("工作区")
                .setItems((tabs.filter{it !in listOf("工作台","聊天","任务")}+"接手本机浏览器").toTypedArray()){_,i->
                    val choice=(tabs.filter{it !in listOf("工作台","聊天","任务")}+"接手本机浏览器")[i]
                    if(choice=="接手本机浏览器")startActivity(Intent(this,BrowserActivity::class.java)) else {current=choice;render()}
                }.setNegativeButton("关闭",null).show() else {current=title;render()}}
            b.contentDescription=title;b.isSelected=selected;if(selected){b.setTextColor(UiStyle.accent);b.background=UiStyle.background(this,Color.rgb(232,243,238));b.contentDescription="$title，当前页面"}
            val params=LinearLayout.LayoutParams(0,-1,1f);params.setMargins(UiStyle.dp(this,2),0,UiStyle.dp(this,2),0);bottomNav.addView(b,params)
        }
        if(current=="聊天"||current=="使用引导"){
            val screen=ConversationScreen(this,content,state,{current=it;render()},
                {path,body->queue(path,body)},{load();if(Workspace.configured(this))sync()},
                {id,sequence,kind->form(if(kind=="feature")"功能目标与验收要求" else "从对话创建任务",
                    listOf("目标与要求" to "","相关链接（每行一个，可选）" to ""),true){v->
                    val body=JSONObject().put("title",v[0]).put("kind",kind).put("expected_sequence",sequence)
                        .put("urls",JSONArray(v[1].lines().filter{it.isNotBlank()}))
                    chooseSources(body,"conversations/$id/tasks")
                }},composerHost)
            if(current=="聊天")screen.chat() else screen.guide()
            if(current=="聊天")bodyScroll.post{
                if(preserveChat&&!atEnd)bodyScroll.scrollTo(0,previousScroll) else bodyScroll.fullScroll(View.FOCUS_DOWN)
                if(wasFocused)composerHost.findViewWithTag<EditText>("chat-input")?.let{it.requestFocus();it.setSelection(selection.coerceIn(0,it.text.length),selectionEnd.coerceIn(0,it.text.length))}
            }
            return
        }
        if(current=="工作台")panel("从聊天开始","讨论目标、发起任务，或一起完善助手的新功能。"){p->p.addView(button("开始聊天"){current="聊天";render()});p.addView(button("如何开始使用"){current="使用引导";render()})}
        val outbox=LocalStore(this).use{it.queue()};if(outbox.isNotEmpty())panel("手机待同步 ${outbox.size} 项",outbox.joinToString("\n"){val b=JSONObject(it.getString("body"));val name=b.optString("title").ifBlank{if(it.getString("path")=="messages")"聊天消息" else "工作区修改"};"$name · ${ReadableState.outbox(it.getString("status"))}"})
        if(current=="资料")items("sources").filter{!it.optBoolean("available",true)}.forEach{s->panel(s.getString("title")+" · 当前资料不可用",s.optString("availability_reason")+"\n保留的原文仅作为历史记录，已退出当前检索与模型上下文。")}
        if(current in listOf("工作台","任务"))items("model_runs").filter{run->run.optString("state")=="unknown"&&!run.optBoolean("retry_authorized")&&items("tasks").any{it.getString("id")==run.getString("task_id")&&it.getString("state") !in listOf("queued","running","completed")}}.forEach{run->
            val task=items("tasks").first{it.getString("id")==run.getString("task_id")}
            panel(task.getString("title")+" · 模型调用待核实",run.optString("notice")){p->
                p.addView(button("确认后重新调用模型"){
                    form("模型调用恢复",listOf("核查结果或重新调用的理由" to ""),true){v->
                        confirm("原调用可能已收费，原费用预留不会取消。确认重新调用并承担一次新调用费用？"){
                            online("model-runs/${run.getString("id")}/retry",JSONObject().put("reason",v[0]).put("accept_unknown_cost",true).put("expected_revision",run.getInt("revision")))
                        }
                    }
                })
            }
        }
        when(current){
            "工作台","任务"->{panel("持续推进重要的事","${items("tasks").count{it.optString("state")!="completed"}} 个任务待处理 · ${items("operations").count{it.optString("state")=="awaiting_approval"}} 项待确认"){p->p.addView(button("＋ 新任务"){form("新任务",listOf("任务目标" to "","相关链接（每行一个，可选）" to ""),true){v->chooseSources(JSONObject().put("title",v[0]).put("urls",JSONArray(v[1].lines().filter{it.isNotBlank()})).put("request_id",UUID.randomUUID().toString()))}})};items("tasks").forEach{t->
                val result=t.optJSONObject("result")
                panel(t.getString("title").take(100),ReadableState.task(t.getString("state"))+"\n"+ReadableState.result(result).take(500)){p->
                    if(result!=null)p.addView(button("查看完整处理记录"){showText("处理记录",ReadableState.fields(result))})
                    if(t.optString("kind")=="chat")p.addView(button("打开对话"){LocalStore(this).use{it.cache("chat:selected",t.getString("conversation_id"))};current="聊天";render()})
                    if(t.optString("kind") !in listOf("chat","feature")&&t.getString("state") !in listOf("running","queued","completed"))p.addView(button("补充并继续"){form("回复",listOf("补充内容" to ""),true){v->queue("tasks/${t.getString("id")}/resume",JSONObject().put("reply",v[0]))}})
                }
            }}
            "资料"->{items("attachments").forEach{a->panel(a.getString("name"),"已保存文件 · ${a.getLong("size")} 字节；原文件可查看，文字提取另行处理。"){p->p.addView(button("查看附件"){Workspace.execute(this){try{val blob=Workspace.request(this,"attachments/${a.getString("id")}");val bytes=Base64.decode(blob.getString("base64"),Base64.DEFAULT);Workspace.onUi(this){val bitmap=android.graphics.BitmapFactory.decodeByteArray(bytes,0,bytes.size);if(bitmap!=null)AlertDialog.Builder(this).setTitle(a.getString("name")).setView(ImageView(this).apply{setImageBitmap(bitmap);adjustViewBounds=true}).setPositiveButton("关闭",null).show()else showText(a.getString("name"),"文件已保存。文字资料可从资料页检索；原文件可在准备邮件时选择作为附件。")}}catch(e:Exception){Workspace.onUi(this){status.text=e.message}}}})}};items("sources").forEach{s->panel(s.getString("title"),s.getString("uri")+"\n"+s.optString("preview")){p->p.addView(button("查看并离线保存原文"){val id=s.getString("version_id");val cached=LocalStore(this).use{it.cached("source:$id")};if(cached!=null)showText(s.getString("title"),cached)else Workspace.execute(this){try{val v=Workspace.request(this,"versions/$id");LocalStore(this).use{it.cache("source:$id",v.getString("body"))};Workspace.onUi(this){showText(s.getString("title"),v.getString("body"))}}catch(e:Exception){Workspace.onUi(this){status.text=e.message}}}});if(!s.optBoolean("cloud_default"))p.addView(button("批准此资料用于云端模型"){confirm("允许按任务需要发送这份资料给配置的模型？"){online("grants",JSONObject().put("scope","source").put("target",s.getString("id")).put("active",true))}})}}}
            "草稿" -> {
                outbox.filter{it.getString("path")=="drafts"}.forEach{op->
                    val d=LocalStore(this).use{it.shownDraft(op)}
                    panel(d.getString("title")+" · 手机草稿",d.getString("body")){p->
                        if(op.getString("status")=="pending")p.addView(button("继续编辑离线草稿"){
                            form("手机草稿",listOf("标题" to d.getString("title"),"正文" to d.getString("body")),true){v->
                                try{LocalStore(this).use{it.editDraft(op.getString("id"),JSONObject().put("title",v[0]).put("body",v[1]))};load()}catch(e:Exception){status.text=e.message}
                            }
                        })
                    }
                }
                content.addView(button("＋ 新草稿"){editDraft(null)})
                items("drafts").forEach{d->
                    val pending=outbox.lastOrNull{it.optString("path")=="drafts/${d.getString("id")}"}
                    val shown=pending?.let{op->LocalStore(this).use{it.shownDraft(op)}}?:d
                    panel(shown.getString("title"),shown.getString("body")){p->
                        p.addView(button("编辑草稿"){editDraft(d,shown)})
                        p.addView(button("准备邮件"){
                            form("准备邮件",listOf("发送账号" to "","收件人" to "","主题" to shown.getString("title")),false){v->
                                chooseAttachments(JSONObject().put("kind","gmail").put("payload",JSONObject().put("from",v[0]).put("to",v[1]).put("subject",v[2]).put("body",shown.getString("body"))))
                            }
                        })
                    }
                }
                items("conflicts").filter{it.optString("state")=="unresolved"}.forEach{c->
                    val displayedRemote=items("drafts").first{it.getString("id")==c.getString("draft_id")}
                    val displayedLocal=LocalStore(this).use{it.conflictDraft(c.optString("request_id"),c.getJSONObject("local"))}
                    panel("草稿冲突","本地：${displayedLocal.getString("title")}\n${displayedLocal.getString("body")}\n服务器：${displayedRemote.getString("title")}\n${displayedRemote.getString("body")}"){p->
                        listOf("local" to "选择本地版本","remote" to "保留服务器版本").forEach{(choice,label)->
                            p.addView(button(label){
                                val selection=JSONObject().put("choice",choice).put("expected_revision",displayedRemote.getInt("revision"))
                                if(choice=="local")selection.put("local",displayedLocal)
                                online("conflicts/${c.getString("id")}/resolve",selection){_->
                                    LocalStore(this).use{it.resolveRequest(c.optString("request_id"))}
                                }
                            })
                        }
                    }
                }
            }
            "待确认"->renderOperations()
            "目标"->{content.addView(button("＋ 长期目标"){form("长期目标",listOf("目标" to "","下一步" to ""),true){v->queue("goals",JSONObject().put("title",v[0]).put("next_step",v[1]))}});items("goals").forEach{g->panel(g.getString("title"),g.optString("next_step"))}}
            "定期跟进"->{
                content.addView(button("＋ 定期跟进"){editSchedule(null)})
                items("schedules").forEach{s->
                    val next=java.text.DateFormat.getDateTimeInstance().format(java.util.Date(s.getLong("next_at")*1000))
                    panel(s.getString("title"),"${if(s.getString("kind")=="research")"定期调查" else "询问进展"} · 每 ${s.getDouble("interval_seconds")/3600} 小时\n下次：$next\n${if(s.getBoolean("active"))"已启用" else "已暂停"}"){p->
                        p.addView(button("编辑"){editSchedule(s)})
                        p.addView(button(if(s.getBoolean("active"))"暂停" else "启用"){
                            queue("schedules",JSONObject(s.toString()).put("expected_revision",s.getInt("revision")).put("active",!s.getBoolean("active")))
                        })
                    }
                }
            }
            "规则"->{content.addView(button("提出纠正规则"){form("候选规则",listOf("名称" to "","适用范围、行为和例子" to ""),true){v->queue("rules",JSONObject().put("title",v[0]).put("body",v[1]))}});items("rules").forEach{r->panel(r.getString("title"),r.getString("body")){p->if(r.getString("state")=="proposed")p.addView(button("批准生效"){online("rules/${r.getString("id")}/approve",JSONObject().put("revision",r.getInt("revision")).put("hash",r.getString("hash")))})}}}
            "表格"->renderTables()
            "设置"->{
                val connection=ConnectionSettings(this){syncing=false;load();Workspace.schedule(this);sync()}
                panel("连接电脑工作区",if(Workspace.configured(this))"已保存地址：${Workspace.url(this)}\n实际连接状态见顶部同步信息。" else "尚未保存连接。先在电脑完成网络设置，再导入连接配置。"){p->
                    p.addView(UiStyle.button(this,"粘贴连接配置",true){connection.importConfig()});p.addView(button("手动填写连接"){connection.manual()})
                    if(Workspace.configured(this))p.addView(button("连接方式："+if(Tailnet.mode(this)=="tailnet")"内置 Tailscale" else "系统网络"){connection.transport()})
                }
                if(Workspace.configured(this)&&Tailnet.mode(this)=="tailnet")panel("Tailscale 私有连接"){p->TailnetSettings(this,p).install()}
                panel("配置从哪里获得","1. 电脑安装 Tailscale 并启用 HTTPS 访问，具体命令见电脑网页“连接与授权”。\n2. 在电脑该页填入 HTTPS 地址，配对新手机并复制连接配置；在此手机粘贴、核对后保存。\n3. 选择“内置 Tailscale”，点击连接，在浏览器中使用电脑的同一账号批准此 App 节点，然后返回同步。手机保留 Clash 即可，不用开启独立 Tailscale。\n\n可信证书的指纹留空。自签名证书需核对电脑提供的 SHA-256 指纹。设备令牌与 Tailscale 登录各自独立。"){p->p.addView(button("查看完整使用引导"){current="使用引导";render()})}
                panel("通知","锁屏只显示“助手有新消息”。当前使用系统周期同步，送达时机受系统调度影响；即时推送通道尚待接入和真机验证。")
                panel("QQ 群授权","撤销仅停止新增采集，旧资料仍可检索和交给模型。"){p->p.addView(button("授权群"){form("指定 QQ 群",listOf("群号" to "","名称" to "")){v->online("groups",JSONObject().put("id",v[0]).put("title",v[1]).put("active",true))}})};items("groups").forEach{g->panel(g.optString("title"),g.getString("id")){p->p.addView(button(if(g.optBoolean("active"))"撤销新增采集" else "重新授权"){online("groups",JSONObject(g.toString()).put("active",!g.optBoolean("active")))})}}
            }
        }
    }
    private fun renderTables(){
        items("tables").forEach{t->panel(t.getString("title")){p->
            val table=TableLayout(this)
            fun row(values:JSONArray,heading:Boolean=false){val row=TableRow(this)
                for(i in 0 until values.length()){
                    val cell=text(values.get(i).toString(),14f).apply{setPadding(UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12),UiStyle.dp(this@MainActivity,12));if(heading){UiStyle.heading(this);setBackgroundColor(UiStyle.surface)}}
                    row.addView(cell,TableRow.LayoutParams(UiStyle.dp(this,170),-2))
                };table.addView(row)
            }
            row(t.getJSONArray("columns"),true);val rows=t.getJSONArray("rows");for(i in 0 until rows.length())row(rows.getJSONArray(i))
            p.addView(HorizontalScrollView(this).apply{contentDescription=t.getString("title")+"，可横向滚动";addView(table)})
        }}
        if(items("tables").isEmpty())content.addView(UiStyle.text(this,"还没有表格",15f,true))
    }
    private fun renderOperations(){
        val labels=mapOf("from" to "发送账号","to" to "收件人","cc" to "抄送","bcc" to "密送","subject" to "主题","body" to "完整正文","url" to "目标网站","account" to "使用账号","fields" to "提交字段")
        val states=mapOf("awaiting_approval" to "等待确认","approved" to "已批准","executing" to "执行中","succeeded" to "已核实成功","failed" to "执行失败","unknown" to "结果待核实")
        items("operations").forEach{o->
            panel((if(o.getString("kind")=="gmail")"邮件" else "表单")+" · "+(states[o.optString("state")]?:o.optString("state"))){p->
                val payload=o.getJSONObject("payload")
                payload.keys().forEach{k->if(k !in listOf("attachment_ids","attachments")){
                    p.addView(UiStyle.text(this,labels[k]?:k,12f,true));p.addView(text(ReadableState.fields(payload.get(k))))
                }}
                val attachments=payload.optJSONArray("attachments")?:JSONArray()
                p.addView(UiStyle.text(this,"附件",12f,true))
                val missingFrozen=attachments.length()==0&&(payload.optJSONArray("attachment_ids")?.length()?:0)>0
                if(attachments.length()==0)p.addView(text(if(missingFrozen)"附件快照缺失，无法核对，请在电脑重新准备此操作。" else "无"))
                for(i in 0 until attachments.length()){val a=attachments.getJSONObject(i)
                    p.addView(text("${a.optString("name")} · ${a.optLong("size")} 字节\nSHA-256：${a.optString("sha256")}\n标识：${a.optString("id")}"))
                }
                p.addView(UiStyle.text(this,o.optString("evidence").ifBlank{"尚无外部完成证据"},12f,true))
                if(o.optString("state")=="awaiting_approval"&&!missingFrozen)p.addView(button("确认以上内容并授权实际执行"){confirm("请核对账号、收件人、完整正文、附件和字段。批准后会实际发送或提交。"){online("operations/${o.getString("id")}/approve",JSONObject().put("revision",o.getInt("revision")).put("payload_hash",o.getString("payload_hash")))}})
            }
        }
    }
    private fun editSchedule(original:JSONObject?){
        AlertDialog.Builder(this).setTitle("跟进类型").setItems(arrayOf("定期调查","询问进展")){_,index->
            val kind=if(index==0)"research" else "check_in"
            form("定期跟进",listOf("调查任务或追问内容" to (original?.optString("title")?:""),"间隔（小时，最少 0.25）" to ((original?.optDouble("interval_seconds")?:86400.0)/3600).toString(),"下次开始（距现在的小时数）" to ((original?.optLong("next_at")?.minus(System.currentTimeMillis()/1000)?.coerceAtLeast(0)?:3600)/3600.0).toString(),"相关链接（每行一个）" to (original?.optJSONArray("urls")?.let{a->(0 until a.length()).joinToString("\n"){a.getString(it)}}?:"")),true){v->
                try{
                    val hours=v[1].toDouble();val delay=v[2].toDouble()
                    require(hours.isFinite()&&hours>=0.25&&hours<=8784&&delay.isFinite()&&delay>=0&&delay<=8784){"请检查间隔与下次开始时间"}
                    val body=JSONObject().put("title",v[0]).put("kind",kind).put("interval_seconds",(hours*3600).toLong()).put("next_at",System.currentTimeMillis()/1000+(delay*3600).toLong()).put("active",original?.optBoolean("active")?:true).put("urls",JSONArray(v[3].lines().filter{it.isNotBlank()})).put("source_ids",original?.optJSONArray("source_ids")?:JSONArray())
                    if(original!=null)body.put("id",original.getString("id")).put("expected_revision",original.getInt("revision"))
                    chooseSources(body,"schedules")
                }catch(e:Exception){status.text=e.message}
            }
        }.show()
    }
    private fun chooseAttachments(operation:JSONObject) {val attachments=items("attachments");if(attachments.isEmpty()){queue("operations",operation);return};val selected=BooleanArray(attachments.size);AlertDialog.Builder(this).setTitle("选择实际邮件附件").setMultiChoiceItems(attachments.map{it.getString("name")}.toTypedArray(),selected){_,index,on->selected[index]=on}.setNegativeButton("取消",null).setPositiveButton("保存待确认邮件"){_,_->operation.getJSONObject("payload").put("attachment_ids",JSONArray(attachments.filterIndexed{index,_->selected[index]}.map{it.getString("id")}));queue("operations",operation)}.show()}
    private fun chooseSources(task:JSONObject,path:String="tasks") {val sources=items("sources");if(sources.isEmpty()){queue(path,task);return};val previous=task.optJSONArray("source_ids")?.let{a->(0 until a.length()).map{a.getString(it)}}?:emptyList();val selected=BooleanArray(sources.size){sources[it].getString("id") in previous};AlertDialog.Builder(this).setTitle("关联资料（云端使用仍需授权）").setMultiChoiceItems(sources.map{it.getString("title")}.toTypedArray(),selected){_,index,on->selected[index]=on}.setNegativeButton("取消",null).setPositiveButton("保存"){_,_->task.put("source_ids",JSONArray(sources.filterIndexed{index,_->selected[index]}.map{it.getString("id")}));queue(path,task)}.show()}
    private fun editDraft(original:JSONObject?,shown:JSONObject?=original){form("编辑草稿",listOf("标题" to (shown?.optString("title")?:""),"正文" to (shown?.optString("body")?:"")),true){v->val body=JSONObject().put("title",v[0]).put("body",v[1]);if(original==null)queue("drafts",body)else queue("drafts/${original.getString("id")}",body.put("expected_revision",original.getInt("revision")),"PUT")}}
    private fun confirm(message:String,action:()->Unit){AlertDialog.Builder(this).setMessage(message).setNegativeButton("取消",null).setPositiveButton("确认"){_,_->action()}.show()}
    private fun showText(title:String,body:String){AlertDialog.Builder(this).setTitle(title).setView(ScrollView(this).apply{addView(text(body))}).setPositiveButton("关闭",null).show()}
    @Suppress("DEPRECATION") private fun handleShare(intent:Intent){if(intent.action !in listOf(Intent.ACTION_SEND,Intent.ACTION_SEND_MULTIPLE))return;val shared=intent.getStringExtra(Intent.EXTRA_TEXT);if(!shared.isNullOrBlank()){queue("documents",JSONObject().put("kind","shared").put("uri","shared:"+UUID.randomUUID()).put("title","手机分享").put("body",shared))};val uris=if(intent.action==Intent.ACTION_SEND_MULTIPLE)intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM)?:arrayListOf() else arrayListOf<Uri>().apply{intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM)?.let{add(it)}};uris.forEach{uri->try{var name="分享文件";contentResolver.query(uri,null,null,null,null)?.use{if(it.moveToFirst()){val col=it.getColumnIndex(OpenableColumns.DISPLAY_NAME);if(col>=0)name=it.getString(col)}};val bytes=contentResolver.openInputStream(uri)!!.use{input->val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192);while(output.size()<=4*1024*1024){val n=input.read(buffer,0,minOf(buffer.size,4*1024*1024+1-output.size()));if(n<0)break;output.write(buffer,0,n)};output.toByteArray()};require(bytes.size<=4*1024*1024){"分享文件暂限 4 MB"};queue("attachments",JSONObject().put("name",name).put("base64",Base64.encodeToString(bytes,Base64.NO_WRAP)))}catch(e:Exception){status.text="导入未完成：${e.message}"}};intent.action=null}
}
