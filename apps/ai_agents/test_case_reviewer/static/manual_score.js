/* =========================================================================
 * 人工评审打分（评审列表页 / 复核页共用）
 *
 * 用例是 AI 生成的，AI 又自评了一遍，容易「自说自话」。留一栏人工打分做交叉
 * 验证：人工分和 AI 分差值超过阈值（后端 SCORE_GAP_THRESHOLD，默认 3 分），
 * 说明这条用例的评审结论不可信，整行加下划线并标「无效」。
 *
 * 抽成独立文件是因为两个页面都要用；各写一份的话，改了阈值展示或校验规则
 * 很容易只改一处。
 * ========================================================================= */
(function () {
    const API = '/test_case_reviewer/api/manual-score/';

    function csrfToken() {
        const el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el && el.value) return el.value;
        const m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? m[1] : '';
    }

    function notify(msg, type) {
        if (typeof showNotification === 'function') {
            showNotification(msg, type || 'info');
        } else {
            console.log('[manual-score]', msg);
        }
    }

    function applyInvalid(input, isInvalid, gap) {
        const cell = input.parentElement;
        const row = input.closest('tr');
        if (row) {
            row.classList.toggle('row-invalid', !!isInvalid);
            // 复核页有独立的「差值」列，跟着一起变
            const gapCell = row.querySelector('.gap-cell');
            if (gapCell) gapCell.textContent = (gap === null || gap === undefined) ? '—' : gap;
        }

        let badge = cell.querySelector('.invalid-badge');
        if (isInvalid) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'invalid-badge';
                cell.appendChild(badge);
            }
            badge.textContent = '无效';
            badge.title = (gap !== null && gap !== undefined)
                ? `人工与 AI 评分相差 ${gap} 分，该用例评审结论不可信`
                : '人工与 AI 评分差距过大';
        } else if (badge) {
            badge.remove();
        }
    }

    async function save(input) {
        const id = input.dataset.id;
        const raw = (input.value || '').trim();

        if (raw !== '') {
            const val = Number(raw);
            if (Number.isNaN(val) || val < 0 || val > 10) {
                notify('人工评分需在 0~10 之间', 'error');
                input.value = input.dataset.prev || '';
                return;
            }
        }

        input.classList.add('saving');
        input.disabled = true;
        try {
            const resp = await fetch(API, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
                body: JSON.stringify({
                    test_case_id: id,
                    score: raw === '' ? null : Number(raw)
                })
            });
            const data = await resp.json();
            if (!data.success) throw new Error(data.message || '保存失败');

            input.dataset.prev = input.value;
            applyInvalid(input, data.is_invalid, data.gap);
            input.classList.add('saved');
            setTimeout(() => input.classList.remove('saved'), 900);
        } catch (err) {
            notify('人工评分保存失败：' + err.message, 'error');
        } finally {
            input.classList.remove('saving');
            input.disabled = false;
        }
    }

    function bind(scope) {
        (scope || document).querySelectorAll('.manual-score-input').forEach(input => {
            if (input.dataset.bound === '1') return;   // 防止重复绑定后一发两次请求
            input.dataset.bound = '1';
            input.dataset.prev = input.value;
            // 用 change（失焦且值变了才触发）而不是 input，避免每敲一个数字打一次接口
            input.addEventListener('change', () => save(input));
            input.addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    input.blur();
                }
            });
        });
    }

    document.addEventListener('DOMContentLoaded', () => bind(document));

    // 复核页动态渲染行时用得上
    window.bindManualScoreInputs = bind;
})();
