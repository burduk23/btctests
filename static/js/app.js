        const webApp = window.Telegram.WebApp;
        webApp.ready();
        webApp.expand();

        function setTheme(theme) {
            const root = document.documentElement;
            if (theme === 'default') {
                root.removeAttribute('data-theme');
                localStorage.removeItem('appTheme');
            } else {
                root.setAttribute('data-theme', theme);
                localStorage.setItem('appTheme', theme);
            }
        }

        // Initialize theme
        const savedTheme = localStorage.getItem('appTheme');
        if (savedTheme) {
            setTheme(savedTheme);
        }

        let stateData = null;
        let currentChatUid = null; // null for user communicating with admin, or UID for admin
        let pollingInterval = null;

        function showToast(msg, isError = false) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = 'toast ' + (isError ? 'error' : 'success');
            toast.textContent = msg;
            container.appendChild(toast);
            setTimeout(() => { if(container.contains(toast)) container.removeChild(toast); }, 3000);
        }

        async function apiCall(action, payload = {}) {
            try {
                const res = await fetch('/api/action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ initData: webApp.initData, action, payload })
                });
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                await loadData(false); // Silent reload
                return true;
            } catch (e) {
                showToast(e.message, true);
                return false;
            }
        }

        async function loadData(showError = true) {
            try {
                const res = await fetch('/api/get', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ initData: webApp.initData })
                });
                stateData = await res.json();
                
                if (stateData.is_admin) {
                    document.getElementById('tab-admin').classList.remove('hidden');
                    document.getElementById('chat-list-container').classList.remove('hidden');
                } else {
                    // Normal user directly sees conversation
                    document.getElementById('chat-conversation').classList.remove('hidden');
                    document.getElementById('chat-back-btn').classList.add('hidden'); // No back button for user
                    document.getElementById('chat-header-avatar').innerText = 'А';
                    document.getElementById('chat-header-avatar').style.background = getAvatarColor('admin');
                    document.getElementById('chat-header-name').innerText = 'Администратор';
                    document.getElementById('chat-header-status').innerText = 'Служба поддержки';
                }
                
                renderUserView();
                
                if (stateData.is_admin) {
                    renderAdminView();
                    renderAdminChatList();
                    if (currentChatUid) {
                        refreshConversation();
                    }
                } else {
                    refreshConversation();
                }
            } catch (e) {
                if(showError) showToast('Ошибка загрузки данных', true);
            }
        }

        // --- Utility ---
        function getAvatarColor(id) {
            const colors = ['#e56555', '#f28c48', '#8e85ee', '#549cdd', '#4cb382', '#56b3f5', '#6dc534'];
            let hash = 0;
            for(let i=0; i<id.length; i++) hash = id.charCodeAt(i) + ((hash << 5) - hash);
            return colors[Math.abs(hash) % colors.length];
        }
        
        function getInitials(name) {
            if(!name) return '?';
            const words = name.trim().split(/\s+/);
            if(words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
            return name.substring(0, 2).toUpperCase();
        }

        function formatTime(ts) {
            const d = new Date(ts * 1000);
            return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
        }
        
        function formatDate(ts) {
            const d = new Date(ts * 1000);
            const today = new Date();
            if (d.toDateString() === today.toDateString()) return formatTime(ts);
            return `${d.getDate().toString().padStart(2, '0')}.${(d.getMonth()+1).toString().padStart(2, '0')}`;
        }

        // --- Render Addresses ---
        function renderUserView() {
            const list = document.getElementById('user-list');
            list.innerHTML = '';
            const groups = stateData.user_data.groups || [];
            
            if (groups.length === 0) {
                list.innerHTML = '<div class="empty-state">У вас пока нет добавленных адресов.<br>Добавьте первый ниже 👇</div>';
                return;
            }

            groups.forEach(g => {
                const gDiv = document.createElement('div');
                gDiv.className = 'group';
                gDiv.innerHTML = `<div class="group-header"><strong>${g.name}</strong> <button onclick="apiCall('delete_group', {gid: '${g.id}'})" class="red btn-small">Удалить</button></div>`;
                (g.addresses || []).forEach(a => {
                    const aDiv = document.createElement('div');
                    aDiv.className = 'address';
                    aDiv.innerHTML = `<div class="address-info"><span class="address-text">${a.addr}</span></div><button onclick="apiCall('delete_address', {gid: '${g.id}', aid: '${a.id}'})" class="red btn-small" style="font-size:16px; padding:6px 10px;">🗑</button>`;
                    gDiv.appendChild(aDiv);
                });
                list.appendChild(gDiv);
            });
        }

        // --- Chat Functions ---
        function renderAdminChatList() {
            const container = document.getElementById('chat-list-container');
            container.innerHTML = '';
            const users = stateData.all_users || {};
            
            // Sort users by last message time
            const chatList = [];
            for (const [uid, udata] of Object.entries(users)) {
                const name = udata.first_name || udata.username || `User ${uid}`;
                const msgs = udata.messages || [];
                const lastMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
                const lastTs = lastMsg ? lastMsg.ts : 0;
                chatList.push({ uid, name, username: udata.username, msgs, lastMsg, lastTs });
            }
            
            chatList.sort((a, b) => b.lastTs - a.lastTs);
            
            if(chatList.length === 0) {
                container.innerHTML = '<div class="empty-state" style="margin:20px;">Нет доступных чатов.</div>';
                return;
            }

            chatList.forEach(chat => {
                const item = document.createElement('div');
                item.className = 'chat-item';
                item.onclick = () => openChat(chat.uid);
                
                const initials = getInitials(chat.name);
                const color = getAvatarColor(chat.uid);
                const timeStr = chat.lastTs ? formatDate(chat.lastTs) : '';
                const lastText = chat.lastMsg ? (chat.lastMsg.from === 'admin' ? 'Вы: ' : '') + chat.lastMsg.text : 'Нет сообщений';
                
                item.innerHTML = `
                    <div class="avatar" style="background: ${color};">${initials}</div>
                    <div class="chat-item-info">
                        <div class="chat-item-header">
                            <div class="chat-item-name">${chat.name}</div>
                            <div class="chat-item-time">${timeStr}</div>
                        </div>
                        <div class="chat-item-last">${lastText}</div>
                    </div>
                `;
                container.appendChild(item);
            });
        }

        function openChat(uid) {
            currentChatUid = uid;
            const user = stateData.all_users[uid];
            const name = user.first_name || user.username || `User ${uid}`;
            const username = user.username ? `@${user.username}` : `ID: ${uid}`;
            
            document.getElementById('chat-header-avatar').innerText = getInitials(name);
            document.getElementById('chat-header-avatar').style.background = getAvatarColor(uid);
            document.getElementById('chat-header-name').innerText = name;
            document.getElementById('chat-header-status').innerText = username;
            
            document.getElementById('chat-conversation').classList.remove('hidden');
            document.getElementById('tabs-container').classList.add('hidden'); // Hide tabs in chat
            
            refreshConversation();
        }

        function closeChat() {
            currentChatUid = null;
            document.getElementById('chat-conversation').classList.add('hidden');
            document.getElementById('tabs-container').classList.remove('hidden'); // Show tabs back
            renderAdminChatList(); // Refresh list to update read statuses/last msgs
        }

        function refreshConversation() {
            let messages = [];
            let myRole = '';
            
            if (stateData.is_admin) {
                if (!currentChatUid) return;
                const user = stateData.all_users[currentChatUid];
                if(user) messages = user.messages || [];
                myRole = 'admin';
            } else {
                messages = stateData.user_data.messages || [];
                myRole = 'user';
            }
            
            const container = document.getElementById('chat-messages');
            
            // Check if we need to auto-scroll (only if we were already at bottom or if it's first render)
            const isAtBottom = container.scrollHeight - container.scrollTop <= container.clientHeight + 50;
            
            container.innerHTML = '';
            if(messages.length === 0) {
                container.innerHTML = '<div style="text-align:center; color:var(--hint-color); margin-top:auto; margin-bottom:auto;">Здесь будет история сообщений.</div>';
            } else {
                let lastDate = '';
                messages.forEach(m => {
                    const d = new Date(m.ts * 1000).toDateString();
                    if(d !== lastDate) {
                        const dateEl = document.createElement('div');
                        dateEl.style.cssText = 'text-align:center; font-size:12px; color:white; background:rgba(0,0,0,0.2); padding:2px 8px; border-radius:10px; align-self:center; margin:8px 0;';
                        dateEl.innerText = new Date(m.ts * 1000).toLocaleDateString('ru-RU', {day:'numeric', month:'short'});
                        container.appendChild(dateEl);
                        lastDate = d;
                    }
                    
                    const isMe = m.from === myRole;
                    const b = document.createElement('div');
                    b.className = `msg-bubble ${isMe ? 'me' : 'them'}`;
                    b.innerHTML = `${m.text}<span class="msg-time">${formatTime(m.ts)}</span>`;
                    container.appendChild(b);
                });
            }
            
            if (isAtBottom || messages.length > 0) {
                container.scrollTop = container.scrollHeight;
            }
        }

        function handleChatKeyPress(e) {
            if (e.key === 'Enter') {
                sendChatMessage();
            }
        }

        async function sendChatMessage() {
            const input = document.getElementById('chat-input');
            const text = input.value.trim();
            if(!text) return;
            
            input.value = '';
            
            // Optimistic render
            const container = document.getElementById('chat-messages');
            const b = document.createElement('div');
            b.className = `msg-bubble me`;
            b.innerHTML = `${text}<span class="msg-time">${formatTime(Math.floor(Date.now()/1000))}</span>`;
            
            // Remove empty state if present
            if(container.children.length === 1 && container.children[0].innerText.includes('Здесь будет')) {
                container.innerHTML = '';
            }
            container.appendChild(b);
            container.scrollTop = container.scrollHeight;

            await apiCall('send_chat_message', { text, uid: currentChatUid });
        }

        // --- Admin Functions ---
        function renderAdminView() {
            const list = document.getElementById('admin-list');
            const bcList = document.getElementById('broadcast-users-list');
            list.innerHTML = '';
            bcList.innerHTML = '';
            const users = stateData.all_users || {};
            
            if(Object.keys(users).length === 0) {
                list.innerHTML = '<div class="empty-state">Пользователей не найдено.</div>';
            } else {
                for (const [uid, udata] of Object.entries(users)) {
                    const name = udata.first_name || udata.username || uid;
                    
                    // Add to broadcast list
                    const bcItem = document.createElement('label');
                    bcItem.className = 'user-checkbox';
                    bcItem.innerHTML = `<input type="checkbox" value="${uid}" class="bc-checkbox"> <span>${name} (${uid})</span>`;
                    bcList.appendChild(bcItem);

                    // Add to main user list
                    const uDiv = document.createElement('div');
                    uDiv.className = 'group';
                    
                    let addrsHtml = '';
                    (udata.groups || []).forEach(g => {
                        (g.addresses || []).forEach(a => {
                            const isOff = a.notify_disabled;
                            addrsHtml += `<div class="address"><div class="address-info"><small style="color:var(--button-color); font-weight:bold;">${g.name}</small><span class="address-text">${a.addr}</span></div><div style="display:flex; flex-direction:column; gap:6px;"><button onclick="apiCall('admin_toggle_notify', {uid: '${uid}', gid: '${g.id}', aid: '${a.id}'})" class="btn-small" style="background:${isOff?'#9e9e9e':'#4caf50'};">${isOff?'❌ Выкл':'✅ Вкл'}</button><button onclick="apiCall('admin_delete_address', {uid: '${uid}', gid: '${g.id}', aid: '${a.id}'})" class="red btn-small">🗑 Удал</button></div></div>`;
                        });
                    });

                    uDiv.innerHTML = `
                        <div class="group-header">
                            <div><strong>👤 ${name}</strong> <span style="color:var(--hint-color); font-size:12px; margin-left:8px;">ID: ${uid}</span></div>
                            <button class="outline btn-small" onclick="switchTab('chat'); openChat('${uid}');">💬 Чат</button>
                        </div>
                        ${addrsHtml}
                    `;
                    list.appendChild(uDiv);
                }
            }

            // Manage Admins Section
            const adminMgmtSection = document.getElementById('admin-management-section');
            if (stateData.is_main_admin) {
                adminMgmtSection.classList.remove('hidden');
                const adminsList = document.getElementById('admins-list');
                adminsList.innerHTML = '';
                if (stateData.admins && stateData.admins.length > 0) {
                    for (const adminId of stateData.admins) {
                        adminsList.innerHTML += `<div class="group" style="display: flex; justify-content: space-between; align-items: center;"><div class="address-info"><strong>👤 ${adminId}</strong></div><div><button onclick="adminRemoveAdmin('${adminId}')" class="red btn-small">🗑 Удалить</button></div></div>`;
                    }
                } else {
                    adminsList.innerHTML = '<div class="empty-state">Нет дополнительных администраторов</div>';
                }
            } else {
                if (adminMgmtSection) adminMgmtSection.classList.add('hidden');
            }
        }

        async function adminAddAdmin() {
            const uidInput = document.getElementById('new-admin-uid');
            const uid = uidInput.value.trim();
            if (!uid) { showToast('Введите ID администратора', true); return; }
            
            if (await apiCall('admin_add_admin', { uid })) {
                uidInput.value = '';
                showToast('✅ Администратор добавлен');
                loadData(false);
            }
        }

        async function adminRemoveAdmin(uid) {
            if (confirm(`Удалить администратора ${uid}?`)) {
                if (await apiCall('admin_remove_admin', { uid })) {
                    showToast('✅ Администратор удален');
                    loadData(false);
                }
            }
        }

        function selectAllUsers() {
            document.querySelectorAll('.bc-checkbox').forEach(cb => cb.checked = true);
        }

        async function sendBroadcast() {
            const text = document.getElementById('broadcast-text').value.trim();
            const uids = Array.from(document.querySelectorAll('.bc-checkbox:checked')).map(cb => cb.value);
            
            if(!text) return showToast('Введите текст рассылки!', true);
            if(uids.length === 0) return showToast('Выберите хотя бы одного пользователя!', true);
            
            if (await apiCall('admin_broadcast', { text, uids })) {
                showToast(`✅ Рассылка отправлена (${uids.length} чел.)`);
                document.getElementById('broadcast-text').value = '';
                document.querySelectorAll('.bc-checkbox').forEach(cb => cb.checked = false);
            }
        }

        async function addAddress() {
            const name = document.getElementById('name').value;
            const address = document.getElementById('address').value;
            if(!name || !address) return showToast('Заполните все поля!', true);
            if (await apiCall('add_address', { group_name: name, address })) {
                showToast('✅ Адрес успешно добавлен');
                document.getElementById('address').value = '';
            }
        }

        async function adminAddAddress() {
            const uid = document.getElementById('admin-uid').value;
            const name = document.getElementById('admin-name').value;
            const address = document.getElementById('admin-address').value;
            if(!uid || !name || !address) return showToast('Заполните все поля!', true);
            if (await apiCall('admin_add_address', { uid, group_name: name, address })) {
                showToast('✅ Адрес добавлен пользователю');
                document.getElementById('admin-address').value = '';
            }
        }

        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            
            document.querySelectorAll('.view-section').forEach(v => v.classList.add('hidden'));
            document.getElementById('view-' + tab).classList.remove('hidden');
            
            // If switching to chat and admin, ensure we show list if no active chat
            if(tab === 'chat' && stateData && stateData.is_admin && !currentChatUid) {
                document.getElementById('chat-list-container').classList.remove('hidden');
                document.getElementById('chat-conversation').classList.add('hidden');
            }
        }

        loadData();
        
        // Auto refresh chat (polling)
        pollingInterval = setInterval(() => {
            if (document.visibilityState === 'visible') {
                loadData(false);
            }
        }, 5000);