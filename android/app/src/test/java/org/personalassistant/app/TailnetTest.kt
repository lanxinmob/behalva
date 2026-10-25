package org.personalassistant.app

import android.content.Context
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Robolectric
import org.robolectric.Shadows
import org.robolectric.annotation.Config
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[28])
class TailnetTest {
    private fun context():Context {
        val c=RuntimeEnvironment.getApplication()
        c.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().clear()
            .putString("database",UUID.randomUUID().toString()+".db")
            .putString("url","https://workspace.example").putString("transport","tailnet").commit()
        return c
    }
    private fun await(latch:CountDownLatch){assertTrue("worker did not finish",latch.await(10,TimeUnit.SECONDS))}
    private fun stop(c:Context,forget:Boolean=false){val done=CountDownLatch(1);Tailnet.stop(c,forget){assertNull(it);done.countDown()};await(done)}
    private fun running(c:Context) {
        val deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(10)
        while(System.nanoTime()<deadline) {
            val ready=CountDownLatch(1);Tailnet.refresh(c){ready.countDown()};await(ready)
            if(Tailnet.state().optString("state")=="Running")return
            Thread.sleep(10)
        }
        fail("private connection did not reach Running")
    }

    @Test fun stopStaysStoppedAndPreservesOfflineWork() {
        val c=context();val calls=AtomicInteger()
        Tailnet.nativeCall={calls.incrementAndGet();JSONObject()}
        try {
            LocalStore(c).use{it.cache("state","private cache");it.enqueue("drafts",JSONObject().put("body","offline draft"))}
            val before=Workspace.databaseFile(c)
            stop(c,true)
            assertFalse(Tailnet.enabled(c))
            try{Tailnet.ensure(c);fail("stopped connection restarted")}catch(_:java.io.IOException){}
            assertEquals(0,calls.get())
            Workspace.setTransport(c,"direct");Workspace.setTransport(c,"tailnet")
            assertEquals(before,Workspace.databaseFile(c))
            LocalStore(c).use{assertEquals("private cache",it.cached("state"));assertEquals(1,it.queue().size)}
        }finally{stop(c);Tailnet.nativeCall={TailnetNative.call(it)}}
    }

    @Test fun queuedWorkCannotUseNewWorkspace() {
        val c=context();val entered=CountDownLatch(1);val release=CountDownLatch(1);val finished=CountDownLatch(1)
        Workspace.executor.execute{entered.countDown();release.await(10,TimeUnit.SECONDS)}
        await(entered)
        var rejected=false
        Workspace.execute(c){try{LocalStore(c).close()}catch(_:IllegalStateException){rejected=true}finally{finished.countDown()}}
        c.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().putString("database","other.db").commit()
        release.countDown();await(finished)
        assertTrue("old queued work was allowed to use the new database",rejected)
    }

    @Test fun lateConnectCannotUndoStop() {
        val c=context();val started=CountDownLatch(1);val closing=CountDownLatch(1);val release=CountDownLatch(1)
        Tailnet.nativeCall={command->when(command.getString("op")){
            "start"->{started.countDown();JSONObject().put("handle","fixture")}
            "stop"->{closing.countDown();release.await(10,TimeUnit.SECONDS);JSONObject()}
            else->JSONObject()
        }}
        try {
            Tailnet.connect(c);await(started)
            // Queue a reconnect behind a blocked native shutdown, then cancel it.
            Tailnet.connect(c);await(closing)
            val finished=CountDownLatch(1);Tailnet.stop(c){finished.countDown()}
            release.countDown();await(finished)
            assertFalse("late reconnect enabled the stopped connection",Tailnet.enabled(c))
            assertEquals("Stopped",Tailnet.state().getString("state"))
        }finally{release.countDown();stop(c);Tailnet.nativeCall={TailnetNative.call(it)}}
    }

    @Test fun loginLinkMustBeDeviceAuthorizationOnOfficialHost() {
        assertTrue(Tailnet.validLogin("https://login.tailscale.com/a/fixture"))
        for(value in listOf("https://login.tailscale.com/","http://login.tailscale.com/a/id","https://login.tailscale.com.evil.example/a/id","https://user@login.tailscale.com/a/id","https://login.tailscale.com/a/id?next=evil"))assertFalse(value,Tailnet.validLogin(value))
    }

    @Test fun capturedTokenCannotCrossWorkspace() {
        val c=context();val starts=CountDownLatch(1);val requests=AtomicInteger()
        Tailnet.nativeCall={command->when(command.getString("op")){
            "start"->{starts.countDown();JSONObject().put("handle","new-workspace")}
            "status"->JSONObject().put("state","Running")
            "request"->{requests.incrementAndGet();JSONObject().put("status",200).put("body","{}")}
            else->JSONObject()
        }}
        try {
            val old=Workspace.connectionIdentity(c)
            c.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().putString("url","https://other.example").commit()
            Tailnet.connect(c);await(starts)
            running(c)
            try{Tailnet.request(c,"browser/command",JSONObject(),"POST","old-private-token",old);fail("old token accepted by new workspace")}catch(_:java.io.IOException){}
            assertEquals("old token reached native HTTP",0,requests.get())
            Tailnet.request(c,"state",null,"GET","new-token",Workspace.connectionIdentity(c))
            assertEquals("new workspace fixture did not actually work",1,requests.get())
        }finally{stop(c);Tailnet.nativeCall={TailnetNative.call(it)}}
    }

    @Test fun stopAndLogoutInvalidateQueuedCommands() {
        for(forget in listOf(false,true)) {
            val c=context();val requests=AtomicInteger();val entered=CountDownLatch(1);val release=CountDownLatch(1);val finished=CountDownLatch(1)
            Tailnet.nativeCall={command->when(command.getString("op")){
                "start"->JSONObject().put("handle","fixture")
                "status"->JSONObject().put("state","Running")
                "request"->{requests.incrementAndGet();JSONObject().put("status",200).put("body","{}")}
                else->JSONObject()
            }}
            try {
                Tailnet.connect(c)
                running(c)
                Workspace.executor.execute{entered.countDown();release.await(10,TimeUnit.SECONDS)};await(entered)
                val expected=Workspace.connectionIdentity(c)
                Workspace.execute(c){try{Tailnet.request(c,"browser/command",JSONObject().put("action","click"),"POST","fixture-token",expected);fail("queued command survived stop")}catch(_:IllegalStateException){}catch(_:java.io.IOException){}finally{finished.countDown()}}
                stop(c,forget)
                Tailnet.connect(c)
                running(c)
                release.countDown();await(finished)
                assertEquals("stopped operation was sent",0,requests.get())
            }finally{release.countDown();stop(c);Tailnet.nativeCall={TailnetNative.call(it)}}
        }
    }

    @Test fun busyAfterReconnect() {
        val c=context();val entered=CountDownLatch(1);val release=CountDownLatch(1)
        Workspace.executor.execute{entered.countDown();release.await(10,TimeUnit.SECONDS)};await(entered)
        Tailnet.nativeCall={command->when(command.getString("op")){
            "start"->JSONObject().put("handle","fixture")
            "status"->JSONObject().put("state","Running")
            else->JSONObject()
        }}
        val controller=Robolectric.buildActivity(MainActivity::class.java).setup()
        try {
            val activity=controller.get()
            fun busy()=MainActivity::class.java.getDeclaredMethod("getSyncing").apply{isAccessible=true}.invoke(activity) as Boolean
            assertTrue(busy())
            stop(c);Tailnet.connect(c);running(c)
            assertFalse("stale sync still blocks the new connection",busy())
            MainActivity::class.java.getDeclaredMethod("sync").apply{isAccessible=true}.invoke(activity)
            assertTrue("new sync did not start",busy())
            release.countDown()
            val drained=CountDownLatch(1);Workspace.executor.execute{drained.countDown()};await(drained)
            Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            // This fixture deliberately has no model/device secret. Its error
            // still must release the busy state so another sync can be tried.
            assertFalse("completed new sync did not release busy state",busy())
        }finally{release.countDown();controller.pause().stop().destroy();stop(c);Tailnet.nativeCall={TailnetNative.call(it)}}
    }
}
