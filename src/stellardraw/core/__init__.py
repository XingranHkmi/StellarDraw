"""核心业务逻辑包。

本包内的模块**不依赖 pywebview、不依赖任何界面代码**，
因此可以被 pytest 直接导入测试（PRD NFR-M4）。

模块划分：
    roster.py            学生记录模型与名单增删改查
    drawer.py            抽取引擎（随机 / 去重 / 候选池 / 加权 / 漂移）
    storage.py           本地数据的持久化读写
    importer.py          CSV 名单导入与模板生成
    launcher.py          外部程序启动（"简洁视图"）
    special_effects.py   P2 三个特殊效果的状态机（不落盘）
"""

from __future__ import annotations

__all__: list[str] = []
