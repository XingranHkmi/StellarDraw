/**
 *  StellarDraw · 特殊效果（CR.FX）
 * ---------------------------------------------------------------------------
 * 需求：F-17a 增大概率 / F-17b 结果漂移 / F-17c 课堂叫醒服务
 *
 * 模块职责：
 *   1. 渲染右侧效果区的 3 个按钮（"1 图标 + 2 文字"）。
 *   2. 每个按钮配套一个 popover 菜单：选择 / 武装 / 确认。
 *   3. 把按钮的 armed/armed/active 状态同步到 UI。
 *   4. 暴露 hook 让主程序在「抽取完成」与「卡牌翻转完成」时同步消费。
 *
 * 设计取舍：
 *   · 菜单以「绝对定位的浮层」呈现，append 到 document.body，避开
 *     .stage 的 overflow: hidden（popover 会越过 stage 边界显示）。
 *   · 关闭菜单用「点空白处」与「再点一次按钮」两种途径，对老师来说足够直观。
 *   · 漂移与叫醒是「一次性」，UI 上抽卡后按钮回到 idle 态；增大概率持续生效，
 *     直到老师主动点 popover 里的「清空」。
 *
 * 依赖：window.CR.Bridge、window.CR.Audio、window.CR.UI（toast）
 * 暴露：window.CR.FX
 */

'use strict';

window.CR = window.CR || {};

window.CR.FX = (function () {
    /** 漂移偏移值与字面量的映射；后端只接受 1 / 3 */
    const DRIFT_PRESETS = [
        { label: '轻移 +1', offset: 1, hint: '下一个抽到的学生会变到下一位' },
        { label: '重移 +3', offset: 3, hint: '下一个抽到的学生会变到第 +3 位' },
    ];

    /** 叫醒服务的音量档（与后端 is_volume_level_allowed 同步） */
    const WAKE_UP_PRESETS = [
        { label: '大', level: 0.9, hint: '系统音量调到 90%' },
        { label: '中', level: 0.6, hint: '系统音量调到 60%' },
        { label: '小', level: 0.4, hint: '系统音量调到 40%' },
    ];

    /** 各按钮的 SVG 图标（与卡背素材同样的内联模式，方便后续替换） */
    const ICONS = {
        boost:
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
            '<path d="M12 2 14 8 20 8 15 12 17 18 12 14 7 18 9 12 4 8 10 8 Z" ' +
            'fill="currentColor" stroke="currentColor" stroke-width="0.5" ' +
            'stroke-linejoin="round"></path>' +
            '</svg>',
        drift:
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
            '<path d="M3 12 H19 M19 12 L15 8 M19 12 L15 16 M21 12 L21 18 H19 V12 Z" ' +
            'fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round"></path>' +
            '</svg>',
        wake:
            '<svg viewBox="0 0 24 24" aria-hidden="true">' +
            '<path d="M5 9 H8 L12 5 V19 L8 15 H5 Z" ' +
            'fill="currentColor"></path>' +
            '<path d="M16 9 Q19 12 16 15" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round"></path>' +
            '<path d="M18 6 Q23 12 18 18" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round"></path>' +
            '</svg>',
    };

    let dom = null;
    let toastFn = null;
    let popoverEl = null;
    let popoverCleanup = null;
    /** 后端状态缓存（避免每个按钮都打一次 API） */
    let state = {
        boost: [],
        drift_armed: false,
        drift_offset: null,
        wake_up_volume: null,
        wake_up_armed: false,
    };

    // ------------------------------------------------------------------
    // 后端同步
    // ------------------------------------------------------------------

    async function refreshState() {
        const res = await window.CR.Bridge.call('get_effect_state');
        if (res && res.ok && res.state) {
            state = res.state;
            renderButtonStates();
        }
    }

    // ------------------------------------------------------------------
    // 按钮渲染与状态
    // ------------------------------------------------------------------

    function setButtonStatus(button, text, armed) {
        const label = button.querySelector('.fx-btn__status');
        if (label) {
            label.textContent = text;
        }
        button.classList.toggle('is-armed', !!armed);
    }

    function renderButtonStates() {
        if (!dom) {
            return;
        }
        // 增大概率：有任意加权时 armed
        const boostCount = Array.isArray(state.boost) ? state.boost.length : 0;
        if (boostCount > 0) {
            setButtonStatus(dom.btnBoost, '已加权 ' + boostCount + ' 人', true);
        } else {
            setButtonStatus(dom.btnBoost, '未生效', false);
        }
        // 结果漂移
        if (state.drift_armed && state.drift_offset) {
            const sign = state.drift_offset >= 0 ? '+' : '';
            setButtonStatus(
                dom.btnDrift,
                '下一次 +' + state.drift_offset + ' 漂移',
                true,
            );
        } else {
            setButtonStatus(dom.btnDrift, '未生效', false);
        }
        // 叫醒
        if (state.wake_up_armed) {
            const pct = Math.round((state.wake_up_volume || 0) * 100);
            setButtonStatus(dom.btnWake, '下一次 bingo · ' + pct + '%', true);
        } else if (state.wake_up_volume !== null) {
            const pct = Math.round(state.wake_up_volume * 100);
            setButtonStatus(dom.btnWake, '已选 ' + pct + '%，待确认', true);
        } else {
            setButtonStatus(dom.btnWake, '未生效', false);
        }
    }

    // ------------------------------------------------------------------
    // Popover 渲染
    // ------------------------------------------------------------------

    function closePopover() {
        if (popoverEl && popoverEl.parentNode) {
            popoverEl.parentNode.removeChild(popoverEl);
        }
        popoverEl = null;
        if (typeof popoverCleanup === 'function') {
            popoverCleanup();
        }
        popoverCleanup = null;
        // 三个按钮全部去掉 is-open
        if (dom) {
            dom.btnBoost.classList.remove('is-open');
            dom.btnDrift.classList.remove('is-open');
            dom.btnWake.classList.remove('is-open');
        }
    }

    function openPopover(anchorEl, buildContent) {
        closePopover();
        const el = document.createElement('div');
        el.className = 'fx-popover';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-label', '效果选项');

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'fx-popover__close';
        close.setAttribute('aria-label', '关闭');
        close.textContent = '×';
        close.addEventListener('click', closePopover);
        el.appendChild(close);

        buildContent(el, closePopover);

        document.body.appendChild(el);
        popoverEl = el;

        // 定位：相对按钮左侧对齐，垂直顶部对齐
        const rect = anchorEl.getBoundingClientRect();
        const popRect = el.getBoundingClientRect();
        const margin = 12;
        let left = rect.right + margin;
        if (left + popRect.width > window.innerWidth - 8) {
            // 右边放不下时翻到按钮左侧
            left = rect.left - popRect.width - margin;
        }
        if (left < 8) {
            left = 8;
        }
        let top = rect.top;
        if (top + popRect.height > window.innerHeight - 8) {
            top = window.innerHeight - popRect.height - 8;
        }
        if (top < 8) {
            top = 8;
        }
        el.style.left = left + 'px';
        el.style.top = top + 'px';

        // 点击空白关闭
        const onDocClick = function (ev) {
            if (!popoverEl) {
                return;
            }
            if (popoverEl.contains(ev.target) || anchorEl.contains(ev.target)) {
                return;
            }
            closePopover();
        };
        // 用 mousedown 比 click 更早触发，避免按钮自身的 click 立刻把它关掉
        window.setTimeout(function () {
            document.addEventListener('mousedown', onDocClick);
            popoverCleanup = function () {
                document.removeEventListener('mousedown', onDocClick);
            };
        }, 0);

        anchorEl.classList.add('is-open');
    }

    // ------------------------------------------------------------------
    // 三种菜单
    // ------------------------------------------------------------------

    async function openBoostMenu(button) {
        const res = await window.CR.Bridge.call('list_students_for_boost');
        if (!res || !res.ok) {
            toastFn('加载名单失败', 'error');
            return;
        }
        const students = Array.isArray(res.students) ? res.students : [];
        const boosted = new Set(
            (Array.isArray(res.boosted) ? res.boosted : []).map(function (s) {
                return (s.num || '') + '|' + s.name;
            }),
        );
        openPopover(button, function (root) {
            const title = document.createElement('h3');
            title.className = 'fx-popover__title';
            title.textContent = '增大概率';
            root.appendChild(title);

            const hint = document.createElement('p');
            hint.className = 'fx-popover__hint';
            hint.textContent =
                '勾选若干学生，他们在后续抽取中的权重会被临时提升。再次取消勾选即解除加权。';
            root.appendChild(hint);

            const list = document.createElement('div');
            list.className = 'fx-popover__list';
            root.appendChild(list);

            if (students.length === 0) {
                const empty = document.createElement('p');
                empty.className = 'fx-popover__hint';
                empty.textContent = '名单为空，无法加权';
                root.appendChild(empty);
            }

            students.forEach(function (s) {
                const key = (s.num || '') + '|' + s.name;
                const label = document.createElement('label');
                label.className = 'fx-popover__check';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = boosted.has(key);
                cb.addEventListener('change', function () {
                    if (cb.checked) {
                        boosted.add(key);
                    } else {
                        boosted.delete(key);
                    }
                });
                const span = document.createElement('span');
                span.textContent = (s.num ? s.num + '  ' : '') + s.name;
                label.appendChild(cb);
                label.appendChild(span);
                list.appendChild(label);
            });

            const actions = document.createElement('div');
            actions.className = 'fx-popover__actions';
            const btnClear = document.createElement('button');
            btnClear.type = 'button';
            btnClear.className = 'fx-btn fx-btn--ghost';
            btnClear.textContent = '清空';
            btnClear.addEventListener('click', async function () {
                const r = await window.CR.Bridge.call('clear_boost');
                if (r && r.ok) {
                    toastFn('已清空加权', 'success');
                    closePopover();
                    refreshState();
                }
            });
            const btnSave = document.createElement('button');
            btnSave.type = 'button';
            btnSave.className = 'fx-btn fx-btn--primary';
            btnSave.textContent = '保存';
            btnSave.addEventListener('click', async function () {
                const list2 = students
                    .filter(function (s) {
                        return boosted.has((s.num || '') + '|' + s.name);
                    })
                    .map(function (s) {
                        return { num: s.num || '', name: s.name };
                    });
                const r = await window.CR.Bridge.call('set_boost', list2);
                if (r && r.ok) {
                    toastFn(
                        list2.length > 0
                            ? '已加权 ' + list2.length + ' 名学生'
                            : '已清空加权',
                        'success',
                    );
                    closePopover();
                    refreshState();
                }
            });
            actions.appendChild(btnClear);
            actions.appendChild(btnSave);
            root.appendChild(actions);
        });
    }

    async function openDriftMenu(button) {
        openPopover(button, function (root) {
            const title = document.createElement('h3');
            title.className = 'fx-popover__title';
            title.textContent = '结果漂移';
            root.appendChild(title);

            const hint = document.createElement('p');
            hint.className = 'fx-popover__hint';
            hint.textContent =
                '下一次抽取时，卡牌翻开后会偏移到指定下标。效果仅对这一次抽卡生效。';
            root.appendChild(hint);

            DRIFT_PRESETS.forEach(function (preset) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'fx-popover__option';
                btn.innerHTML =
                    '<span class="fx-popover__option-label">' +
                    preset.label +
                    '</span>' +
                    '<span class="fx-popover__option-hint">' +
                    preset.hint +
                    '</span>';
                btn.addEventListener('click', async function () {
                    const r = await window.CR.Bridge.call('arm_drift', preset.offset);
                    if (r && r.ok) {
                        toastFn(
                            '下一次抽卡将漂移 ' +
                                (preset.offset >= 0 ? '+' : '') +
                                preset.offset,
                            'success',
                        );
                        closePopover();
                        refreshState();
                    } else if (r) {
                        toastFn(r.message || '操作失败', 'error');
                    }
                });
                root.appendChild(btn);
            });

            if (state.drift_armed) {
                const cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'fx-btn fx-btn--ghost';
                cancel.textContent = '取消已武装的漂移';
                cancel.addEventListener('click', async function () {
                    const r = await window.CR.Bridge.call('cancel_drift');
                    if (r && r.ok) {
                        toastFn('已取消漂移', 'info');
                        closePopover();
                        refreshState();
                    }
                });
                root.appendChild(cancel);
            }
        });
    }

    async function openWakeUpMenu(button) {
        openPopover(button, function (root) {
            const title = document.createElement('h3');
            title.className = 'fx-popover__title';
            title.textContent = '课堂叫醒服务';
            root.appendChild(title);

            const hint = document.createElement('p');
            hint.className = 'fx-popover__hint';
            hint.textContent =
                '选择 bingo 音量档后点「确认使用」：下一次卡牌翻开的同时按所选音量播放 bingo 五连音。系统主音量由本软件**不再**调整。';
            root.appendChild(hint);

            const opts = document.createElement('div');
            opts.className = 'fx-popover__opts';
            root.appendChild(opts);

            let chosenLevel = state.wake_up_volume;
            WAKE_UP_PRESETS.forEach(function (preset) {
                const label = document.createElement('label');
                label.className = 'fx-popover__radio';
                const rb = document.createElement('input');
                rb.type = 'radio';
                rb.name = 'fx-wake-up';
                rb.value = String(preset.level);
                rb.checked = chosenLevel !== null && Math.abs(chosenLevel - preset.level) < 1e-3;
                rb.addEventListener('change', async function () {
                    if (!rb.checked) {
                        return;
                    }
                    chosenLevel = preset.level;
                    const r = await window.CR.Bridge.call(
                        'set_wake_up_volume',
                        preset.level,
                    );
                    if (r && r.ok) {
                        toastFn('已选 ' + preset.label + '（' + Math.round(preset.level * 100) + '%），请点确认使用', 'info');
                        refreshState();
                    } else if (r) {
                        toastFn(r.message || '操作失败', 'error');
                    }
                });
                const span = document.createElement('span');
                span.textContent =
                    preset.label + ' · ' + Math.round(preset.level * 100) + '%';
                label.appendChild(rb);
                label.appendChild(span);
                opts.appendChild(label);
            });

            const actions = document.createElement('div');
            actions.className = 'fx-popover__actions';
            const btnCancel = document.createElement('button');
            btnCancel.type = 'button';
            btnCancel.className = 'fx-btn fx-btn--ghost';
            btnCancel.textContent = '取消';
            btnCancel.addEventListener('click', async function () {
                const r = await window.CR.Bridge.call('cancel_wake_up');
                if (r && r.ok) {
                    toastFn('已取消叫醒', 'info');
                    closePopover();
                    refreshState();
                }
            });
            const btnConfirm = document.createElement('button');
            btnConfirm.type = 'button';
            btnConfirm.className = 'fx-btn fx-btn--primary';
            btnConfirm.textContent = '确认使用';
            btnConfirm.addEventListener('click', async function () {
                if (chosenLevel === null || chosenLevel === undefined) {
                    toastFn('请先选择音量档', 'info');
                    return;
                }
                const r = await window.CR.Bridge.call('confirm_wake_up');
                if (r && r.ok) {
                    toastFn(
                        '已装备 bingo（音量 ' +
                            Math.round(chosenLevel * 100) +
                            '%）；下一次卡牌翻开即按此音量播 bingo',
                        'success',
                    );
                    closePopover();
                    refreshState();
                } else if (r) {
                    toastFn(r.message || '操作失败', 'error');
                }
            });
            actions.appendChild(btnCancel);
            actions.appendChild(btnConfirm);
            root.appendChild(actions);
        });
    }

    // ------------------------------------------------------------------
    // 公开 API
    // ------------------------------------------------------------------

    function init(options) {
        toastFn = (options && options.toast) || function () {};
        dom = {
            bar: document.getElementById('fx-bar'),
            btnBoost: document.getElementById('fx-boost'),
            btnDrift: document.getElementById('fx-drift'),
            btnWake: document.getElementById('fx-wake'),
        };

        // 注入 SVG 图标（用 innerHTML 比 createElement + setAttribute 更省字）
        if (dom.btnBoost) {
            dom.btnBoost
                .querySelector('.fx-btn__icon')
                .insertAdjacentHTML('beforeend', ICONS.boost);
        }
        if (dom.btnDrift) {
            dom.btnDrift
                .querySelector('.fx-btn__icon')
                .insertAdjacentHTML('beforeend', ICONS.drift);
        }
        if (dom.btnWake) {
            dom.btnWake
                .querySelector('.fx-btn__icon')
                .insertAdjacentHTML('beforeend', ICONS.wake);
        }

        // 事件：点哪个按钮开哪个菜单
        if (dom.btnBoost) {
            dom.btnBoost.addEventListener('click', function () {
                window.CR.Audio.play('click');
                if (dom.btnBoost.classList.contains('is-open')) {
                    closePopover();
                } else {
                    openBoostMenu(dom.btnBoost);
                }
            });
        }
        if (dom.btnDrift) {
            dom.btnDrift.addEventListener('click', function () {
                window.CR.Audio.play('click');
                if (dom.btnDrift.classList.contains('is-open')) {
                    closePopover();
                } else {
                    openDriftMenu(dom.btnDrift);
                }
            });
        }
        if (dom.btnWake) {
            dom.btnWake.addEventListener('click', function () {
                window.CR.Audio.play('click');
                if (dom.btnWake.classList.contains('is-open')) {
                    closePopover();
                } else {
                    openWakeUpMenu(dom.btnWake);
                }
            });
        }

        renderButtonStates();
    }

    /** 主程序在初始化完成后调一次，把当前状态同步进来 */
    async function sync() {
        await refreshState();
    }

    /** 主程序在「抽取完成后」调一次：保留 armed 视觉，因为单次性效果现在改在
     *  reveal 阶段才真正"消费"（drift 替换学生 / wake_up 播 bingo）。
     *  按 2026-09-13 第三轮修订，drift 与 wake_up 都在第一次翻牌 click 时立即
     *  生效，因此 armed 视觉必须保留到 reveal 真的发生。 */
    function onDrawFinished(outcome) {
        if (!outcome) {
            return;
        }
        // 这里只是占位 / 日志点 —— 当前不再主动清任何 armed，
        // 真正的清理由 onDriftRevealed / onBingoPlayed 各自负责。
        return outcome;
    }

    /** 主程序在第一次翻牌完成漂移替换后调一次：清掉 drift 的 armed 视觉。 */
    function onDriftRevealed() {
        state.drift_armed = false;
        state.drift_offset = null;
        renderButtonStates();
    }

    /** bingo 播完后调用，清掉 armed 视觉 */
    function onBingoPlayed() {
        state.wake_up_armed = false;
        renderButtonStates();
    }

    return {
        init: init,
        sync: sync,
        onDrawFinished: onDrawFinished,
        onDriftRevealed: onDriftRevealed,
        onBingoPlayed: onBingoPlayed,
        closePopover: closePopover,
    };
})();