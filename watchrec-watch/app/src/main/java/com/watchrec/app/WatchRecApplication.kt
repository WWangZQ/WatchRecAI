package com.watchrec.app

import android.app.Application
import com.watchrec.app.uploader.Config

class WatchRecApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Config.init(this)
    }
}
