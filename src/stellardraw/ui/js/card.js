/**
 * StellarDraw · 卡牌渲染与动画
 * ---------------------------------------------------------------------------
 * 需求：PRD F-06 / F-07 / F-08 / F-12 / F-13
 *      验收：AC-05（滑出 + 点击翻转）、AC-06（5 张并排、点哪张翻哪张）、
 *            AC-07（学号/姓名两行、字号下限、学号为空时姓名垂直居中）
 *
 * 分层结构（务必保持，否则两层动画会互相覆盖）：
 *   .card         外层 → 只负责「滑出」的 translate + scale + opacity
 *   .card__inner  内层 → 只负责「翻转」的 rotateY
 *   .card__face   卡面 → backface-visibility: hidden，正反两面重叠
 *
 * 性能纪律（PRD NFR-P6 / P8 / P10 / NFR-P14）：
 *   · 只改 transform 与 opacity
 *   · will-change 仅在动画期间挂上，结束立即移除
 *   · 5 张卡同时滑出 = 5 个并发动画元素，未超过「单帧 ≤ 8」的上限
 *
 * 依赖：window.CR.Audio（可选，缺失时静默无声）
 * 暴露：window.CR.Cards
 */

'use strict';

window.CR = window.CR || {};

window.CR.Cards = (function () {
    /** 与 CSS 中的 --dur-slide / --dur-flip / --dur-fade 保持一致（PRD NFR-M6 要求收敛魔数） */
    const DUR_SLIDE = 400;
    const DUR_FLIP = 600;
    const DUR_FADE = 200;

    /**
     * 结果漂移（F-17b）的时序常量，集中定义避免魔数散落（NFR-M6）。
     *
     * 完整时序（2026-09-24 修订，见 PRD TI-31）：
     *   点击卡背 → 翻转 DUR_FLIP(600ms) → **正面原样显示 DRIFT_DELAY(200ms)**
     *   → 覆盖层闪入 DRIFT_COVER(220ms) → 此刻换字（被闪光遮住）
     *   → 停留至 DRIFT_FLASH(400ms) → 淡出 180ms → 呈现偏移结果
     */
    const DRIFT_DELAY = 200;
    const DRIFT_FLASH = 400;
    const DRIFT_COVER = 220;

    /** 退场卡牌移出 DOM 的延迟（略大于 transform 过渡时长） */
    const GHOST_LIFETIME = 460;

    /** 滑出动画的最小缩放（从牌堆飞出时不应缩成一个点） */
    const MIN_SCALE = 0.18;

    /** 单张卡与 5 张卡各自的修饰符与并发上限 */
    const VARIANT_SOLO = 'solo';
    const VARIANT_MULTI = 'multi';

    /**
     * 创建一张卡牌的 DOM。
     *
     * @param {{num: string, name: string}} student 学生记录
     * @param {string} variant 'solo' | 'multi'
     * @param {number} index 在本次结果中的序号
     * @returns {HTMLElement}
     */
    function createCard(student, variant, index) {
        const card = document.createElement('div');
        card.className = 'card card--' + variant;
        // 用 data-* 而非 id：动态元素不产生 id，便于静态契约测试校验
        card.dataset.index = String(index);

        const inner = document.createElement('div');
        inner.className = 'card__inner';

        // --- 卡背：可点击的翻牌入口 ---
        const back = document.createElement('div');
        back.className = 'card__face card__face--back';
        back.setAttribute('role', 'button');
        back.setAttribute('tabindex', '0');
        back.setAttribute('aria-label', '点击翻开卡牌');

        const hint = document.createElement('span');
        hint.className = 'card__hint';
        hint.textContent = '点击翻开';
        back.appendChild(hint);

        // --- 卡面：学号（上行）+ 姓名（下行） ---
        const front = document.createElement('div');
        front.className = 'card__face card__face--front';

        const num = String(student && student.num ? student.num : '').trim();
        if (num) {
            // PRD AC-07⑤：学号为空时**不渲染**该行，姓名自然垂直居中
            const numEl = document.createElement('span');
            numEl.className = 'card__num';
            numEl.textContent = num;
            front.appendChild(numEl);
        }

        const nameEl = document.createElement('span');
        nameEl.className = 'card__name';
        nameEl.textContent = String(student && student.name ? student.name : '');
        front.appendChild(nameEl);

        inner.appendChild(back);

        // --- 侧棱：只在翻转中途显形，制造「卡牌有厚度」的观感（PRD F-13 / AC-13） ---
        // 它靠 CSS 里静态的 rotateY(90deg) 实现，不需要任何动画或 JS 参与，
        // 详见 card.css 中 .card__edge 的说明。
        const edge = document.createElement('span');
        edge.className = 'card__edge';
        edge.setAttribute('aria-hidden', 'true');
        inner.appendChild(edge);

        inner.appendChild(front);
        card.appendChild(inner);

        return card;
    }

    /**
     * 让一张卡从牌堆位置滑到它的最终位置。
     *
     * 做法：先把 transform 设成「牌堆位置 → 目标位置」的差值（同时关掉过渡，
     * 避免这一帧被动画化），强制一次回流固定住起点，再移除过渡类并清空
     * inline transform —— 浏览器便会从起点补间到终点。
     *
     * @param {HTMLElement} card 目标卡牌
     * @param {DOMRect} originRect 牌堆的包围盒（动画起点）
     */
    function slideIn(card, originRect) {
        const target = card.getBoundingClientRect();
        if (!originRect || !target.width || !target.height) {
            return; // 无法测量（例如容器隐藏）时直接显示在终点位置
        }

        const dx = (originRect.left + originRect.width / 2) - (target.left + target.width / 2);
        const dy = (originRect.top + originRect.height / 2) - (target.top + target.height / 2);
        const scale = Math.min(1, Math.max(MIN_SCALE, originRect.width / target.width));

        card.classList.add('card--entering');
        card.style.transform = 'translate(' + dx + 'px, ' + dy + 'px) scale(' + scale + ')';
        card.style.opacity = '0.35';

        // 强制回流：确保「起点」这一帧确实被浏览器记录，否则不会产生补间
        void card.offsetWidth;

        card.classList.remove('card--entering');
        card.style.willChange = 'transform, opacity';
        card.style.transform = '';
        card.style.opacity = '';

        // 动画结束立刻摘掉 will-change，避免长期占用合成层（NFR-P8）
        const finish = function (event) {
            if (event && event.target !== card) {
                return; // 忽略子元素冒泡上来的 transitionend
            }
            card.style.willChange = '';
            card.removeEventListener('transitionend', finish);
        };
        card.addEventListener('transitionend', finish);
        // 兜底：过渡被打断时 transitionend 不会触发
        window.setTimeout(function () {
            card.style.willChange = '';
        }, DUR_SLIDE + DUR_FADE + 80);
    }

    /**
     * 翻开一张卡。
     *
     * @param {HTMLElement} card 卡牌元素
     * @returns {boolean} 本次是否真的产生了翻转（重复点击返回 false）
     */
    function flip(card) {
        if (card.classList.contains('card--flipped')) {
            return false;
        }
        card.classList.add('card--flipped');
        card.querySelectorAll('.card__face--back').forEach(function (face) {
            face.setAttribute('aria-label', '已翻开');
        });
        return true;
    }

    /**
     * 清空结果区里上一轮的卡牌：原地淡出后移除。
     *
     * 为让淡出动画与「新卡立刻进入布局」同时成立，旧卡会被搬进一个
     * 绝对定位的幽灵层（卡片位置用测量值锁定，视觉上不跳动）。
     *
     * @param {HTMLElement} container 结果展示区
     */
    function clear(container) {
        const existing = Array.prototype.slice.call(container.querySelectorAll('.card'));
        if (!existing.length) {
            return;
        }

        const base = container.getBoundingClientRect();
        const layer = document.createElement('div');
        layer.className = 'card-ghost-layer';

        existing.forEach(function (card) {
            const rect = card.getBoundingClientRect();
            // 用测量值把卡片"钉"在原位，再脱离文档流
            card.style.position = 'absolute';
            card.style.left = (rect.left - base.left) + 'px';
            card.style.top = (rect.top - base.top) + 'px';
            card.style.width = rect.width + 'px';
            card.style.height = rect.height + 'px';
            card.style.margin = '0';
            card.style.willChange = '';
            card.classList.add('card--leaving');
            layer.appendChild(card);
        });

        container.appendChild(layer);
        window.setTimeout(function () {
            layer.remove();
        }, GHOST_LIFETIME);
    }

    /**
     * 渲染本轮结果。
     *
     * @param {HTMLElement} container 结果展示区
     * @param {Array<{num: string, name: string}>} students 本轮抽中的学生
     * @param {DOMRect|null} originRect 动画起点（牌堆包围盒）
     * @param {function(number): void} onReveal 某张卡被翻开时的回调，参数为序号
     * @returns {number} 渲染出的卡牌数量
     */
    function render(container, students, originRect, onReveal) {
        const list = Array.isArray(students) ? students : [];
        if (!list.length) {
            return 0;
        }

        const variant = list.length > 1 ? VARIANT_MULTI : VARIANT_SOLO;

        list.forEach(function (student, index) {
            const card = createCard(student, variant, index);
            container.appendChild(card);

            // 点击 / 回车 / 空格都能翻牌（触摸屏与键盘都可用，NFR-C3）
            const back = card.querySelector('.card__face--back');
            const reveal = function (event) {
                event.stopPropagation();
                if (flip(card) && typeof onReveal === 'function') {
                    onReveal(index);
                }
            };
            back.addEventListener('click', reveal);
            back.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    reveal(event);
                }
            });

            slideIn(card, originRect);
        });

        return list.length;
    }

    /**
     * F-17b 结果漂移：在卡片正面替换为「目标学生」的内容。
     *
     * 用法：抽卡时拿到的是漂移前的学生，界面先按这些学生正常渲染；
     * 翻牌回调里拿到 onReveal(index) 时，前端告知是漂移的结果（drift_applied=true）
     * 并把最终展示的下标传进来；本函数把对应那张卡的正面换成最终学生的信息。
     *
     * 不改 DOM、不重渲染——直接复用卡面上已有的 .card__num / .card__name 元素，
     * 只换文本。这样既避免重新测量动画位置，也能保留翻牌前的「神秘感」。
     *
     * @param {HTMLElement} card 卡牌元素
     * @param {{num: string, name: string}} student 最终要展示的学生
     */
    function swapFront(card, student) {
        const front = card.querySelector('.card__face--front');
        if (!front) {
            return;
        }
        const num = String((student && student.num) || '').trim();
        const name = String((student && student.name) || '').trim();

        let numEl = front.querySelector('.card__num');
        if (num) {
            if (!numEl) {
                numEl = document.createElement('span');
                numEl.className = 'card__num';
                // 插入到 name 之前，保持「学号在上、姓名在下」的顺序
                const nameEl = front.querySelector('.card__name');
                front.insertBefore(numEl, nameEl);
            }
            numEl.textContent = num;
        } else if (numEl) {
            // 学号原本存在但漂移目标没有学号 → 移除元素，姓名行垂直居中（AC-07⑤）
            numEl.remove();
        }

        let nameEl = front.querySelector('.card__name');
        if (!nameEl) {
            nameEl = document.createElement('span');
            nameEl.className = 'card__name';
            front.appendChild(nameEl);
        }
        nameEl.textContent = name;
    }

    /**
     * F-17b 漂移覆盖层：在卡牌正面之上临时盖一层半透明闪光，
     * 让「翻牌 → 看清原结果 → 闪一下 → 变成另一个人」有过渡感。
     * 覆盖层会在动画结束后自动移除。
     *
     * 性能：纯 transform + opacity，不引入新合成层（PRD NFR-P6）。
     *
     * @param {HTMLElement} card 卡牌元素
     * @param {number} duration 覆盖层停留时长（毫秒），默认 320ms
     * @param {function(): void} [onCovered] 覆盖层**完全不透明**（DRIFT_COVER 之后）时
     *        回调一次；调用方应在此刻替换卡面文本 —— 换字动作被闪光盖住，
     *        老师看到的是"闪一下之后结果变了"，而不是生硬地突然变字。
     * @returns {Promise<void>} 覆盖层移除后 resolve
     */
    function driftOverlay(card, duration, onCovered) {
        const dur = typeof duration === 'number' ? duration : 320;
        return new Promise(function (resolve) {
            if (!card) {
                resolve();
                return;
            }
            const overlay = document.createElement('div');
            overlay.className = 'card__drift-overlay';
            overlay.setAttribute('aria-hidden', 'true');
            card.appendChild(overlay);
            // 强制回流，让浏览器把起始态落地后再添加 is-on
            void overlay.offsetWidth;
            overlay.classList.add('is-on');

            // 闪光覆盖到位后才换字（换字时机与 CSS 的 opacity 过渡时长绑定）
            if (typeof onCovered === 'function') {
                window.setTimeout(onCovered, DRIFT_COVER);
            }

            window.setTimeout(function () {
                overlay.classList.add('is-out');
                window.setTimeout(function () {
                    if (overlay.parentNode) {
                        overlay.parentNode.removeChild(overlay);
                    }
                    resolve();
                }, 180);
            }, dur);
        });
    }

    return {
        render: render,
        clear: clear,
        flip: flip,
        swapFront: swapFront,
        driftOverlay: driftOverlay,
        // 供上层复用同一套时长常量，避免各写一份魔数（NFR-M6）
        DUR_SLIDE: DUR_SLIDE,
        DUR_FLIP: DUR_FLIP,
        DRIFT_DELAY: DRIFT_DELAY,
        DRIFT_FLASH: DRIFT_FLASH,
    };
})();
