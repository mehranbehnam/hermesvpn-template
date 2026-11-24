package com.hermesvpn.app

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView

/** لیست کانفیگ‌ها: سرگروه‌ها و آیتم‌ها توی یک RecyclerView، مثل V2Box. */
class ConfigsAdapter(
    private val onSelect: (ConfigStore.Entry) -> Unit,
    private val onRename: (ConfigStore.Entry) -> Unit,
    private val onShare: (ConfigStore.Entry) -> Unit,
    private val onDelete: (ConfigStore.Entry) -> Unit,
    private val onFavorite: (ConfigStore.Entry) -> Unit,
    private val onGroupPing: (String) -> Unit,
    private val onGroupToggle: (String) -> Unit,
    private val onGroupMenu: (String, View) -> Unit,
) : RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    sealed class Row {
        data class Group(val name: String, val count: Int, val expanded: Boolean, val info: String,
                         val key: String = name, val group: String = name) : Row()
        data class Config(val entry: ConfigStore.Entry, val selected: Boolean, val expanded: Boolean) : Row()
    }

    private var rows: List<Row> = emptyList()

    fun submit(newRows: List<Row>) {
        rows = newRows
        notifyDataSetChanged()
    }

    override fun getItemViewType(position: Int) = if (rows[position] is Row.Group) 0 else 1

    override fun getItemCount() = rows.size

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val inflater = LayoutInflater.from(parent.context)
        return if (viewType == 0) {
            GroupHolder(inflater.inflate(R.layout.item_config_group, parent, false))
        } else {
            ConfigHolder(inflater.inflate(R.layout.item_config, parent, false))
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        when (val row = rows[position]) {
            is Row.Group -> (holder as GroupHolder).bind(row)
            is Row.Config -> (holder as ConfigHolder).bind(row)
        }
    }

    inner class GroupHolder(view: View) : RecyclerView.ViewHolder(view) {
        private val name: TextView = view.findViewById(R.id.groupName)
        private val info: TextView = view.findViewById(R.id.groupInfo)
        private val expandIcon: ImageView = view.findViewById(R.id.groupExpandIcon)
        private val pingButton: ImageButton = view.findViewById(R.id.groupPingButton)
        private val menuButton: ImageButton = view.findViewById(R.id.groupMenuButton)

        fun bind(row: Row.Group) {
            name.text = "${row.name} (${row.count})"
            info.text = row.info
            expandIcon.rotation = if (row.expanded) 0f else -90f
            itemView.setOnClickListener { onGroupToggle(row.key) }
            expandIcon.setOnClickListener { onGroupToggle(row.key) }
            pingButton.setOnClickListener { onGroupPing(row.group) }
            menuButton.setOnClickListener { onGroupMenu(row.group, menuButton) }
        }
    }

    inner class ConfigHolder(view: View) : RecyclerView.ViewHolder(view) {
        private val badge: TextView = view.findViewById(R.id.protocolBadge)
        private val body: LinearLayout = view.findViewById(R.id.configBody)
        private val name: TextView = view.findViewById(R.id.configName)
        private val address: TextView = view.findViewById(R.id.configAddress)
        private val ping: TextView = view.findViewById(R.id.configPing)
        private val favoriteMark: ImageView = view.findViewById(R.id.configFavorite)
        private val actions: LinearLayout = view.findViewById(R.id.configActions)

        fun bind(row: Row.Config) {
            val e = row.entry
            val ctx = itemView.context
            badge.text = e.protocol
            name.text = e.name
            address.text = maskAddress(e.address)
            favoriteMark.visibility = if (e.favorite) View.VISIBLE else View.GONE

            when {
                e.ping == ConfigStore.PING_TIMEOUT -> {
                    ping.text = ctx.getString(R.string.ping_timeout)
                    ping.setTextColor(ContextCompat.getColor(ctx, R.color.text_on_primary))
                    ping.setBackgroundResource(R.drawable.bg_ping_badge)
                    ping.background.setTint(ContextCompat.getColor(ctx, R.color.danger))
                }
                e.ping >= 0 -> {
                    ping.text = "${e.ping} ms"
                    ping.setTextColor(ContextCompat.getColor(ctx, R.color.text_on_primary))
                    ping.setBackgroundResource(R.drawable.bg_ping_badge)
                    ping.background.setTint(
                        ContextCompat.getColor(ctx, if (e.ping < 300) R.color.connected else R.color.primary)
                    )
                }
                else -> {
                    ping.text = ctx.getString(R.string.ping_unknown)
                    ping.setTextColor(ContextCompat.getColor(ctx, R.color.text_secondary))
                    ping.setBackgroundResource(R.drawable.bg_ping_badge)
                    ping.background.setTintList(null)
                }
            }

            body.setBackgroundResource(
                if (row.selected) R.drawable.bg_config_selected else R.drawable.bg_config_item
            )
            actions.visibility = if (row.expanded) View.VISIBLE else View.GONE

            body.setOnClickListener { onSelect(e) }
            itemView.findViewById<ImageButton>(R.id.configEditButton).setOnClickListener { onRename(e) }
            itemView.findViewById<ImageButton>(R.id.configShareButton).setOnClickListener { onShare(e) }
            itemView.findViewById<ImageButton>(R.id.configDeleteButton).setOnClickListener { onDelete(e) }
            itemView.findViewById<ImageButton>(R.id.configFavoriteButton).setOnClickListener { onFavorite(e) }
        }

        /** آدرس سرور را نیمه‌پنهان نشان می‌دهد — همان کاری که V2Box می‌کند،
         * تا اسکرین‌شات‌های مشتری‌ها آدرس سرور را لو ندهند. */
        private fun maskAddress(addr: String): String {
            if (addr.length <= 8) return addr
            return addr.take(4) + "****" + addr.takeLast(4)
        }
    }
}
