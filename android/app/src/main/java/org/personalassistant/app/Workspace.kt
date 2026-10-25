package org.personalassistant.app

import android.app.*
import android.app.job.*
import android.content.*
import android.database.sqlite.*
import android.security.keystore.*
import android.util.Base64
import org.json.*
import java.net.URL
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.UUID
import java.util.concurrent.Executors
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.*

class LocalStore(context: Context) : SQLiteOpenHelper(context, Workspace.databaseFile(context), null, 3), java.io.Closeable {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE cache (key TEXT PRIMARY KEY,body TEXT NOT NULL)")
        db.execSQL("CREATE TABLE outbox(id TEXT PRIMARY KEY,path TEXT NOT NULL,method TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,local_body TEXT NOT NULL DEFAULT '')")
    }
    override fun onUpgrade(db: SQLiteDatabase, old: Int, new: Int) {
        if(old<2)db.execSQL("ALTER TABLE outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
        if(old<3)db.execSQL("ALTER TABLE outbox ADD COLUMN local_body TEXT NOT NULL DEFAULT ''")
    }
    fun cached(key: String): String? = readableDatabase.rawQuery("SELECT body FROM cache WHERE key=?", arrayOf(key)).use { if(it.moveToFirst()) it.getString(0) else null }
    fun enqueueChat(body:JSONObject,draftKey:String) {
        val db=writableDatabase;db.beginTransaction()
        try{enqueue("messages",body);cache(draftKey,"");db.setTransactionSuccessful()}finally{db.endTransaction()}
    }
    fun rebaseConversationRequest(id:String,sequence:Int) {
        val db=writableDatabase;db.beginTransaction()
        try {
            val op=queue().first{it.getString("id")==id}
            val path=op.getString("path")
            check((path=="messages"||path.matches(Regex("conversations/[a-f0-9]{32}/tasks")))&&op.getString("status")=="needs_attention")
            val body=JSONObject(op.getString("body")).put("expected_sequence",sequence)
            body.remove("request_id");enqueue(path,body);remove(id);db.setTransactionSuccessful()
        } finally {db.endTransaction()}
    }
    fun cache(key: String, value: String) { val v=ContentValues();v.put("key",key);v.put("body",value);writableDatabase.insertWithOnConflict("cache",null,v,SQLiteDatabase.CONFLICT_REPLACE) }
    fun enqueue(path: String, body: JSONObject, method: String="POST") {
        val db=writableDatabase;db.beginTransaction()
        try {
            // Coalesce only edits never handed to the network. A possibly applied
            // request retains its identity and body until its receipt is known.
            if(method=="PUT"&&path.startsWith("drafts/")){
                val previous=db.rawQuery("SELECT id FROM outbox WHERE path=? AND status='pending' ORDER BY rowid LIMIT 1",arrayOf(path)).use{if(it.moveToFirst())it.getString(0)else null}
                if(previous!=null){editDraft(previous,body);db.setTransactionSuccessful();return}
            }
            val v=ContentValues();val id=UUID.randomUUID().toString();v.put("id",id);v.put("path",path);v.put("method",method)
            if(!body.has("request_id"))body.put("request_id",id)
            v.put("body",body.toString());v.put("status","pending");v.put("error","");v.put("attempts",0)
            db.insertOrThrow("outbox",null,v);db.setTransactionSuccessful()
        } finally {db.endTransaction()}
    }
    fun beginAttempt(id:String):Boolean {val db=writableDatabase;db.execSQL("UPDATE outbox SET attempts=attempts+1 WHERE id=? AND status='pending'",arrayOf(id));return db.rawQuery("SELECT changes()",null).use{it.moveToFirst();it.getInt(0)==1}}
    fun editDraft(id:String,body:JSONObject) {
        val db=writableDatabase;db.beginTransaction()
        try {
            val row=db.rawQuery("SELECT body,attempts FROM outbox WHERE id=? AND status='pending'",arrayOf(id)).use{if(it.moveToFirst())Pair(JSONObject(it.getString(0)),it.getInt(1))else null}
            check(row!=null){"原请求已有冲突，请先选择版本"}
            val values=ContentValues()
            if(row.second==0){body.put("request_id",row.first.getString("request_id"));values.put("body",body.toString())}
            else {body.remove("request_id");values.put("local_body",body.toString())}
            db.update("outbox",values,"id=?",arrayOf(id));db.setTransactionSuccessful()
        } finally {db.endTransaction()}
    }
    fun acknowledge(id:String,response:JSONObject) {
        val db=writableDatabase;db.beginTransaction()
        try {
            val row=db.rawQuery("SELECT path,local_body FROM outbox WHERE id=?",arrayOf(id)).use{if(it.moveToFirst())Pair(it.getString(0),it.getString(1))else null}
            if(row!=null){
                db.delete("outbox","id=?",arrayOf(id))
                if(row.first.startsWith("drafts")&&row.second.isNotBlank()){
                    val edited=JSONObject(row.second).put("expected_revision",response.getInt("revision"))
                    enqueue("drafts/"+response.getString("id"),edited,"PUT")
                }
            }
            db.setTransactionSuccessful()
        } finally {db.endTransaction()}
    }
    fun conflictDraft(requestId:String,fallback:JSONObject):JSONObject = queue().firstOrNull{
        it.getString("status")=="needs_attention"&&JSONObject(it.getString("body")).optString("request_id")==requestId
    }?.let{shownDraft(it)}?:fallback
    fun resolveRequest(requestId:String){
        if(requestId.isBlank())return
        // Conflict selection includes the displayed continuation, so it must not
        // become another automatic update after the chosen version is accepted.
        for(op in queue())if(op.getString("status")=="needs_attention"&&JSONObject(op.getString("body")).optString("request_id")==requestId)remove(op.getString("id"))
    }
    fun reconcileConflicts(state:JSONObject){
        val conflicts=state.optJSONArray("conflicts")?:return
        for(index in 0 until conflicts.length()){
            val conflict=conflicts.getJSONObject(index)
            if(conflict.optString("state")=="resolved")resolveRequest(conflict.optString("request_id"))
        }
    }
    fun shownDraft(op:JSONObject):JSONObject=JSONObject(op.optString("local_body").ifBlank{op.getString("body")})
    fun queue(): List<JSONObject> = readableDatabase.rawQuery("SELECT * FROM outbox ORDER BY rowid",null).use { c -> buildList { while(c.moveToNext()) { val o=JSONObject();for(i in 0 until c.columnCount)o.put(c.getColumnName(i),c.getString(i));add(o) } } }
    fun remove(id: String) { writableDatabase.delete("outbox","id=?",arrayOf(id)) }
    fun failed(id: String, msg: String) { val v=ContentValues();v.put("status","needs_attention");v.put("error",msg);writableDatabase.update("outbox",v,"id=?",arrayOf(id)) }
}

object Workspace {
    val executor=Executors.newSingleThreadExecutor()
    private val boundConnection=ThreadLocal<String?>()
    private val syncLock=Any()
    private fun prefs(c:Context)=c.getSharedPreferences("connection",Context.MODE_PRIVATE)
    @Synchronized fun connectionIdentity(c:Context)=listOf(prefs(c).getString("database","workspace.db"),url(c),Tailnet.mode(c),prefs(c).getLong("connection_revision",0)).joinToString("|")
    fun requireCurrent(c:Context) {check(boundConnection.get()?.let{it==connectionIdentity(c)}!=false){"工作区已切换，旧操作已取消"}}
    fun databaseFile(c:Context):String {requireCurrent(c);return prefs(c).getString("database","workspace.db")!!}
    fun execute(c:Context,action:()->Unit) {
        val expected=connectionIdentity(c)
        executor.execute {boundConnection.set(expected);try{action()}finally{boundConnection.remove()}}
    }
    fun onUi(activity:Activity,action:()->Unit) {
        val expected=boundConnection.get()?:connectionIdentity(activity)
        activity.runOnUiThread {if(!activity.isDestroyed&&expected==connectionIdentity(activity))action()}
    }
    private fun key():SecretKey { val ks=KeyStore.getInstance("AndroidKeyStore");ks.load(null);if(!ks.containsAlias("workspace-token")){val g=KeyGenerator.getInstance("AES","AndroidKeyStore");g.init(KeyGenParameterSpec.Builder("workspace-token",KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build());g.generateKey()};return ks.getKey("workspace-token",null) as SecretKey }
    @Synchronized fun configure(c:Context,url:String,token:String,pin:String,transport:String=Tailnet.mode(c)) {
        require(transport in listOf("direct","tailnet")){"不支持此连接方式"}
        val config=ConnectionConfig.validated(url,token,pin)
        val cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());val encrypted=cipher.doFinal(token.toByteArray())
        val identity=databaseName(config.url,config.token,config.fingerprint)
        val database=databaseForConnection(c,identity)
        Tailnet.invalidate(c)
        prefs(c).edit().putString("database_for:$identity",database).putString("database",database).putString("url",config.url).putString("token",Base64.encodeToString(encrypted,Base64.NO_WRAP)).putString("iv",Base64.encodeToString(cipher.iv,Base64.NO_WRAP)).putString("pin",config.fingerprint).putString("transport",transport).putLong("connection_revision",prefs(c).getLong("connection_revision",0)+1).commit()
    }
    fun databaseForConnection(c:Context,identity:String):String {
        val p=prefs(c)
        // Bind initial offline work to the first connection without copying or
        // losing request identities. Later connections have separate databases.
        return p.getString("database_for:$identity",null)?:if(!configured(c))p.getString("database","workspace.db")!! else identity
    }
    @Synchronized fun setTransport(c:Context,transport:String) {
        require(transport in listOf("direct","tailnet"))
        check(configured(c)){"请先保存工作区连接"}
        Tailnet.invalidate(c)
        prefs(c).edit().putString("transport",transport).putLong("connection_revision",prefs(c).getLong("connection_revision",0)+1).commit()
    }
    fun databaseName(url:String,token:String,pin:String)="workspace-"+MessageDigest.getInstance("SHA-256").digest((url.trimEnd('/')+"|"+token+"|"+pin.lowercase().replace(":","").trim()).toByteArray()).joinToString(""){"%02x".format(it)}+".db"
    fun configured(c:Context)=prefs(c).contains("url")
    fun url(c:Context)=prefs(c).getString("url","")!!
    private fun token(c:Context):String {val p=prefs(c);val ci=Cipher.getInstance("AES/GCM/NoPadding");ci.init(Cipher.DECRYPT_MODE,key(),GCMParameterSpec(128,Base64.decode(p.getString("iv",""),Base64.NO_WRAP)));return String(ci.doFinal(Base64.decode(p.getString("token",""),Base64.NO_WRAP)))}
    class Failure(val code:Int,val response:JSONObject):Exception(response.optString("error","请求失败"))
    fun request(c:Context,path:String,body:JSONObject?=null,method:String=if(body==null)"GET" else "POST"):JSONObject {
        val captured=synchronized(this){requireCurrent(c);listOf(connectionIdentity(c),url(c),token(c),prefs(c).getString("pin","")!!,Tailnet.mode(c))}
        if(captured[4]=="tailnet") {
            val response=Tailnet.request(c,path,body,method,captured[2],captured[0])
            check(captured[0]==connectionIdentity(c)){"工作区已切换，请核对原操作结果"};requireCurrent(c)
            return response
        }
        val connection=URL(captured[1]+"/api/"+path).openConnection() as HttpsURLConnection
        val pin=captured[3]
        if(pin.isNotBlank()) {
            require(pin.matches(Regex("[a-f0-9]{64}"))){"证书指纹应为 64 位 SHA-256"}
            val trust=object:X509TrustManager {
                override fun getAcceptedIssuers()=emptyArray<X509Certificate>()
                override fun checkClientTrusted(chain:Array<X509Certificate>,type:String){throw java.security.cert.CertificateException("Client certificates unsupported")}
                override fun checkServerTrusted(chain:Array<X509Certificate>,type:String){val actual=MessageDigest.getInstance("SHA-256").digest(chain.first().encoded).joinToString(""){"%02x".format(it)};if(actual!=pin)throw java.security.cert.CertificateException("Server certificate fingerprint changed");chain.first().checkValidity()}
            }
            val ssl=SSLContext.getInstance("TLS");ssl.init(null,arrayOf(trust),null);connection.sslSocketFactory=ssl.socketFactory
            // A user-verified exact leaf certificate pin authenticates a self-hosted endpoint even at a VPN IP.
            connection.hostnameVerifier=HostnameVerifier { _,session -> val actual=MessageDigest.getInstance("SHA-256").digest(session.peerCertificates.first().encoded).joinToString(""){"%02x".format(it)};actual==pin }
        }
        connection.instanceFollowRedirects=false;connection.connectTimeout=10000;connection.readTimeout=20000;connection.requestMethod=method
        connection.setRequestProperty("Authorization","Bearer "+captured[2]);connection.setRequestProperty("Content-Type","application/json")
        try {if(body!=null){connection.doOutput=true;connection.outputStream.use{it.write(body.toString().toByteArray())}}
            val status=connection.responseCode;val stream=if(status in 200..299)connection.inputStream else connection.errorStream
            val raw=stream?.bufferedReader()?.use{it.readText()}?:"{}";val json=JSONObject(raw)
            check(captured[0]==connectionIdentity(c)){"工作区已切换，请核对原操作结果"};requireCurrent(c)
            if(status !in 200..299)throw Failure(status,json);return json
        } finally {connection.disconnect()}
    }
    fun sync(c:Context):JSONObject = synchronized(syncLock) {
        check(configured(c)){"请先配置工作区"}
        LocalStore(c).use {db ->
            for(op in db.queue().filter{it.getString("status")=="pending"}) {
                if(!db.beginAttempt(op.getString("id")))continue
                val current=db.queue().firstOrNull{it.getString("id")==op.getString("id")}?:continue
                try {val response=request(c,current.getString("path"),JSONObject(current.getString("body")),current.getString("method"));db.acknowledge(current.getString("id"),response)}
                catch(e:Failure){if(e.code in listOf(400,403,404,409,413,415))db.failed(op.getString("id"),e.response.toString());else throw e}
            }
            val state=request(c,"state");db.reconcileConflicts(state);db.cache("state",state.toString());state
        }
    }
    fun schedule(c:Context){val manager=c.getSystemService(JobScheduler::class.java);manager.schedule(JobInfo.Builder(100,ComponentName(c,SyncJob::class.java)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(15*60*1000L).setPersisted(true).build())}
    fun notify(c:Context){val n=c.getSystemService(NotificationManager::class.java);n.createNotificationChannel(NotificationChannel("updates","助手消息",NotificationManager.IMPORTANCE_DEFAULT));val intent=PendingIntent.getActivity(c,0,Intent(c,MainActivity::class.java),PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT);try{n.notify(1,Notification.Builder(c,"updates").setSmallIcon(android.R.drawable.ic_dialog_info).setContentTitle("助手有新消息").setContentText("打开工作区查看").setContentIntent(intent).setAutoCancel(true).setVisibility(Notification.VISIBILITY_PRIVATE).build())}catch(_:SecurityException){}}
}

class SyncJob:JobService(){override fun onStartJob(params:JobParameters):Boolean {Workspace.execute(this) {var retry=false;try{val old=LocalStore(this).use{it.cached("state")};val latest=Workspace.sync(this);if(old!=null&&JSONObject(old).optLong("cursor")<latest.optLong("cursor"))Workspace.notify(this)}catch(_:Exception){retry=Tailnet.mode(this)!="tailnet"||Tailnet.enabled(this)};jobFinished(params,retry)};return true};override fun onStopJob(params:JobParameters)=Tailnet.mode(this)!="tailnet"||Tailnet.enabled(this)}
