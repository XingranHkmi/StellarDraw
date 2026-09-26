/**
 * StellarDraw · JS Bridge 封装
 * ---------------------------------------------------------------------------
 * 职责：把 pywebview 注入的 window.pywebview.api 包装成「永不抛异常」的调用接口。
 *
 * 为什么必须封装：
 *   1. pywebview 在页面加载后才注入 window.pywebview，直接调用会拿到 undefined；
 *   2. 调用 Python 方法返回的是 Promise，一旦 Python 侧异常，前端只会收到一个
 *      undefined 或 rejected Promise —— 界面无从提示；
 *   3. api.py 已约定所有方法返回 {"ok": bool, ...} 信封，前端只需统一处理。
 *
 * 【模块加载方式说明】
 *   本文件不是 ES Module。pywebview 通过 file:// 加载页面，Chromium 对
 *   file:// 下的 <script type="module"> 会按 CORS 失败处理而拒绝执行，
 *   因此全部前端脚本使用经典 script，并通过 window.CR 命名空间通信。
 *
 * 依赖：无（必须最先加载）
 * 暴露：window.CR.Bridge
 */

'use strict';

window.CR = window.CR || {};

window.CR.Bridge = (function () {
    /**
     * 等待桥接就绪的超时时间（毫秒）。
     * 用普通浏览器直接打开页面时不会触发 pywebviewready，
     * 超时后仍要放行，让上层给出「未检测到程序接口」的明确提示，
     * 而不是让所有按钮永远卡在等待状态。
     */
    const READY_TIMEOUT = 3000;

    /** 就绪 Promise（只创建一次，重复调用复用同一个） */
    let readyPromise = null;

    /**
     * 取出 pywebview 注入的 API 对象。
     * @returns {object|null} 未就绪时返回 null
     */
    function getApi() {
        const pw = window.pywebview;
        return pw && pw.api ? pw.api : null;
    }

    /**
     * 返回一个在桥接就绪后 resolve 的 Promise。
     * @returns {Promise<void>}
     */
    function whenReady() {
        if (!readyPromise) {
            readyPromise = new Promise(function (resolve) {
                if (getApi()) {
                    resolve();
                    return;
                }
                // pywebview 注入完成后会派发该事件
                window.addEventListener('pywebviewready', function () {
                    resolve();
                }, { once: true });
                // 兜底：普通浏览器环境下不会有该事件
                window.setTimeout(resolve, READY_TIMEOUT);
            });
        }
        return readyPromise;
    }

    /**
     * 调用 Python 侧的公开方法。
     *
     * 无论发生什么（桥接未就绪、方法不存在、Python 抛异常），
     * 都返回统一信封而**不抛异常**，调用方只需判断 res.ok。
     *
     * @param {string} method Api 类的公开方法名
     * @param {...*} args 位置参数
     * @returns {Promise<{ok: boolean, code?: string, message?: string}>}
     */
    async function call(method) {
        const args = Array.prototype.slice.call(arguments, 1);

        await whenReady();

        const api = getApi();
        if (!api || typeof api[method] !== 'function') {
            // 契约测试会保证 JS 调用的方法名一定存在于 api.py；
            // 这里兜住的是「桥接整体没起来」的情况
            return {
                ok: false,
                code: 'bridge_missing',
                message: '未能连接到程序接口，请重启软件后重试',
            };
        }

        try {
            const res = await api[method].apply(api, args);

            // api.py 约定：成功 {"ok":true,...}，失败 {"ok":false,"code":...,"message":...}
            if (res && typeof res === 'object' && 'ok' in res) {
                return res;
            }
            // 防御：万一某个方法没按信封返回，归一化成成功信封
            return { ok: true, value: res };
        } catch (err) {
            console.error('[Bridge] 调用 ' + method + ' 失败：', err);
            return {
                ok: false,
                code: 'bridge_error',
                message: '调用失败：' + (err && err.message ? err.message : String(err)),
            };
        }
    }

    /**
     * 桥接是否已经可用（同步判断，供初始化时决定是否显示"未连接"提示）。
     * @returns {boolean}
     */
    function isReady() {
        return getApi() !== null;
    }

    return {
        whenReady: whenReady,
        isReady: isReady,
        call: call,
    };
})();
