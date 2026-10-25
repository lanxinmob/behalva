package org.personalassistant.app

import android.app.Activity
import android.content.Context
import android.widget.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RuntimeEnvironment
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.util.UUID

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[28])
class ConversationScreenTest {
    @Before fun isolatedWorkspace(){RuntimeEnvironment.getApplication()
        .getSharedPreferences("connection",Context.MODE_PRIVATE).edit().clear()
        .putString("database",UUID.randomUUID().toString()+".db").commit()}

    @Test fun offlineComposerSurvivesReopenAndQueuesOnce(){
        val activity=Robolectric.buildActivity(Activity::class.java).setup()
        try {
            val box=LinearLayout(activity.get())
            fun render(){box.removeAllViews();ConversationScreen(activity.get(),box,JSONObject(),{},
                {_,_->fail("Send should durably queue locally")},{},{_,_,_->}).chat()}
            render()
            fun input()=box.findViewWithTag<EditText>("chat-input")
            fun send()=box.findViewWithTag<ImageButton>("chat-send")
            input().setText("Keep my fixture feature discussion")
            render()
            assertEquals("Keep my fixture feature discussion",input().text.toString())
            send().performClick()
            render()
            assertEquals("",input().text.toString())
            assertFalse(send().isEnabled)
            LocalStore(activity.get()).use{db->
                assertEquals(1,db.queue().size)
                val sent=JSONObject(db.queue().single().getString("body"))
                assertEquals("Keep my fixture feature discussion",sent.getString("body"))
                assertEquals(0,sent.getInt("expected_sequence"))
            }
        }finally{activity.pause().stop().destroy()}
    }

    @Test fun explicitRebasePreservesTextWithFreshIdentity(){
        LocalStore(RuntimeEnvironment.getApplication()).use{db->
            db.enqueueChat(JSONObject().put("conversation_id","fixture").put("body","Original phone input")
                .put("expected_sequence",2),"chat:draft:fixture")
            val old=db.queue().single();val oldBody=JSONObject(old.getString("body"))
            // A network-unknown request cannot be relabelled as a fresh request.
            try{db.rebaseConversationRequest(old.getString("id"),4);fail("Unknown delivery must retain request identity")}catch(_:IllegalStateException){}
            db.failed(old.getString("id"),"409: newer conversation")
            db.rebaseConversationRequest(old.getString("id"),4)
            val fresh=JSONObject(db.queue().single().getString("body"))
            assertEquals("Original phone input",fresh.getString("body"))
            assertEquals(4,fresh.getInt("expected_sequence"))
            assertNotEquals(oldBody.getString("request_id"),fresh.getString("request_id"))
            assertEquals("pending",db.queue().single().getString("status"))
        }
    }

    @Test fun taskConflictIsVisibleAndRebasesOriginalSpecification(){
        val activity=Robolectric.buildActivity(Activity::class.java).setup()
        val id="1234567890abcdef1234567890abcdef"
        try {
            var oldIdentity=""
            LocalStore(activity.get()).use{db->
                db.cache("chat:selected",id)
                db.enqueue("conversations/$id/tasks",JSONObject().put("kind","feature")
                    .put("title","Fixture export specification").put("expected_sequence",1)
                    .put("source_ids",org.json.JSONArray().put("fixture-source")))
                val op=db.queue().single();oldIdentity=JSONObject(op.getString("body")).getString("request_id")
                db.failed(op.getString("id"),"Conversation changed")
            }
            val box=LinearLayout(activity.get())
            val state=JSONObject().put("conversations",org.json.JSONArray().put(JSONObject()
                .put("id",id).put("title","Fixture discussion").put("sequence",2)))
            ConversationScreen(activity.get(),box,state,{},{_,_->},{},{_,_,_->}).chat()
            assertTrue((0 until box.childCount).map{box.getChildAt(it)}.filterIsInstance<TextView>()
                .any{it.text.contains("Fixture export specification")})
            (0 until box.childCount).map{box.getChildAt(it)}.filterIsInstance<Button>()
                .single{it.text=="核对最新对话后重新发送"}.performClick()
            org.robolectric.shadows.ShadowAlertDialog.getLatestAlertDialog()
                .getButton(android.content.DialogInterface.BUTTON_POSITIVE).performClick()
            org.robolectric.Shadows.shadowOf(android.os.Looper.getMainLooper()).idle()
            LocalStore(activity.get()).use{db->
                val op=db.queue().single();val body=JSONObject(op.getString("body"))
                assertEquals("conversations/$id/tasks",op.getString("path"))
                assertEquals("Fixture export specification",body.getString("title"))
                assertEquals("fixture-source",body.getJSONArray("source_ids").getString(0))
                assertEquals(2,body.getInt("expected_sequence"))
                assertNotEquals(oldIdentity,body.getString("request_id"))
            }
        }finally{activity.pause().stop().destroy()}
    }
}
