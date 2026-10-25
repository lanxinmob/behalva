package org.personalassistant.app

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.view.View
import android.widget.Button
import android.widget.TextView

internal object UiStyle {
    val ink=Color.rgb(32,37,43)
    val muted=Color.rgb(98,105,115)
    val accent=Color.rgb(22,104,84)
    val surface=Color.rgb(246,247,249)
    val line=Color.rgb(228,231,235)
    fun dp(context:Context,value:Int)=(value*context.resources.displayMetrics.density+.5f).toInt()
    fun background(context:Context,color:Int,border:Boolean=false)=GradientDrawable().apply {
        setColor(color);cornerRadius=dp(context,6).toFloat()
        if(border)setStroke(dp(context,1),line)
    }
    fun text(context:Context,value:String,size:Float=15f,muted:Boolean=false)=TextView(context).apply {
        text=value;textSize=size;setTextColor(if(muted)UiStyle.muted else ink)
        setPadding(0,dp(context,8),0,dp(context,8));setLineSpacing(dp(context,3).toFloat(),1f)
        setTextIsSelectable(true)
    }
    fun button(context:Context,label:String,primary:Boolean=false,action:()->Unit)=Button(context).apply {
        text=label;isAllCaps=false;textSize=14f;minHeight=dp(context,48)
        setPadding(dp(context,14),dp(context,8),dp(context,14),dp(context,8))
        setTextColor(if(primary)Color.WHITE else ink)
        background=RippleDrawable(ColorStateList.valueOf(Color.rgb(218,230,223)),
            background(context,if(primary)accent else surface),null)
        layoutParams=android.widget.LinearLayout.LayoutParams(-1,-2).apply{topMargin=dp(context,8)}
        setOnClickListener{action()}
    }
    fun heading(view:TextView){view.typeface=Typeface.create("sans-serif-medium",Typeface.NORMAL);view.isAccessibilityHeading=true}
}
