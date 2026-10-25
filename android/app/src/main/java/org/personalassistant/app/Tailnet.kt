package org.personalassistant.app

import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.NetworkInterface
import java.net.URI
import java.security.KeyStore
import java.util.Collections
import java.util.concurrent.Executors
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509TrustManager

internal object TailnetNative {
    private val loaded by lazy { System.loadLibrary("assistant_tailnet"); true }
    private external fun callNative(input:ByteArray):ByteArray?
    fun call(command:JSONObject):JSONObject {
        check(loaded)
        val raw=callNative(command.toString().toByteArray(Charsets.UTF_8))?:throw IOException("内置连接未返回结果")
        val response=JSONObject(String(raw,Charsets.UTF_8))
        if(response.has("error"))throw IOException(response.getString("error"))
        return response
    }
}

/** Owns only app-local networking. It never creates an Android VPN service. */
internal object Tailnet {
    private val control=Executors.newSingleThreadExecutor()
    private val lock=Any()
    private var handle:String?=null
    private var identity:String?=null
    private var generation=0L
    private var callback:ConnectivityManager.NetworkCallback?=null
    @Volatile private var status=JSONObject().put("state","Stopped")
    internal var nativeCall:(JSONObject)->JSONObject={TailnetNative.call(it)}

    fun state():JSONObject=JSONObject(status.toString())
    fun enabled(c:Context)=c.getSharedPreferences("connection",Context.MODE_PRIVATE).getBoolean("tailnet_enabled",false)
    fun mode(c:Context)=c.getSharedPreferences("connection",Context.MODE_PRIVATE).getString("transport","direct")!!
    fun validLogin(value:String):Boolean=try {
        val uri=URI(value)
        uri.scheme=="https"&&uri.rawAuthority=="login.tailscale.com"&&uri.rawPath.startsWith("/a/")&&uri.rawQuery==null&&uri.rawFragment==null
    }catch(_:Exception){false}

    fun connect(c:Context) {
        check(mode(c)=="tailnet"&&Workspace.configured(c)){"请先保存内置连接的工作区配置"}
        close(c,false){error,epoch,expected->
            synchronized(Workspace) {
                if(expected!=Workspace.connectionIdentity(c))return@close
                synchronized(lock) {
                    if(generation!=epoch)return@close
                    if(error!=null){status=JSONObject().put("state","Error").put("detail",error);return@close}
                    c.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().putBoolean("tailnet_enabled",true).commit()
                }
                runCatching{ensure(c)}.onFailure{if(synchronized(lock){generation==epoch})status=JSONObject().put("state","Error").put("detail",it.message)}
            }
        }
    }

    fun ensure(c:Context) {
        if(mode(c)!="tailnet"||!enabled(c))throw IOException("内置连接已停止，请在设置中连接")
        val key=Workspace.connectionIdentity(c)
        val epoch:Long
        synchronized(lock) {
            if(identity==key)return
            check(handle==null){"旧连接尚未关闭"}
            identity=key;generation++;epoch=generation
            status=JSONObject().put("state","Starting")
        }
        val app=c.applicationContext
        val prefs=app.getSharedPreferences("connection",Context.MODE_PRIVATE)
        val folder=File(File(app.noBackupFilesDir,"tailnet"),Workspace.databaseFile(app))
        val origin=Workspace.url(app)
        val pin=prefs.getString("pin","")
        control.execute {
            var created:String?=null
            try {
                if(!current(key,epoch))return@execute
                network(app)
                val response=nativeCall(JSONObject().put("op","start").put("directory",folder.absolutePath)
                    .put("origin",origin).put("pin",pin)
                    .put("roots",trustedRoots()))
                created=response.getString("handle")
                synchronized(lock) {
                    if(current(key,epoch)){handle=created;status=JSONObject().put("state","Starting");created=null}
                }
                observe(app)
            }catch(_:UnsatisfiedLinkError){if(current(key,epoch))status=JSONObject().put("state","Error").put("detail","此安装包没有适用的内置连接组件")}
            catch(e:Exception){if(current(key,epoch))status=JSONObject().put("state","Error").put("detail",e.message?:"内置连接启动失败")}
            finally {created?.let{runCatching{nativeCall(JSONObject().put("op","stop").put("handle",it))}}}
        }
    }

    private fun current(key:String,epoch:Long)=synchronized(lock){identity==key&&generation==epoch}

    fun refresh(c:Context,done:()->Unit) {
        val captured=synchronized(lock){Triple(handle,identity,generation)}
        if(captured.first==null){done();return}
        control.execute {
            try {
                val value=nativeCall(JSONObject().put("op","status").put("handle",captured.first))
                if(!validLogin(value.optString("auth_url")))value.remove("auth_url")
                if(current(captured.second!!,captured.third))status=value
            }catch(e:Exception){if(current(captured.second!!,captured.third))status=JSONObject().put("state","Error").put("detail",e.message)}
            done()
        }
    }

    fun stop(c:Context,forget:Boolean=false,done:(String?)->Unit={}) {
        close(c,forget){error,_,_->done(error)}
    }

    private fun close(c:Context,forget:Boolean,done:(String?,Long,String)->Unit) {
        val captured=synchronized(Workspace) {
            val prefs=c.getSharedPreferences("connection",Context.MODE_PRIVATE)
            prefs.edit().putBoolean("tailnet_enabled",false).putLong("connection_revision",prefs.getLong("connection_revision",0)+1).commit()
            val old=synchronized(lock){val previous=handle;handle=null;identity=null;generation++;status=JSONObject().put("state",if(forget)"LoggingOut" else "Stopped");Pair(previous,generation)}
            Triple(old,File(File(c.noBackupFilesDir,"tailnet"),Workspace.databaseFile(c)),Workspace.connectionIdentity(c))
        }
        val old=captured.first
        val folder=captured.second
        control.execute {
            var error:String?=null
            try {
                if(old.first!=null)nativeCall(JSONObject().put("op",if(forget)"logout" else "stop").put("handle",old.first))
                else if(forget&&folder.exists()&&!folder.deleteRecursively())throw IOException("无法清除本机登录状态")
            }catch(e:Exception){error=e.message}
            synchronized(lock){if(generation==old.second&&forget)status=JSONObject().put("state",if(error==null)"LoggedOut" else "Error").put("detail",error?:"")}
            done(error,old.second,captured.third)
        }
    }

    fun invalidate(c:Context) = stop(c)

    fun request(c:Context,path:String,body:JSONObject?,method:String,token:String,expected:String):JSONObject {
        val captured=synchronized(Workspace) {
            Workspace.requireCurrent(c)
            if(expected!=Workspace.connectionIdentity(c))throw IOException("工作区已切换，旧请求已取消")
            ensure(c)
            synchronized(lock){
                if(identity!=expected)throw IOException("连接归属已改变，旧请求已取消")
                Pair(handle,identity)
            }
        }
        val value=captured.first?:throw IOException("正在建立内置连接，请稍后同步或前往设置登录")
        if(captured.second!=Workspace.connectionIdentity(c))throw IOException("工作区已切换，旧请求已取消")
        val response=nativeCall(JSONObject().put("op","request").put("handle",value).put("path",path)
            .put("method",method).put("body",body?.toString()?:"").put("token",token))
        if(captured.second!=Workspace.connectionIdentity(c)||!enabled(c))throw IOException("连接已改变，请重新核对结果")
        val code=response.getInt("status")
        val json=try{JSONObject(response.getString("body").ifBlank{"{}"})}catch(_:Exception){throw IOException("工作区返回了非 JSON 响应（HTTP $code）")}
        if(code !in 200..299)throw Workspace.Failure(code,json)
        return json
    }

    private fun trustedRoots():String {
        val factory=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
        factory.init(null as KeyStore?)
        return factory.trustManagers.filterIsInstance<X509TrustManager>().flatMap{it.acceptedIssuers.toList()}.distinctBy{it.serialNumber to it.issuerX500Principal}.joinToString("\n") {
            "-----BEGIN CERTIFICATE-----\n"+Base64.encodeToString(it.encoded,Base64.NO_WRAP).chunked(64).joinToString("\n")+"\n-----END CERTIFICATE-----"
        }
    }

    private fun network(c:Context) {
        val values=JSONArray()
        for(item in Collections.list(NetworkInterface.getNetworkInterfaces())) {
            if(item.index<=0)continue
            var flags=0
            if(item.isUp)flags=flags or 1 or 32
            if(item.isLoopback)flags=flags or 4
            if(item.isPointToPoint)flags=flags or 8
            if(item.supportsMulticast())flags=flags or 16
            val addresses=JSONArray()
            for(address in item.interfaceAddresses)address.address.hostAddress?.let{addresses.put(it.substringBefore('%')+"/"+address.networkPrefixLength)}
            values.put(JSONObject().put("name",item.name).put("index",item.index).put("mtu",item.mtu.coerceAtLeast(0)).put("flags",flags).put("addresses",addresses))
        }
        val manager=c.getSystemService(ConnectivityManager::class.java)
        val name=manager.activeNetwork?.let{manager.getLinkProperties(it)?.interfaceName}?:""
        nativeCall(JSONObject().put("op","network").put("interfaces",values).put("default_interface",name))
    }

    private fun observe(c:Context) {
        if(callback!=null)return
        val observer=object:ConnectivityManager.NetworkCallback(){
            override fun onAvailable(network:Network){update()}
            override fun onLost(network:Network){update()}
            override fun onLinkPropertiesChanged(network:Network,properties:LinkProperties){update()}
            private fun update(){control.execute{if(synchronized(lock){handle!=null})runCatching{network(c)}}}
        }
        c.getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(observer)
        callback=observer
    }
}
