/**
 *  StellarDraw · 应用初始化与状态编排
 * ---------------------------------------------------------------------------
 * 需求：PRD §6.1（单次抽取时序）/ §6.2（五次抽取时序）/ §6.4（简洁视图）/
 *       E-01 ~ E-06 / AC-05 / AC-06 / AC-10 / AC-14 / AC-15
 *
 * 本文件包含三部分：
 *   CR.UI     —— 通用交互件：轻提示（toast）与模态对话框（dialog）
 *   CR.Mascot —— 看板娘三态状态机（PRD F-14 / AC-14）
 *   CR.App    —— 主界面：初始化、抽取流程编排、按钮与状态机
 *
 * 抽取时序（PRD §6.1）：
 *   点击 → 按钮禁用 + 加载图标旋转 + 看板娘「抽取中」→ 调用 Python 抽取（<10ms）
 *        → 卡牌从牌堆滑出（400ms）→ 加载图标停止 + 按钮恢复 + 看板娘回「待机」
 *        → 老师点击卡背 → 3D 翻转（600ms）→ 出结果音 + 看板娘「出结果」
 *
 * 依赖：window.CR.Bridge / Cards / Audio / Settings
 * 暴露：window.CR.UI、window.CR.Mascot、window.CR.App
 */

'use strict';

window.CR = window.CR || {};

/* ==========================================================================
   一、通用交互件 CR.UI
   ========================================================================== */
window.CR.UI = (function () {
    /** 提示自动消失时间（毫秒）：带操作按钮的停留更久，给老师反应时间 */
    const TOAST_TTL = 4000;
    const TOAST_TTL_ACTION = 8000;

    /** 同时最多显示的提示条数，避免刷屏 */
    const TOAST_MAX = 3;

    /** 预览输入防抖（毫秒） */
    const PREVIEW_DEBOUNCE = 300;

    let dom = null;

    /** 当前对话框的上下文；为 null 表示没有对话框在显示 */
    let dialogCtx = null;

    /** 预览防抖计时器 */
    let previewTimer = null;

    /**
     * 弹出一条轻提示。
     *
     * @param {string} text 文案
     * @param {'info'|'success'|'error'} [type] 类型，决定配色
     * @param {{label: string, onClick: function}} [action] 可选的操作按钮（如「去设置」）
     */
    function toast(text, type, action) {
        const el = document.createElement('div');
        el.className = 'toast toast--' + (type || 'info');

        const span = document.createElement('span');
        span.className = 'toast__text';
        span.textContent = String(text || '');
        el.appendChild(span);

        const remove = function () {
            window.clearTimeout(timer);
            el.remove();
        };

        if (action && action.label) {
            const btn = document.createElement('button');
            btn.className = 'toast__action';
            btn.type = 'button';
            btn.textContent = action.label;
            btn.addEventListener('click', function () {
                remove();
                if (typeof action.onClick === 'function') {
                    action.onClick();
                }
            });
            el.appendChild(btn);
        }

        // 超出上限时先挤掉最旧的一条
        const existing = dom.toastArea.querySelectorAll('.toast');
        if (existing.length >= TOAST_MAX) {
            existing[0].remove();
        }

        dom.toastArea.appendChild(el);
        const timer = window.setTimeout(remove, action ? TOAST_TTL_ACTION : TOAST_TTL);
    }

    /** 只显示预览文字（内部使用） */
    function setPreview(text) {
        const value = String(text || '');
        dom.dialogPreview.textContent = value;
        dom.dialogPreview.hidden = value === '';
    }

    /**
     * 打开对话框并等待用户选择。
     *
     * @param {object} cfg 配置
     * @param {string} cfg.title 标题
     * @param {string} cfg.text 正文
     * @param {'confirm'|'text'|'area'|'choice'} cfg.mode 形态
     * @param {string} [cfg.value] 输入框初值
     * @param {string} [cfg.placeholder] 输入框提示
     * @param {string} [cfg.okLabel] 主按钮文案
     * @param {boolean} [cfg.okDanger] 主按钮是否用危险色
     * @param {string} [cfg.altLabel] 第二按钮文案（仅 choice 用）
     * @param {boolean} [cfg.altDanger] 第二按钮是否用危险色
     * @param {function(string, function(string): void)} [cfg.preview] 输入预览回调
     * @returns {Promise<'ok'|'alt'|null>} 用户的选择
     */
    function showDialog(cfg) {
        return new Promise(function (resolve) {
            dialogCtx = { resolve: resolve, mode: cfg.mode || 'confirm', preview: cfg.preview };

            dom.dialogTitle.textContent = cfg.title || '';
            dom.dialogText.textContent = cfg.text || '';

            // 三种输入形态互斥，其余一律隐藏
            const isText = dialogCtx.mode === 'text';
            const isArea = dialogCtx.mode === 'area';
            dom.dialogInput.hidden = !isText;
            dom.dialogInput.value = isText ? (cfg.value || '') : '';
            dom.dialogInput.placeholder = cfg.placeholder || '';
            dom.dialogTextarea.hidden = !isArea;
            dom.dialogTextarea.value = isArea ? (cfg.value || '') : '';
            dom.dialogTextarea.placeholder = cfg.placeholder || '';
            setPreview('');

            dom.dialogOk.textContent = cfg.okLabel || '确定';
            dom.dialogOk.classList.toggle('btn--danger', Boolean(cfg.okDanger));

            if (cfg.altLabel) {
                dom.dialogAlt.hidden = false;
                dom.dialogAlt.textContent = cfg.altLabel;
                dom.dialogAlt.classList.toggle('btn--danger', Boolean(cfg.altDanger));
            } else {
                dom.dialogAlt.hidden = true;
            }

            dom.dialog.hidden = false;

            // 聚焦到最可能被使用的控件，减少一次点击
            if (isText) {
                dom.dialogInput.focus();
                dom.dialogInput.select();
            } else if (isArea) {
                dom.dialogTextarea.focus();
            } else {
                dom.dialogOk.focus();
            }
        });
    }

    /**
     * 关闭对话框并回传选择结果。
     * @param {'ok'|'alt'|null} result 选择结果
     */
    function settleDialog(result) {
        if (!dialogCtx) {
            return;
        }
        const ctx = dialogCtx;
        dialogCtx = null;
        window.clearTimeout(previewTimer);
        dom.dialog.hidden = true;
        ctx.resolve(result);
    }

    /** 当前输入框的值（按形态取值） */
    function currentInputValue() {
        if (!dialogCtx) {
            return '';
        }
        if (dialogCtx.mode === 'area') {
            return dom.dialogTextarea.value;
        }
        if (dialogCtx.mode === 'text') {
            return dom.dialogInput.value;
        }
        return '';
    }

    /** 绑定对话框的静态事件（只在初始化时调用一次） */
    function bindDialogEvents() {
        dom.dialogOk.addEventListener('click', function () {
            settleDialog('ok');
        });
        dom.dialogAlt.addEventListener('click', function () {
            settleDialog('alt');
        });
        dom.dialogCancel.addEventListener('click', function () {
            settleDialog(null);
        });
        document.querySelectorAll('[data-dialog-cancel]').forEach(function (node) {
            node.addEventListener('click', function () {
                settleDialog(null);
            });
        });

        // 单行输入：回车即确认
        dom.dialogInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                settleDialog('ok');
            }
        });

        // 多行输入：Ctrl+Enter 确认，普通回车用于换行
        const onAreaInput = function () {
            if (!dialogCtx || !dialogCtx.preview) {
                return;
            }
            window.clearTimeout(previewTimer);
            previewTimer = window.setTimeout(function () {
                dialogCtx.preview(currentInputValue(), setPreview);
            }, PREVIEW_DEBOUNCE);
        };
        dom.dialogTextarea.addEventListener('input', onAreaInput);
        dom.dialogTextarea.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                settleDialog('ok');
            }
        });

        // Esc 关闭
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                if (dialogCtx) {
                    settleDialog(null);
                }
            }
        });
    }

    /** 对话框：确认 */
    function confirm(cfg) {
        return showDialog(Object.assign({ mode: 'confirm' }, cfg)).then(function (token) {
            return token === 'ok';
        });
    }

    /** 对话框：单行输入；取消返回 null */
    function prompt(cfg) {
        return showDialog(Object.assign({ mode: 'text' }, cfg)).then(function (token) {
            if (token !== 'ok') {
                return null;
            }
            const value = String(dom.dialogInput.value || '').trim();
            return value === '' ? null : value;
        });
    }

    /** 对话框：多行输入；取消返回 null */
    function area(cfg) {
        return showDialog(Object.assign({ mode: 'area' }, cfg)).then(function (token) {
            if (token !== 'ok') {
                return null;
            }
            return String(dom.dialogTextarea.value || '');
        });
    }

    /**
     * 对话框：二选一。
     * options[0] 渲染为主按钮，options[1] 渲染为次按钮。
     *
     * @param {{options: Array<{value: string, label: string, primary?: boolean, danger?: boolean}>}} cfg
     * @returns {Promise<string|null>} 选中的 value，取消返回 null
     */
    function choose(cfg) {
        const options = cfg.options || [];
        const first = options[0] || {};
        const second = options[1] || {};

        return showDialog({
            title: cfg.title,
            text: cfg.text,
            mode: 'choice',
            okLabel: first.label,
            okDanger: first.danger,
            altLabel: second.label,
            altDanger: second.danger,
        }).then(function (token) {
            if (token === 'ok') {
                return first.value || null;
            }
            if (token === 'alt') {
                return second.value || null;
            }
            return null;
        });
    }

    /** 缓存 DOM 并绑定静态事件 */
    function init() {
        dom = {
            toastArea: document.getElementById('toast-area'),
            dialog: document.getElementById('dialog'),
            dialogTitle: document.getElementById('dialog-title'),
            dialogText: document.getElementById('dialog-text'),
            dialogInput: document.getElementById('dialog-input'),
            dialogTextarea: document.getElementById('dialog-textarea'),
            dialogPreview: document.getElementById('dialog-preview'),
            dialogOk: document.getElementById('dialog-ok'),
            dialogAlt: document.getElementById('dialog-alt'),
            dialogCancel: document.getElementById('dialog-cancel'),
        };
        bindDialogEvents();
    }

    return {
        init: init,
        toast: toast,
        dialog: {
            confirm: confirm,
            prompt: prompt,
            area: area,
            choose: choose,
        },
    };
})();

/* ==========================================================================
   二、看板娘 CR.Mascot
   ==========================================================================
   需求：PRD F-14 / AC-14（三态切换 + 呼吸动效 + 可关闭）

   三态：idle 待机 / loading 抽取中 / result 出结果。

   实现取舍（决策 TI-22）：
     · 三张同名 SVG 在 index.html 里就**全部叠放**好了，这里只切换
       .mascot 容器上的 data-state，由 CSS 负责 opacity 交叉淡入与呼吸动效。
     · JS 不直接读写任何样式属性 —— 这样「替换同名素材即可、无需改代码」
       （AC-14②）依然成立，将来换真人立绘也不用动这里一行。
     · 不做「眨眼」：那需要控制 SVG 内部的眼睛元素，会破坏上面那条约定。
   ========================================================================== */
window.CR.Mascot = (function () {
    /** 合法状态，与 index.html 的 data-state 及 layout.css 的选择器严格对应 */
    const STATES = ['idle', 'loading', 'result'];

    /** 看板娘容器（在 init 时注入，避免本模块依赖 DOM 查询时机） */
    let el = null;

    /** 当前状态；空串表示尚未初始化 */
    let current = '';

    /**
     * 绑定容器并复位到待机态。
     * @param {HTMLElement} element .mascot 容器
     */
    function init(element) {
        el = element;
        current = '';
        setState('idle');
    }

    /**
     * 切换看板娘状态。
     *
     * @param {'idle'|'loading'|'result'} state 目标状态；非法值一律按待机处理
     */
    function setState(state) {
        const next = STATES.indexOf(state) >= 0 ? state : 'idle';
        if (!el || next === current) {
            return; // 状态没变就不写 DOM，避免无谓的样式重算
        }
        current = next;
        el.dataset.state = next;
    }

    /** @returns {string} 当前状态名 */
    function getState() {
        return current;
    }

    return {
        init: init,
        setState: setState,
        getState: getState,
    };
})();

/* ==========================================================================
   三、主界面 CR.App
   ========================================================================== */
window.CR.App = (function () {
    /** 状态文案 */
    const TEXT_READY = '准备就绪';
    const TEXT_DRAWING = '抽取中…';

    /** 空状态文案 */
    const EMPTY_NO_ROSTER = '还没有学生名单，点左下角「设置」先录入';
    const EMPTY_READY = '点击右下角「抽取」开始';

    let dom = null;
    let busy = false;
    let rosterCount = 0;

    /** 当前是否静音（**本次运行**的有效状态；true = 静音） */
    let muted = false;

    /**
     * 配置里「音效默认开关」（muted）上一次已知的取值。
     *
     * 【为什么需要它】2026-09-24 修订（PRD TI-30）：主界面「静音」按钮改为
     * **只影响本次运行、不写盘**；配置里的 muted 只代表设置面板的默认值。
     * 由于 applySettings() 会在任何设置项变动时被调用（音量、看板娘、去重……），
     * 不能无条件用它覆盖运行态 —— 否则老师临时静音后拖一下音量条就会被"悄悄取消静音"。
     * 这里只在「默认开关本身发生了变化」（含首次启动加载）时才把默认值同步到运行态。
     */
    let configMuted = null;

    /**
     * 本轮抽取的令牌。
     * 每开始一轮抽取就 +1；翻牌后延时播放「出结果音」时会比对令牌，
     * 若老师已经开始了下一轮（令牌已变），就丢弃这次待播的音效，
     * 避免上一轮的余音串到新一局里。
     */
    let roundToken = 0;

    // ----------------------------------------------------------------------
    // 状态与呈现
    // ----------------------------------------------------------------------

    /**
     * 显示/取消「抽取中」状态。
     * @param {boolean} loading 是否正在抽取
     */
    function setLoading(loading) {
        dom.status.classList.toggle('status--loading', loading);
        dom.statusText.textContent = loading ? TEXT_DRAWING : TEXT_READY;
    }

    /**
     * 控制抽取按钮可用性（PRD E-06：抽取过程中禁止重复点击）。
     * @param {boolean} enabled 是否可用
     */
    function setButtonsEnabled(enabled) {
        dom.draw1.disabled = !enabled;
        dom.draw5.disabled = !enabled;
    }

    /** 清掉结果区的空状态占位 */
    function clearEmptyState() {
        const empty = dom.results.querySelector('.stage__empty');
        if (empty) {
            empty.remove();
        }
    }

    /**
     * 结果区是否已有「有效卡牌」。
     *
     * 退场中的旧卡会被搬进幽灵层，它们不算有效卡牌 —— 否则幽灵淡出的
     * 那 460ms 内会被误判成"有卡"，导致空状态提示不出现。
     *
     * @returns {boolean}
     */
    function hasActiveCards() {
        const cards = dom.results.querySelectorAll('.card');
        return Array.prototype.some.call(cards, function (card) {
            return !card.classList.contains('card--leaving');
        });
    }

    /**
     * 设置结果区的空状态占位文案（不存在则创建，已存在则更新）。
     * @param {string} text 文案
     */
    function showEmptyState(text) {
        if (hasActiveCards()) {
            return;
        }
        let node = dom.results.querySelector('.stage__empty');
        if (!node) {
            node = document.createElement('p');
            node.className = 'stage__empty';
            dom.results.appendChild(node);
        }
        node.textContent = text;
    }

    /** 根据名单情况刷新空状态文案（PRD AC-10 / E-01 的预防性提示） */
    function refreshEmptyState() {
        showEmptyState(rosterCount > 0 ? EMPTY_READY : EMPTY_NO_ROSTER);
    }

    /**
     * 把后端设置应用到界面。
     *
     * 【音效状态的处理口径（2026-09-24 修订，PRD TI-30）】
     *   · 配置里的 muted = 设置面板「音效默认开关」，是**默认值**；
     *   · 界面上的有效音效状态是运行态 muted，启动时继承默认值，
     *     之后只由「设置面板改了开关」或「主界面静音按钮」改变；
     *   · 因此这里只在默认值**发生变化**时同步运行态，避免其它设置项
     *     （音量 / 看板娘 / 去重）的改动把临时静音冲掉。
     *
     * @param {object} settings get_settings 返回的设置字典
     */
    function applySettings(settings) {
        const next = settings || {};

        const nextConfigMuted = Boolean(next.muted);
        if (configMuted === null || nextConfigMuted !== configMuted) {
            // 首次加载（启动继承）或老师主动改了设置面板的音效开关：以默认值为准
            configMuted = nextConfigMuted;
            muted = nextConfigMuted;
        }
        window.CR.Audio.setMuted(muted);
        window.CR.Audio.setVolume(Number.isFinite(next.volume) ? next.volume : 0.7);

        // TI-16：关闭看板娘时**只隐藏图片**，容器尺寸不变，布局不塌陷。
        // 隐藏 .mascot__stage 而不是容器本身，容器仍占着原尺寸的空白。
        const enabled = next.mascot_enabled !== false;
        dom.mascotStage.classList.toggle('is-invisible', !enabled);

        updateMuteButton();
    }

    /**
     * 刷新一键静音按钮的图标、文案与无障碍状态（PRD F-15 / AC-15④）。
     * 按钮外观与 muted 严格对应，因此无论从按钮还是从设置面板改动，都走这里同步。
     */
    function updateMuteButton() {
        dom.muteIcon.textContent = muted ? '🔇' : '🔊';
        dom.muteLabel.textContent = muted ? '已静音' : '音效';
        dom.muteBtn.setAttribute('aria-pressed', muted ? 'true' : 'false');
        dom.muteBtn.setAttribute(
            'aria-label',
            muted ? '当前已静音，点击开启音效' : '当前音效开启，点击静音'
        );
    }

    /**
     * 一键静音（PRD F-15 / AC-15④、决策 TI-30）。
     *
     * **只做本次运行的临时调整**：不写配置、也不回写设置面板，
     * 因此设置面板「外观音效」页里的开关始终代表"默认值"，不会被这里改掉。
     * 重启程序后音效状态由设置里的默认开关决定。
     */
    function toggleMute() {
        const nextMuted = !muted;

        // 即时听觉反馈：将要静音时先响一声；将要恢复声音时等应用完再响
        if (nextMuted) {
            window.CR.Audio.play('click');
        }

        muted = nextMuted;
        window.CR.Audio.setMuted(muted);
        updateMuteButton();

        if (!nextMuted) {
            window.CR.Audio.play('click');
        }
        window.CR.UI.toast(
            nextMuted ? '已静音（仅本次运行，不影响默认设置）' : '已开启音效（仅本次运行）',
            'info',
        );
    }

    /**
     * 统一处理失败信封（PRD E-01 / E-04 / E-05 / AC-10）。
     * @param {{message?: string, code?: string}} res 接口返回值
     */
    function handleFailure(res) {
        const message = (res && res.message) || '操作失败';

        // 名单为空 / 未设置外部程序：都提供一步直达设置的入口
        if (res && res.code === 'empty_roster') {
            window.CR.UI.toast(message, 'error', {
                label: '去设置',
                onClick: function () {
                    window.CR.Settings.open('roster');
                },
            });
            return;
        }
        if (res && (res.code === 'not_set' || res.code === 'not_found')) {
            window.CR.UI.toast(message, 'error', {
                label: '去设置',
                onClick: function () {
                    window.CR.Settings.open('external');
                },
            });
            return;
        }
        window.CR.UI.toast(message, 'error');
    }

    // ----------------------------------------------------------------------
    // 抽取流程（PRD §6.1 / §6.2）
    // ----------------------------------------------------------------------

    /**
     * 执行一次抽取。
     *
     * @param {number} count 抽取人数（1 或 5）
     */
    async function draw(count) {
        if (busy) {
            return; // E-06：防重复点击
        }
        busy = true;
        roundToken += 1; // 作废上一轮尚未播放的「出结果音」
        setButtonsEnabled(false);
        clearEmptyState();

        // 上一轮卡牌原地淡出（§6.2⑥），新卡随后立刻进入布局
        window.CR.Cards.clear(dom.results);

        // PRD §6.1 第 1 步：按钮禁用 + 加载图标旋转 + 看板娘切「抽取中」
        setLoading(true);
        window.CR.Mascot.setState('loading');

        const res = await window.CR.Bridge.call('draw', count);
        if (!res.ok) {
            setLoading(false);
            window.CR.Mascot.setState('idle');
            busy = false;
            setButtonsEnabled(true);
            refreshEmptyState();
            handleFailure(res);
            return;
        }

        // F-17b / F-17c：本轮的 armed 状态由 draw 返回（**不**消费）。真正的
        // 消费与"视觉生效"都改到 reveal 阶段（2026-09-13 第三轮修订）：
        //   · drift_offset != null —— 第一次翻牌时把卡的正面 swap 成偏移后的学生；
        //   · wake_up_played == true —— 第一次翻牌 click 与 bingo 同步播放。
        // 两者都延迟到"原结果已展示"之后才生效（drift 为翻转结束 +200ms，
        // 见 TI-31），因此各自用一个独立哨兵守护一次性消费。
        const driftOffset = Number(res.drift_offset);
        const hasDrift = Number.isFinite(driftOffset) && driftOffset !== 0;
        const wakeUpArmed = Boolean(res.wake_up_played);
        // F-17c 选定的 bingo 音量乘数（0.4/0.6/0.9）；未武装时为 null，
        // 让 Audio.play 用 base volume（不再叠乘数）。
        const wakeUpVolume =
            Number.isFinite(res.wake_up_volume) && res.wake_up_volume > 0
                ? res.wake_up_volume
                : null;
        // 【第七轮修复 2026-09-13】把原本共用的 firstFlipDone 哨兵拆成两个：
        //   · bingoPlayed 守护 F-17c「bingo 武装一次性消费」
        //   · driftConsumed 守护 F-17b「drift 偏移一次性消费」
        // 旧版用同一个标志位导致 ① 挡掉第 2~5 次翻牌 click 的反馈音（老师反馈），
        // ② bingo + drift 同时启用时 drift 段被 bingo 提前设的 true 跳过。
        let bingoPlayed = false;
        let driftConsumed = false;

        // 记录动画起点（牌堆位置），必须在渲染前测量
        const origin = dom.deck.getBoundingClientRect();
        const token = roundToken;
        window.CR.Cards.render(dom.results, res.students, origin, function (index) {
            // onReveal —— 翻牌 click 触发。
            //
            // 【每次翻牌的反馈音】第七轮修复 2026-09-13：
            //   之前 `firstFlipDone` 哨兵在最顶端拦截，导致只有第一次翻牌 click 会
            //   触发后续逻辑，后续翻牌全部 return —— 老师反馈"翻卡时只有第一次会
            //   响"。新版把哨兵下移到只守护「一次性」效果（bingo / drift 武装消费），
            //   翻牌反馈音（bingo 武装时播 bingo、否则播 result）每次翻牌 click 都响。
            //
            // 【为什么 bingo 与翻牌同步播】2026-09-13 第三轮修订：
            //   b i n g o 与翻牌 click 同步启动，不再 setTimeout(DUR_FLIP)。
            //   bingo 自然长度 ≈ 750ms，正好覆盖整个翻牌动画（600ms），
            //   听感是"翻一下同时响一串上行音"，更直观地传达"使用即生效"。
            if (token !== roundToken) {
                return; // 老师已开始下一轮，本轮余音不再补放
            }

            window.CR.Mascot.setState('result');

            // F-17c：bingo 武装时，第一次翻牌 click 同步播 bingo（替代当次
            // result 音）；后续翻牌仍正常播 result 音（取消早前"武装后
            // 全程静默"的副作用）。
            if (wakeUpArmed && !bingoPlayed) {
                bingoPlayed = true;
                window.CR.Audio.play('bingo', {
                    volumeOverride: wakeUpVolume || 1,
                });
                window.CR.FX.onBingoPlayed();
                window.CR.Bridge.call('consume_wake_up_armed').catch(function () {});
            } else {
                // 每次翻牌 click 都播 result 音（DUR_FLIP 之后）
                window.setTimeout(function () {
                    if (token !== roundToken) {
                        return;
                    }
                    window.CR.Audio.play('result');
                }, window.CR.Cards.DUR_FLIP);
            }

            // F-17b：第一次翻牌时按 offset 替换显示的学生。
            //
            // 【时序】2026-09-24 修订（PRD TI-31）：翻牌动画走完（卡面正面可见）后
            //   先让老师看清**原始结果** DRIFT_DELAY(200ms)，然后才起闪；闪光覆盖
            //   到位（DRIFT_COVER）的那一刻才换字 —— 换字被闪光盖住，观感是
            //   "闪一下之后结果变了"。旧版在 click 瞬间就 swapFront，卡牌转到一半
            //   文字已被偷换，老师根本看不到原始结果（老师实跑反馈）。
            if (hasDrift && !driftConsumed) {
                driftConsumed = true;
                const card = dom.results.querySelector(
                    '.card[data-index="' + index + '"]',
                );
                const raw = res.students[index] || { num: '', name: '' };
                window.setTimeout(function () {
                    if (token !== roundToken) {
                        return; // 老师已开始下一轮，本轮的漂移视觉不再补放
                    }
                    window.CR.Bridge.call('apply_drift_reveal', raw)
                        .then(function (r) {
                            if (token !== roundToken || !r || !r.ok) {
                                return;
                            }
                            if (card) {
                                window.CR.Cards.driftOverlay(
                                    card,
                                    window.CR.Cards.DRIFT_FLASH,
                                    function () {
                                        window.CR.Cards.swapFront(card, r.student);
                                    },
                                );
                            }
                            // drift armed 在 reveal 后才消费 —— 通知 FX 清视觉
                            window.CR.FX.onDriftRevealed();
                        })
                        .catch(function () {
                            /* 漂移失败时静默走 raw，不影响流程 */
                        });
                }, window.CR.Cards.DUR_FLIP + window.CR.Cards.DRIFT_DELAY);
            }
        });

        window.CR.Audio.play('slide');

        // 滑出结束后恢复界面：加载图标停止、按钮可用、看板娘回到待机
        // （PRD §6.1 第 3 步）
        // 时长取自 Cards 模块，避免同一魔数在两处各写一份（NFR-M6）
        window.setTimeout(function () {
            setLoading(false);
            window.CR.Mascot.setState('idle');
            busy = false;
            setButtonsEnabled(true);
        }, window.CR.Cards.DUR_SLIDE + 20);

        // 同步 FX 状态：armed 视觉延后到第一次 reveal 真正生效才清（参见 onDriftRevealed / onBingoPlayed）
        window.CR.FX.onDrawFinished();

        if (res.message) {
            // E-02（人数不足）/ E-03（新一轮开始）/ F-17b 漂移 / F-17c 叫醒 的提示由后端拼好
            window.CR.UI.toast(res.message, 'info');
        }
    }

    /** 「简洁视图」：启动外部程序（PRD F-09 / §6.4） */
    async function launchExternal() {
        const res = await window.CR.Bridge.call('launch_external');
        if (!res.ok) {
            handleFailure(res);
        }
    }

    // ----------------------------------------------------------------------
    // 初始化
    // ----------------------------------------------------------------------

    /** 绑定主界面按钮 */
    function bindEvents() {
        dom.draw1.addEventListener('click', function () {
            window.CR.Audio.play('click');
            draw(1);
        });
        dom.draw5.addEventListener('click', function () {
            window.CR.Audio.play('click');
            draw(5);
        });

        dom.btnSettings.addEventListener('click', function () {
            window.CR.Audio.play('click');
            window.CR.Settings.open('roster');
        });
        dom.btnLite.addEventListener('click', function () {
            window.CR.Audio.play('click');
            launchExternal();
        });

        // 一键静音（PRD F-15 / AC-15④）：点一下即静音，再点恢复
        dom.muteBtn.addEventListener('click', toggleMute);

        // 首次用户交互解锁 AudioContext（浏览器自动播放策略要求）
        const unlock = function () {
            window.CR.Audio.unlock();
            document.removeEventListener('pointerdown', unlock);
        };
        document.addEventListener('pointerdown', unlock);
    }

    /** 拉取初始设置并应用到界面 */
    async function loadInitialSettings() {
        const res = await window.CR.Bridge.call('get_settings');
        if (!res.ok) {
            return;
        }
        applySettings(res.settings);
    }

    /** 拉取班级信息，用于空状态文案 */
    async function loadInitialRoster() {
        const res = await window.CR.Bridge.call('list_rosters');
        if (!res.ok) {
            return;
        }
        const current = res.current;
        if (!current) {
            rosterCount = 0;
            refreshEmptyState();
            return;
        }
        const loaded = await window.CR.Bridge.call('load_roster', current);
        rosterCount = loaded.ok ? loaded.count : 0;
        refreshEmptyState();
    }

    /** 入口 */
    async function init() {
        dom = {
            deck: document.getElementById('deck'),
            status: document.getElementById('status'),
            statusText: document.getElementById('status-text'),
            mascot: document.getElementById('mascot'),
            mascotStage: document.getElementById('mascot-stage'),
            results: document.getElementById('results'),
            draw1: document.getElementById('btn-draw-1'),
            draw5: document.getElementById('btn-draw-5'),
            btnSettings: document.getElementById('btn-settings'),
            btnLite: document.getElementById('btn-lite'),
            muteBtn: document.getElementById('btn-mute'),
            muteIcon: document.getElementById('btn-mute-icon'),
            muteLabel: document.getElementById('btn-mute-label'),
        };

        window.CR.UI.init();
        window.CR.Mascot.init(dom.mascot);
        window.CR.FX.init({
            toast: window.CR.UI.toast,
        });
        window.CR.Settings.init({
            toast: window.CR.UI.toast,
            dialog: window.CR.UI.dialog,
            onSettingsChanged: applySettings,
            onRosterChanged: function (name, count) {
                rosterCount = Number(count) || 0;
                refreshEmptyState();
                // 切换班级后一次性效果（drift/wake_up）会被后端清掉，
                // 按钮上的 armed 视觉也要立刻消
                window.CR.FX.sync();
            },
        });

        bindEvents();
        setLoading(false);
        showEmptyState(EMPTY_NO_ROSTER);

        await window.CR.Bridge.whenReady();

        if (!window.CR.Bridge.isReady()) {
            window.CR.UI.toast('未能连接到程序接口，请用 Python 启动本程序', 'error');
            return;
        }

        await loadInitialSettings();
        await loadInitialRoster();
        // 拉取三个特殊效果的当前状态，让按钮上的 armed 视觉与后端一致
        window.CR.FX.sync();
    }

    return {
        init: init,
        draw: draw,
    };
})();

// 页面结构就绪后启动
document.addEventListener('DOMContentLoaded', function () {
    window.CR.App.init();
});
