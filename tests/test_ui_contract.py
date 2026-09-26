"""前端静态契约测试。

对应需求：PRD NFR-M3 / NFR-M5 / NFR-P6 / NFR-P7 / NFR-P9 / NFR-S1 / AC-07 / AC-09

【为什么用「静态契约」而不是浏览器测试】
    PRD NFR-M5 明令零前端构建工具（不用 npm / webpack / vite），因此装不了
    jsdom 之类的 DOM 测试环境；而 pywebview 界面测试又需要真实窗口，无法在
    pytest 里稳定跑。折中方案是把**能用文本验证的契约**全部自动化，它能拦住
    前端开发中最高频的几类事故：

        1. 改了 HTML 的 id，忘了改 JS —— 界面某块静默失效
        2. JS 调了一个 Python 侧不存在的方法 —— 按钮点了没反应
        3. 引用了不存在的资源文件 —— 4K 下图片空白
        4. 不小心引入了外网请求 —— 违反 NFR-S1「完全离线」
        5. 给 box-shadow / filter 加了动画 —— 4K 无独显机器直接掉帧
        6. 改了 JS 文件名忘了改 index.html —— 脚本静默不加载

    这些检查跑起来只要几毫秒，却比人工点一遍更可靠。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from stellardraw import paths

# 前端资源根目录（走 paths 解析，保证与运行时看到的是同一份文件）
UI_DIR = paths.resource_path()
INDEX_HTML = paths.resource_path("index.html")
CSS_DIR = paths.resource_path("css")
JS_DIR = paths.resource_path("js")

# 五个前端脚本必须按此顺序加载（后者依赖前者定义的 window.CR.* 命名空间）
EXPECTED_SCRIPT_ORDER = ["bridge.js", "audio.js", "card.js", "settings.js", "fx.js", "app.js"]

# 允许出现在 transition 里的属性（PRD NFR-P6：只允许 transform 与 opacity）
ALLOWED_TRANSITION_PROPS = ("transform", "opacity")

# 被禁止动画化的属性（PRD NFR-P7 / NFR-P9）
FORBIDDEN_ANIMATED_PROPS = ("box-shadow", "border-radius", "filter", "blur")


# ---------------------------------------------------------------------------
# 读取工具
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    """按 UTF-8 读取文本文件。"""
    return path.read_text(encoding="utf-8")


def _read_css(path: Path) -> str:
    """读取 CSS 并剔除注释。

    性能约束的检查必须只看**生效的声明**：本项目在注释里大量引用了
    PRD 的禁用项原文（例如"禁止 filter: blur()"），若不剔注释就会误报。
    """
    return re.sub(r"/\*.*?\*/", "", _read(path), flags=re.DOTALL)


def _js_files() -> list[Path]:
    """全部前端脚本文件。"""
    return sorted(JS_DIR.glob("*.js"))


def _css_files() -> list[Path]:
    """全部前端样式文件。"""
    return sorted(CSS_DIR.glob("*.css"))


def _api_public_methods() -> set[str]:
    """从 api.py 中解析出 Api 类**公开方法**的名字集合。

    用 AST 而不是正则：只有真正定义在 Api 类里、且不以单下划线开头的方法
    才会被 pywebview 暴露给前端，这正是前端能调用的全集。
    """
    source = _read(Path(paths.__file__).resolve().parent / "api.py")
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Api":
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
    raise AssertionError("在 api.py 中未找到 Api 类")  # pragma: no cover —— 结构性保护


def _js_called_api_methods() -> dict[str, set[str]]:
    """扫描全部前端脚本，收集它们调用的 Python 方法名。

    返回：
        {文件名: {方法名, ...}}
    """
    pattern = re.compile(r"""Bridge\.call\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
    result: dict[str, set[str]] = {}
    for path in _js_files():
        found = set(pattern.findall(_read(path)))
        if found:
            result[path.name] = found
    return result


def _js_referenced_ids() -> dict[str, set[str]]:
    """扫描全部前端脚本，收集 getElementById 引用的 id（按文件分组）。"""
    pattern = re.compile(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]""")
    result: dict[str, set[str]] = {}
    for path in _js_files():
        found = set(pattern.findall(_read(path)))
        if found:
            result[path.name] = found
    return result


# ---------------------------------------------------------------------------
# 1. 文件引用完整性
# ---------------------------------------------------------------------------


class TestAssetReferences:
    """HTML / CSS 引用的文件必须真实存在。"""

    def test_stylesheets_exist(self) -> None:
        """<link href> 指向的 CSS 都要存在。"""
        html = _read(INDEX_HTML)
        hrefs = re.findall(r"""<link[^>]+href=["']([^"']+)["']""", html)
        assert hrefs, "index.html 没有引用任何样式表"

        for href in hrefs:
            assert not href.startswith(("http://", "https://")), f"不允许引用外部样式：{href}"
            assert (UI_DIR / href).is_file(), f"样式文件缺失：{href}"

    def test_scripts_exist(self) -> None:
        """<script src> 指向的 JS 都要存在。"""
        html = _read(INDEX_HTML)
        srcs = re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html)
        assert srcs, "index.html 没有引用任何脚本"

        for src in srcs:
            assert not src.startswith(("http://", "https://")), f"不允许引用外部脚本：{src}"
            assert (UI_DIR / src).is_file(), f"脚本文件缺失：{src}"

    def test_script_load_order(self) -> None:
        """脚本必须按依赖顺序加载（bridge 最先，app 最后）。

        全部脚本是经典 script、通过 window.CR 命名空间通信，
        顺序错了会直接抛「Cannot read properties of undefined」。
        """
        html = _read(INDEX_HTML)
        srcs = re.findall(r"""<script[^>]+src=["']js/([^"']+)["']""", html)
        assert srcs == EXPECTED_SCRIPT_ORDER, f"脚本顺序不符，实际为 {srcs}"

    def test_every_css_and_js_file_is_loaded(self) -> None:
        """目录里的 css / js 文件都必须被 index.html 引用，避免孤儿文件。"""
        html = _read(INDEX_HTML)

        for css in _css_files():
            assert f"css/{css.name}" in html, f"{css.name} 没有被 index.html 引用"

        for js in _js_files():
            assert f"js/{js.name}" in html, f"{js.name} 没有被 index.html 引用"

    def test_local_assets_exist(self) -> None:
        """CSS 的 url(...) 与 HTML 的 src 引用的本地素材都要存在。"""
        checked = 0

        for css in _css_files():
            for raw in re.findall(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", _read(css)):
                if raw.startswith("data:") or raw.startswith(("http://", "https://")):
                    continue
                target = (css.parent / raw).resolve()
                assert target.is_file(), f"{css.name} 引用的素材缺失：{raw}"
                checked += 1

        html = _read(INDEX_HTML)
        for raw in re.findall(r"""src=["']([^"']+)["']""", html):
            if raw.startswith(("data:", "http://", "https://")):
                continue
            assert (UI_DIR / raw).is_file(), f"index.html 引用的素材缺失：{raw}"
            checked += 1

        assert checked > 0, "没有校验到任何本地素材引用"


# ---------------------------------------------------------------------------
# 2. 前后端契约
# ---------------------------------------------------------------------------


class TestBridgeContract:
    """JS 与 api.py 的接口必须严格对齐。"""

    def test_every_called_method_exists_on_api(self) -> None:
        """JS 调用的每个方法，都必须是 Api 类的公开方法。

        这是最重要的一条：方法名写错在界面上只表现为「点了没反应」，
        极难排查，用一条静态检查就能彻底杜绝。
        """
        available = _api_public_methods()
        called = _js_called_api_methods()

        assert called, "没有扫描到任何 Bridge.call 调用，正则可能已失效"

        for filename, methods in called.items():
            unknown = sorted(methods - available)
            assert not unknown, f"{filename} 调用了 Api 上不存在的方法：{unknown}"

    def test_core_flows_are_wired_up(self) -> None:
        """P0 主流程涉及的方法必须真的被前端调用，避免功能写好了却没接线。"""
        called: set[str] = set()
        for methods in _js_called_api_methods().values():
            called |= methods

        required = {
            # 抽取
            "draw",
            # 班级与名单
            "list_rosters",
            "create_roster",
            "load_roster",
            "save_roster",
            "rename_roster",
            "delete_roster",
            # 名单录入
            "parse_batch_text",
            "import_csv",
            "apply_import",
            "create_csv_template",
            # 设置
            "get_settings",
            "save_settings",
            "get_app_info",
            # 外部程序
            "choose_external_exe",
            "launch_external",
            "open_data_dir",
            # P2 特殊效果
            "get_effect_state",
            "list_students_for_boost",
            "set_boost",
            "clear_boost",
            "arm_drift",
            "cancel_drift",
            "apply_drift_reveal",
            "set_wake_up_volume",
            "confirm_wake_up",
            "cancel_wake_up",
            "consume_wake_up_armed",
        }
        missing = sorted(required - called)
        assert not missing, f"以下 P0/P2 接口未在前端接线：{missing}"

    def test_no_private_api_called(self) -> None:
        """前端不得调用带下划线前缀的方法（pywebview 不会暴露它们）。"""
        for filename, methods in _js_called_api_methods().items():
            bad = sorted(m for m in methods if m.startswith("_"))
            assert not bad, f"{filename} 调用了私有方法：{bad}"


# ---------------------------------------------------------------------------
# 3. DOM 契约
# ---------------------------------------------------------------------------


class TestDomContract:
    """JS 引用的 DOM id 必须都在 HTML 里。"""

    def test_referenced_ids_exist_in_html(self) -> None:
        """getElementById 的每个 id 都要在 index.html 中静态声明。

        这也是「动态元素不用 id」这条约定的守卫：所有 id 都必须静态存在，
        才能被本测试校验到。
        """
        html = _read(INDEX_HTML)
        declared = set(re.findall(r"""\bid=["']([^"']+)["']""", html))

        referenced = _js_referenced_ids()
        assert referenced, "没有扫描到任何 getElementById，正则可能已失效"

        for filename, ids in referenced.items():
            missing = sorted(ids - declared)
            assert not missing, f"{filename} 引用了 HTML 中不存在的 id：{missing}"

    def test_no_duplicate_ids(self) -> None:
        """id 必须唯一，重复 id 会让 getElementById 取到错误的元素。"""
        html = _read(INDEX_HTML)
        ids = re.findall(r"""\bid=["']([^"']+)["']""", html)
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        assert not duplicates, f"index.html 存在重复 id：{duplicates}"

    def test_settings_has_five_tabs(self) -> None:
        """设置面板必须有 PRD §4.2 规定的 5 个标签页，且各有对应内容页。"""
        html = _read(INDEX_HTML)
        tabs = re.findall(r"""data-tab=["']([^"']+)["']""", html)
        pages = re.findall(r"""data-page=["']([^"']+)["']""", html)

        assert tabs == ["roster", "rules", "appearance", "external", "about"]
        assert pages == tabs, "每个标签页都要有对应的内容页"

    def test_p0_entry_points_present(self) -> None:
        """P0 的四个操作入口都要在界面上。"""
        html = _read(INDEX_HTML)
        for token in (
            "抽取 1 次",
            "抽取 5 次",
            "简洁视图",
            "设置",
        ):
            assert token in html, f"界面缺少入口：{token}"

    def test_draw_modes_are_one_and_five_only(self) -> None:
        """PRD 规定抽取模式只有 1 次与 5 次，不应出现第三种按钮。"""
        html = _read(INDEX_HTML)
        buttons = re.findall(r"""<button[^>]+id=["']btn-draw-\d+["']""", html)
        assert len(buttons) == 2, f"抽取按钮数量应为 2，实际为 {len(buttons)}"

    def test_no_inline_event_handlers(self) -> None:
        """禁止 HTML 内联事件属性，所有事件都在 JS 中绑定。

        好处：行为集中可测、无需放宽 CSP、便于统一做触摸与键盘适配。
        """
        html = _read(INDEX_HTML)
        inline = re.findall(r"""\son(?:click|change|input|keydown|load)=["']""", html, re.I)
        assert not inline, "index.html 存在内联事件属性，请改到 JS 中绑定"

    def test_no_es_module_scripts(self) -> None:
        """必须使用经典 script。

        pywebview 通过 file:// 加载页面，Chromium 会以 CORS 失败拒绝执行
        <script type="module">，导致整个界面白屏。
        """
        html = _read(INDEX_HTML)
        assert 'type="module"' not in html, "file:// 下 ES Module 会被 CORS 拦截"

    def test_three_fx_buttons_with_icon_and_two_text(self) -> None:
        """三个特殊效果按钮都必须存在，且每个都是「1 个图标 + 2 段文字」结构。

        按钮在 fx-bar 容器里**竖向排列**（PRD 2026-09-13 第七轮：从 .app 独立行的
        横排改回 .stage 内右侧浮层的竖列）。每个按钮含 .fx-btn__icon / .fx-btn__title /
        .fx-btn__status 三个 BEM 元素。F-17a/b/c 的入口都依赖这组静态 DOM，
        没有它们界面就跑不起来 —— 因此本测试是契约级的硬约束。
        """
        html = _read(INDEX_HTML)
        # 必须存在 #fx-bar 容器
        assert 'id="fx-bar"' in html, "缺少 #fx-bar 容器"
        # 三个按钮
        for btn_id in ("fx-boost", "fx-drift", "fx-wake"):
            assert f'id="{btn_id}"' in html, f"缺少按钮 {btn_id}"
        # 每个按钮都含三个 BEM 元素
        for token in ("fx-btn__icon", "fx-btn__title", "fx-btn__status"):
            count = html.count(token)
            assert count == 3, f"{token} 出现 {count} 次，期望 3 次（每个按钮各一份）"


# ---------------------------------------------------------------------------
# 4. 离线与性能约束
# ---------------------------------------------------------------------------


class TestOfflineAndPerformance:
    """NFR-S1（完全离线）与 NFR-P6 / P7 / P9（性能红线）。"""

    def test_no_network_urls(self) -> None:
        """界面里不允许出现任何 http(s) 地址（NFR-S1：软件完全离线）。"""
        offenders: list[str] = []

        for path in [INDEX_HTML, *_css_files(), *_js_files()]:
            text = _read(path)
            for match in re.findall(r"""https?://[^\s"'()<>]+""", text):
                offenders.append(f"{path.name}: {match}")

        assert not offenders, f"发现外网地址，违反完全离线约束：{offenders}"

    def test_transitions_only_animate_transform_and_opacity(self) -> None:
        """transition 只允许出现 transform / opacity（NFR-P6）。"""
        for css in _css_files():
            text = _read_css(css)
            for value in re.findall(r"""transition\s*:\s*([^;}]+)""", text):
                stripped = value.strip()

                # transition: none 是"关闭过渡"，属于合法用法（如入场前瞬移）
                if stripped == "none":
                    continue

                assert "all" not in value, f"{css.name} 使用了 transition: all（性能红线）"
                for prop in FORBIDDEN_ANIMATED_PROPS:
                    assert prop not in value, f"{css.name} 对 {prop} 做了过渡（性能红线）"

                assert any(prop in value for prop in ALLOWED_TRANSITION_PROPS), (
                    f"{css.name} 的过渡未包含 transform/opacity：{stripped}"
                )

    def test_no_blur_filter(self) -> None:
        """禁止实时模糊（NFR-P7：核显上大面积滤镜会直接掉帧）。"""
        for css in _css_files():
            text = _read_css(css)
            assert not re.search(r"""filter\s*:\s*[^;}]*blur""", text), (
                f"{css.name} 使用了模糊滤镜，违反 NFR-P7"
            )

    def test_will_change_is_limited(self) -> None:
        """will-change 只允许声明 transform / opacity（NFR-P8）。"""
        for css in _css_files():
            text = _read_css(css)
            for value in re.findall(r"""will-change\s*:\s*([^;}]+)""", text):
                for token in value.split(","):
                    token = token.strip()
                    assert token in ALLOWED_TRANSITION_PROPS, (
                        f"{css.name} 的 will-change 含非法属性：{token}"
                    )

    def test_card_back_uses_backface_visibility(self) -> None:
        """卡面必须 backface-visibility: hidden，否则翻转后会看到镜像文字。

        对应 PRD NFR-P9 / AC-13。
        """
        card_css = _read_css(CSS_DIR / "card.css")
        assert "backface-visibility: hidden" in card_css
        assert "transform-style: preserve-3d" in card_css
        assert "rotateY(180deg)" in card_css

    def test_font_size_floors_match_acceptance_criteria(self) -> None:
        """字号下限必须满足 AC-07③④（单张 64/30px，5 张 40/22px）。

        这些下限写在 clamp() 里，一旦被人调低就会静默违反验收标准，
        因此用测试锁死。
        """
        card_css = _read_css(CSS_DIR / "card.css")

        assert re.search(r"\.card--solo\s+\.card__name\s*\{[^}]*clamp\(64px", card_css), (
            "单张卡姓名行字号下限应为 64px（AC-07③）"
        )
        assert re.search(r"\.card--solo\s+\.card__num\s*\{[^}]*clamp\(30px", card_css), (
            "单张卡学号行字号下限应为 30px（AC-07③）"
        )
        assert re.search(r"\.card--multi\s+\.card__name\s*\{[^}]*clamp\(40px", card_css), (
            "5 张卡姓名行字号下限应为 40px（AC-07④）"
        )
        assert re.search(r"\.card--multi\s+\.card__num\s*\{[^}]*clamp\(22px", card_css), (
            "5 张卡学号行字号下限应为 22px（AC-07④）"
        )

    def test_card_width_is_also_capped_by_viewport_height(self) -> None:
        """卡牌宽度必须同时受视口**高度**约束，而不能只看视口宽度。

        卡牌宽高比是 5:7，若宽度只按 vw 计算，在 1280×720 这种最小窗口下
        算出来的高度会超过结果展示区的可用高度，被 overflow:hidden 裁掉
        （违反 AC-22「4K @200% 缩放下无溢出」与 §4.3 的最小尺寸要求）。
        """
        card_css = _read_css(CSS_DIR / "card.css")

        for selector in (".card--solo", ".card--multi"):
            block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", card_css)
            assert block, f"card.css 中找不到 {selector}"

            width_line = next(
                (line for line in block.group(1).splitlines() if "width" in line),
                "",
            )
            assert "vw" in width_line, f"{selector} 的宽度应随视口宽度自适应"
            assert "vh" in width_line, f"{selector} 的宽度必须同时用 vh 封顶，否则小窗口下会被裁切"


# ---------------------------------------------------------------------------
# 5. 关键交互约束
# ---------------------------------------------------------------------------


class TestInteractionConstraints:
    """几个容易被后续改动破坏的行为约定。"""

    def test_mascot_hidden_by_visibility_not_display(self) -> None:
        """看板娘关闭时必须保留占位（TI-16）：用 visibility 而非 display:none。"""
        base_css = _read(CSS_DIR / "base.css")
        app_js = _read(JS_DIR / "app.js")

        assert "is-invisible" in base_css, "base.css 缺少 is-invisible 工具类"
        assert re.search(r"\.is-invisible\s*\{[^}]*visibility\s*:\s*hidden", base_css), (
            "is-invisible 必须使用 visibility: hidden 才能保留占位"
        )
        assert "is-invisible" in app_js, "app.js 未根据开关切换看板娘显隐"
        assert "style.display" not in app_js, "不得用 display 控制看板娘，会破坏布局"
        # 容器尺寸固定，关掉图片后仍占位
        layout_css = _read_css(CSS_DIR / "layout.css")
        mascot_block = re.search(r"\.mascot\s*\{([^}]*)\}", layout_css)
        assert mascot_block, "layout.css 中找不到 .mascot"
        assert re.search(r"aspect-ratio", mascot_block.group(1)), (
            "看板娘容器必须有固定比例，关闭后才能保留空白占位"
        )
        mascot_width = next(
            (line for line in mascot_block.group(1).splitlines() if "width" in line),
            "",
        )
        assert "vh" in mascot_width, (
            "看板娘宽度需用 vh 封顶，否则矮窗口下顶栏会挤掉结果展示区的可用高度"
        )

    def test_effects_strip_is_overlay_on_right_edge(self) -> None:
        """特效栏是 .stage 内的右侧浮层（屏幕右下角），3 个按钮竖向排列。

        第七轮修订（2026-09-13）：从 .app 独立行改回 .stage 内的右侧浮层。
        早期版本（v1.7 之前）就是这样：旧版只是装饰容器；v1.7 接入 3 个按钮；
        v1.9.1（本轮）再次回归——老师认为"独立一行 + 水平排列"过于喧宾夺主，
        还是"屏幕右下角竖条"最自然。

        契约：
            · .stage__effects：position:absolute + right:0 + pointer-events:none
            · .fx-bar：flex-direction:column
            · .app：3 行（topbar/stage/dock）
            · .stage：relative 块级，不为特效栏分列
        """
        effects_css = _read_css(CSS_DIR / "effects.css")
        effects_block = re.search(r"\.stage__effects\s*\{([^}]*)\}", effects_css)
        assert effects_block, "effects.css 中找不到 .stage__effects"

        body = effects_block.group(1)
        assert re.search(r"position\s*:\s*absolute", body), (
            "特效栏必须绝对定位（做成浮层），否则会挤压结果展示区"
        )
        assert re.search(r"right\s*:\s*0", body), "特效栏应贴右边缘（屏幕右下角）"
        assert re.search(r"pointer-events\s*:\s*none", body), (
            "特效栏是占位容器，不应拦截指针事件（否则会挡住卡牌的点击翻牌）"
        )

        # 按钮组必须是竖列（第七轮回归 column）
        bar_block = re.search(r"\.fx-bar\s*\{([^}]*)\}", effects_css)
        assert bar_block, "effects.css 中缺少 .fx-bar 规则"
        bar_body = bar_block.group(1)
        assert re.search(r"flex-direction\s*:\s*column", bar_body), (
            ".fx-bar 第七轮回归竖列（贴在屏幕右下角）"
        )

        layout_css = _read_css(CSS_DIR / "layout.css")
        app_block = re.search(r"\.app\s*\{([^}]*)\}", layout_css)
        assert app_block, "layout.css 中缺少 .app 规则"
        # 第七轮：.app 回到 3 行（topbar / stage / dock）
        rows_line = next(
            (line for line in app_block.group(1).splitlines() if "grid-template-rows" in line),
            "",
        )
        tokens = re.findall(r"[a-z]+\([^)]+\)|\d+%|\d+px|auto", rows_line)
        assert len(tokens) == 3, (
            f".app 应当有 3 行（topbar/stage/dock），当前 rows 定义为：{rows_line.strip()}"
        )

        stage_block = re.search(r"\.stage\s*\{([^}]*)\}", layout_css)
        assert stage_block, "layout.css 中找不到 .stage"
        assert "grid-template-columns" not in stage_block.group(1), ".stage 不应再为特效栏分列"
        assert re.search(r"position\s*:\s*relative", stage_block.group(1)), (
            ".stage 需为特效栏提供定位参照"
        )

    def test_student_number_row_is_optional(self) -> None:
        """学号为空时不渲染该行（PRD AC-07⑤ / E-07）。"""
        card_js = _read(JS_DIR / "card.js")
        assert re.search(r"if\s*\(num\)\s*\{", card_js), (
            "card.js 必须按学号是否为空决定是否渲染 .card__num"
        )

    def test_card_layers_are_separated(self) -> None:
        """位移与旋转必须分在两层，避免两个 transform 互相覆盖。

        外层 .card 负责滑出，内层 .card__inner 负责翻转。
        """
        card_js = _read(JS_DIR / "card.js")
        assert "card__inner" in card_js
        assert "card__face--back" in card_js
        assert "card__face--front" in card_js

        card_css = _read(CSS_DIR / "card.css")
        assert re.search(r"\.card\s*\{[^}]*transition", card_css), "外层应负责滑出过渡"
        assert re.search(r"\.card--flipped\s+\.card__inner", card_css), "内层应负责翻转"

    def test_bridge_never_throws(self) -> None:
        """bridge.js 必须把异常转成失败信封，绝不让异常冒到调用方。"""
        bridge_js = _read(JS_DIR / "bridge.js")
        assert "try {" in bridge_js and "catch" in bridge_js
        assert "ok: false" in bridge_js
        assert "bridge_missing" in bridge_js

    def test_audio_uses_web_audio_api(self) -> None:
        """音效必须用 Web Audio API 合成，不加载任何音频文件（AC-15②）。"""
        audio_js = _read(JS_DIR / "audio.js")

        # 必须是代码合成
        assert "AudioContext" in audio_js
        assert "createOscillator" in audio_js
        # 必须做淡入淡出包络，避免爆音（PRD 附录 A 明确要求）
        assert "exponentialRampToValueAtTime" in audio_js

        # 不得依赖任何音频素材文件
        assert "new Audio(" not in audio_js
        assert not re.search(r"""['"][^'"]*\.(mp3|wav|ogg|m4a|flac)['"]""", audio_js), (
            "audio.js 不应引用音频文件（v1.1 约定零素材依赖）"
        )


# ---------------------------------------------------------------------------
# 6. P1 视觉动画层（PRD F-11 ~ F-16 / AC-11 ~ AC-16）
# ---------------------------------------------------------------------------
#
# 【为什么把 P1 也纳入契约测试】
#    P1 全是「视觉打磨」，没有一行业务逻辑，改坏了也不会抛异常 —— 它只会静默
#    地变丑或变卡。而 P1 恰恰踩在两条最容易失守的红线上：
#        · 性能（NFR-P6/P7/P8）：4K 无独显下多一个 box-shadow 动画就掉帧
#        · 素材可替换（AC-14②）：JS 一旦硬编码素材路径，老师换图就得改代码
#    所以这里把这两条锁死，而不是只靠肉眼看效果。
# ---------------------------------------------------------------------------

# 看板娘的三个状态（与 index.html 的 data-state、layout.css 的选择器一致）
MASCOT_STATES = ("idle", "loading", "result")


class TestP1DeckAndCardAnimation:
    """牌堆层叠（F-11）、滑出缓动（F-12）、翻转厚度感（F-13）。"""

    def test_deck_shows_multiple_stacked_cards(self) -> None:
        """AC-11：牌堆必须是**多张**卡背层叠，而不是单张卡。"""
        html = _read(INDEX_HTML)
        layers = set(re.findall(r"""deck__card--back\d+""", html))
        assert len(layers) >= 4, f"牌堆只有 {len(layers)} 层，看不出叠放效果（AC-11）"

    def test_every_deck_layer_is_offset_and_faded(self) -> None:
        """每层都要有错位与透明度差异，层与层之间才能被区分开。

        错位必须用**百分比**：translate 的百分比相对元素自身尺寸，
        这样牌堆随窗口缩小时层叠间距等比收缩，不会在小窗口糊成一团。
        """
        html = _read(INDEX_HTML)
        indexes = sorted(set(re.findall(r"""deck__card--back(\d+)""", html)), key=int)
        css = _read_css(CSS_DIR / "layout.css")

        for index in indexes:
            block = re.search(rf"\.deck__card--back{index}\s*\{{([^}}]*)\}}", css)
            assert block, f"layout.css 缺少 .deck__card--back{index}"
            body = block.group(1)
            assert "opacity" in body, f"back{index} 缺少透明度区分"
            if index != "1":
                # 最上面那张不偏移；其余每层都要有明显的错位与倾斜
                assert "translate(-" in body and "rotate(-" in body, (
                    f"back{index} 缺少错位/倾斜，无法形成层叠观感"
                )
                assert "%" in body, f"back{index} 的错位应使用百分比（随窗口等比缩放）"

    def test_deck_shadow_is_static_not_animated(self) -> None:
        """AC-11 要求牌堆「带错位与阴影」，但阴影必须是**静态**的。

        NFR-P7 明令禁止动画化 box-shadow —— 4K 无独显下这是直接掉帧的根因。
        """
        css = _read_css(CSS_DIR / "layout.css")
        assert "box-shadow" in css, "牌堆缺少阴影（AC-11 明确要求）"

        # 不得进入 transition
        for value in re.findall(r"""transition\s*:\s*([^;}]+)""", css):
            assert "box-shadow" not in value, "牌堆阴影不得进入 transition（NFR-P7）"

        # 也不得出现在关键帧里（动画化的阴影同样是 CPU 密集重绘）
        for block in re.findall(r"@keyframes\s+[\w-]+\s*\{.*?\n\}", css, flags=re.DOTALL):
            assert "box-shadow" not in block, "关键帧中不得动画化 box-shadow（NFR-P7）"

    def test_slide_uses_dedicated_easing_token(self) -> None:
        """F-12「带缓动曲线」：滑出使用专门的缓动令牌，而非随手写一条曲线。"""
        base_css = _read_css(CSS_DIR / "base.css")
        card_css = _read_css(CSS_DIR / "card.css")

        assert re.search(r"--ease-slide\s*:\s*cubic-bezier", base_css), (
            "base.css 缺少 --ease-slide 缓动令牌（NFR-M6：魔数须收敛为令牌）"
        )
        assert "--ease-slide" in card_css, "卡牌滑出未使用专用缓动令牌（F-12）"

    def test_card_has_perspective_for_3d_flip(self) -> None:
        """AC-13「中途可见厚度感」：必须有透视。

        没有 perspective 时 rotateY 会退化成「水平压扁」的仿射变换，
        翻转全程看不出任何立体感。
        """
        base_css = _read_css(CSS_DIR / "base.css")
        card_css = _read_css(CSS_DIR / "card.css")

        assert re.search(r"--card-depth\s*:\s*\d+px", base_css), (
            "base.css 缺少 --card-depth 景深令牌"
        )
        assert re.search(r"perspective\s*:\s*var\(--card-depth\)", card_css), (
            "card.css 未给卡牌设置透视（AC-13）"
        )

    def test_card_edge_creates_thickness_without_animation(self) -> None:
        """侧棱元素负责「厚度感」，且必须零动画实现。

        它的原理是静态的 rotateY(90deg)：垂直于卡面，只在卡牌转到侧对观众
        （约 45°~135°）时才显出宽度。这样既有厚度，又不增加任何动画开销。
        """
        card_js = _read(JS_DIR / "card.js")
        assert "card__edge" in card_js, "card.js 未创建卡牌侧棱元素（AC-13）"

        css = _read_css(CSS_DIR / "card.css")
        block = re.search(r"\.card__edge\s*\{([^}]*)\}", css)
        assert block, "card.css 缺少 .card__edge"
        body = block.group(1)

        assert re.search(r"rotateY\(90deg\)", body), "侧棱必须垂直于卡面（rotateY(90deg)）"
        assert "pointer-events: none" in body, "侧棱不得拦截指针事件（否则点不翻牌）"
        assert "animation" not in body and "transition" not in body, (
            "侧棱必须是纯静态的，不得引入任何动画（NFR-P6/P7）"
        )


class TestP1Mascot:
    """看板娘三态与呼吸动效（F-14 / AC-14）。"""

    def test_three_state_assets_are_present(self) -> None:
        """AC-14①③：三个状态的素材都必须在页面里被引用。

        【为什么只断言「基名 + 点号」而不锁定扩展名】
        素材格式是可替换的实现细节：v1.9.2 之前是占位的 .svg（几何色块），
        v1.9.2 换成流萤 Q 版 .png 位图。测试若写死 .svg，换格式就要改测试 ——
        这与 AC-14②「替换同名素材即可换形象，不碰代码」的契约相悖。
        所以这里只要求「mascot_<state>.<ext>」在 HTML 里被引用，格式不限。
        """
        html = _read(INDEX_HTML)
        for state in MASCOT_STATES:
            assert f"mascot_{state}." in html, (
                f"index.html 缺少 {state} 态素材引用（当前素材为 PNG）"
            )

    def test_css_switches_state_by_data_attribute(self) -> None:
        """三态的显示规则必须挂在 data-state 上，素材叠放、靠 opacity 交叉淡入。"""
        css = _read_css(CSS_DIR / "layout.css")
        for state in MASCOT_STATES:
            assert f'.mascot[data-state="{state}"]' in css, f"CSS 缺少 {state} 态的规则"

        # 交叉淡入只能动 opacity
        img_block = re.search(r"\.mascot__img\s*\{([^}]*)\}", css)
        assert img_block, "layout.css 缺少 .mascot__img"
        assert "opacity: 0" in img_block.group(1), "未选中态的图片应完全透明"

    def test_js_switches_state_without_hardcoding_assets(self) -> None:
        """AC-14②：JS 只下发 data-state，不硬编码素材路径。

        否则老师替换同名素材后，代码里还留着旧路径，就变成「换图还得改代码」。
        """
        app_js = _read(JS_DIR / "app.js")
        assert "dataset.state" in app_js, "看板娘状态应通过 data-state 下发"
        # 不带扩展名断言：无论素材是 .svg 还是 .png，路径都不该出现在 JS 里
        assert "mascot_idle" not in app_js, "app.js 不应硬编码素材路径（AC-14②）"
        assert "mascot_result" not in app_js, "app.js 不应硬编码素材路径（AC-14②）"

        for state in MASCOT_STATES:
            assert f"'{state}'" in app_js, f"app.js 未切换 {state} 态"

    def test_breathing_effect_is_transform_only(self) -> None:
        """F-14 待机呼吸：只允许 transform/opacity，且只有一个元素在动。

        NFR-P10 要求单帧并发动画元素 ≤ 8。三张图若各自起动画就是 3 个，
        挂在唯一的叠放层上则只需要 1 个。
        """
        css = _read_css(CSS_DIR / "layout.css")
        keyframes = re.search(r"@keyframes\s+mascot-float\s*\{.*?\n\}", css, flags=re.DOTALL)
        assert keyframes, "layout.css 缺少 mascot-float 关键帧"

        body = keyframes.group(0)
        assert "transform" in body, "呼吸动效应基于 transform"
        assert "opacity" in body, "呼吸动效可叠加 opacity"
        assert "rotate" not in body, "呼吸动效不应引入旋转（视觉须克制，NFR-M7）"

        assert len(re.findall(r"animation\s*:\s*mascot-float", css)) == len(MASCOT_STATES), (
            "呼吸动效应挂在 .mascot__stage 这一层（三态各一条规则），而不是三张图各自动画"
        )

    def test_mascot_off_keeps_space_and_stops_animation(self) -> None:
        """AC-14④ / TI-16：关闭后保留空白占位，并连动画一起停掉。"""
        css = _read_css(CSS_DIR / "layout.css")
        assert re.search(r"\.mascot__stage\.is-invisible\s*\{[^}]*animation\s*:\s*none", css), (
            "关闭看板娘时应停止呼吸动画，避免在看不见的地方空烧 GPU"
        )

        mascot_block = re.search(r"\.mascot\s*\{([^}]*)\}", css)
        assert mascot_block and "aspect-ratio" in mascot_block.group(1), (
            "看板娘容器须有固定比例，关闭后才能保留空白占位（布局不塌陷）"
        )


class TestP1AudioAndMuteButton:
    """音效完整性与一键静音（F-15 / AC-15）。"""

    def test_result_sound_is_actually_played(self) -> None:
        """AC-15①：出结果音必须真的被播放。

        【回归背景】P0 只在 audio.js 里合成了 result 配方，但从未调用它 ——
        配方存在 ≠ 音效存在，这条测试专门盯住这种「有实现没接线」的情况。
        """
        audio_js = _read(JS_DIR / "audio.js")
        app_js = _read(JS_DIR / "app.js")

        assert "result:" in audio_js, "audio.js 缺少 result 音效配方"
        assert re.search(r"""play\(\s*['"]result['"]\s*\)""", app_js), (
            "app.js 从未播放 result 音效（AC-15① 要求出结果音可辨识）"
        )

    def test_result_sound_waits_for_flip_to_finish(self) -> None:
        """出结果音必须在**翻转完成之后**才响（PRD §6.1 第 5 步）。

        翻转时长由 card.js 统一导出（NFR-M6），app.js 只引用该常量，
        不得自己再写一个 600 —— 否则将来调时长必然漏改一处。
        """
        app_js = _read(JS_DIR / "app.js")
        card_js = _read(JS_DIR / "card.js")

        assert "DUR_FLIP" in card_js, "card.js 应导出 DUR_FLIP 供上层复用"
        assert re.search(
            r"window\.setTimeout\([\s\S]*?\}\s*,\s*window\.CR\.Cards\.DUR_FLIP\s*\)", app_js
        ), "app.js 应以 Cards.DUR_FLIP 为延时等待翻转结束（NFR-M6）"

    def test_mute_button_exists_and_meets_touch_target(self) -> None:
        """AC-15④：主界面要有一键静音按钮，且触摸目标 ≥ 44×44px（NFR-C3）。"""
        html = _read(INDEX_HTML)
        tag = next((t for t in re.findall(r"<button[^>]*>", html) if 'id="btn-mute"' in t), "")

        assert tag, "主界面缺少一键静音按钮（AC-15④）"
        assert "btn--tool" in tag, "静音按钮应复用 .btn--tool（min-height 52px，满足触摸目标）"

    def test_mute_button_is_temporary_and_never_writes_settings(self) -> None:
        """主界面静音按钮只做本次运行的临时调整（F-15 / AC-15④ / TI-30）。

        【回归背景】旧版按钮把 muted 写回配置、并回写设置面板，导致"临时静音"
        变成了"改掉默认设置"。2026-09-24 老师要求彻底解耦：

            · 主界面按钮 → 只改本次运行的有效状态（``Audio.setMuted``），不写盘；
            · 设置面板开关 → 才是持久化的**默认值**，重启后由它决定初始音效状态。
        """
        app_js = _read(JS_DIR / "app.js")
        settings_js = _read(JS_DIR / "settings.js")

        # 按钮路径不得写配置，也不得回写设置面板
        assert not re.search(r"""save_settings['"]\s*,\s*\{\s*muted""", app_js), (
            "静音按钮不得把 muted 写回配置（写盘是设置面板开关的职责）"
        )
        assert "syncSettings" not in app_js, "静音按钮不得回写设置面板"
        assert "syncSettings" not in settings_js, "settings.js 不应再提供静音回写入口"

        # 按钮只切换运行态
        toggle_fn = re.search(r"function toggleMute\(\)\s*\{[\s\S]*?\n    \}", app_js)
        assert toggle_fn and "Audio.setMuted" in toggle_fn.group(0), (
            "静音按钮应只调用 Audio.setMuted 切换本次运行的状态"
        )
        assert toggle_fn and "Bridge.call" not in toggle_fn.group(0), (
            "静音按钮不应再与后端交互（临时状态不落盘）"
        )

        # 启动时仍需继承设置里的默认开关
        assert "configMuted" in app_js, "启动时应继承设置里的音效默认开关（TI-30）"

    def test_settings_sound_switch_is_the_persisted_default(self) -> None:
        """设置面板的音效开关仍是持久化默认值（TI-15 / TI-30）。"""
        settings_js = _read(JS_DIR / "settings.js")
        assert re.search(
            r"""patchSettings\(\{\s*muted:\s*!dom\.toggleSound\.checked""", settings_js
        ), "设置面板的音效开关必须写回 muted 配置项（它才是持久化的默认值）"


class TestP2DriftRevealTiming:
    """结果漂移的触发时机（F-17b / TI-31）。

    【回归背景】旧版在翻牌 click 的瞬间就 ``swapFront``：卡牌还在翻转、老师
    根本看不到原始结果，只看到"翻出来就是偏移后的人"。2026-09-24 老师要求改为
    「原结果显示 200ms → 起闪 → 闪光覆盖期间换字 → 露出偏移结果」。
    """

    def test_timing_constants_are_centralized_in_card_js(self) -> None:
        """时序常量集中在 card.js 并由上层复用（NFR-M6：不得各写一份魔数）。"""
        card_js = _read(JS_DIR / "card.js")
        assert "const DRIFT_DELAY = 200;" in card_js, "card.js 应定义 DRIFT_DELAY = 200"
        assert "const DRIFT_FLASH = 400;" in card_js, "card.js 应定义 DRIFT_FLASH = 400"
        assert "DRIFT_DELAY: DRIFT_DELAY" in card_js, "card.js 应导出 DRIFT_DELAY"
        assert "DRIFT_FLASH: DRIFT_FLASH" in card_js, "card.js 应导出 DRIFT_FLASH"

    def test_drift_starts_only_after_flip_plus_delay(self) -> None:
        """漂移视觉必须延迟到「翻转结束 + DRIFT_DELAY」才启动，而不是 click 即刻。"""
        app_js = _read(JS_DIR / "app.js")
        assert re.search(r"DUR_FLIP\s*\+\s*window\.CR\.Cards\.DRIFT_DELAY", app_js), (
            "漂移覆盖层应延迟到翻转结束 + DRIFT_DELAY 才启动（先让老师看清原结果）"
        )

    def test_swap_happens_while_overlay_covers_the_card(self) -> None:
        """换字必须由覆盖层「已覆盖到位」的回调驱动，而不是与闪光各做各的。"""
        app_js = _read(JS_DIR / "app.js")
        card_js = _read(JS_DIR / "card.js")

        assert re.search(
            r"driftOverlay\([\s\S]{0,200}?DRIFT_FLASH[\s\S]{0,200}?swapFront", app_js
        ), "app.js 应把 swapFront 作为 driftOverlay 的覆盖回调传入"
        assert "onCovered" in card_js and "DRIFT_COVER" in card_js, (
            "card.js 的 driftOverlay 需支持『覆盖到位』回调（换字被闪光盖住）"
        )
        assert re.search(r"setTimeout\(onCovered,\s*DRIFT_COVER\)", card_js), (
            "覆盖回调应在 DRIFT_COVER 之后触发"
        )

    def test_drift_cover_matches_css_fade_in(self) -> None:
        """换字时机必须与 CSS 闪光的不透明度过渡时长一致（跨文件常量，NFR-M6）。

        若只改 CSS 的 220ms 而不改 JS 的 DRIFT_COVER，换字会发生在闪光还没盖住
        卡面的时候 —— 老师就会看到"文字突然变了一下"的破绽。这条测试把两者绑死。
        """
        card_js = _read(JS_DIR / "card.js")
        overlay_css = _read_css(CSS_DIR / "card.css")

        js_cover = re.search(r"const DRIFT_COVER = (\d+)", card_js)
        css_fade = re.search(
            r"\.card__drift-overlay\s*\{[^}]*transition:\s*opacity\s+(\d+)ms", overlay_css
        )

        assert js_cover, "card.js 缺少 DRIFT_COVER 常量"
        assert css_fade, "card.css 的 .card__drift-overlay 缺少 opacity 过渡时长"
        assert js_cover.group(1) == css_fade.group(1), (
            "card.js 的 DRIFT_COVER 必须等于 CSS 里 opacity 过渡的时长"
        )


class TestP1LoadingIcon:
    """加载图标两态（F-16 / AC-16）。"""

    def test_loading_icon_syncs_with_draw_state(self) -> None:
        """AC-16：加载图标只在抽取过程中旋转，与状态严格同步。"""
        app_js = _read(JS_DIR / "app.js")
        assert "status--loading" in app_js, "app.js 未切换加载中的图标状态"
        assert "setLoading(true)" in app_js, "开始抽取时应进入加载态"
        assert "setLoading(false)" in app_js, "结束抽取时应退出加载态"

        css = _read_css(CSS_DIR / "layout.css")
        assert re.search(r"\.status--loading\s+\.status__icon\s*\{[^}]*animation", css), (
            "抽取中应让加载图标旋转（F-16）"
        )
        # 旋转只允许用 transform（NFR-P6）
        spin = re.search(r"@keyframes\s+status-spin\s*\{.*?\n\}", css, flags=re.DOTALL)
        assert spin and "transform" in spin.group(0)
