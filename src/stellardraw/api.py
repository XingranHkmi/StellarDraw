"""JS Bridge —— 暴露给前端调用的接口层。

前端通过 ``window.pywebview.api.<方法名>(...)`` 调用本文件中 Api 类的**公开方法**。
两侧的约定：

    1. 公开方法（前端可调用）：不加下划线前缀。
    2. 内部方法：以单下划线开头，pywebview 不会暴露给前端。
    3. 返回值必须是可 JSON 序列化的类型；统一使用 ``{"ok": bool, ...}`` 信封：
           · 成功 → ``{"ok": True, ...业务字段}``
           · 失败 → ``{"ok": False, "code": "机器可判定的错误码", "message": "给老师看的中文提示"}``
       前端据此决定弹什么提示、要不要引导用户去设置面板。
    4. 方法签名保持简单：前端传位置参数或关键字参数均可。
    5. **本层永不抛异常**。任何底层错误都必须转成 ``ok=False`` 的信封
       —— 异常穿过 JS Bridge 会让前端只收到一个 undefined，界面无从提示。

本层只做「参数校验 + 调用 core + 组装返回值」，
**不实现任何业务逻辑** —— 业务逻辑属于 core/ 包。

错误码（code）一览，供前端分支使用：

    empty_roster   名单为空，无法抽取（PRD E-01，附带 open_settings=True）
    invalid_count  抽取人数非法
    not_set        尚未设置外部程序（PRD E-04，附带 open_settings=True）
    not_found      外部程序路径失效（PRD E-05，附带 open_settings=True）
    cancelled      用户取消了文件选择框
    invalid_csv    CSV 内容格式非法（PRD E-08）
    invalid_roster 班级名称为空 / 重名 / 越界等可预期的业务错误
    io_error       磁盘读写失败

前端可调用的方法一览（对照 PRD 需求编号）：

    ┌─ 连通性与"关于" ─────────────────────────────────────────────┐
    │ ping()                        探测桥接是否就绪               │
    │ get_app_info()                F-09 关于页：名称/版本/占位文案 │
    ├─ 班级档案（F-03 / F-04 / AC-03）──────────────────────────────┤
    │ list_rosters()                列出全部班级 + 当前班级         │
    │ create_roster(name)           新建班级并切换过去              │
    │ load_roster(name)             切换班级（按新班级重建候选池）   │
    │ save_roster(name, students)   整体覆盖保存名单                │
    │ rename_roster(old, new)       重命名班级                     │
    │ delete_roster(name)           删除班级                       │
    ├─ 名单录入（F-01 / F-02 / F-02b）──────────────────────────────┤
    │ parse_batch_text(text)        批量粘贴解析（返回预览，不落盘） │
    │ import_csv()                  选文件并解析；已有名单时先询问   │
    │ apply_import(mode)            落实「覆盖」或「追加」（TI-11）  │
    │ create_csv_template()         生成带 BOM 的标准 CSV 模板      │
    ├─ 抽取（F-05 / F-06 / F-07）──────────────────────────────────┤
    │ draw(count)                   抽取 1 次或 5 次                │
    │ set_dedupe(enabled)           切换去重模式（并落盘）           │
    ├─ 特殊效果（F-17a / F-17b / F-17c）──────────────────────────┤
    │ get_effect_state()           拉取三个效果的当前状态            │
    │ list_students_for_boost()    F-17a 菜单的学生列表              │
    │ set_boost(students)          整体替换加权名单                  │
    │ clear_boost()                清空加权                          │
    │ arm_drift(offset)            F-17b 武装下一次漂移              │
    │ cancel_drift()               取消漂移                          │
    │ set_wake_up_volume(volume)   F-17c 选档（40/60/90%）           │
    │ confirm_wake_up()            F-17c 确认使用（同时调系统音量）   │
    │ cancel_wake_up()             取消叫醒                          │
    │ consume_wake_up_armed()      首张翻牌后消费 bingo armed        │
    ├─ 设置与外部程序（F-09 / F-10 / F-02c）───────────────────────┤
    │ get_settings() / save_settings(settings)   配置读写           │
    │ choose_external_exe()         选择「简洁视图」要启动的 exe     │
    │ launch_external()             启动该 exe（主程序不受影响）     │
    │ open_data_dir()               打开 data 目录（F-02c）         │
    └──────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging

from stellardraw import __version__
from stellardraw.core import importer, launcher
from stellardraw.core.drawer import DRAW_ONCE, Drawer
from stellardraw.core.roster import Roster, Student
from stellardraw.core.special_effects import (
    DRIFT_OFFSETS,
    SpecialEffectsState,
)
from stellardraw.core.storage import Storage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 面向界面的固定文案（集中定义，与 PRD 措辞一致，便于测试锁定）
# ---------------------------------------------------------------------------

APP_NAME = "StellarDraw 课堂抽号机"
TECH_LINE = "基于 Python + Pywebview(WebView2) + 原生 HTML/CSS/JS 构建"

# "关于"页的文字占位区域（PRD TI-12 已确认的默认文案）
ABOUT_PLACEHOLDER = "Devloped by XingranHkmi / Deepseek"

# PRD E-01：空名单时的提示
MSG_EMPTY_ROSTER = "请先添加学生名单"

# PRD E-02：候选池不足时的提示
MSG_SHORT_TEMPLATE = "本轮仅剩 {count} 人，已全部抽出"

# PRD E-03：本轮抽完自动重置时的提示
MSG_NEW_ROUND = "全班已抽完，新一轮开始"

# 首个自动创建的班级名（首次使用直接导入 CSV 时用，保证零配置可用）
DEFAULT_ROSTER_NAME = "默认班级"

# 文件选择框的类型过滤器
_CSV_FILE_TYPES = ("CSV 文件 (*.csv)", "所有文件 (*.*)")
_EXE_FILE_TYPES = ("可执行文件 (*.exe)", "所有文件 (*.*)")

# 导入冲突时的两种处理方式（PRD TI-11）
IMPORT_MODES = ("overwrite", "append")


def _open_dialog_type(webview_module):
    """返回"打开文件"对话框的类型常量（内部函数）。

    pywebview 6.x 起 ``webview.OPEN_DIALOG`` 被标记为弃用（会打印警告），
    推荐改用 ``webview.FileDialog.OPEN``。这里做一次兼容取值，
    保证 5.x / 6.x 都能正常工作且不产生弃用警告。
    """
    file_dialog = getattr(webview_module, "FileDialog", None)
    if file_dialog is not None and hasattr(file_dialog, "OPEN"):
        return file_dialog.OPEN
    return webview_module.OPEN_DIALOG  # pragma: no cover —— 老版本 pywebview 兜底


class Api:
    """暴露给前端的 API 集合。

    参数：
        storage: 可选的数据层，注入后所有读写都落在指定目录（测试隔离用）。
        drawer:  可选的抽取引擎，注入后可固定随机种子（测试复现用）。
    """

    def __init__(self, storage: Storage | None = None, drawer: Drawer | None = None) -> None:
        # 主窗口引用，由 main.create_window() 注入，用于窗口级操作（文件选择框等）
        self._window = None

        self._storage = storage if storage is not None else Storage()

        # 进程内状态：配置、当前班级名单、抽取引擎、待确认的导入数据
        self._config = self._storage.load_config()
        self._current = Roster()
        self._drawer = (
            drawer if drawer is not None else Drawer(dedupe=bool(self._config.get("dedupe", True)))
        )
        self._pending_import: list[Student] = []
        self._fx = SpecialEffectsState()

        # 恢复上次选中的班级（PRD AC-03④）
        self._restore_last_roster()

    def attach_window(self, window) -> None:
        """注入主窗口引用（内部方法，前端不可见）。

        参数：
            window: pywebview 的窗口对象。
        """
        self._window = window

    # ==================================================================
    # 内部工具（下划线开头，前端不可见）
    # ==================================================================

    @staticmethod
    def _ok(**payload) -> dict:
        """组装成功信封。"""
        return {"ok": True, **payload}

    @staticmethod
    def _fail(message: str, code: str = "error", **payload) -> dict:
        """组装失败信封。

        参数：
            message: 面向老师的中文提示，可被前端直接显示。
            code:    机器可判定的错误码，供前端做分支处理。
        """
        return {"ok": False, "code": code, "message": message, **payload}

    def _settings_payload(self) -> dict:
        """把内存中的配置整理成前端需要的设置字典。

        字段含义（键名与 PRD 需求对应）：
            dedupe          去重模式开关（F-05）
            muted           音效总开关的**反相**表达：True = 静音（F-15 / TI-15）
            volume          音量 0.0 ~ 1.0
            mascot_enabled  看板娘显隐开关（F-14 / TI-16），默认 True
            external_exe    「简洁视图」外部程序路径（F-09）
            current_roster  上次选中的班级（F-03）
        """
        return {
            "dedupe": bool(self._config.get("dedupe", True)),
            "muted": bool(self._config.get("muted", False)),
            "volume": float(self._config.get("volume", 0.7)),
            "mascot_enabled": bool(self._config.get("mascot_enabled", True)),
            "external_exe": str(self._config.get("external_exe", "")),
            "current_roster": str(self._config.get("current_roster", "")),
        }

    def _sync_drawer(self) -> None:
        """把当前班级名单同步给抽取引擎，并重建候选池（PRD §6.3 切换班级）。

        同步完成后，**加权名单按 (num, name) 在新名单里重新校验**：原本不在新名单
        中的学生会自然被忽略（set_boost 内部做这件事）；同时所有一次性效果
        （漂移 / 叫醒）也会被清掉。
        """
        self._drawer.set_students(list(self._current.students))
        self._fx.reset_for_roster_switch()
        # 重新尝试套用残存的加权集合（reset 已清空，所以这里是把已经清掉的状态再同步一次；
        # 主要目的是让「切到相同班级」时 set_boost 重建池生效）
        self._drawer.set_boost([])

    def _persist_current_name(self) -> None:
        """把当前班级名写进配置，供下次启动恢复（PRD F-03 / AC-03④）。"""
        self._config = self._storage.update_config({"current_roster": self._current.name})

    def _restore_last_roster(self) -> None:
        """启动时恢复上次选中的班级（PRD AC-03④）。"""
        name = str(self._config.get("current_roster") or "").strip()
        if name and self._storage.roster_exists(name):
            self._current = self._storage.load_roster(name)
        elif name:
            # 班级档案已被删除：清掉失效的指针，避免界面显示一个不存在的班级
            self._config = self._storage.update_config({"current_roster": ""})
        self._sync_drawer()

    def _next_available_name(self, base: str) -> str:
        """在 base 已被占用时，返回第一个可用的「base2 / base3 ...」名称。"""
        if not self._storage.roster_exists(base):
            return base
        index = 2
        while self._storage.roster_exists(f"{base}{index}"):
            index += 1
        return f"{base}{index}"

    def _ensure_current_roster(self) -> Roster:
        """保证存在一个「当前班级」。

        首次使用（一个班级都没有）时直接导入 CSV 是常见路径，
        此时自动创建一个名为「默认班级」的档案，保证零配置即可开始
        （对应 PRD G-04 的零学习成本目标）。
        """
        if self._current.name:
            return self._current
        name = self._next_available_name(DEFAULT_ROSTER_NAME)
        self._current = Roster(name=name)
        self._storage.save_roster(self._current)
        self._persist_current_name()
        return self._current

    def _pick_file(self, file_types: tuple[str, ...]) -> str:
        """弹出系统文件选择框，返回选中的绝对路径（内部函数）。

        返回空字符串表示：用户取消、窗口不可用或对话框调用失败。
        测试时通过替换 ``self._window`` 注入假窗口即可覆盖本函数。
        """
        if self._window is None:
            logger.warning("窗口尚未注入，无法弹出文件选择框")
            return ""
        try:
            import webview
        except Exception:  # pragma: no cover —— pywebview 是必需依赖，此处仅防御
            logger.exception("导入 pywebview 失败，无法弹出文件选择框")
            return ""

        try:
            result = self._window.create_file_dialog(
                _open_dialog_type(webview),
                allow_multiple=False,
                file_types=file_types,
            )
        except Exception:
            logger.exception("文件选择框调用失败")
            return ""

        # pywebview 在不同版本 / 平台上返回 tuple、list 或 str，统一归一化
        if isinstance(result, (list, tuple)):
            result = result[0] if result else ""
        return launcher.normalize_path(result)

    def _apply_import_locked(self, mode: str) -> dict:
        """把待导入数据写入当前班级（内部函数，调用前已确保有待导入数据）。"""
        students = self._pending_import
        roster = self._ensure_current_roster()

        if mode == "overwrite":
            # 覆盖：整表替换。先构造新列表再赋值，避免中途异常留下半成品
            roster.students = [s.normalized() for s in students]
        else:  # append
            roster.extend(students)

        try:
            self._storage.save_roster(roster)
        except (ValueError, OSError) as exc:
            return self._fail(f"保存名单失败：{exc}", code="io_error")

        self._pending_import = []
        self._sync_drawer()
        return self._ok(
            imported=len(students),
            mode=mode,
            roster=roster.to_dict(),
            count=len(roster),
        )

    # ==================================================================
    # 连通性
    # ==================================================================

    def ping(self) -> dict:
        """连通性探测：前端启动时调用，用于确认 JS Bridge 已就绪。

        返回：
            {"ok": True, "version": 版本号, "message": 提示文本}
        """
        return {
            "ok": True,
            "version": __version__,
            "message": "JS Bridge 已就绪",
        }

    def get_app_info(self) -> dict:
        """返回"关于"页所需的全部信息（PRD §4.2 / AC-09③）。

        版本号取自 ``stellardraw.__version__``，保证「单一数据源」，
        避免界面里再硬编码一份版本号。
        """
        return self._ok(
            name=APP_NAME,
            version=__version__,
            tech=TECH_LINE,
            placeholder=ABOUT_PLACEHOLDER,
            data_dir=str(self._storage.data_root),
        )

    # ==================================================================
    # 班级档案（PRD F-03 / AC-03）
    # ==================================================================

    def list_rosters(self) -> dict:
        """列出全部班级名称。

        对应需求：PRD F-03。

        返回：
            {"ok": True, "rosters": [...], "current": 当前班级名}
        """
        return self._ok(
            rosters=self._storage.list_rosters(),
            current=self._current.name,
        )

    def create_roster(self, name: str) -> dict:
        """新建班级档案并切换过去。

        对应需求：PRD F-03 / AC-03①。

        参数：
            name: 新班级名称。
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return self._fail("班级名称不能为空", code="invalid_roster")
        if self._storage.roster_exists(cleaned):
            return self._fail(f"已存在名为「{cleaned}」的班级", code="invalid_roster")

        roster = Roster(name=cleaned)
        try:
            self._storage.save_roster(roster)
        except (ValueError, OSError) as exc:
            return self._fail(f"新建班级失败：{exc}", code="io_error")

        self._current = roster
        self._sync_drawer()
        self._persist_current_name()
        return self._ok(
            roster=roster.to_dict(),
            current=roster.name,
            rosters=self._storage.list_rosters(),
        )

    def load_roster(self, name: str) -> dict:
        """载入指定班级的名单并切换为当前班级。

        对应需求：PRD F-03 / F-04 / AC-03②。

        参数：
            name: 班级名称。

        说明：
            班级不存在时**不做静默创建**，而是返回错误。
            这样可以避免老师打错一个字就凭空多出一个空班级；
            界面上班级列表来自 list_rosters()，正常情况下不会触发此分支。
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return self._fail("班级名称不能为空", code="invalid_roster")
        if not self._storage.roster_exists(cleaned):
            return self._fail(f"班级「{cleaned}」不存在", code="invalid_roster")

        roster = self._storage.load_roster(cleaned)
        self._current = roster
        # PRD §6.3：切换班级必须清空候选池并按新班级重建
        self._sync_drawer()
        self._persist_current_name()
        return self._ok(
            roster=roster.to_dict(),
            count=len(roster),
            remaining=self._drawer.remaining,
        )

    def save_roster(self, name: str, students: list[dict]) -> dict:
        """保存班级名单（整体覆盖）。

        对应需求：PRD F-04 / F-01 / AC-01①②③⑤。

        参数：
            name:     班级名称。
            students: [{"num": "07", "name": "张三"}, ...]
                      姓名为空的记录会被跳过（前端"新增空行"是常见中间态）。

        注意：
            若保存的是**当前班级**，候选池会被重建（丢弃本轮去重进度）。
            这样做是为了避免刚被删掉的学生仍留在候选池里被抽出。
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return self._fail("班级名称不能为空", code="invalid_roster")
        try:
            roster = Roster.from_payload(cleaned, students)
        except TypeError as exc:
            return self._fail(f"名单数据格式不正确：{exc}", code="invalid_roster")

        try:
            self._storage.save_roster(roster)
        except (ValueError, OSError) as exc:
            return self._fail(f"保存名单失败：{exc}", code="io_error")

        if cleaned == self._current.name:
            self._current = roster
            self._sync_drawer()

        return self._ok(
            count=len(roster),
            duplicate=roster.has_duplicate_students(),
            current=self._current.name,
        )

    def rename_roster(self, old_name: str, new_name: str) -> dict:
        """重命名班级档案。

        对应需求：PRD F-03 / AC-03③。
        """
        try:
            roster = self._storage.rename_roster(str(old_name or ""), str(new_name or ""))
        except ValueError as exc:
            return self._fail(str(exc), code="invalid_roster")
        except OSError as exc:
            return self._fail(f"重命名失败：{exc}", code="io_error")

        if self._current.name == str(old_name or "").strip():
            self._current = roster
            self._persist_current_name()

        return self._ok(
            current=self._current.name,
            rosters=self._storage.list_rosters(),
        )

    def delete_roster(self, name: str) -> dict:
        """删除班级档案。

        对应需求：PRD F-03 / AC-03③。

        若删除的是当前班级，会自动切换到剩余班级中的第一个；
        一个都不剩时回到"无班级"状态（名单为空，触发 PRD E-01 的引导）。
        """
        cleaned = str(name or "").strip()
        if not cleaned:
            return self._fail("班级名称不能为空", code="invalid_roster")

        if not self._storage.delete_roster(cleaned):
            return self._fail(f"班级「{cleaned}」不存在", code="invalid_roster")

        if self._current.name == cleaned:
            remaining = self._storage.list_rosters()
            self._current = self._storage.load_roster(remaining[0]) if remaining else Roster()
            self._sync_drawer()
            self._persist_current_name()

        return self._ok(
            current=self._current.name,
            rosters=self._storage.list_rosters(),
        )

    # ==================================================================
    # CSV 导入与模板（PRD F-02 / F-02b / AC-02 / AC-02b）
    # ==================================================================

    def import_csv(self) -> dict:
        """弹出系统文件选择框，导入 CSV 名单。

        对应需求：PRD F-02 / E-08 / §6.5。

        实现要点：
            · 编码按 utf-8-sig → utf-8 → gbk 顺序嗅探
            · 首行若为表头（Num/Name 或 学号/姓名）则跳过
            · 解析失败不得破坏当前已有名单（E-08）—— 解析全部成功后才写盘
            · 若当前班级已有名单，先返回 need_confirm，等前端询问老师后
              再调用 apply_import() 落实「覆盖」或「追加」（PRD TI-11）

        返回：
            · 当前班级为空 → {"ok": True, "imported": n, ...}（已直接导入）
            · 当前班级非空 → {"ok": True, "need_confirm": True,
                             "pending": n, "existing": m, "modes": [...]}
            · 失败         → {"ok": False, "code": ..., "message": ...}
        """
        path = self._pick_file(_CSV_FILE_TYPES)
        if not path:
            return self._fail("已取消导入", code="cancelled")

        try:
            students = importer.import_csv(path)
        except ValueError as exc:
            # PRD E-08：给出明确错误，且**不修改现有名单**（此处尚未写盘）
            return self._fail(str(exc), code="invalid_csv")
        except OSError as exc:
            return self._fail(f"读取文件失败：{exc}", code="io_error")

        self._pending_import = students

        if self._current.students:
            return self._ok(
                need_confirm=True,
                pending=len(students),
                existing=len(self._current.students),
                modes=list(IMPORT_MODES),
                message=(
                    f"当前班级已有 {len(self._current.students)} 名学生，"
                    f"本次将导入 {len(students)} 名，请选择处理方式"
                ),
            )

        # 目标班级为空：直接追加，不打断老师
        result = self._apply_import_locked("append")
        if result.get("ok"):
            result["need_confirm"] = False
        return result

    def apply_import(self, mode: str) -> dict:
        """落实上一次 import_csv() 解析出的数据（PRD TI-11）。

        参数：
            mode: "overwrite"（覆盖当前班级）或 "append"（追加到当前班级）。
        """
        if not self._pending_import:
            return self._fail("没有待导入的数据，请重新选择文件", code="invalid_csv")

        cleaned = str(mode or "").strip().lower()
        if cleaned not in IMPORT_MODES:
            return self._fail("导入方式只能是「覆盖」或「追加」", code="invalid_roster")
        return self._apply_import_locked(cleaned)

    def create_csv_template(self) -> dict:
        """在 data/ 目录生成标准 CSV 模板。

        对应需求：PRD F-02b / AC-02b。

        实现要点：
            · 文件名为 roster_template.csv
            · 内容仅含 Num,Name 表头，**不含示例数据行**
            · 必须写为 UTF-8 with BOM（utf-8-sig），否则 Excel 打开中文乱码

        返回：
            {"ok": True, "path": ..., "overwritten": 是否覆盖了已有文件}
        """
        target = self._storage.file_path(importer.TEMPLATE_FILENAME)
        already_exists = target.is_file()
        try:
            written = importer.create_template(target)
        except OSError as exc:
            return self._fail(f"创建模板失败：{exc}", code="io_error")
        return self._ok(path=str(written), overwritten=already_exists)

    # ==================================================================
    # 抽取（PRD F-05 / F-06 / F-07 / E-01 / E-02 / E-03 / §6.3）
    # ==================================================================

    def draw(self, count: int = DRAW_ONCE) -> dict:
        """执行一次抽取。

        对应需求：PRD F-06（count=1）/ F-07（count=5）/ E-01 / E-02 / E-03。

        抽取**不消费**单次性 armed 状态（drift_offset / wake_up_armed）：
        · drift_offset —— 由 reveal 时前端调用 apply_drift_reveal() 消费，
                         让"翻到的是另一个人"在视觉上与翻牌动作绑定；
        · wake_up_armed —— 由 reveal 时前端调用 consume_wake_up_armed() 消费，
                          bingo 与翻牌 click 同步播放。
        旧版在 draw 时就消费掉这两个 armed，导致"纯后端的偏移与翻牌脱钩、
        老师完全看不见效果"，2026-09-13 第三轮反馈后重构。

        参数：
            count: 抽取人数，界面只有 1 和 5 两个取值。

        返回：
            {"ok": True, "students": [{"num": ..., "name": ...}, ...],
             "reset": 本轮是否刚重置,
             "drifted": 当前是否仍带漂移 armed（前端 reveal 时上报 raw 学生）,
             "drift_offset": 当前 armed 的漂移偏移值,
             "wake_up_played": 本轮是否仍 armed 待播 bingo,
             "message": 提示文本, "remaining": 候选池余量}
        """
        try:
            amount = int(count)
        except (TypeError, ValueError):
            return self._fail(f"抽取人数不合法：{count!r}", code="invalid_count")

        # peek —— 只读不消费，交给 reveal 阶段独占消费
        drift = self._fx.peek_drift()
        wake_up_to_play = self._fx.wake_up_armed

        try:
            outcome = self._drawer.draw(amount)
        except ValueError as exc:
            if self._drawer.is_empty:
                # PRD E-01：名单为空的引导提示，前端可一键跳转到设置面板
                return self._fail(
                    MSG_EMPTY_ROSTER,
                    code="empty_roster",
                    open_settings=True,
                )
            return self._fail(str(exc), code="invalid_count")

        messages: list[str] = []
        if outcome.reset:
            messages.append(MSG_NEW_ROUND)  # PRD E-03
        if outcome.is_short:
            # PRD E-02：候选池不足时提示实际抽出的人数
            messages.append(MSG_SHORT_TEMPLATE.format(count=outcome.count))
        if drift is not None:
            messages.append(
                f"漂移已武装（{'+' if drift > 0 else ''}{drift}），第一次翻牌将替换显示的学生"
            )
        if wake_up_to_play:
            pct = (
                int(round(self._fx.wake_up_volume * 100))
                if self._fx.wake_up_volume is not None
                else 0
            )
            messages.append(f"本轮叫醒已武装：翻开卡牌的同时立即播 bingo（音量 {pct}%）")

        return self._ok(
            students=[s.to_dict() for s in outcome.students],
            count=outcome.count,
            requested=outcome.requested,
            reset=outcome.reset,
            drifted=drift is not None,
            drift_offset=drift,
            wake_up_played=wake_up_to_play,
            wake_up_volume=self._fx.wake_up_volume,  # 暴露给前端做音量乘数
            remaining=self._drawer.remaining,
            message="；".join(messages),
        )

    def set_dedupe(self, enabled: bool) -> dict:
        """切换去重模式。

        对应需求：PRD F-05 / §6.3 / AC-04④。

        注意：切换到"开"时必须重建候选池，避免残留脏状态。
        """
        flag = bool(enabled)
        self._drawer.set_dedupe(flag)
        self._config = self._storage.update_config({"dedupe": flag})
        return self._ok(dedupe=self._drawer.dedupe, remaining=self._drawer.remaining)

    # ==================================================================
    # 特殊效果（F-17a / F-17b / F-17c —— 替换 F-18 后的 P2 三件套）
    # ==================================================================

    def get_effect_state(self) -> dict:
        """拉取三个特殊效果的当前状态，供界面渲染按钮状态。

        返回的字典包括：
            boost             加权名单 [{"num","name"}, ...]
            drift_armed       是否已武装下一次漂移
            drift_offset      漂移偏移值
            drift_options     可选的漂移档位 [{label,offset}, ...]
            wake_up_volume    老师选择的音量档
            wake_up_armed     是否已"确认使用"
            wake_up_options   音量档位 [{label,level}, ...]
        """
        return self._ok(state=self._fx.to_dict())

    def list_students_for_boost(self) -> dict:
        """返回当前班级的全部学生（供 F-17a 增大概率菜单渲染选择列表）。

        对应需求：F-17a「弹出选择菜单，列出全部学生供用户选择」。
        """
        return self._ok(
            students=[s.to_dict() for s in self._current.students],
            boosted=self._fx.get_boost_list(),
        )

    def set_boost(self, students: list[dict]) -> dict:
        """设置 F-17a 加权名单（整体替换）。

        参数：
            students: [{"num": ..., "name": ...}, ...]
                       姓名为空的记录会被忽略；不在当前班级里的记录也会被忽略。
        """
        if not isinstance(students, list):
            return self._fail("加权名单格式不正确", code="invalid_roster")

        self._fx.set_boost(students)
        # 同步给抽取引擎，触发候选池重建
        self._drawer.set_boost(
            {(s.num, s.name) for s in self._current.students if (s.num, s.name) in self._fx.boost}
        )
        return self._ok(boosted=self._fx.get_boost_list())

    def clear_boost(self) -> dict:
        """清空 F-17a 加权名单。"""
        self._fx.clear_boost()
        self._drawer.clear_boost()
        return self._ok(boosted=[])

    def arm_drift(self, offset: int) -> dict:
        """F-17b：武装下一次抽卡的漂移偏移。

        参数：
            offset: 1（轻移）或 3（重移）。
        """
        try:
            offset_int = int(offset)
        except (TypeError, ValueError):
            return self._fail(f"漂移偏移必须为整数：{offset!r}", code="invalid_count")
        if offset_int not in DRIFT_OFFSETS:
            return self._fail(
                f"漂移偏移只能是 {DRIFT_OFFSETS[0]} 或 {DRIFT_OFFSETS[-1]}，收到 {offset_int}",
                code="invalid_count",
            )
        self._fx.arm_drift(offset_int)
        return self._ok(offset=offset_int)

    def cancel_drift(self) -> dict:
        """F-17b：取消已武装的漂移。"""
        self._fx.cancel_drift()
        return self._ok()

    def apply_drift_reveal(self, raw_student: dict) -> dict:
        """F-17b：reveal 阶段由前端调用，消费 armed 并返回「按 offset 偏移后的学生」。

        旧版逻辑（draw 阶段就偏移）：
            把抽到的学生按下标循环偏移 N 位、然后交给前端渲染。
            老师翻牌时完全感知不到"漂"，反馈"无实际意义"（PRD 2026-09-13 第三轮）。

        新版逻辑（reveal 阶段才偏移）：
            1. draw 时只产出真实随机结果；
            2. 前端 reveal 时把当前卡的 raw 学生（仍然显示的是 RNG 抽到的那个）
               上报到这里；
            3. 后端按当前 armed offset 在全班中循环偏移，replace 出一个新学生
               供前端 swapFront；
            4. 同时清空 armed （一次性）；多次 reveal 同一 armed 也只能生效一次。

        参数：
            raw_student: {"num": ..., "name": ...}

        返回：
            ok=true, student=替换后的学生（offset=None/0 时等于 raw）,
            applied=本轮是否真的发生漂移, offset=本次实际偏移值
        """
        if not isinstance(raw_student, dict):
            return self._fail("漂移参数必须是 {num, name}", code="invalid_roster")

        # 一次性消费
        offset = self._fx.consume_drift()
        if offset is None or offset <= 0:
            return self._ok(
                student={
                    "num": str(raw_student.get("num") or ""),
                    "name": str(raw_student.get("name") or ""),
                },
                applied=False,
                offset=None,
            )

        # 在当前班级里按 (num, name) 找下标；找不到则兜底返回原值
        try:
            target = next(
                s
                for s in self._current.students
                if s.num == str(raw_student.get("num") or "")
                and s.name == str(raw_student.get("name") or "")
            )
        except StopIteration:
            return self._ok(
                student={
                    "num": str(raw_student.get("num") or ""),
                    "name": str(raw_student.get("name") or ""),
                },
                applied=False,
                offset=offset,
            )

        students = list(self._current.students)
        n = len(students)
        if n == 0:
            return self._ok(student=target.to_dict(), applied=False, offset=offset)
        idx = students.index(target)
        target_idx = (idx + int(offset)) % n
        replaced = students[target_idx].to_dict()
        return self._ok(student=replaced, applied=True, offset=offset)

    def set_wake_up_volume(self, volume: float) -> dict:
        """F-17c：设置待确认的音量档（仅影响 bingo 音量，不再调系统音量）。

        参数：
            volume: 0.4 / 0.6 / 0.9 三档之一。
        """
        try:
            level = float(volume)
        except (TypeError, ValueError):
            return self._fail(f"音量值不合法：{volume!r}", code="invalid_count")
        # 复用状态机内部的 3 档校验（避免接口层与状态机各写一份魔数）
        if not self._fx.set_wake_up_volume(level):
            return self._fail("音量档只能是 40% / 60% / 90%", code="invalid_count")
        return self._ok(volume=self._fx.wake_up_volume)

    def confirm_wake_up(self) -> dict:
        """F-17c：老师点击「确认使用」后，仅 **武装 bingo 在下一次翻牌时按选定音量播放**。

        第六轮修订（2026-09-13）：不再调 Windows 系统音量。
        所选音量档（0.4 / 0.6 / 0.9）作为 **bingo 音量乘数**传给前端，由前端在
        播 bingo 时使用。系统主音量由老师用系统托盘控制，不再被本软件影响。

        返回信封：
            ok=true, volume=<0.4/0.6/0.9>, bingo_volume=同上（语义别名）
        """
        if not self._fx.confirm_wake_up():
            return self._fail("请先选择音量档", code="invalid_count")
        vol = self._fx.wake_up_volume
        return self._ok(volume=vol, bingo_volume=vol)

    def cancel_wake_up(self) -> dict:
        """F-17c：取消待确认或已确认的叫醒服务。"""
        self._fx.cancel_wake_up()
        return self._ok()

    def consume_wake_up_armed(self) -> dict:
        """F-17c：首张翻牌完成后由前端调用，消费 bingo armed 标志。

        若已 armed 则返回 was_armed=True，前端据此播 bingo 五连音并提示「本轮叫醒生效」。
        """
        was_armed = self._fx.consume_wake_up()
        return self._ok(was_armed=was_armed)

    # ==================================================================
    # 名单录入辅助（PRD F-01 / AC-01④）
    # ==================================================================

    def parse_batch_text(self, text: str) -> dict:
        """解析"批量粘贴"文本，返回预览用的学生列表（不落盘）。

        对应需求：PRD F-01 / AC-01④。

        兼容 ``学号,姓名`` / ``学号<空格或Tab>姓名`` / 纯 ``姓名`` 三种写法，
        空行自动过滤（详见 core/importer.parse_batch_text 的说明）。

        返回：
            {"ok": True, "students": [...], "count": n, "duplicate": 与现有名单重复的条数}
        """
        try:
            students = importer.parse_batch_text(str(text or ""))
        except ValueError as exc:
            return self._fail(str(exc), code="invalid_csv")

        existing = {(s.num, s.name) for s in self._current.students}
        duplicate = sum(1 for s in students if (s.num, s.name) in existing)

        return self._ok(
            students=[s.to_dict() for s in students],
            count=len(students),
            duplicate=duplicate,
        )

    # ==================================================================
    # 设置（PRD F-10 / AC-09）
    # ==================================================================

    def get_settings(self) -> dict:
        """读取全部配置。

        对应需求：PRD F-10 / AC-09④。
        """
        return self._ok(settings=self._settings_payload())

    def save_settings(self, settings: dict) -> dict:
        """保存配置（部分字段更新）。

        对应需求：PRD F-10 / AC-09④（重启后保留）。

        参数：
            settings: 只包含需要变更的键，例如 {"muted": True, "volume": 0.5}。
        """
        if not isinstance(settings, dict):
            return self._fail("设置数据格式不正确", code="invalid_roster")

        try:
            config = self._storage.update_config(settings)
        except (TypeError, OSError) as exc:
            return self._fail(f"保存设置失败：{exc}", code="io_error")

        self._config = config
        # 去重开关同时要作用于抽取引擎，否则界面开关与引擎状态会脱节
        if "dedupe" in settings:
            self._drawer.set_dedupe(bool(config.get("dedupe", True)))

        return self._ok(settings=self._settings_payload())

    # ==================================================================
    # 外部程序与目录（PRD F-09 / F-02c / E-04 / E-05）
    # ==================================================================

    def choose_external_exe(self) -> dict:
        """弹出文件选择框，指定「简洁视图」要启动的外部程序。

        对应需求：PRD F-09 / AC-08①②。

        选中后立即写入配置，重启后仍保留（AC-08②）。
        """
        path = self._pick_file(_EXE_FILE_TYPES)
        if not path:
            return self._fail("已取消选择", code="cancelled")

        try:
            self._config = self._storage.update_config({"external_exe": path})
        except OSError as exc:
            return self._fail(f"保存路径失败：{exc}", code="io_error")
        return self._ok(path=path)

    def launch_external(self) -> dict:
        """启动设置中指定的外部程序。

        对应需求：PRD F-09 / E-04 / E-05 / §6.4。

        注意：该程序与 StellarDraw **完全独立** —— 不传参、不共享数据、
        不建立进程间通信。主程序不退出、不最小化。
        """
        raw = str(self._config.get("external_exe", ""))
        ok, message = launcher.launch(raw)
        if ok:
            return self._ok(path=launcher.normalize_path(raw))

        # PRD E-04（未设置）与 E-05（路径失效）需要不同的引导文案，
        # 两者都引导老师一步直达设置面板
        code = "not_set" if not launcher.normalize_path(raw) else "not_found"
        return self._fail(message, code=code, open_settings=True)

    def open_data_dir(self) -> dict:
        """调用系统文件管理器打开 data/ 目录。

        对应需求：PRD F-02c / AC-26（TI-14 已确认）。

        目录不存在时会先自动创建再打开。
        """
        try:
            opened = launcher.open_directory(self._storage.data_root)
        except OSError as exc:
            return self._fail(f"无法打开数据目录：{exc}", code="io_error")
        return self._ok(path=str(opened))
