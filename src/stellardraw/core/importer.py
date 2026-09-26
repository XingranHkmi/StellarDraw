"""名单文本解析：CSV 文件导入、CSV 模板生成、批量粘贴解析。

对应需求：PRD F-01（批量粘贴）/ F-02 / F-02b / E-08 / §6.5 / AC-01④ / AC-02

⚠️ 本模块最容易出错的地方是**编码**，务必遵守下列约定：

    1. 模板文件必须写为 **UTF-8 with BOM**（Python 中的 "utf-8-sig"）。
       原因：Windows 版 Excel 双击打开"无 BOM 的 UTF-8 CSV"时会按 GBK 解析，
       中文姓名会显示成乱码。带 BOM 后 Excel 才能正确识别编码。

    2. 导入时按 utf-8-sig → utf-8 → gbk 的顺序依次尝试，任一成功即采用。
       这样同时兼容三类来源：本软件生成的模板、编辑器另存的 UTF-8、
       Excel 另存的 GBK。

    3. 首行若识别为表头（Num / Name 的各种大小写，或中文"学号""姓名"）则跳过。

    4. 解析失败时必须给出明确错误，且**不得破坏当前已有名单**（PRD E-08）。
       因此本模块只负责"文件 → 学生列表"的纯转换，不接触存储；
       由上层（api.py）先解析成功、再落盘。

    5. 写文件时必须显式指定 ``newline=""``。
       否则在 Windows 上 Python 会把 ``"\\r\\n"`` 再翻译一次，变成 ``"\\r\\r\\n"``，
       模板文件每行会多出一个空行。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from stellardraw.core.roster import Student

# 标准 CSV 表头（PRD F-02b 规定为 Num,Name）
CSV_HEADER: tuple[str, str] = ("Num", "Name")

# 模板文件名
TEMPLATE_FILENAME = "roster_template.csv"

# 模板正文：仅表头 + 换行，**不含示例数据行**
# 用 CRLF 是因为 Windows 版 Excel 对 CRLF 的兼容性最好
TEMPLATE_CONTENT = "Num,Name\r\n"

# 编码嗅探顺序：必须先试带 BOM 的 utf-8-sig，否则 BOM 字符会残留在首个字段里
ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "utf-8", "gbk")

# UTF-8 BOM 的字节前缀
UTF8_BOM = b"\xef\xbb\xbf"

# 可识别的表头写法（比较时统一转小写，中文不受影响）
NUM_HEADER_ALIASES = frozenset({"num", "no", "number", "id", "学号", "编号"})
NAME_HEADER_ALIASES = frozenset({"name", "姓名", "名字", "学生姓名"})

# CSV 期望的列数（学号, 姓名）
_EXPECTED_COLUMNS = 2

# 全角字符容错：中文输入法下老师很容易打出全角逗号与全角空格
_FULLWIDTH_COMMA = "，"
_FULLWIDTH_SPACE = "\u3000"


# ======================================================================
# 模板生成（PRD F-02b / AC-02b）
# ======================================================================


def create_template(target: Path) -> Path:
    """生成标准 CSV 模板文件。

    对应需求：PRD F-02b / AC-02b。

    模板内容**仅含表头、不含示例数据行** —— 避免老师忘记删除示例行，
    把"张三"当成本班学生导入（PRD F-02b 明确规定）。

    参数：
        target: 模板文件的完整路径，通常传 paths.data_file(TEMPLATE_FILENAME)。

    返回：
        实际写入的文件路径。

    异常：
        OSError: 目录不可写时抛出，由上层转换为友好提示。
    """
    target = Path(target)
    # 父目录可能尚未创建（首次运行），先补齐
    target.parent.mkdir(parents=True, exist_ok=True)

    # encoding="utf-8-sig" 会自动写入 BOM；newline="" 阻止 Python 二次翻译换行
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        fh.write(TEMPLATE_CONTENT)
    return target


# ======================================================================
# 编码嗅探（PRD §6.5）
# ======================================================================


def detect_encoding(raw: bytes) -> str:
    """嗅探字节流的文本编码。

    对应需求：PRD §6.5。

    实现说明：
        ``utf-8-sig`` 解码器对**不带 BOM** 的 UTF-8 也能成功，因此不能无脑按
        候选顺序返回第一个成功者，否则任何 UTF-8 文件都会被标成 ``utf-8-sig``。
        这里先看字节流有没有 BOM 前缀，有才用 ``utf-8-sig``。

    参数：
        raw: 文件的原始字节内容。

    返回：
        第一个能成功解码的编码名。

    异常：
        ValueError: 全部候选编码都解码失败时抛出。
    """
    has_bom = raw.startswith(UTF8_BOM)
    for encoding in ENCODING_CANDIDATES:
        if encoding == "utf-8-sig" and not has_bom:
            # 没有 BOM 就没必要用 utf-8-sig，交给后续的 utf-8 处理即可
            continue
        try:
            raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        return encoding
    raise ValueError("无法识别文件编码，请将文件另存为 UTF-8 或 GBK 编码的 CSV")


# ======================================================================
# 行/表头判定
# ======================================================================


def normalize_cell(value: object) -> str:
    """把单元格值规整为去空白、全角空格转半角的字符串。"""
    text = "" if value is None else str(value)
    return text.replace(_FULLWIDTH_SPACE, " ").strip()


def is_header_row(row: list[str]) -> bool:
    """判断某一行是否为表头。

    对应需求：PRD §6.5。

    判定规则：
        第一列命中 NUM_HEADER_ALIASES，或第二列命中 NAME_HEADER_ALIASES，
        即视为表头（大小写不敏感）。

    参数：
        row: 已按逗号切分的一行。

    返回：
        True 表示这是表头行，应当跳过。
    """
    if not row:
        return False
    first = normalize_cell(row[0]).lower()
    second = normalize_cell(row[1]).lower() if len(row) > 1 else ""
    return first in NUM_HEADER_ALIASES or second in NAME_HEADER_ALIASES


def _has_extra_columns(row: list[str]) -> bool:
    """第 3 列及以后是否存在非空内容（用于判定"列数过多"的格式错误）。"""
    return any(normalize_cell(cell) for cell in row[_EXPECTED_COLUMNS:])


# ======================================================================
# CSV 文本解析（PRD F-02 / E-08 / AC-02）
# ======================================================================


def parse_csv_text(text: str) -> list[Student]:
    """把 CSV 文本解析为学生列表（纯函数，不接触文件系统）。

    对应需求：PRD F-02 / E-08 / §6.5。

    行为约定：
        · 首行若识别为表头则跳过（只对第一行生效，避免误伤中间的数据行）
        · 逐行读取，姓名列为空的行整行跳过
        · 学号列可留空（对应 PRD E-07）
        · 单列文件视为"只有姓名"（与 PRD F-01 的纯姓名写法保持一致）

    参数：
        text: CSV 文本内容（编码问题已在调用前解决）。

    返回：
        解析出的学生列表。

    异常：
        ValueError: 格式非法或没有可导入数据时抛出，错误信息中**带行号**，
                    便于老师自行定位问题行（PRD E-08）。
    """
    reader = csv.reader(io.StringIO(text))
    students: list[Student] = []
    first_data_row_seen = False

    # enumerate 从 1 开始，与老师在 Excel / 记事本里看到的行号一致
    for line_no, row in enumerate(reader, start=1):
        if not row or not any(normalize_cell(cell) for cell in row):
            # 全空行（含 Excel 末尾的连续空行）：直接跳过
            continue

        # 表头只可能出现在第一行有效内容上
        if not first_data_row_seen:
            first_data_row_seen = True
            if is_header_row(row):
                continue

        if len(row) > _EXPECTED_COLUMNS and _has_extra_columns(row):
            raise ValueError(
                f"第 {line_no} 行格式不正确：应为「学号,姓名」两列，实际有 "
                f"{len(row)} 列非空内容。请检查该行是否含有逗号或多余的分栏。"
            )

        if len(row) == 1:
            # 只有一列：按"纯姓名"处理（与 PRD F-01 的纯姓名写法保持一致）
            num, name = "", normalize_cell(row[0])
        else:
            num, name = normalize_cell(row[0]), normalize_cell(row[1])

        if not name:
            # 姓名为空的行整行跳过（PRD §6.5 明文规定），包括"只有学号"的残行
            continue

        students.append(Student(num=num, name=name))

    if not students:
        raise ValueError("文件中没有可导入的学生数据，请检查文件内容是否为空或格式不正确。")
    return students


def import_csv(path: Path) -> list[Student]:
    """解析 CSV 文件并返回学生列表。

    对应需求：PRD F-02 / E-08 / AC-02。

    参数：
        path: CSV 文件的完整路径。

    返回：
        解析出的学生列表。

    异常：
        ValueError: 文件不存在、编码无法识别或格式非法时抛出，
                    由上层转换为面向老师的友好提示（PRD E-08）。
        OSError:   文件读取失败（如被其他程序占用）时由 Path.read_bytes 抛出。
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"文件不存在：{path}")

    raw = path.read_bytes()
    if not raw.strip():
        raise ValueError("文件内容为空，没有可导入的学生数据。")

    encoding = detect_encoding(raw)
    text = raw.decode(encoding)
    return parse_csv_text(text)


# ======================================================================
# 批量粘贴解析（PRD F-01 / AC-01④）
# ======================================================================


def parse_batch_text(text: str) -> list[Student]:
    """解析"批量粘贴"的多行文本，返回学生列表。

    对应需求：PRD F-01 / AC-01④。需兼容三种写法：
        1. ``学号,姓名``   —— 逗号分隔（全角逗号也接受）
        2. ``学号<空格或 Tab>姓名`` —— 空白分隔
        3. ``姓名``        —— 只有姓名

    关于第 2 条的歧义处理：
        英文姓名（如 ``Li Lei``）与"学号+姓名"在空白分隔下无法从形式上区分，
        因此采用**首段是否含数字**作为判据：
            · ``07 张小雨``  → 首段含数字 → 学号 07，姓名 张小雨
            · ``Li Lei``     → 首段无数字 → 整行视为姓名 Li Lei
        中文姓名本身不含空格，故该判据在实际课堂数据上足够可靠。

    参数：
        text: 老师粘贴的原始文本。

    返回：
        学生列表（已过滤空行、已去除首尾空白）。

    异常：
        ValueError: 一行有效数据都没有时抛出（对应 PRD E-08 的明确报错要求）。
    """
    students: list[Student] = []
    first_data_row_seen = False

    for raw_line in text.splitlines():
        line = raw_line.replace(_FULLWIDTH_SPACE, " ").strip()
        if not line:
            # 空行：直接跳过（PRD F-01 要求自动过滤空行）
            continue

        # 若老师把表头一起粘进来，同样跳过
        if not first_data_row_seen:
            first_data_row_seen = True
            if is_header_row(line.split(",")):
                continue

        num, name = _split_batch_line(line)
        if not name:
            # 只有学号没有姓名：跳过而非报错 —— 粘贴场景下多半是误带进来的残行
            continue
        students.append(Student(num=num, name=name))

    if not students:
        raise ValueError("没有解析到有效的学生记录，请检查粘贴内容的格式。")
    return students


def _split_batch_line(line: str) -> tuple[str, str]:
    """把批量粘贴的单行拆成 (学号, 姓名)（内部函数）。

    "首段是否含数字"是区分「学号+姓名」与「纯姓名」的判据：
        · ``07 张小雨`` → 学号 07，姓名 张小雨
        · ``Li Lei``   → 整行视为姓名
        · ``01``       → 整行都是数字，视为"只有学号的残行"，返回空姓名（由调用方跳过）
        · ``学生2``    → 含数字但并非全数字，仍视为姓名

    返回的学号可能为空字符串（对应"只有姓名"的写法）。
    """
    comma_line = line.replace(_FULLWIDTH_COMMA, ",")

    if "," in comma_line:
        # 逗号是显式分隔符，优先采信
        num_raw, _, name_raw = comma_line.partition(",")
        num, name = normalize_cell(num_raw), normalize_cell(name_raw)
        if not name:
            # 形如 "张小雨," 的写法：把前半段当作姓名
            return "", num
        return num, name

    tokens = line.split()
    if len(tokens) >= 2:
        if any(ch.isdigit() for ch in tokens[0]):
            return tokens[0], " ".join(tokens[1:])
        return "", line

    # 单段：整行都是数字视为"只有学号的残行"，否则视为纯姓名
    # （用 isdigit 而不是"含数字"，避免把"学生2"这类姓名误判成学号）
    if line.isdigit():
        return line, ""
    return "", line
