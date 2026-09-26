/**
 * StellarDraw · 设置面板
 * ---------------------------------------------------------------------------
 * 需求：PRD §4.2 / F-01 / F-02 / F-02b / F-02c / F-03 / F-04 / F-05 / F-09 /
 *       F-10 / F-14 / F-15 / TI-11 / TI-15 / TI-16
 *       验收：AC-01 / AC-02 / AC-02b / AC-03 / AC-04 / AC-08 / AC-09
 *
 * 五个标签页：名单管理 / 抽取规则 / 外观音效 / 外部程序 / 关于
 *
 * 交互约定：
 *   · 名单改动采用「防抖自动保存」——老师改完名字不用再找保存按钮，
 *     400ms 无输入即写盘；状态在右下角以「已保存」轻提示体现。
 *   · 班级的新建 / 重命名 / 删除统一走通用对话框，不使用原生 prompt
 *     （pywebview 会屏蔽原生模态框）。
 *
 * 依赖：window.CR.Bridge、window.CR.UI（由 app.js 提供）、window.CR.Audio
 * 暴露：window.CR.Settings
 */

'use strict';

window.CR = window.CR || {};

window.CR.Settings = (function () {
    /** 名单自动保存的防抖时长（毫秒） */
    const SAVE_DEBOUNCE = 400;

    /** 默认音量：与 storage.DEFAULT_CONFIG 的 volume 保持一致 */
    const DEFAULT_VOLUME = 0.7;

    /** 外部依赖与 DOM 缓存（在 init 时填充） */
    let deps = null;
    let dom = null;

    /** 当前状态 */
    let isOpen = false;
    let currentRoster = '';
    let rows = [];
    let saveTimer = null;

    /**
     * 当前面板内的设置快照。
     * 初值与 storage.DEFAULT_CONFIG 对齐，保证「尚未从后端读取」时
     * 控件也不会被填成 undefined。
     */
    let settings = {
        dedupe: true,
        muted: false,
        volume: DEFAULT_VOLUME,
        mascot_enabled: true,
        external_exe: '',
    };

    // ======================================================================
    // 内部工具
    // ======================================================================

    /** 轻提示（转调 app.js 提供的能力） */
    function toast(text, type, action) {
        deps.toast(text, type, action);
    }

    /**
     * 统一的失败处理：把信封里的中文提示直接显示给老师。
     * @param {{message?: string}} res 接口返回值
     */
    function fail(res) {
        toast((res && res.message) || '操作失败', 'error');
    }

    /** 把后端 settings 快照应用到面板控件上 */
    function applySettingsToControls() {
        dom.toggleDedupe.checked = Boolean(settings.dedupe);
        // muted 是反相语义：勾选 = 音效开。
        // 这里显示的是**默认值**（持久化的配置项），不是主界面按钮的临时状态
        // —— 后者只在本次运行有效、不写盘（PRD TI-30）。
        dom.toggleSound.checked = !settings.muted;
        dom.rangeVolume.value = String(settings.volume);
        dom.volumeValue.textContent = Math.round(settings.volume * 100) + '%';
        dom.toggleMascot.checked = Boolean(settings.mascot_enabled);
        dom.inputExternal.value = settings.external_exe || '';
    }

    // ======================================================================
    // 班级与名单
    // ======================================================================

    /** 拉取班级列表并刷新下拉框 */
    async function refreshRosterList() {
        const res = await window.CR.Bridge.call('list_rosters');
        if (!res.ok) {
            fail(res);
            return;
        }
        renderRosterSelect(res.rosters, res.current);
    }

    /**
     * 渲染班级下拉框。
     * @param {string[]} names 班级名列表
     * @param {string} current 当前班级
     */
    function renderRosterSelect(names, current) {
        const list = Array.isArray(names) ? names : [];
        dom.rosterSelect.textContent = '';

        if (!list.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = '（还没有班级）';
            dom.rosterSelect.appendChild(option);
            dom.rosterSelect.disabled = true;
            currentRoster = '';
            rows = [];
            renderRows();
            // 同步给主界面：没有班级也就没有学生
            deps.onRosterChanged('', 0);
            return;
        }

        dom.rosterSelect.disabled = false;
        list.forEach(function (name) {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            dom.rosterSelect.appendChild(option);
        });

        const target = list.indexOf(current) >= 0 ? current : list[0];
        dom.rosterSelect.value = target;
        currentRoster = target;
        loadRoster(target);
    }

    /**
     * 切换/加载某个班级的名单。
     * @param {string} name 班级名
     */
    async function loadRoster(name) {
        if (!name) {
            return;
        }
        const res = await window.CR.Bridge.call('load_roster', name);
        if (!res.ok) {
            fail(res);
            return;
        }
        currentRoster = name;
        rows = (res.roster && res.roster.students ? res.roster.students : []).map(function (s) {
            return { num: String(s.num || ''), name: String(s.name || '') };
        });
        renderRows();
        deps.onRosterChanged(currentRoster, rows.length);
    }

    /** 渲染名单表格 */
    function renderRows() {
        dom.rosterRows.textContent = '';
        updateCount();

        if (!rows.length) {
            const empty = document.createElement('p');
            empty.className = 'table__empty';
            empty.textContent = currentRoster
                ? '名单为空，用下方「添加学生」或「批量粘贴」录入'
                : '请先新建一个班级';
            dom.rosterRows.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        rows.forEach(function (row, index) {
            fragment.appendChild(createRow(row, index));
        });
        dom.rosterRows.appendChild(fragment);
    }

    /**
     * 创建一行可编辑的学生记录。
     * @param {{num: string, name: string}} row 数据
     * @param {number} index 行号
     * @returns {HTMLElement} 该行的容器
     */
    function createRow(row, index) {
        const wrapper = document.createElement('div');
        wrapper.className = 'table__row';
        wrapper.dataset.index = String(index);

        const numInput = document.createElement('input');
        numInput.className = 'table__cell';
        numInput.type = 'text';
        numInput.value = row.num;
        numInput.placeholder = '学号';
        numInput.setAttribute('aria-label', '学号');

        const nameInput = document.createElement('input');
        nameInput.className = 'table__cell';
        nameInput.type = 'text';
        nameInput.value = row.name;
        nameInput.placeholder = '姓名';
        nameInput.setAttribute('aria-label', '姓名');

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn--sm btn--danger';
        removeBtn.type = 'button';
        removeBtn.textContent = '删除';

        // 输入即更新内存行，并触发防抖保存（AC-01②）
        const onEdit = function () {
            rows[index] = { num: numInput.value.trim(), name: nameInput.value.trim() };
            scheduleSave();
        };
        numInput.addEventListener('input', onEdit);
        nameInput.addEventListener('input', onEdit);

        removeBtn.addEventListener('click', function () {
            rows.splice(index, 1);
            renderRows(); // 行号会变化，整体重绘最稳妥
            scheduleSave();
        });

        wrapper.appendChild(numInput);
        wrapper.appendChild(nameInput);
        wrapper.appendChild(removeBtn);
        return wrapper;
    }

    /** 更新「共 N 人」计数 */
    function updateCount() {
        // 与后端一致：姓名为空的行不计入（PRD §6.5 字段规则）
        const valid = rows.filter(function (r) {
            return String(r.name || '').trim() !== '';
        }).length;
        dom.rosterCount.textContent = '共 ' + valid + ' 人';
    }

    /** 标记录入中（视觉反馈） */
    function markDirty() {
        dom.rosterSaveState.textContent = '录入中…';
    }

    /** 标记已保存 */
    function markSaved() {
        dom.rosterSaveState.textContent = '已保存';
    }

    /** 防抖触发保存 */
    function scheduleSave() {
        updateCount();
        markDirty();
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(saveCurrentRoster, SAVE_DEBOUNCE);
    }

    /** 立即把当前班级名单写盘 */
    async function saveCurrentRoster() {
        if (!currentRoster) {
            return;
        }
        const payload = rows.filter(function (r) {
            return String(r.name || '').trim() !== '';
        });

        const res = await window.CR.Bridge.call('save_roster', currentRoster, payload);
        if (!res.ok) {
            fail(res);
            return;
        }
        markSaved();

        if (res.duplicate) {
            toast('名单中存在完全相同的重复记录，请检查学号与姓名', 'info');
        }
        deps.onRosterChanged(currentRoster, payload.length);
    }

    /** 新增一个空行并自动聚焦到姓名输入框 */
    function addStudentRow() {
        rows.push({ num: '', name: '' });
        renderRows();
        // 每行两个输入框（学号、姓名），最后一个即本行姓名
        const inputs = dom.rosterRows.querySelectorAll('.table__cell');
        const last = inputs[inputs.length - 1];
        if (last) {
            last.focus();
        }
    }

    // ======================================================================
    // 班级的新建 / 重命名 / 删除
    // ======================================================================

    async function createRoster() {
        const name = await deps.dialog.prompt({
            title: '新建班级',
            text: '请输入班级名称，例如「初一(3)班」',
            okLabel: '创建',
        });
        if (name === null) {
            return;
        }
        const res = await window.CR.Bridge.call('create_roster', name);
        if (!res.ok) {
            fail(res);
            return;
        }
        toast('已创建班级「' + res.current + '」', 'success');
        renderRosterSelect(res.rosters, res.current);
    }

    async function renameRoster() {
        if (!currentRoster) {
            toast('请先选择要重命名的班级', 'info');
            return;
        }
        const name = await deps.dialog.prompt({
            title: '重命名班级',
            text: '把「' + currentRoster + '」重命名为：',
            value: currentRoster,
            okLabel: '重命名',
        });
        if (name === null || name === currentRoster) {
            return;
        }
        const res = await window.CR.Bridge.call('rename_roster', currentRoster, name);
        if (!res.ok) {
            fail(res);
            return;
        }
        toast('已重命名为「' + res.current + '」', 'success');
        renderRosterSelect(res.rosters, res.current);
    }

    async function deleteRoster() {
        if (!currentRoster) {
            toast('请先选择要删除的班级', 'info');
            return;
        }
        const confirmed = await deps.dialog.confirm({
            title: '删除班级',
            text: '确定删除班级「' + currentRoster + '」吗？\n该班级的名单会一并删除，且无法恢复。',
            okLabel: '删除',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        const res = await window.CR.Bridge.call('delete_roster', currentRoster);
        if (!res.ok) {
            fail(res);
            return;
        }
        toast('已删除班级', 'success');
        renderRosterSelect(res.rosters, res.current);
    }

    // ======================================================================
    // 批量粘贴 / CSV 导入
    // ======================================================================

    /**
     * 批量粘贴（PRD F-01 / AC-01④）。
     * 兼容「学号,姓名」「学号 姓名」「纯姓名」三种写法，空行自动过滤。
     */
    async function batchPaste() {
        const text = await deps.dialog.area({
            title: '批量粘贴名单',
            text: '每行一条，支持「学号,姓名」「学号 姓名」或只写「姓名」；空行会自动忽略。',
            placeholder: '01,张小雨\n02 李雷\n韩梅梅',
            okLabel: '添加到当前班级',
            preview: async function (raw, setPreview) {
                if (!String(raw || '').trim()) {
                    setPreview('');
                    return;
                }
                // 输入防抖由 CR.UI 负责，这里只管取结果
                const res = await window.CR.Bridge.call('parse_batch_text', raw);
                if (!res.ok) {
                    setPreview('无法解析：' + (res.message || '格式不正确'));
                    return;
                }
                let line = '已识别 ' + res.count + ' 条记录';
                if (res.duplicate) {
                    line += '，其中 ' + res.duplicate + ' 条与现有名单重复';
                }
                setPreview(line);
            },
        });
        if (text === null) {
            return;
        }

        const parsed = await window.CR.Bridge.call('parse_batch_text', text);
        if (!parsed.ok) {
            fail(parsed);
            return;
        }
        if (!parsed.count) {
            toast('没有解析出任何记录，请检查内容', 'info');
            return;
        }

        parsed.students.forEach(function (s) {
            rows.push({ num: String(s.num || ''), name: String(s.name || '') });
        });
        renderRows();
        await saveCurrentRoster();
        toast('已添加 ' + parsed.count + ' 条记录', 'success');
    }

    /**
     * 导入 CSV（PRD F-02 / E-08 / TI-11）。
     * 当前班级已有名单时，先弹选择框让老师明确选择覆盖或追加。
     */
    async function importCsv() {
        const res = await window.CR.Bridge.call('import_csv');
        if (!res.ok) {
            // 用户主动取消不算错误，不打扰老师
            if (res.code !== 'cancelled') {
                fail(res);
            }
            return;
        }

        if (!res.need_confirm) {
            toast('已导入 ' + res.imported + ' 条记录', 'success');
            await refreshRosterList();
            return;
        }

        // TI-11：不静默覆盖，让老师明确选择；默认把非破坏性的「追加」放主按钮
        const mode = await deps.dialog.choose({
            title: 'CSV 导入',
            text: res.message,
            options: [
                { value: 'append', label: '追加到当前班级', primary: true },
                { value: 'overwrite', label: '覆盖当前班级', danger: true },
            ],
        });
        if (mode === null) {
            return;
        }

        const applied = await window.CR.Bridge.call('apply_import', mode);
        if (!applied.ok) {
            fail(applied);
            return;
        }
        toast('已导入 ' + applied.imported + ' 条记录（' + (mode === 'append' ? '追加' : '覆盖') + '）', 'success');
        await loadRoster(currentRoster);
    }

    /** 创建 CSV 模板（PRD F-02b） */
    async function createCsvTemplate() {
        const res = await window.CR.Bridge.call('create_csv_template');
        if (!res.ok) {
            fail(res);
            return;
        }
        toast('模板已生成：' + res.path + (res.overwritten ? '（已覆盖原文件）' : ''), 'success');
    }

    /** 打开 data 目录（PRD F-02c） */
    async function openDataDir() {
        const res = await window.CR.Bridge.call('open_data_dir');
        if (!res.ok) {
            fail(res);
            return;
        }
        toast('已打开数据目录', 'info');
    }

    // ======================================================================
    // 设置项改动
    // ======================================================================

    /**
     * 保存部分设置并回传主界面。
     * @param {object} patch 需要变更的键
     */
    async function patchSettings(patch) {
        const res = await window.CR.Bridge.call('save_settings', patch);
        if (!res.ok) {
            fail(res);
            return;
        }
        settings = res.settings;
        applySettingsToControls();
        deps.onSettingsChanged(settings);
    }

    /** 外部程序选择（PRD F-09 / AC-08） */
    async function chooseExternalExe() {
        const res = await window.CR.Bridge.call('choose_external_exe');
        if (!res.ok) {
            if (res.code !== 'cancelled') {
                fail(res);
            }
            return;
        }
        dom.inputExternal.value = res.path;
        await patchSettings({ external_exe: res.path });
        toast('已设置外部程序', 'success');
    }

    // ======================================================================
    // 标签页
    // ======================================================================

    /**
     * 切换到指定标签页。
     * @param {string} name 页面名（与 data-tab / data-page 对应）
     */
    function switchTab(name) {
        const target = name || 'roster';
        document.querySelectorAll('.settings__tab').forEach(function (tab) {
            tab.classList.toggle('settings__tab--active', tab.dataset.tab === target);
        });
        document.querySelectorAll('.settings__page').forEach(function (page) {
            page.hidden = page.dataset.page !== target;
        });
    }

    // ======================================================================
    // 打开 / 关闭
    // ======================================================================

    /** 打开设置面板 */
    async function open(tab) {
        dom.settings.hidden = false;
        isOpen = true;
        switchTab(tab || 'roster');
        await refreshAll();
    }

    /** 关闭设置面板；关闭前把未落盘的名单补写一次 */
    async function close() {
        if (saveTimer) {
            window.clearTimeout(saveTimer);
            saveTimer = null;
            await saveCurrentRoster();
        }
        dom.settings.hidden = true;
        isOpen = false;
    }

    /** 拉取全部面板数据 */
    async function refreshAll() {
        const settingsRes = await window.CR.Bridge.call('get_settings');
        if (settingsRes.ok) {
            settings = settingsRes.settings;
            applySettingsToControls();
        } else {
            fail(settingsRes);
        }

        await refreshRosterList();
        await fillAbout();
    }

    /** 填充「关于」页（PRD §4.2 / AC-09③） */
    async function fillAbout() {
        const res = await window.CR.Bridge.call('get_app_info');
        if (!res.ok) {
            return;
        }
        dom.aboutName.textContent = res.name;
        dom.aboutVersion.textContent = '版本 v' + res.version;
        dom.aboutTech.textContent = res.tech;
        dom.aboutPlaceholder.textContent = res.placeholder;
        dom.aboutDatadir.textContent = res.data_dir;
    }

    // ======================================================================
    // 初始化
    // ======================================================================

    /**
     * 绑定全部事件。
     * @param {object} options 依赖注入
     */
    function init(options) {
        deps = options;

        dom = {
            settings: document.getElementById('settings'),
            close: document.getElementById('settings-close'),
            rosterSelect: document.getElementById('roster-select'),
            rosterNew: document.getElementById('btn-roster-new'),
            rosterRename: document.getElementById('btn-roster-rename'),
            rosterDelete: document.getElementById('btn-roster-delete'),
            rosterRows: document.getElementById('roster-rows'),
            rosterCount: document.getElementById('roster-count'),
            rosterSaveState: document.getElementById('roster-save-state'),
            addStudent: document.getElementById('btn-add-student'),
            batch: document.getElementById('btn-batch'),
            importCsv: document.getElementById('btn-import-csv'),
            csvTemplate: document.getElementById('btn-csv-template'),
            openData: document.getElementById('btn-open-data'),
            toggleDedupe: document.getElementById('toggle-dedupe'),
            toggleSound: document.getElementById('toggle-sound'),
            rangeVolume: document.getElementById('range-volume'),
            volumeValue: document.getElementById('volume-value'),
            toggleMascot: document.getElementById('toggle-mascot'),
            inputExternal: document.getElementById('input-external'),
            browseExe: document.getElementById('btn-browse-exe'),
            aboutName: document.getElementById('about-name'),
            aboutVersion: document.getElementById('about-version'),
            aboutTech: document.getElementById('about-tech'),
            aboutPlaceholder: document.getElementById('about-placeholder'),
            aboutDatadir: document.getElementById('about-datadir'),
        };

        // --- 关闭 ---
        dom.close.addEventListener('click', close);
        document.querySelectorAll('[data-settings-close]').forEach(function (node) {
            node.addEventListener('click', close);
        });

        // --- 标签页 ---
        document.querySelectorAll('.settings__tab').forEach(function (tab) {
            tab.addEventListener('click', function () {
                switchTab(tab.dataset.tab);
            });
        });

        // --- 班级 ---
        dom.rosterSelect.addEventListener('change', function () {
            loadRoster(dom.rosterSelect.value);
        });
        dom.rosterNew.addEventListener('click', createRoster);
        dom.rosterRename.addEventListener('click', renameRoster);
        dom.rosterDelete.addEventListener('click', deleteRoster);

        // --- 名单录入 ---
        dom.addStudent.addEventListener('click', addStudentRow);
        dom.batch.addEventListener('click', batchPaste);
        dom.importCsv.addEventListener('click', importCsv);
        dom.csvTemplate.addEventListener('click', createCsvTemplate);
        dom.openData.addEventListener('click', openDataDir);

        // --- 抽取规则 ---
        dom.toggleDedupe.addEventListener('change', function () {
            patchSettings({ dedupe: dom.toggleDedupe.checked });
        });

        // --- 外观音效（TI-15：音效**默认**开关复用 muted；TI-16：看板娘开关） ---
        // 开关写盘后，主程序会在本次运行中立即采用该默认值（PRD TI-30）
        dom.toggleSound.addEventListener('change', function () {
            patchSettings({ muted: !dom.toggleSound.checked });
        });
        dom.rangeVolume.addEventListener('input', function () {
            dom.volumeValue.textContent = Math.round(dom.rangeVolume.value * 100) + '%';
        });
        dom.rangeVolume.addEventListener('change', function () {
            patchSettings({ volume: Number(dom.rangeVolume.value) });
        });
        dom.toggleMascot.addEventListener('change', function () {
            patchSettings({ mascot_enabled: dom.toggleMascot.checked });
        });

        // --- 外部程序 ---
        dom.browseExe.addEventListener('click', chooseExternalExe);

        // 初始把控件按默认值摆好，避免打开面板前出现空档
        applySettingsToControls();
    }

    return {
        init: init,
        open: open,
        close: close,
        switchTab: switchTab,
        isOpen: function () {
            return isOpen;
        },
        DEFAULT_VOLUME: DEFAULT_VOLUME,
    };
})();
