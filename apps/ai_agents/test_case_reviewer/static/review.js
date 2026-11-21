// 测试用例评审页面专用脚本

// 获取CSRF Token的辅助函数
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// 等待页面加载完成
document.addEventListener('DOMContentLoaded', function() {
    // 获取所有评审按钮
    const reviewButtons = document.querySelectorAll('.review-button');
    
    // 获取 CSRF Token
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;
    
    // 为每个评审按钮添加点击事件监听器
    reviewButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault(); // 阻止默认行为
            
            // 获取测试用例ID
            const testCaseId = this.getAttribute('data-id');
            
            // 验证测试用例ID是否存在
            if (!testCaseId) {
                alert('错误：未找到测试用例ID');
                return;
            }
            
            // 直接打开新窗口，进入详细评审页面
            window.open(`/test_case_reviewer/case-review-detail/?id=${testCaseId}`, 'TestCaseReview', 
                'width=800,height=600,scrollbars=yes,resizable=yes');
        });
    });
    
    // 获取所有状态更新按钮
    const statusButtons = document.querySelectorAll('.status-button');
    
    // 为每个状态更新按钮添加点击事件
    statusButtons.forEach(button => {
        button.addEventListener('click', function() {
            const testCaseId = this.getAttribute('data-id');
            const status = this.getAttribute('data-status');
            const commentsElement = document.getElementById(`review-comments-${testCaseId}`);
            const comments = commentsElement ? commentsElement.value.trim() : '';
            
            if (status === 'rejected' && !comments) {
                showNotification('拒绝测试用例时必须提供评审意见', 'error');
                return;
            }
            
            // 禁用按钮
            this.disabled = true;
            
            // 发送请求到后端
            fetch('/api/update-status/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify({
                    test_case_id: testCaseId,
                    status: status,
                    comments: comments
                })
            })
            .then(response => response.json())
            .then(data => {
                this.disabled = false;
                
                if (data.success) {
                    showNotification('测试用例状态已更新', 'success');
                    
                    // 更新UI
                    const testCaseItem = document.getElementById(`test-case-${testCaseId}`);
                    if (testCaseItem) {
                        // 移除旧的状态类
                        testCaseItem.classList.remove('pending', 'approved', 'rejected');
                        
                        // 添加新的状态类
                        testCaseItem.classList.add(status);
                        
                        // 更新状态标签
                        const statusBadge = testCaseItem.querySelector('.status-badge');
                        if (statusBadge) {
                            statusBadge.textContent = status === 'approved' ? '评审通过' : '评审未通过';
                            statusBadge.classList.remove('badge-warning', 'badge-success', 'badge-danger');
                            statusBadge.classList.add(
                                status === 'approved' ? 'badge-success' : 'badge-danger'
                            );
                        }
                        
                        // 如果在待评审标签页，则移动到相应标签页
                        if (document.querySelector('#pending-tab.active')) {
                            setTimeout(() => {
                                testCaseItem.remove();
                                
                                // 检查是否还有待评审的测试用例
                                const pendingItems = document.querySelectorAll('#pending .test-case-item');
                                if (pendingItems.length === 0) {
                                    document.querySelector('#pending').innerHTML = 
                                        '<div class="alert alert-info">没有待评审的测试用例</div>';
                                }
                                
                                // 更新计数
                                updateTabCounts();
                            }, 500);
                        }
                    }
                } else {
                    showNotification(data.message || '更新测试用例状态失败', 'error');
                }
            })
            .catch(error => {
                this.disabled = false;
                showNotification('请求失败: ' + error.message, 'error');
            });
        });
    });
    
    // 显示通知
    function showNotification(message, type = 'info') {
        // 如果页面上有通知容器，使用它
        let container = document.getElementById('notification-container');
        
        // 如果没有，创建一个
        if (!container) {
            container = document.createElement('div');
            container.id = 'notification-container';
            container.style.position = 'fixed';
            container.style.top = '20px';
            container.style.right = '20px';
            container.style.zIndex = '9999';
            document.body.appendChild(container);
        }
        
        // 创建通知元素
        const notification = document.createElement('div');
        notification.className = `alert alert-${type} alert-dismissible fade show`;
        notification.innerHTML = `
            ${message}
            <button type="button" class="close" data-dismiss="alert" aria-label="Close">
                <span aria-hidden="true">&times;</span>
            </button>
        `;
        
        // 添加到容器
        container.appendChild(notification);
        
        // 设置自动关闭
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    }
    
    // 更新标签页计数
    function updateTabCounts() {
        const pendingCount = document.querySelectorAll('#pending .test-case-item').length;
        const approvedCount = document.querySelectorAll('#approved .test-case-item').length;
        const rejectedCount = document.querySelectorAll('#rejected .test-case-item').length;
        
        document.querySelector('#pending-tab .badge').textContent = pendingCount;
        document.querySelector('#approved-tab .badge').textContent = approvedCount;
        document.querySelector('#rejected-tab .badge').textContent = rejectedCount;
    }

    // 添加分页链接的点击事件处理
    document.querySelectorAll('.pagination .page-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const pageType = this.getAttribute('data-page-type');
            const url = new URL(this.href);
            const page = url.searchParams.get(`${pageType}_page`);
            
            // 保持当前标签页的状态
            const currentTab = document.querySelector('.nav-tabs .nav-link.active');
            if (currentTab) {
                url.searchParams.set('active_tab', currentTab.getAttribute('href').substring(1));
            }
            
            // 跳转到新页面
            window.location.href = url.toString();
        });
    });
});

/* =========================================================================
 * 批量 AI 评审
 *
 * 单个评审是「点一行 → 开弹窗 → 等一次」，几十条时不可用。
 * 这里把「勾选的 N 条」或「该状态全部（跨分页）」提交成一个后台任务，
 * 前端轮询进度，完成后给出汇总。
 *
 * 任务跑在服务端内存里，刷新页面会丢掉 task_id，所以用 sessionStorage 记住，
 * 重进页面时自动接上进度——34 条要跑一分多钟，用户很可能中途刷新。
 * ========================================================================= */
document.addEventListener('DOMContentLoaded', function () {
    const panel = document.getElementById('batch-review-panel');
    if (!panel) return;

    const STORAGE_KEY = 'tb_batch_review_task';
    const POLL_INTERVAL = 1500;          // 轮询间隔：太密打服务端，太疏进度条卡顿
    const LOW_SCORE_WARN = '低于 6 分的用例会在汇总里单独列出';

    const barEl = document.getElementById('batch-bar');
    const titleEl = document.getElementById('batch-title');
    const counterEl = document.getElementById('batch-counter');
    const messageEl = document.getElementById('batch-message');
    const summaryEl = document.getElementById('batch-summary');

    let pollTimer = null;
    let running = false;

    function csrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el && el.value) return el.value;
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : '';
    }

    function notify(msg, type) {
        if (typeof showNotification === 'function') {
            showNotification(msg, type || 'info');
        } else {
            console.log('[batch-review]', msg);
        }
    }

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ---------- 按钮状态跟随勾选数 ----------
    const selectedButtons = [
        { btn: document.getElementById('batch-review-selected'), checkbox: '.pending-case-checkbox' },
        { btn: document.getElementById('batch-review-selected-rejected'), checkbox: '.rejected-case-checkbox' },
    ].filter(x => x.btn);

    function selectedIds(selector) {
        return Array.from(document.querySelectorAll(selector + ':checked'))
            .map(cb => cb.getAttribute('data-id'))
            .filter(Boolean);
    }

    function refreshSelectedButtons() {
        selectedButtons.forEach(({ btn, checkbox }) => {
            const count = selectedIds(checkbox).length;
            if (!btn.dataset.baseLabel) {
                btn.dataset.baseLabel = btn.textContent.trim();
            }
            btn.textContent = count > 0
                ? `${btn.dataset.baseLabel}（${count} 条）`
                : btn.dataset.baseLabel;
            btn.classList.toggle('btn-primary', count > 0);
            btn.classList.toggle('btn-outline-primary', count === 0);
        });
    }

    document.addEventListener('change', function (e) {
        if (e.target.matches('.pending-case-checkbox, .rejected-case-checkbox, #select-all-pending, #select-all-rejected')) {
            // 全选框的联动由原有逻辑处理，这里只刷新按钮文案
            setTimeout(refreshSelectedButtons, 0);
        }
    });
    refreshSelectedButtons();

    // ---------- 进度渲染 ----------
    function setProgressView(data) {
        const extra = data.extra || {};
        const total = extra.total || 0;
        const done = extra.done || 0;
        const pct = typeof data.percentage === 'number' ? data.percentage : (total ? done / total * 100 : 0);

        barEl.style.width = Math.min(100, Math.max(0, pct)).toFixed(1) + '%';
        barEl.classList.toggle('bg-success', !!extra.finished);
        counterEl.textContent = total ? `${done} / ${total}` : '';
        if (data.message) messageEl.textContent = data.message;

        const finished = !!extra.finished;
        titleEl.textContent = finished ? '批量 AI 评审完成' : '批量 AI 评审进行中…';

        // 未完成时只显示一行最新的动态，完成后再给完整汇总
        if (!finished) {
            summaryEl.innerHTML = '';
            return;
        }

        const bits = [];
        // 「通过/不通过」是评审结论，和「调用是否成功」是两码事，分开呈现
        bits.push(`<span class="batch-stat ok">通过 ${extra.passed || 0} 条</span>`);
        bits.push(`<span class="batch-stat bad">不通过 ${extra.not_passed || 0} 条</span>`);
        if (extra.no_verdict) {
            bits.push(`<span class="batch-stat">无结论 ${extra.no_verdict} 条</span>`);
        }
        if (typeof extra.pass_rate === 'number') {
            bits.push(`<span class="batch-stat">本次通过率 <strong>${extra.pass_rate}%</strong></span>`);
        }
        if (typeof extra.avg_score === 'number') {
            bits.push(`<span class="batch-stat">平均分 <strong>${extra.avg_score}</strong></span>`);
        }
        if (extra.failed) {
            bits.push(`<span class="batch-stat bad">调用失败 ${extra.failed} 条</span>`);
        }
        if (extra.low_score) {
            bits.push(`<span class="batch-stat bad">低于 6 分 ${extra.low_score} 条</span>`);
        }

        let html = `<div class="batch-stats">${bits.join('')}</div>`;

        if (extra.status_updated) {
            html += `<div class="batch-status-note">
                        已按评审结论更新 <strong>${extra.status_updated}</strong> 条用例状态，
                        首页通过率同步变化。
                     </div>`;
        } else if (extra.auto_update_status === false) {
            html += `<div class="batch-status-note muted">
                        本次未自动更新用例状态（开关已关闭），用例仍留在待评审列表。
                     </div>`;
        }

        if (extra.low_score_cases && extra.low_score_cases.length) {
            const rows = extra.low_score_cases.map(c => `
                <li>
                    <a href="javascript:void(0)" class="batch-low-link" data-id="${c.id}">
                        <span class="ai-score ai-score-low">${c.score}</span>
                        ${escapeHtml(c.description)}
                    </a>
                </li>`).join('');
            html += `<div class="batch-low-title">需要人工再看一眼（${LOW_SCORE_WARN}）：</div><ul class="batch-low-list">${rows}</ul>`;
        }

        if (extra.errors && extra.errors.length) {
            const rows = extra.errors.map(e => `<li>用例 #${e.id}：${escapeHtml(e.message)}</li>`).join('');
            html += `<div class="batch-low-title">失败明细：</div><ul class="batch-low-list batch-error-list">${rows}</ul>`;
        }

        if (extra.fatal) {
            html += `<div class="batch-fatal">任务异常：${escapeHtml(extra.fatal)}</div>`;
        }

        html += `<div class="batch-actions">
                    <button type="button" class="btn btn-sm btn-primary" id="batch-reload">刷新列表查看结果</button>
                    <button type="button" class="btn btn-sm btn-secondary" id="batch-close">收起</button>
                 </div>`;
        summaryEl.innerHTML = html;

        const reloadBtn = document.getElementById('batch-reload');
        if (reloadBtn) reloadBtn.addEventListener('click', () => window.location.reload());
        const closeBtn = document.getElementById('batch-close');
        if (closeBtn) closeBtn.addEventListener('click', () => { panel.style.display = 'none'; });
        summaryEl.querySelectorAll('.batch-low-link').forEach(link => {
            link.addEventListener('click', () => {
                window.open(`/test_case_reviewer/case-review-detail/?id=${link.dataset.id}`,
                    'TestCaseReview', 'width=800,height=600,scrollbars=yes,resizable=yes');
            });
        });
    }

    function showPanel() {
        panel.style.display = 'block';
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
        running = false;
        setButtonsDisabled(false);
    }

    function setButtonsDisabled(disabled) {
        document.querySelectorAll('.batch-toolbar button').forEach(btn => {
            btn.disabled = disabled;
        });
    }

    // ---------- 轮询 ----------
    function poll(taskId) {
        fetch(`/test_case_reviewer/api/batch-review/progress/?task_id=${encodeURIComponent(taskId)}`, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
            .then(resp => resp.json().then(body => ({ ok: resp.ok, body })))
            .then(({ ok, body }) => {
                if (!ok || !body.success) {
                    // 任务丢了（服务重启）：清掉记录，别让页面一直转
                    sessionStorage.removeItem(STORAGE_KEY);
                    stopPolling();
                    messageEl.textContent = body.message || '进度获取失败，任务可能已失效';
                    return;
                }
                setProgressView(body);
                if (body.extra && body.extra.finished) {
                    sessionStorage.removeItem(STORAGE_KEY);
                    stopPolling();
                }
            })
            .catch(err => {
                console.warn('[batch-review] 轮询失败', err);
            });
    }

    function startPolling(taskId) {
        running = true;
        setButtonsDisabled(true);
        showPanel();
        poll(taskId);
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(() => poll(taskId), POLL_INTERVAL);
    }

    // ---------- 提交任务 ----------
    function autoUpdateEnabled() {
        const el = document.getElementById('auto-update-status');
        return el ? el.checked : true;
    }

    function submit(payload, label) {
        if (running) {
            notify('已有评审任务在跑，请等待完成', 'warning');
            return;
        }
        payload.auto_update_status = autoUpdateEnabled();
        showPanel();
        panel.scrollIntoView({ behavior: 'smooth', block: 'center' });
        titleEl.textContent = '正在提交评审任务…';
        counterEl.textContent = '';
        messageEl.textContent = label || '';
        summaryEl.innerHTML = '';
        barEl.style.width = '2%';
        running = true;
        setButtonsDisabled(true);

        fetch('/test_case_reviewer/api/batch-review/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
        })
            .then(resp => resp.json())
            .then(data => {
                if (!data.success) {
                    stopPolling();
                    titleEl.textContent = '提交失败';
                    messageEl.textContent = data.message || '未知错误';
                    notify(data.message || '提交失败', 'error');
                    return;
                }
                sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                    task_id: data.task_id, total: data.total, started_at: Date.now()
                }));
                titleEl.textContent = `批量 AI 评审进行中（${data.scope_label} ${data.total} 条）`;
                startPolling(data.task_id);
            })
            .catch(err => {
                stopPolling();
                titleEl.textContent = '提交失败';
                messageEl.textContent = err.message;
                notify('提交失败：' + err.message, 'error');
            });
    }

    document.querySelectorAll('.batch-toolbar button[id^="batch-review-"]').forEach(btn => {
        btn.addEventListener('click', function () {
            const scope = this.dataset.scope;
            const checkboxSelector = this.dataset.checkbox;   // 只有「选中」按钮有
            const total = parseInt(this.dataset.total || '0', 10);

            if (checkboxSelector) {
                const ids = selectedIds(checkboxSelector);
                if (!ids.length) {
                    notify('请先勾选要评审的用例，或用旁边的「全部评审」', 'warning');
                    return;
                }
                submit({ ids }, `即将评审 ${ids.length} 条勾选用例`);
                return;
            }

            if (!total) {
                notify('当前没有需要评审的用例', 'info');
                return;
            }
            const willUpdate = autoUpdateEnabled()
                ? '，评审结论会自动更新用例状态（首页通过率同步变化）'
                : '，评审结论不会改变用例状态';
            const estimate = Math.ceil(total / 4 * 8);
            if (!confirm(`将对该状态下全部 ${total} 条用例发起 AI 评审，预计耗时约 ${estimate} 秒${willUpdate}。确认继续？`)) {
                return;
            }
            submit({ scope }, `即将评审全部 ${total} 条`);
        });
    });

    // ---------- 刷新页面后接上正在跑的任务 ----------
    (function resume() {
        let saved = null;
        try {
            saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null');
        } catch (e) { /* 忽略脏数据 */ }
        if (!saved || !saved.task_id) return;
        // 超过 10 分钟的记录视为失效（服务重启后内存进度早没了）
        if (Date.now() - (saved.started_at || 0) > 10 * 60 * 1000) {
            sessionStorage.removeItem(STORAGE_KEY);
            return;
        }
        startPolling(saved.task_id);
    })();
});
