// 知识库问答页面脚本
// 只做三件事：攒历史、调 /kb_chat/api/ask/、把回答和引用渲染出来

document.addEventListener('DOMContentLoaded', function () {
    const messagesEl = document.getElementById('chat-messages');
    const emptyEl = document.getElementById('chat-empty');
    const inputEl = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const clearBtn = document.getElementById('clear-btn');
    const providerEl = document.getElementById('llm-provider');
    const statsEl = document.getElementById('kb-stats');

    // 发给后端的历史（不含当前这条问题）
    const history = [];
    let sending = false;

    const csrfToken = document.querySelector('#csrf-holder input[name=csrfmiddlewaretoken]').value;

    // ---------- 工具函数 ----------
    function escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // 先转义再加标记，保证模型输出里的 HTML 不会被当标签执行
    function formatAnswer(text) {
        let html = escapeHtml(text);
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\[(\d+)\]/g, '<span class="cite">[$1]</span>');
        return html;
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function hideEmpty() {
        if (emptyEl) emptyEl.style.display = 'none';
    }

    function showEmpty() {
        if (emptyEl) emptyEl.style.display = '';
    }

    function autoGrow() {
        inputEl.style.height = 'auto';
        inputEl.style.height = Math.min(inputEl.scrollHeight, 132) + 'px';
    }

    // ---------- 渲染 ----------
    function appendMessage(role, content, sources) {
        hideEmpty();

        const wrap = document.createElement('div');
        wrap.className = 'msg ' + role;

        const avatar = document.createElement('div');
        avatar.className = 'msg-avatar';
        avatar.textContent = role === 'user' ? '我' : 'AI';

        const body = document.createElement('div');
        body.className = 'msg-body';

        const bubble = document.createElement('div');
        bubble.className = 'bubble ' + (role === 'assistant' ? '' : '');
        bubble.innerHTML = formatAnswer(content);
        body.appendChild(bubble);

        if (role === 'assistant' && sources && sources.length) {
            body.appendChild(buildSources(sources));
        }

        wrap.appendChild(avatar);
        wrap.appendChild(body);
        messagesEl.appendChild(wrap);
        scrollToBottom();
        return bubble;
    }

    function buildSources(sources) {
        const details = document.createElement('details');
        details.className = 'answer-sources';

        const summary = document.createElement('summary');
        summary.textContent = '引用片段（' + sources.length + ' 条）';
        details.appendChild(summary);

        const list = document.createElement('ol');
        sources.forEach(function (src) {
            const li = document.createElement('li');
            const name = document.createElement('span');
            name.className = 'src-name';
            name.textContent = src.source || '未知来源';

            const score = document.createElement('span');
            score.className = 'src-score';
            score.textContent = '相似度 ' + src.score;

            const snippet = document.createElement('span');
            snippet.className = 'src-snippet';
            snippet.textContent = src.snippet || '';

            li.appendChild(name);
            li.appendChild(score);
            li.appendChild(snippet);
            list.appendChild(li);
        });
        details.appendChild(list);
        return details;
    }

    function appendError(message) {
        hideEmpty();
        const wrap = document.createElement('div');
        wrap.className = 'msg assistant';
        wrap.innerHTML =
            '<div class="msg-avatar">AI</div>' +
            '<div class="msg-body"><div class="bubble error">' + escapeHtml(message) + '</div></div>';
        messagesEl.appendChild(wrap);
        scrollToBottom();
    }

    function appendTyping() {
        hideEmpty();
        const wrap = document.createElement('div');
        wrap.className = 'msg assistant';
        wrap.id = 'typing-msg';
        wrap.innerHTML =
            '<div class="msg-avatar">AI</div>' +
            '<div class="msg-body"><div class="bubble">' +
            '<span class="typing"><span></span><span></span><span></span></span>' +
            '</div></div>';
        messagesEl.appendChild(wrap);
        scrollToBottom();
    }

    function removeTyping() {
        const typing = document.getElementById('typing-msg');
        if (typing) typing.remove();
    }

    // ---------- 交互 ----------
    async function sendQuestion() {
        if (sending) return;
        const question = inputEl.value.trim();
        if (!question) {
            inputEl.focus();
            return;
        }

        sending = true;
        sendBtn.disabled = true;
        sendBtn.textContent = '思考中';

        appendMessage('user', question);
        inputEl.value = '';
        autoGrow();
        appendTyping();

        // 传给后端的是「这条问题之前」的历史，当前问题由表单单独提交
        const payload = {
            question: question,
            history: history.slice(-6),
            llm_provider: providerEl ? providerEl.value : ''
        };

        try {
            const resp = await fetch('/kb_chat/api/ask/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            removeTyping();

            if (!data.success) {
                appendError(data.message || '请求失败，请稍后重试');
            } else {
                appendMessage('assistant', data.answer || '（模型没有返回内容）', data.sources);
                history.push({ role: 'user', content: question });
                history.push({ role: 'assistant', content: data.answer || '' });
            }
        } catch (err) {
            removeTyping();
            appendError('请求出错：' + err.message);
        } finally {
            sending = false;
            sendBtn.disabled = false;
            sendBtn.textContent = '发送';
            inputEl.focus();
        }
    }

    async function loadStats() {
        try {
            const resp = await fetch('/kb_chat/api/stats/');
            const data = await resp.json();
            if (!data.success) {
                statsEl.textContent = '知识库状态读取失败';
                statsEl.classList.add('warn');
                return;
            }
            if (!data.total_chunks) {
                statsEl.textContent = '知识库为空，先在「项目文档上传到知识库」上传文档后再提问';
                statsEl.classList.add('warn');
            } else {
                statsEl.textContent = '已接入 ' + data.total_documents + ' 个文档 · ' + data.total_chunks + ' 个向量片段';
                statsEl.classList.remove('warn');
            }
        } catch (err) {
            statsEl.textContent = '知识库状态读取失败';
            statsEl.classList.add('warn');
        }
    }

    sendBtn.addEventListener('click', sendQuestion);

    inputEl.addEventListener('input', autoGrow);
    inputEl.addEventListener('keydown', function (e) {
        // Enter 发送；Shift+Enter 与输入法组合状态下的 Enter 都放行给换行
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
            e.preventDefault();
            sendQuestion();
        }
    });

    clearBtn.addEventListener('click', function () {
        history.length = 0;
        messagesEl.querySelectorAll('.msg').forEach(function (node) { node.remove(); });
        showEmpty();
        inputEl.value = '';
        autoGrow();
        inputEl.focus();
    });

    document.querySelectorAll('.suggest-chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            inputEl.value = chip.textContent.trim();
            autoGrow();
            sendQuestion();
        });
    });

    loadStats();
    inputEl.focus();
});
