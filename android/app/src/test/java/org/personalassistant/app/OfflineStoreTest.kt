package org.personalassistant.app

import android.content.Context
import org.json.JSONObject
import org.junit.Test
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import java.util.UUID

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[28])
class OfflineStoreTest {
    private fun context():Context {
        val context=RuntimeEnvironment.getApplication()
        context.getSharedPreferences("connection",Context.MODE_PRIVATE).edit().putString("database",UUID.randomUUID().toString()+".db").commit()
        return context
    }
    private fun draft(body:String)=JSONObject().put("title","Fixture").put("body",body).put("expected_revision",1)
    @Test fun consecutiveOfflineEditsCoalesceUntilFirstAttempt(){
        LocalStore(context()).use { db ->
            db.enqueue("drafts/fixture",draft("First"),"PUT")
            val removed=db.queue().single().getString("id")
            db.enqueue("drafts/fixture",draft("Second"),"PUT")
            assertEquals(1,db.queue().size)
            assertEquals("Second",JSONObject(db.queue().single().getString("body")).getString("body"))
            assertEquals(removed,db.queue().single().getString("id"))
            val sent=db.queue().single();assertTrue(db.beginAttempt(sent.getString("id")))
            db.enqueue("drafts/fixture",draft("Third"),"PUT")
            assertEquals(1,db.queue().size)
            assertEquals(sent.getString("body"),db.queue().first().getString("body"))
            assertEquals("Third",db.shownDraft(db.queue().single()).getString("body"))
            db.acknowledge(sent.getString("id"),JSONObject().put("id","fixture").put("revision",2))
            val followup=db.queue().single()
            assertEquals("Third",JSONObject(followup.getString("body")).getString("body"))
            assertEquals(2,JSONObject(followup.getString("body")).getInt("expected_revision"))
        }
    }
    @Test fun newOfflineDraftCanContinueWithoutDuplicatingRequest(){
        LocalStore(context()).use { db ->
            db.enqueue("drafts",draft("Start"))
            val queued=db.queue().single();val original=JSONObject(queued.getString("body"))
            db.editDraft(queued.getString("id"),draft("Continue writing"))
            assertEquals(1,db.queue().size)
            val updated=JSONObject(db.queue().single().getString("body"))
            assertEquals("Continue writing",updated.getString("body"))
            assertEquals(original.getString("request_id"),updated.getString("request_id"))
            db.beginAttempt(queued.getString("id"))
            db.editDraft(queued.getString("id"),draft("Still editable after offline send failure"))
            assertEquals("Continue writing",JSONObject(db.queue().single().getString("body")).getString("body"))
            assertEquals("Still editable after offline send failure",db.shownDraft(db.queue().single()).getString("body"))
            db.acknowledge(queued.getString("id"),JSONObject().put("id","server-created").put("revision",1))
            assertEquals("drafts/server-created",db.queue().single().getString("path"))
        }
    }
    // Robolectric includes the method name in its temporary directory; keep it
    // short enough for the Windows SQLite driver to create WAL sidecar files.
    @Test fun workspaceIsolation(){
        val context=context();val prefs=context.getSharedPreferences("connection",Context.MODE_PRIVATE)
        val a=Workspace.databaseName("https://a.example","token-a","")
        val b=Workspace.databaseName("https://b.example","token-b","")
        prefs.edit().putString("database",a).commit()
        LocalStore(context).use{it.enqueue("drafts",draft("Private A"));it.cache("state","cached A")}
        prefs.edit().putString("database",b).commit()
        LocalStore(context).use{assertTrue(it.queue().isEmpty());assertNull(it.cached("state"))}
        prefs.edit().putString("database",a).commit()
        LocalStore(context).use{assertEquals(1,it.queue().size);assertEquals("cached A",it.cached("state"))}
    }
    @Test fun initialOfflineWork(){
        val context=context();val prefs=context.getSharedPreferences("connection",Context.MODE_PRIVATE)
        prefs.edit().remove("url").commit()
        LocalStore(context).use{it.enqueue("drafts",draft("Before pairing"))}
        val initial=prefs.getString("database",null)
        val first=Workspace.databaseName("https://first.example","first-token","")
        assertEquals(initial,Workspace.databaseForConnection(context,first))
        prefs.edit().putString("url","https://first.example").putString("database_for:$first",initial).commit()
        val second=Workspace.databaseName("https://second.example","second-token","")
        assertEquals(second,Workspace.databaseForConnection(context,second))
        assertEquals(initial,Workspace.databaseForConnection(context,first))
        LocalStore(context).use{assertEquals("Before pairing",it.shownDraft(it.queue().single()).getString("body"))}
    }
    @Test fun resolvingOneConflictKeepsOtherEditsForTheSameDraft(){
        LocalStore(context()).use{db->
            db.enqueue("drafts/fixture",draft("Earlier"),"PUT")
            val earlier=db.queue().single();db.beginAttempt(earlier.getString("id"));db.failed(earlier.getString("id"),"conflict")
            db.enqueue("drafts/fixture",draft("Later"),"PUT")
            val later=db.queue().last();db.beginAttempt(later.getString("id"));db.failed(later.getString("id"),"conflict")
            val request=JSONObject(earlier.getString("body")).getString("request_id")
            db.resolveRequest(request)
            assertEquals(1,db.queue().size)
            assertEquals(later.getString("id"),db.queue().single().getString("id"))
            assertEquals("Later",db.shownDraft(db.queue().single()).getString("body"))
        }
    }
    @Test fun conflictSelectionIncludesContinuationWithoutQueuingAnOverwrite(){
        LocalStore(context()).use{db->
            db.enqueue("drafts/fixture",draft("Attempted A"),"PUT")
            val sent=db.queue().single();db.beginAttempt(sent.getString("id"))
            db.editDraft(sent.getString("id"),draft("Latest B"))
            db.failed(sent.getString("id"),"conflict with server C")
            val request=JSONObject(sent.getString("body")).getString("request_id")
            assertEquals("Latest B",db.conflictDraft(request,draft("Attempted A")).getString("body"))
            db.resolveRequest(request)
            assertTrue("A conflict choice must not trigger a hidden continuation PUT",db.queue().isEmpty())
        }
    }
    @Test fun lostConflictReceipt(){
        LocalStore(context()).use{db->
            db.enqueue("drafts/fixture",draft("Original phone request"),"PUT")
            val sent=db.queue().single();db.beginAttempt(sent.getString("id"))
            db.editDraft(sent.getString("id"),draft("Latest phone version"));db.failed(sent.getString("id"),"conflict")
            val request=JSONObject(sent.getString("body")).getString("request_id")
            val state=JSONObject().put("conflicts",org.json.JSONArray().put(JSONObject().put("state","resolved").put("choice","remote").put("draft_id","fixture").put("request_id",request)))
            // No immediate acknowledgement: the owner chose remotely, but the
            // phone lost the response and learns the result on the next sync.
            db.reconcileConflicts(state)
            assertTrue(db.queue().isEmpty())
            db.reconcileConflicts(state)
            assertTrue(db.queue().isEmpty())
        }
    }
}
