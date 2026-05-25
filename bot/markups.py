from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from core.config import config

def main_menu_markup(is_admin: bool = False):
    kb = []
    kb.extend([
        [InlineKeyboardButton("📂 Список адресов", callback_data="menu_groups")],
        [InlineKeyboardButton("➕ Добавить адрес", callback_data="menu_add_address")],
        [InlineKeyboardButton("🗑 Удалить название", callback_data="menu_remove_group")]
    ])
    if is_admin:
        kb.append([InlineKeyboardButton("👑 Админ панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def groups_list_markup(groups):
    kb = [[InlineKeyboardButton(g.name, callback_data=f"group:{g.id}")] for g in groups]
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(kb)

def group_view_markup(group):
    kb = [
        [InlineKeyboardButton("➕ Добавить адрес", callback_data=f"group_add_addr:{group.id}")],
        [InlineKeyboardButton("✏ Заменить адрес", callback_data=f"group_edit_addr:{group.id}")],
        [InlineKeyboardButton("🗑 Удалить адрес", callback_data=f"group_del_addr:{group.id}")],
        [InlineKeyboardButton("🗑 Удалить название", callback_data=f"group_del:{group.id}")],
        [InlineKeyboardButton("⬅ Назад", callback_data="menu_groups")],
    ]
    return InlineKeyboardMarkup(kb)

def cancel_markup(target="menu_main"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Отмена", callback_data=f"cancel:{target}")]])

def confirmations_markup(target="menu_main"):
    kb = [
        [InlineKeyboardButton("1 подтверждение", callback_data="conf:1")],
        [InlineKeyboardButton("2 подтверждения", callback_data="conf:2")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cancel:{target}")]
    ]
    return InlineKeyboardMarkup(kb)

def admin_panel_markup(users, is_main_admin=False):
    kb = []
    for user in users:
        name = user.first_name or user.username or f"ID: {user.telegram_id}"
        kb.append([InlineKeyboardButton(f"👤 {name} ({user.telegram_id})", callback_data=f"admin_user:{user.telegram_id}")])
    kb.append([InlineKeyboardButton("✉ Рассылка сообщений", callback_data="admin_broadcast_menu")])
    if is_main_admin:
        kb.append([InlineKeyboardButton("👥 Управление админами", callback_data="admin_manage_admins")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(kb)

def admin_manage_admins_markup(admins, main_admin_id):
    kb = []
    for uid in admins:
        if str(uid) != str(main_admin_id):
            kb.append([InlineKeyboardButton(f"👤 {uid} ❌ Удалить", callback_data=f"admin_del_admin:{uid}")])
        else:
            kb.append([InlineKeyboardButton(f"👑 Главный: {uid}", callback_data="noop")])
    kb.append([InlineKeyboardButton("➕ Добавить администратора", callback_data="admin_add_admin_prompt")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def admin_broadcast_menu_markup(users, selected_uids):
    kb = []
    for user in users:
        uid = str(user.telegram_id)
        name = user.first_name or user.username or f"ID: {uid}"
        mark = "✅ " if uid in selected_uids else "⬜ "
        kb.append([InlineKeyboardButton(f"{mark}{name} ({uid})", callback_data=f"admin_broadcast_toggle:{uid}")])
    
    if selected_uids:
        kb.append([InlineKeyboardButton("✍️ Написать сообщение", callback_data="admin_broadcast_write")])
    kb.append([InlineKeyboardButton("☑ Выбрать всех", callback_data="admin_broadcast_select_all")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def admin_user_markup(uid, user):
    kb = []
    for g in user.groups:
        for a in g.addresses:
            status = "❌ Откл." if a.notify_disabled else "✅ Вкл."
            kb.append([InlineKeyboardButton(f"{g.name} - {a.address[:8]}... [{status}]", callback_data=f"admin_addr:{uid}:{g.id}:{a.id}")])
    kb.append([InlineKeyboardButton("➕ Добавить адрес пользователю", callback_data=f"admin_add_addr:{uid}")])
    kb.append([InlineKeyboardButton("⬅ Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def admin_addr_markup(uid, gid, aid, disabled):
    kb = [
        [InlineKeyboardButton("✅ Включить уведомления" if disabled else "❌ Отключить уведомления", callback_data=f"admin_toggle_notify:{uid}:{gid}:{aid}")],
        [InlineKeyboardButton("🗑 Удалить адрес", callback_data=f"admin_del_addr:{uid}:{gid}:{aid}")],
        [InlineKeyboardButton("⬅ Назад", callback_data=f"admin_user:{uid}")]
    ]
    return InlineKeyboardMarkup(kb)
