# -*- coding: utf-8 -*-
"""测试共享件包（发布保障批次 2，Spec `docs/specs/release-assurance-batch2.md` §2.2–§2.4）。

这里只放**可被多条红测复用**的稳健工具：JS 函数体抠取、集合封闭断言、前后端编号表比对。
包内模块不含业务实现，也不反过来 import 任何业务模块（`stage_table` 读后端编号表除外）。
"""
