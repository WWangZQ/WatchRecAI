package com.watchrec.app.util

import android.animation.Animator
import android.animation.AnimatorListenerAdapter
import android.animation.ObjectAnimator
import android.app.Activity
import android.content.Context
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import android.widget.FrameLayout

/**
 * 简易滑动返回容器。
 * 从屏幕左半侧开始的右滑手势，水平位移超过 [SWIPE_THRESHOLD] 时
 * 将内容平移出屏幕并 finish() 当前 Activity。
 */
class SwipeDismissFrameLayout @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
    defStyleAttr: Int = 0
) : FrameLayout(context, attrs, defStyleAttr) {

    companion object {
        /** 滑动起点须在屏幕左半部分内 */
        private const val START_REGION = 0.4f
        /** 水平位移占屏幕宽度的比例阈值 */
        private const val SWIPE_THRESHOLD = 0.3f
    }

    private var startX = 0f
    private var startY = 0f
    private var tracking = false

    override fun onInterceptTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                startX = ev.rawX
                startY = ev.rawY
                tracking = ev.x < width * START_REGION
            }
            MotionEvent.ACTION_MOVE -> {
                if (tracking) {
                    val dx = ev.rawX - startX
                    val dy = ev.rawY - startY
                    if (dx > 20 && dx > dy * 1.5f) {
                        return true // 拦截，交给 onTouchEvent 处理
                    }
                }
            }
        }
        return false
    }

    override fun onTouchEvent(ev: MotionEvent): Boolean {
        if (!tracking) return super.onTouchEvent(ev)

        when (ev.actionMasked) {
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                val dx = ev.rawX - startX
                val threshold = width * SWIPE_THRESHOLD
                if (dx >= threshold) {
                    animateDismiss()
                } else {
                    // 未达到阈值，回弹
                    animateCancel()
                }
                tracking = false
                return true
            }
        }
        return true
    }

    private fun animateDismiss() {
        val activity = context as? Activity ?: return
        ObjectAnimator.ofFloat(this, View.TRANSLATION_X, translationX, width.toFloat()).apply {
            duration = 200
            addListener(object : AnimatorListenerAdapter() {
                override fun onAnimationEnd(animation: Animator) {
                    activity.finish()
                    activity.overridePendingTransition(0, 0)
                }
            })
            start()
        }
    }

    private fun animateCancel() {
        ObjectAnimator.ofFloat(this, View.TRANSLATION_X, translationX, 0f).apply {
            duration = 150
            start()
        }
    }
}
