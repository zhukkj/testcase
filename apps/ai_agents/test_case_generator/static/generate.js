// 测试用例生成页面专用脚本

document.addEventListener('DOMContentLoaded', function() {
    const generateForm = document.getElementById('generate-form');
    const inputTypeRadios = document.querySelectorAll('input[name="input_type"]');
    const inputTextLabel = document.getElementById('input-text-label');
    const inputText = document.getElementById('input-text');
    const generateButton = document.getElementById('generate-button');
    const loadingIndicator = document.getElementById('loading-indicator');
    const resultContainer = document.getElementById('result-container');
    const saveButton = document.getElementById('save-button');
    
    // 根据输入类型更改标签文本
    if (inputTypeRadios && inputTypeRadios.length > 0) {
        inputTypeRadios.forEach(radio => {
            radio.addEventListener('change', function() {
                if (this.value === 'requirement') {
                    inputTextLabel.textContent = '需求描述:';
                    inputText.placeholder = '请输入需求描述...';
                } else {
                    inputTextLabel.textContent = '代码片段:';
                    inputText.placeholder = '请输入代码片段...';
                }
            });
        });
    }

    // ===== 测试点模式：接收 PRD 分析页带来的测试点，并支持编辑 =====
    let pointModePoints = [];      // 当前（可能已被用户编辑过的）测试点
    let originalPointPayload = null; // 原始快照，用于「重置」
    let editingIndex = -1;         // 正在编辑的行下标，-1 表示无

    const pointPanel = document.getElementById('point-mode-panel');
    const pointCountEl = document.getElementById('point-count');
    const pointListEl = document.getElementById('point-list');
    const countPerPointEl = document.getElementById('count-per-point');
    const estimateEl = document.getElementById('estimate-total');

    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));

    function loadPointMode() {
        let payload = null;
        try {
            payload = JSON.parse(sessionStorage.getItem('prd_test_points') || 'null');
        } catch (e) {
            console.warn('测试点数据解析失败', e);
        }
        if (!payload || !payload.test_points || !payload.test_points.length) {
            return;
        }
        originalPointPayload = JSON.parse(JSON.stringify(payload));
        pointModePoints = payload.test_points;
        pointPanel.style.display = 'block';
        pointCountEl.textContent = pointModePoints.length;
        inputTextLabel.textContent = '整体需求背景（可选，用于补充上下文）:';
        inputText.placeholder = '可以不填。填写后作为整体背景传给模型，但每个测试点仍会单独生成。';
        // textarea 带了 required，测试点模式下允许留空，否则浏览器会拦掉提交
        inputText.removeAttribute('required');
        renderPointList();
        updateEstimate();
    }

    // 把当前编辑结果写回 sessionStorage，刷新页面不丢改动
    function savePointState() {
        sessionStorage.setItem('prd_test_points', JSON.stringify({
            test_points: pointModePoints,
            summary: (originalPointPayload && originalPointPayload.summary) || {},
            edited: true,
            created_at: new Date().toISOString()
        }));
        pointCountEl.textContent = pointModePoints.length;
    }

    function renderPointList() {
        if (!pointModePoints.length) {
            pointListEl.innerHTML = '<div class="text-muted py-2">测试点已全部删除，请「重置」或直接切换回需求描述模式。</div>';
            return;
        }

        pointListEl.innerHTML = pointModePoints.map((p, i) => {
            if (i === editingIndex) {
                return renderEditRow(p, i);
            }
            const scenarios = p.scenarios || [];
            const chips = scenarios.length
                ? scenarios.map(s => `<span class="scenario-chip" title="${esc(s.description || '')}">
                        ${esc(s.id || '')} ${esc(s.title || '')}
                    </span>`).join('')
                : '<span class="text-muted">无细化场景（生成时由模型自行补齐）</span>';

            return `
                <div class="point-item" data-idx="${i}">
                    <div class="point-head">
                        <code class="point-id">${esc(p.id || '')}</code>
                        <span class="point-title">${esc(p.title || '')}</span>
                        <span class="point-meta">${scenarios.length} 个场景</span>
                        <select class="point-priority" data-idx="${i}" title="优先级">
                            ${['高', '中', '低'].map(v =>
                                `<option value="${v}" ${p.priority === v ? 'selected' : ''}>${v}</option>`).join('')}
                        </select>
                        <span class="point-actions">
                            <button type="button" class="btn btn-xs btn-outline-secondary" data-act="edit" data-idx="${i}">编辑</button>
                            <button type="button" class="btn btn-xs btn-outline-secondary" data-act="merge" data-idx="${i}"
                                    ${i === 0 ? 'disabled title="第一条没有上一条可合并"' : 'title="把本条的场景并入上一条" '}>并入上条</button>
                            <button type="button" class="btn btn-xs btn-outline-danger" data-act="del" data-idx="${i}">删除</button>
                        </span>
                    </div>
                    <div class="point-scenarios">${chips}</div>
                </div>`;
        }).join('');
    }

    function renderEditRow(p, i) {
        const scenarios = p.scenarios || [];
        const rows = scenarios.map((s, si) => `
            <div class="d-flex align-items-center mb-1">
                <code class="mr-2" style="white-space:nowrap;">${esc(s.id || '')}</code>
                <input class="form-control form-control-sm mr-2" data-field="scenario-title" data-sidx="${si}"
                       value="${esc(s.title || '')}" placeholder="场景标题">
                <button type="button" class="btn btn-xs btn-outline-danger" data-act="del-scenario"
                        data-idx="${i}" data-sidx="${si}">删除</button>
            </div>`).join('');

        return `
            <div class="point-item editing" data-idx="${i}">
                <div class="point-head">
                    <code class="point-id">${esc(p.id || '')}</code>
                    <input class="form-control form-control-sm" data-field="title" data-idx="${i}"
                           value="${esc(p.title || '')}" placeholder="测试点标题">
                </div>
                <div class="mt-2">
                    <textarea class="form-control form-control-sm" rows="2" data-field="description" data-idx="${i}"
                              placeholder="测试点描述（会作为生成用例的上下文）">${esc(p.description || '')}</textarea>
                </div>
                <div class="mt-2">
                    <div class="small text-muted mb-1">测试场景（删掉不需要的，或新增）</div>
                    ${rows || '<div class="text-muted small mb-1">暂无场景</div>'}
                    <button type="button" class="btn btn-xs btn-outline-secondary" data-act="add-scenario" data-idx="${i}">+ 添加场景</button>
                </div>
                <div class="mt-2 text-right">
                    <button type="button" class="btn btn-sm btn-primary" data-act="save" data-idx="${i}">保存</button>
                    <button type="button" class="btn btn-sm btn-outline-secondary" data-act="cancel" data-idx="${i}">取消</button>
                </div>
            </div>`;
    }

    // 事件委托：行的增删改都走这里
    if (pointListEl) {
        pointListEl.addEventListener('click', function (e) {
            const btn = e.target.closest('button[data-act]');
            if (!btn) return;
            const act = btn.dataset.act;
            const idx = parseInt(btn.dataset.idx, 10);

            if (act === 'edit') {
                editingIndex = idx;
                renderPointList();
            } else if (act === 'del') {
                const p = pointModePoints[idx];
                if (!confirm(`确认删除测试点「${p.title || p.id}」？`)) return;
                pointModePoints.splice(idx, 1);
                editingIndex = -1;
                savePointState();
                renderPointList();
                updateEstimate();
            } else if (act === 'merge') {
                if (idx <= 0) return;
                const prev = pointModePoints[idx - 1];
                const cur = pointModePoints[idx];
                prev.scenarios = (prev.scenarios || []).concat(cur.scenarios || []);
                // 合并后描述也带上，避免丢失信息
                if (cur.description) {
                    prev.description = [prev.description, cur.description].filter(Boolean).join('\n');
                }
                pointModePoints.splice(idx, 1);
                editingIndex = -1;
                savePointState();
                renderPointList();
                updateEstimate();
            } else if (act === 'save') {
                const item = pointListEl.querySelector(`.point-item[data-idx="${idx}"]`);
                if (!item) return;
                const p = pointModePoints[idx];
                const titleInput = item.querySelector('input[data-field="title"]');
                const descInput = item.querySelector('textarea[data-field="description"]');
                p.title = (titleInput?.value || '').trim() || p.title;
                p.description = (descInput?.value || '').trim();

                // 收集场景标题（保持 id / description / test_type 不变）
                (p.scenarios || []).forEach((s, si) => {
                    const input = item.querySelector(`input[data-field="scenario-title"][data-sidx="${si}"]`);
                    if (input) s.title = input.value.trim() || s.title;
                });
                editingIndex = -1;
                savePointState();
                renderPointList();
                updateEstimate();
            } else if (act === 'cancel') {
                editingIndex = -1;
                renderPointList();
            } else if (act === 'add-scenario') {
                const p = pointModePoints[idx];
                p.scenarios = p.scenarios || [];
                const n = p.scenarios.length + 1;
                p.scenarios.push({
                    id: `${p.id || 'TP'}-NEW${n}`,
                    title: '新场景（请填写）',
                    description: '',
                    test_type: '功能测试'
                });
                savePointState();
                renderPointList();
                updateEstimate();
            } else if (act === 'del-scenario') {
                const si = parseInt(btn.dataset.sidx, 10);
                const p = pointModePoints[idx];
                p.scenarios.splice(si, 1);
                savePointState();
                renderPointList();
                updateEstimate();
            }
        });

        // 优先级下拉直接改
        pointListEl.addEventListener('change', function (e) {
            const sel = e.target.closest('select.point-priority');
            if (!sel) return;
            const idx = parseInt(sel.dataset.idx, 10);
            if (pointModePoints[idx]) {
                pointModePoints[idx].priority = sel.value;
                savePointState();
            }
        });
    }

    function updateEstimate() {
        const per = parseInt(countPerPointEl.value, 10) || 1;
        // 与后端一致：每个点实际条数 = max(每点条数, 该点场景数)
        const total = pointModePoints.reduce((sum, p) => {
            const scenarios = (p.scenarios || []).length;
            return sum + Math.max(per, scenarios || 0 || per);
        }, 0);
        estimateEl.textContent = `约 ${total} 条（每点至少 ${per} 条；若该点场景数更多，按场景数生成以保证覆盖）`;
    }

    window.resetPointMode = function () {
        if (!originalPointPayload) return;
        pointModePoints = JSON.parse(JSON.stringify(originalPointPayload.test_points));
        editingIndex = -1;
        savePointState();
        renderPointList();
        updateEstimate();
    };

    window.clearPointMode = function () {
        sessionStorage.removeItem('prd_test_points');
        pointModePoints = [];
        originalPointPayload = null;
        editingIndex = -1;
        pointPanel.style.display = 'none';
        inputTextLabel.textContent = '需求描述:';
        inputText.placeholder = '请输入需求描述...';
        inputText.setAttribute('required', 'required');
    };

    if (countPerPointEl) {
        countPerPointEl.addEventListener('change', updateEstimate);
    }

    loadPointMode();
    
    // 保存用户选择的大模型到本地存储
    const llmProviderSelect = document.getElementById('llm-provider');
    if (llmProviderSelect) {
        llmProviderSelect.addEventListener('change', function() {
            localStorage.setItem('preferred-llm-provider', this.value);
        });
        
        // 页面加载时恢复用户之前的选择（如果后端没有指定值）
        if (!llmProviderSelect.options[llmProviderSelect.selectedIndex].hasAttribute('selected')) {
            const savedProvider = localStorage.getItem('preferred-llm-provider');
            if (savedProvider) {
                // 确保保存的值在当前选项中存在
                for (let i = 0; i < llmProviderSelect.options.length; i++) {
                    if (llmProviderSelect.options[i].value === savedProvider) {
                        llmProviderSelect.value = savedProvider;
                        break;
                    }
                }
            }
        }
    }
    
    // 表单提交时显示加载指示器
    const form = document.querySelector('form');
    if (form) {
        form.addEventListener('submit', function() {
            if (loadingIndicator) {
                loadingIndicator.style.display = 'block';
            }
            if (generateButton) {
                generateButton.disabled = true;
            }
        });
    }
    
    // 提交表单生成测试用例 (原有功能，保留以兼容API调用方式)
    if (generateForm) {
        generateForm.addEventListener('submit', function(e) {
            e.preventDefault();
            console.log('表单提交事件触发');
            
            // 获取必要的 DOM 元素
            const loadingIndicator = document.getElementById('loading-indicator');
            const generateButton = document.getElementById('generate-button');
            const resultContainer = document.getElementById('result-container');
            const inputText = document.getElementById('input-text');
            
            // 获取输入文本
            const inputTextValue = inputText?.value?.trim();
            
            // 获取选择框元素
            const designMethodsSelect = document.getElementById('case_design_methods');
            const caseCategoriesSelect = document.getElementById('case_categories');
            
            if (!designMethodsSelect || !caseCategoriesSelect) {
                console.error('找不到选择框元素');
                return;
            }
            
            // 获取选中的值
            const selectedDesignMethods = Array.from(designMethodsSelect.selectedOptions || []).map(option => option.textContent);
            const selectedCaseCategories = Array.from(caseCategoriesSelect.selectedOptions || []).map(option => option.textContent);
            
            if (!inputTextValue && pointModePoints.length === 0) {
                showNotification('请输入需求描述', 'error');
                return;
            }
            
            // 显示加载指示器和清空结果容器（添加空值检查）
            if (loadingIndicator) {
                loadingIndicator.style.display = 'block';
            }
            if (resultContainer) {
                resultContainer.innerHTML = '';
            }
            if (generateButton) {
                generateButton.disabled = true;
            }
            
            // 构造请求数据
            const requestData = {
                requirements: inputTextValue,
                llm_provider: document.getElementById('llm-provider')?.value || 'deepseek',
                case_design_methods: selectedDesignMethods,
                case_categories: selectedCaseCategories,
                case_count: document.getElementById('case_count')?.value || '10'
            };

            // ===== 测试点模式：走逐点生成接口 =====
            if (pointModePoints.length > 0) {
                const beforePoints = pointModePoints.length;
                const payload = {
                    test_points: pointModePoints,
                    case_count_per_point: parseInt(countPerPointEl?.value || '1', 10),
                    llm_provider: requestData.llm_provider,
                    case_design_methods: selectedDesignMethods,
                    case_categories: selectedCaseCategories,
                    context: inputTextValue || '',
                    auto_review: document.getElementById('auto-review')?.checked || false
                };
                console.log(`测试点模式：${beforePoints} 个测试点逐条生成`, payload);

                fetch('/test_case_generator/generate-by-points/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCookie('csrftoken')
                    },
                    body: JSON.stringify(payload)
                })
                .then(resp => resp.json())
                .then(data => {
                    if (loadingIndicator) loadingIndicator.style.display = 'none';
                    if (generateButton) generateButton.disabled = false;

                    if (!data.success) {
                        alert('生成失败：' + (data.message || '未知错误'));
                        return;
                    }
                    displayTestCases(data.test_cases || [], data.stats);
                    // 保存按钮复用原有逻辑，需要这两个 key
                    sessionStorage.setItem('generatedTestCases', JSON.stringify(data.test_cases || []));
                    sessionStorage.setItem('inputText', inputTextValue || '');
                })
                .catch(err => {
                    if (loadingIndicator) loadingIndicator.style.display = 'none';
                    if (generateButton) generateButton.disabled = false;
                    alert('请求失败：' + err.message);
                });
                return;
            }

            console.log('发送的数据:', requestData);
            
            // 发送请求
            fetch('/test_case_generator/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify(requestData)
            })
            .then(response => {
                console.log('收到服务器响应:', response.status);
                return response.json();
            })
            .then(data => {
                console.log('解析后的响应数据:', data);
                
                // 隐藏加载指示器
                if (loadingIndicator) {
                    loadingIndicator.style.display = 'none';
                }
                if (generateButton) {
                    generateButton.disabled = false;
                }
                
                if (data.success) {
                    // 创建或获取 result-container
                    let resultContainer = document.getElementById('result-container');
                    if (!resultContainer) {
                        resultContainer = document.createElement('div');
                        resultContainer.id = 'result-container';
                        resultContainer.className = 'mt-4';
                        generateForm.parentNode.insertBefore(resultContainer, generateForm.nextSibling);
                    }
                    
                    // 使用已有的 displayTestCases 函数显示测试用例
                    displayTestCases(data.test_cases);
                    
                    // 保存生成的测试用例到会话存储
                    sessionStorage.setItem('generatedTestCases', JSON.stringify(data.test_cases));
                    sessionStorage.setItem('inputText', inputTextValue);
                    
                    // 重新绑定保存按钮事件
                    const saveButton = document.getElementById('save-button');
                    if (saveButton) {
                        saveButton.disabled = false;
                    }
                } else {
                    console.error('服务器返回错误:', data.message);
                    if (resultContainer) {
                        resultContainer.innerHTML = `<div class="alert alert-danger">${data.message || '生成测试用例时出错'}</div>`;
                    }
                }
            })
            .catch(error => {
                console.error('请求发生错误:', error);
                if (loadingIndicator) {
                    loadingIndicator.style.display = 'none';
                }
                if (resultContainer) {
                    resultContainer.innerHTML = `<div class="alert alert-danger">请求失败: ${error.message}</div>`;
                }
            });
        });
    }
    
    // 显示测试用例
    function displayTestCases(testCases, stats) {
        // 获取或创建 resultContainer
        let resultContainer = document.getElementById('result-container');
        if (!resultContainer) {
            // 如果不存在，创建一个新的
            resultContainer = document.createElement('div');
            resultContainer.id = 'result-container';
            resultContainer.className = 'mt-4';
            // 将新创建的容器插入到表单后面
            const generateForm = document.getElementById('generate-form');
            if (generateForm) {
                generateForm.parentNode.insertBefore(resultContainer, generateForm.nextSibling);
            } else {
                // 如果找不到表单，插入到body
                document.body.appendChild(resultContainer);
            }
        }

        if (!testCases || !testCases.length) {
            resultContainer.innerHTML = '<div class="alert alert-info">没有生成测试用例</div>';
            return;
        }
        
        // 用例带 test_point_id 时，说明是「按测试点生成」的，多显示一列来源
        const showPointCol = testCases.some(tc => tc.test_point_id);

        let statsHtml = '';
        if (stats) {
            const parts = [];
            parts.push(`<strong>覆盖率：${stats.coverage}%</strong>（${stats.covered_points}/${stats.total_points} 个测试点已产出用例）`);
            parts.push(`共 <strong>${stats.total_cases}</strong> 条用例`);
            if (stats.dedup_removed) {
                parts.push(`已合并重复 <strong>${stats.dedup_removed}</strong> 条`);
            }
            if (typeof stats.avg_score === 'number' && stats.avg_score > 0) {
                parts.push(`平均质量分 <strong>${stats.avg_score}</strong>`);
            }
            if (stats.low_score_cases) {
                parts.push(`低于阈值的用例 <strong>${stats.low_score_cases}</strong> 条`);
            }
            if (stats.regenerated) {
                parts.push(`低分重生成 <strong>${stats.regenerated}</strong> 个测试点`);
            }
            if (stats.token_usage && stats.token_usage.llm_calls) {
                const u = stats.token_usage;
                parts.push(`<span class="text-muted">LLM 调用 ${u.llm_calls} 次，token ${u.total_tokens}</span>`);
            }
            const failed = (stats.failed_points || 0) > 0
                ? `<span class="text-danger">｜失败 ${stats.failed_points} 个</span>` : '';
            statsHtml = `
                <div class="alert alert-info">
                    ${parts.join(' ｜ ')} ${failed}
                </div>`;
        }

        let html = `
            ${statsHtml}
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">生成的测试用例</h5>
                </div>
                <div class="card-body">
                    <div class="table-responsive">
                        <table class="table table-bordered table-hover">
                            <thead class="thead-light">
                                <tr>
                                    <th width="5%">编号</th>
                                    ${showPointCol ? '<th width="12%">来源测试点</th>' : ''}
                                    <th width="23%">测试用例描述</th>
                                    <th width="30%">测试步骤</th>
                                    <th width="30%">预期结果</th>
                                </tr>
                            </thead>
                            <tbody>
        `;

        testCases.forEach((testCase, index) => {
            // 确保test_steps和expected_results是数组
            const testSteps = Array.isArray(testCase.test_steps) 
                ? testCase.test_steps 
                : testCase.test_steps.split('\n').filter(step => step.trim());
            
            const expectedResults = Array.isArray(testCase.expected_results)
                ? testCase.expected_results
                : testCase.expected_results.split('\n').filter(result => result.trim());

            html += `
                <tr>
                    <td>${index + 1}</td>
                    ${showPointCol ? `<td><small>${testCase.test_point_id || ''}<br>${testCase.test_point_title || ''}</small></td>` : ''}
                    <td>${testCase.description || ''}</td>
                    <td>
                        ${testSteps.map(step => `<div class="mb-2">${step}</div>`).join('')}
                    </td>
                    <td>
                        ${expectedResults.map(result => `<div class="mb-2">${result}</div>`).join('')}
                    </td>
                </tr>
            `;
        });

        html += `
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
            <div class="text-right mt-3">
                <button id="save-button" class="btn btn-success">保存测试用例</button>
            </div>
        `;

        resultContainer.innerHTML = html;

        // 重新绑定保存按钮事件
        const saveButton = document.getElementById('save-button');
        if (saveButton) {
            saveButton.disabled = false;
            
            // 添加保存按钮的点击事件监听器
            saveButton.addEventListener('click', function() {
                console.log('保存按钮被点击');
                
                // 尝试从会话存储获取数据
                let testCases = null;
                try {
                    testCases = JSON.parse(sessionStorage.getItem('generatedTestCases') || '[]');
                } catch (error) {
                    console.error('解析测试用例数据失败:', error);
                    alert('解析测试用例数据失败，请查看控制台获取详细信息');
                    return;
                }
                
                if (!testCases || testCases.length === 0) {
                    alert('没有可保存的测试用例');
                    return;
                }
                
                // 获取其他必要数据
                const requirementElement = document.getElementById('input-text');
                const llmProviderElement = document.getElementById('llm-provider');
                
                if (!requirementElement || !llmProviderElement) {
                    console.error('缺失必要的页面元素');
                    alert('页面元素缺失，无法保存数据');
                    return;
                }
                
                // 准备请求数据
                const requestData = {
                    test_cases: testCases,
                    requirement: requirementElement.value,
                    llm_provider: llmProviderElement.value
                };
                
                // 禁用按钮防止重复提交
                saveButton.disabled = true;
                saveButton.textContent = '保存中...';
                
                // 发送保存请求
                fetch('/test_case_generator/save-test-case/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': getCookie('csrftoken'),
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(requestData)
                })
                .then(response => response.json())
                .then(data => {
                    saveButton.textContent = '保存测试用例';
                    
                    if (data.success) {
                        alert('测试用例保存成功！');
                        // 清除会话存储
                        sessionStorage.removeItem('generatedTestCases');
                        sessionStorage.removeItem('inputText');
                    } else {
                        saveButton.disabled = false;
                        alert('保存失败：' + (data.message || '未知错误'));
                    }
                })
                .catch(error => {
                    saveButton.disabled = false;
                    saveButton.textContent = '保存测试用例';
                    console.error('保存失败:', error);
                    alert('保存失败，请查看控制台获取详细信息');
                });
            });
        }
    }
    
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
}); 