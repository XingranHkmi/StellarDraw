/**
 * StellarDraw · 音效合成（Web Audio API）
 * ---------------------------------------------------------------------------
 * 需求：PRD F-15 / AC-15；音效参数见 PRD 附录 A
 *
 * 设计要点：
 *   1. **零素材依赖**：四段提示音全部用振荡器实时合成，不加载任何音频文件。
 *   2. **延迟创建 AudioContext**：浏览器自动播放策略要求 AudioContext 必须在
 *      用户交互之后才能出声，因此首次播放时才创建并 resume。
 *   3. **静音时完全不碰音频设备**：muted 为真时直接返回，连 AudioContext 都不创建，
 *      既省资源，也避免在「考试巡视」等场景下唤醒扬声器。
 *   4. **无声卡不报错**：环境不支持或创建失败时静默降级为「无声」，
 *      绝不让音效问题影响抽取（PRD NFR-C6）。
 *   5. **每段音效都做淡入淡出包络**，避免出现爆音（PRD 附录 A 明确要求）。
 *
 * 依赖：无
 * 暴露：window.CR.Audio
 */

'use strict';

window.CR = window.CR || {};

window.CR.Audio = (function () {
    /** 淡入时长（秒） */
    const ATTACK = 0.008;

    /** 包络收尾（秒）：指数衰减不能到 0，用一个极小值代替 */
    const SILENCE = 0.0001;

    /**
     * 音效的合成配方（PRD 附录 A「音效合成参考参数」）：
     *   click   按钮点击    短促方波 ≈880Hz            60ms
     *   slide   卡牌滑出    轻微扫频（用三角波代替白噪声） 200ms
     *   result  出结果      三音和弦上行（C5-E5-G5）    400ms
     *   bingo   课堂叫醒    五音上行（C5-E5-G5-B5-D6）   750ms
     *
     * 【关于 flip】配方仍然保留，但**当前没有任何地方调用它**：
     * 翻牌与出结果之间只隔 600ms，翻牌音与出结果音听感上重复，老师已要求移除。
     * 之所以不删配方，是因为它属于 P0 已验收的音频资产（PRD 附录 A 的原始参数），
     * 删除编号/资产不符合项目「已分配内容永久保留」的约定；想恢复只需在 app.js
     * 的 reveal 回调里补一行 play('flip')。
     *
     * 【关于 bingo】按 F-17c 设计：5 个音依次上行（C5-E5-G5-B5-D6），每音 130ms，
     * 总长 ≈ 650ms + 收尾 100ms。配方写为「和弦」形式（5 个 freq 同时列），
     * playChord 内部按 index * 0.13s 错开起点，自然形成上行听感。
     */
    const RECIPES = {
        click: { shape: 'square', from: 880, to: 880, dur: 0.06, gain: 0.16 },
        slide: { shape: 'triangle', from: 300, to: 640, dur: 0.20, gain: 0.12 },
        flip: { shape: 'triangle', from: 660, to: 1320, dur: 0.18, gain: 0.18 },
        result: { chord: [523.25, 659.25, 783.99], dur: 0.40, gain: 0.13 },
        // bingo：5 个音串成上行；playChord 会按 index * 0.13s 错开起点
        bingo: {
            chord: [523.25, 659.25, 783.99, 987.77, 1174.66],
            dur: 0.18,
            stagger: 0.13,
            gain: 0.18,
        },
    };

    /** AudioContext 实例（惰性创建） */
    let ctx = null;

    /** 是否静音；与配置项 muted 同义（true = 静音） */
    let muted = false;

    /** 音量 0.0 ~ 1.0 */
    let volume = 0.7;

    /**
     * 惰性创建 AudioContext。
     * @returns {AudioContext|null} 环境不支持时返回 null
     */
    function ensureContext() {
        if (ctx) {
            return ctx;
        }
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) {
            return null; // 环境不支持 Web Audio：静默降级
        }
        try {
            ctx = new Ctor();
        } catch (err) {
            console.warn('[Audio] 无法创建 AudioContext：', err);
            ctx = null;
        }
        return ctx;
    }

    /**
     * 在用户交互后解锁音频（浏览器自动播放策略）。
     */
    function unlock() {
        const c = ensureContext();
        if (c && c.state === 'suspended' && typeof c.resume === 'function') {
            c.resume().catch(function () {
                /* 解锁失败不影响功能，静音即可 */
            });
        }
    }

    /**
     * 播放一段扫频音（用于 click / slide / flip）。
     *
     * @param {AudioContext} c 音频上下文
     * @param {{shape: string, from: number, to: number, dur: number, gain: number}} r 配方
     * @param {number} [effectiveVolume] 本次播放的目标音量（已经合并 base × override），
     *        缺省时回退到模块当前 volume
     */
    function playSweep(c, r, effectiveVolume) {
        const t0 = c.currentTime;
        const v = Number.isFinite(effectiveVolume) ? effectiveVolume : volume;
        const peak = Math.max(0, r.gain * v);
        if (peak <= 0) {
            return;
        }

        const osc = c.createOscillator();
        const amp = c.createGain();

        osc.type = r.shape;
        osc.frequency.setValueAtTime(r.from, t0);
        if (r.to !== r.from) {
            osc.frequency.linearRampToValueAtTime(r.to, t0 + r.dur);
        }

        // 淡入 → 指数衰减，形成干净的包络
        amp.gain.setValueAtTime(0, t0);
        amp.gain.linearRampToValueAtTime(peak, t0 + ATTACK);
        amp.gain.exponentialRampToValueAtTime(SILENCE, t0 + r.dur);

        osc.connect(amp);
        amp.connect(c.destination);
        osc.start(t0);
        osc.stop(t0 + r.dur + 0.02);
    }

    /**
     * 播放和弦（用于出结果 / bingo）。
     *
     * @param {AudioContext} c 音频上下文
     * @param {{chord: number[], dur: number, gain: number, stagger?: number}} r 配方
     * @param {number} [effectiveVolume] 同 playSweep
     */
    function playChord(c, r, effectiveVolume) {
        const t0 = c.currentTime;
        const v = Number.isFinite(effectiveVolume) ? effectiveVolume : volume;
        const peak = Math.max(0, r.gain * v);
        if (peak <= 0) {
            return;
        }

        // 各音之间的错开：默认 40ms（出结果）；bingo 用 130ms 让 5 个音明显可辨
        const stagger = typeof r.stagger === 'number' ? r.stagger : 0.04;

        r.chord.forEach(function (freq, index) {
            const start = t0 + index * stagger;
            const end = start + r.dur;

            const osc = c.createOscillator();
            const amp = c.createGain();

            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, start);

            amp.gain.setValueAtTime(0, start);
            amp.gain.linearRampToValueAtTime(peak, start + ATTACK);
            amp.gain.exponentialRampToValueAtTime(SILENCE, end);

            osc.connect(amp);
            amp.connect(c.destination);
            osc.start(start);
            osc.stop(end + 0.02);
        });
    }

    /**
     * 播放指定音效。
     *
     * @param {'click'|'slide'|'flip'|'result'|'bingo'} name 音效名
     * @param {{volumeOverride?: number}} [options] 临时音量乘数（0.0~1.0），仅本次生效，
     *        不修改模块内部的 base volume；主要用于 F-17c「课堂叫醒服务」让 bingo
     *        按所选档位（0.4 / 0.6 / 0.9）响一点。
     */
    function play(name, options) {
        if (muted) {
            return; // 静音时连上下文都不创建（NFR-C6 友好）
        }
        const recipe = RECIPES[name];
        if (!recipe) {
            return;
        }
        const c = ensureContext();
        if (!c) {
            return;
        }
        unlock();

        // 本次实际峰值：base volume × 临时乘数（若提供）
        const override =
            options && Number.isFinite(options.volumeOverride)
                ? Math.max(0, Math.min(1, options.volumeOverride))
                : 1;
        const effectiveVolume = Math.max(0, Math.min(1, volume * override));

        try {
            if (recipe.chord) {
                playChord(c, recipe, effectiveVolume);
            } else {
                playSweep(c, recipe, effectiveVolume);
            }
        } catch (err) {
            // 无音频设备等异常一律吞掉：音效绝不能让抽取流程失败
            console.warn('[Audio] 播放 ' + name + ' 失败：', err);
        }
    }

    /**
     * 设置静音状态。
     * @param {boolean} value true = 静音
     */
    function setMuted(value) {
        muted = Boolean(value);
        if (!muted) {
            // 取消静音时顺手解锁，避免老师点了按钮却没声音
            unlock();
        }
    }

    /**
     * 设置音量。
     *
     * 音量在每次播放时参与峰值计算，因此这里只更新数值即可，
     * 不需要（也不应该）去改已创建的节点 —— 改节点反而容易引入爆音。
     *
     * @param {number} value 0.0 ~ 1.0
     */
    function setVolume(value) {
        const next = Number(value);
        volume = Number.isFinite(next) ? Math.min(1, Math.max(0, next)) : 0.7;
    }

    /** @returns {boolean} 当前是否静音 */
    function isMuted() {
        return muted;
    }

    return {
        play: play,
        unlock: unlock,
        setMuted: setMuted,
        setVolume: setVolume,
        isMuted: isMuted,
    };
})();
