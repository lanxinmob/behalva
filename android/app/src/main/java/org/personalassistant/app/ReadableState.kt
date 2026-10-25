package org.personalassistant.app

import org.json.JSONArray
import org.json.JSONObject

internal object ReadableState {
    fun task(value:String)=mapOf("queued" to "等待处理","running" to "正在处理","completed" to "已完成","blocked" to "等待配置或接入","waiting_input" to "需要补充信息","waiting_authorization" to "需要资料授权","waiting_approval" to "等待确认","failed" to "处理失败","unknown" to "结果待核实","cancelled" to "已取消")[value]?:"待处理（$value）"
    fun outbox(value:String)=if(value=="pending")"待同步" else if(value=="needs_attention")"需要你处理" else "待核实"
    fun result(result:JSONObject?):String {
        if(result==null)return ""
        return listOf("body","message","summary","answer").map{result.optString(it)}.firstOrNull{it.isNotBlank()}?:
            "处理记录已保存，可查看详情。"
    }
    fun fields(value:Any):String=when(value){
        is JSONObject->value.keys().asSequence().joinToString("\n\n"){"$it：${fields(value.get(it))}"}
        is JSONArray->(0 until value.length()).joinToString("\n"){fields(value.get(it))}
        JSONObject.NULL->"未填写"
        else->value.toString()
    }
}
