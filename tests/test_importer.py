"""core/importer.py 的单元测试。

对应需求：PRD F-01 / F-02 / F-02b / E-08 / §6.5 / AC-01④ / AC-02 / AC-02b

CSV 编码是本项目最容易在真实教室环境翻车的地方（R-09），
因此这里用**真实字节**构造 UTF-8 BOM / UTF-8 / GBK 三种文件，
逐一验证中文姓名不乱码；模板文件则逐字节校验 BOM 与换行。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from stellardraw.core import importer
from stellardraw.core.importer import (
    ENCODING_CANDIDATES,
    TEMPLATE_CONTENT,
    TEMPLATE_FILENAME,
    create_template,
    detect_encoding,
    import_csv,
    is_header_row,
    parse_batch_text,
    parse_csv_text,
)

# 中文姓名样张：覆盖三种文件的编码往返
SAMPLE_ROWS = [("01", "张小雨"), ("02", "李雷"), ("03", "韩梅梅")]

CHINESE_HEADER_CSV = "学号,姓名\r\n01,张小雨\r\n"


def write_csv(path: Path, text: str, encoding: str) -> Path:
    """按指定编码把文本写成 CSV 文件（用二进制写入以精确控制字节）。"""
    path.write_bytes(text.encode(encoding))
    return path


class TestCreateTemplate:
    """CSV 模板生成（PRD F-02b / AC-02b）。"""

    def test_creates_file(self, tmp_path: Path) -> None:
        """点击按钮后模板文件被创建。"""
        target = tmp_path / "roster_template.csv"
        created = create_template(target)
        assert created == target
        assert target.is_file()

    def test_content_is_header_only(self, tmp_path: Path) -> None:
        """内容仅含 Num,Name 表头，**不含示例数据行**（避免被误导入）。"""
        target = create_template(tmp_path / "roster_template.csv")
        raw = target.read_bytes()
        assert raw.decode("utf-8-sig") == TEMPLATE_CONTENT
        assert raw.decode("utf-8-sig").strip() == "Num,Name"

    def test_has_utf8_bom(self, tmp_path: Path) -> None:
        """必须带 BOM，否则 Windows Excel 双击打开中文会乱码（R-09）。"""
        target = create_template(tmp_path / "roster_template.csv")
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_no_doubled_line_breaking(self, tmp_path: Path) -> None:
        """换行不能被二次翻译成 CRCRLF。"""
        target = create_template(tmp_path / "roster_template.csv")
        assert b"\r\r\n" not in target.read_bytes()

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        """父目录不存在时先创建（首次运行 data/ 可能还没有）。"""
        target = tmp_path / "data" / "roster_template.csv"
        create_template(target)
        assert target.is_file()

    def test_template_can_be_filled_and_imported(self, tmp_path: Path) -> None:
        """AC-02b④：在模板里填 3 名学生后能被导入流程正确读取。"""
        target = create_template(tmp_path / TEMPLATE_FILENAME)
        with target.open("a", encoding="utf-8-sig", newline="") as fh:
            fh.write("01,张小雨\r\n02,李雷\r\n03,韩梅梅\r\n")

        students = import_csv(target)
        assert [s.to_dict() for s in students] == [
            {"num": "01", "name": "张小雨"},
            {"num": "02", "name": "李雷"},
            {"num": "03", "name": "韩梅梅"},
        ]

    def test_csv_module_reads_template(self, tmp_path: Path) -> None:
        """用标准 csv 模块读模板，表头应恰好是 Num,Name（无 BOM 残留）。"""
        target = create_template(tmp_path / TEMPLATE_FILENAME)
        with target.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        assert rows == [["Num", "Name"]]

    def test_overwrite_existing_template(self, tmp_path: Path) -> None:
        """模板已存在时按调用方要求覆盖（是否确认由界面负责）。"""
        target = tmp_path / TEMPLATE_FILENAME
        target.write_text("旧内容", encoding="utf-8")
        create_template(target)
        assert target.read_bytes().decode("utf-8-sig") == TEMPLATE_CONTENT


class TestDetectEncoding:
    """编码嗅探（PRD §6.5）。"""

    def test_utf8_with_bom(self) -> None:
        """带 BOM 的文件必须识别为 utf-8-sig，否则首字段会残留 \\ufeff。"""
        assert detect_encoding("Num,Name".encode("utf-8-sig")) == "utf-8-sig"

    def test_utf8_without_bom(self) -> None:
        """不带 BOM 的 UTF-8 识别为 utf-8。"""
        assert detect_encoding("Num,Name\r\n01,张小雨".encode()) == "utf-8"

    def test_gbk(self) -> None:
        """Excel 另存的 GBK 文件识别为 gbk。"""
        assert detect_encoding("01,张小雨".encode("gbk")) == "gbk"

    def test_undecodable_bytes_raise(self) -> None:
        """全部候选编码都失败时抛出 ValueError（供上层转成友好提示）。"""
        with pytest.raises(ValueError, match="编码"):
            detect_encoding(b"\xff\xfe\x00\x01\x80\x81")

    def test_empty_bytes_decodes_as_utf8(self) -> None:
        """空字节流没有 BOM，因此按 utf-8 处理（不会误报成 utf-8-sig）。"""
        assert detect_encoding(b"") == "utf-8"

    def test_bom_is_detected_by_prefix(self) -> None:
        """是否使用 utf-8-sig 取决于字节流开头有没有 BOM 前缀。"""
        assert detect_encoding(b"\xef\xbb\xbfNum,Name") == "utf-8-sig"
        assert detect_encoding(b"Num,Name") == "utf-8"

    def test_candidate_order_puts_bom_first(self) -> None:
        """嗅探顺序必须是 utf-8-sig → utf-8 → gbk（顺序错了会乱码）。"""
        assert ENCODING_CANDIDATES == ("utf-8-sig", "utf-8", "gbk")


class TestHeaderDetection:
    """表头识别（PRD §6.5）。"""

    @pytest.mark.parametrize(
        "row",
        [
            ["Num", "Name"],
            ["num", "name"],
            ["NUM", "NAME"],
            ["No", "Name"],
            ["学号", "姓名"],
            ["编号", "名字"],
            ["Num", ""],
        ],
    )
    def test_recognized_headers(self, row: list[str]) -> None:
        """英文/中文表头（含大小写差异）都能被识别。"""
        assert is_header_row(row) is True

    @pytest.mark.parametrize(
        "row",
        [
            ["01", "张小雨"],
            ["01", "李雷"],
            ["张小雨", ""],
            [],
            [""],
        ],
    )
    def test_data_rows_are_not_headers(self, row: list[str]) -> None:
        """数据行不能被误判成表头，否则会整行丢失。"""
        assert is_header_row(row) is False


class TestImportCsv:
    """CSV 文件导入（PRD F-02 / AC-02 / E-08）。"""

    def test_import_utf8_bom(self, tmp_path: Path) -> None:
        """AC-02③：UTF-8 带 BOM 的 Excel 文件不乱码。"""
        path = write_csv(tmp_path / "a.csv", "Num,Name\r\n01,张小雨\r\n", "utf-8-sig")
        assert import_csv(path)[0].to_dict() == {"num": "01", "name": "张小雨"}

    def test_import_utf8_without_bom(self, tmp_path: Path) -> None:
        """AC-02③：UTF-8 无 BOM 同样不乱码。"""
        path = write_csv(tmp_path / "b.csv", "Num,Name\r\n01,张小雨\r\n", "utf-8")
        assert import_csv(path)[0].name == "张小雨"

    def test_import_gbk(self, tmp_path: Path) -> None:
        """AC-02②：Windows Excel 另存的 GBK 文件中文不乱码。"""
        path = write_csv(tmp_path / "c.csv", "Num,Name\r\n01,张小雨\r\n", "gbk")
        assert import_csv(path)[0].name == "张小雨"

    def test_import_chinese_header(self, tmp_path: Path) -> None:
        """AC-02④：中文表头（学号/姓名）同样能被正确跳过。"""
        path = write_csv(tmp_path / "d.csv", CHINESE_HEADER_CSV, "utf-8-sig")
        students = import_csv(path)
        assert len(students) == 1
        assert students[0].to_dict() == {"num": "01", "name": "张小雨"}

    def test_import_45_students(self, tmp_path: Path) -> None:
        """AC-02①：导入 45 人名单，条数与学号姓名对应关系都正确。"""
        lines = ["Num,Name"]
        for index in range(1, 46):
            lines.append(f"{index:02d},学生{index}")
        path = write_csv(tmp_path / "e.csv", "\r\n".join(lines) + "\r\n", "utf-8-sig")

        students = import_csv(path)
        assert len(students) == 45
        assert students[0].to_dict() == {"num": "01", "name": "学生1"}
        assert students[-1].to_dict() == {"num": "45", "name": "学生45"}
        # 学号与姓名必须一一对应，不能串行
        assert all(s.num == f"{i:02d}" and s.name == f"学生{i}" for i, s in enumerate(students, 1))

    def test_import_without_header(self, tmp_path: Path) -> None:
        """没有表头的文件也能导入（老师手工另存的情况很常见）。"""
        path = write_csv(tmp_path / "f.csv", "01,张小雨\r\n02,李雷\r\n", "utf-8-sig")
        assert len(import_csv(path)) == 2

    def test_import_skips_blank_lines(self, tmp_path: Path) -> None:
        """全空行被过滤，不影响计数。"""
        text = "Num,Name\r\n\r\n01,张小雨\r\n\r\n\r\n02,李雷\r\n\r\n"
        path = write_csv(tmp_path / "g.csv", text, "utf-8-sig")
        assert [s.name for s in import_csv(path)] == ["张小雨", "李雷"]

    def test_import_skips_rows_without_name(self, tmp_path: Path) -> None:
        """姓名为空的行整行跳过（PRD §6.5 字段规则）。"""
        path = write_csv(tmp_path / "h.csv", "Num,Name\r\n01,\r\n02,李雷\r\n", "utf-8-sig")
        assert [s.name for s in import_csv(path)] == ["李雷"]

    def test_import_allows_empty_num(self, tmp_path: Path) -> None:
        """学号可留空（PRD E-07：结果卡不显示学号行）。"""
        path = write_csv(tmp_path / "i.csv", "Num,Name\r\n,张小雨\r\n", "utf-8-sig")
        student = import_csv(path)[0]
        assert student.has_num is False
        assert student.name == "张小雨"

    def test_import_single_column_as_name(self, tmp_path: Path) -> None:
        """只有一列时按"纯姓名"处理（与 F-01 的纯姓名写法一致）。"""
        path = write_csv(tmp_path / "j.csv", "张小雨\r\n李雷\r\n", "utf-8-sig")
        assert [s.to_dict() for s in import_csv(path)] == [
            {"num": "", "name": "张小雨"},
            {"num": "", "name": "李雷"},
        ]

    def test_import_missing_file(self, tmp_path: Path) -> None:
        """文件不存在时给出明确错误而不是崩溃（PRD E-08）。"""
        with pytest.raises(ValueError, match="文件不存在"):
            import_csv(tmp_path / "不存在.csv")

    def test_import_empty_file(self, tmp_path: Path) -> None:
        """空文件报"内容为空"。"""
        path = tmp_path / "empty.csv"
        path.write_bytes(b"")
        with pytest.raises(ValueError, match="为空"):
            import_csv(path)

    def test_import_header_only(self, tmp_path: Path) -> None:
        """只有表头没有数据时报"没有可导入的学生数据"。"""
        path = write_csv(tmp_path / "k.csv", "Num,Name\r\n", "utf-8-sig")
        with pytest.raises(ValueError, match="没有可导入"):
            import_csv(path)

    def test_import_too_many_columns_reports_line_number(self, tmp_path: Path) -> None:
        """AC-02⑤/E-08：列数异常时指出具体行号，方便老师自己改。"""
        text = "Num,Name\r\n01,张小雨\r\n02,李雷,初一(3)班\r\n"
        path = write_csv(tmp_path / "l.csv", text, "utf-8-sig")
        with pytest.raises(ValueError, match="第 3 行"):
            import_csv(path)

    def test_import_trailing_empty_column_is_tolerated(self, tmp_path: Path) -> None:
        """Excel 常见的行尾多余空列（"01,张小雨,"）应被容忍。"""
        path = write_csv(tmp_path / "m.csv", "Num,Name\r\n01,张小雨,\r\n", "utf-8-sig")
        assert import_csv(path)[0].name == "张小雨"

    def test_import_undecodable_file(self, tmp_path: Path) -> None:
        """编码无法识别时给出可读错误。"""
        path = tmp_path / "n.csv"
        path.write_bytes(b"\xff\xfe\x00\x01\x80\x81")
        with pytest.raises(ValueError, match="编码"):
            import_csv(path)


class TestParseCsvText:
    """纯文本解析（不触盘的核心转换）。"""

    def test_header_skipped_only_if_first(self, tmp_path: Path) -> None:
        """表头只可能出现在第一行有效内容上，中间行不误判。"""
        students = parse_csv_text("01,张小雨\r\n02,姓名\r\n")
        assert [s.to_dict() for s in students] == [
            {"num": "01", "name": "张小雨"},
            {"num": "02", "name": "姓名"},
        ]

    def test_whitespace_cells_are_trimmed(self) -> None:
        """单元格首尾空白被清理。"""
        students = parse_csv_text(" 01 , 张小雨 \r\n")
        assert students[0].to_dict() == {"num": "01", "name": "张小雨"}

    def test_empty_text_raises(self) -> None:
        """空文本报错。"""
        with pytest.raises(ValueError, match="没有可导入"):
            parse_csv_text("")


class TestParseBatchText:
    """批量粘贴解析（PRD F-01 / AC-01④）。"""

    def test_three_styles_mixed(self) -> None:
        """AC-01④：混用「学号,姓名」与「纯姓名」等写法都能正确解析出记录。"""
        text = """
        01,张小雨
        02 李雷
        05\t韩梅梅
        王小二
        06,赵六
        """
        students = parse_batch_text(text)
        assert [s.to_dict() for s in students] == [
            {"num": "01", "name": "张小雨"},
            {"num": "02", "name": "李雷"},
            {"num": "05", "name": "韩梅梅"},
            {"num": "", "name": "王小二"},
            {"num": "06", "name": "赵六"},
        ]

    def test_ten_lines_with_blank_lines(self) -> None:
        """AC-01④：10 行粘贴内容 + 穿插空行，最终得到 10 条记录。"""
        lines = [f"{i:02d},学生{i}" if i % 2 else f"学生{i}" for i in range(1, 11)]
        text = "\n\n".join(lines) + "\n\n"
        assert len(parse_batch_text(text)) == 10

    def test_fullwidth_comma_tolerated(self) -> None:
        """中文输入法打出的全角逗号也能正确切分。"""
        assert parse_batch_text("01，张小雨")[0].to_dict() == {"num": "01", "name": "张小雨"}

    def test_fullwidth_space_tolerated(self) -> None:
        """全角空格同样作为分隔符。"""
        assert parse_batch_text("01　张小雨")[0].to_dict() == {"num": "01", "name": "张小雨"}

    def test_english_name_is_not_split(self) -> None:
        """英文姓名（首段无数字）不应被拆成"学号 + 姓名"。"""
        assert parse_batch_text("Li Lei")[0].to_dict() == {"num": "", "name": "Li Lei"}

    def test_trailing_comma_treated_as_name(self) -> None:
        """形如"张小雨,"的写法按纯姓名处理，不产生空姓名记录。"""
        assert parse_batch_text("张小雨,")[0].to_dict() == {"num": "", "name": "张小雨"}

    def test_header_line_skipped(self) -> None:
        """老师连表头一起粘贴时，表头被跳过。"""
        assert [s.name for s in parse_batch_text("Num,Name\n01,张小雨")] == ["张小雨"]

    def test_num_only_line_skipped(self) -> None:
        """只有学号的残行被跳过，不影响其它记录。"""
        assert [s.name for s in parse_batch_text("01\n02,李雷")] == ["李雷"]

    def test_empty_input_raises(self) -> None:
        """全空内容报错，供界面提示。"""
        with pytest.raises(ValueError, match="没有解析到"):
            parse_batch_text("\n\n   \n")

    def test_name_with_inner_space_preserved(self) -> None:
        """姓名内部空格保留（如复姓英文写法），不丢字符。"""
        assert parse_batch_text("Mary Jane")[0].name == "Mary Jane"

    def test_importer_module_exports_filename(self) -> None:
        """模板文件名与 PRD F-02b 约定一致。"""
        assert TEMPLATE_FILENAME == "roster_template.csv"
        assert importer.CSV_HEADER == ("Num", "Name")
