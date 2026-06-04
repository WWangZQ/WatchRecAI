package com.watchrec.app.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.watchrec.app.R
import com.watchrec.app.model.RecordingItem
import com.watchrec.app.util.TimeUtils

/**
 * 录音列表适配器。
 * 支持点击（播放）和长按（删除）回调。
 */
class RecordingAdapter(
    private val onItemClick: (RecordingItem) -> Unit,
    private val onItemLongClick: (RecordingItem) -> Unit
) : RecyclerView.Adapter<RecordingAdapter.ViewHolder>() {

    private var items: List<RecordingItem> = emptyList()
    private var uploadedFiles: Set<String> = emptySet()

    fun submitList(newItems: List<RecordingItem>) {
        items = newItems
        notifyDataSetChanged()
    }

    fun setUploadedFiles(files: Set<String>) {
        uploadedFiles = files
        notifyDataSetChanged()
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_recording, parent, false)
        return ViewHolder(view)
    }

    override fun onBindViewHolder(holder: ViewHolder, position: Int) {
        val item = items[position]
        holder.bind(item, item.fileName in uploadedFiles)
        holder.itemView.setOnClickListener { onItemClick(item) }
        holder.itemView.setOnLongClickListener {
            onItemLongClick(item)
            true
        }
    }

    override fun getItemCount(): Int = items.size

    class ViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val dateTimeText: TextView = itemView.findViewById(R.id.dateTimeText)
        private val durationText: TextView = itemView.findViewById(R.id.durationText)
        private val uploadStatusText: TextView = itemView.findViewById(R.id.uploadStatusText)

        fun bind(item: RecordingItem, uploaded: Boolean) {
            dateTimeText.text = TimeUtils.formatDateTime(item.timestamp)
            durationText.text = if (item.duration > 0) {
                TimeUtils.formatDuration(item.duration)
            } else {
                "--:--"
            }

            if (uploaded) {
                uploadStatusText.text = "✓"
                uploadStatusText.setTextColor(
                    itemView.context.getColor(R.color.upload_done)
                )
            } else {
                uploadStatusText.text = "↑"
                uploadStatusText.setTextColor(
                    itemView.context.getColor(R.color.text_secondary)
                )
            }
        }
    }
}
