# 包装演示 mock 数值以低值为准（锚定酒盒实际 BOM）

状态：Spec + 红测 + 加价脚本（默认只打印计划；写入 PG 需 `--apply --backup`）。
红测：`tests/test_packaging_demo_mock_lower_of_red.py`；脚本：`scripts/packaging_demo_kb_relower.py`。

取代关系：本批取代同日先前的「按名字关键字取族」写法（`packaging_demo_20261014_lower_of`，
valid_from 00:00）。两批都在库里只追加、不覆盖；读侧 (`ORDER BY valid_from DESC, price_id DESC`)
按最新一条取值，所以本批（valid_from 12:00）生效。

## 1. 唯一规则

对每一个**包装演示 mock 数值**（`cpq_kb` 里 `MAT-PKG-*` 的单价），按顺序做三步：

1. **锚**：把它锚到**酒盒实际 BOM**里**最接近**的那条材料（`master_data.md_clm_material_base_info`
   里 `YT-JW-XR21-700ML-*` 的 22 件，`desc` 的「材料：…」段）；与 BOM 材料逐字同名时直接用同名那条。
   锚定用 `packaging_material_match.score_candidate`（同材质族 + 克重/厚度 + 字面相似度），不按名字里的
   `PET`/`光银` 关键字猜族。
2. **定族**：材质族由**锚到的那条 BOM 材料**定（灰板 / 面纸 / 白卡 / EVA / 磁铁 …）；
   复合料（如「1.8MM双灰裱225G太阳铜版底PET光银」「325G白卡底PET光银+3层300G白卡…」）
   按**承印基材**定族，不按表面处理关键字。
3. **取低**：取**两张业务工作簿里同族吨价的低值**（`报价逻辑-0903.xlsx` vs `成本测算明细.xlsx`）。
   锚不到对口的材料保持现值，并在留痕里写明「BOM 无对口，保持」。不猜、不编、不做加权。

## 2. 对照来源（只读、逐格引用）

- 报价逻辑-0903.xlsx「报价-工费率」：`N2=6300`（面纸 157g）、`N3=3950`（灰板 1650G）、`N4=6700`（围条 300g）
- 成本测算明细.xlsx「报价-行业标准」：`M2=12050`（面纸 150G 黑卡）、`M3=3950`（灰板 1650G）、`M4=10200`（围条 300G 黑卡）
- KB 现值：`kb_material_price` 里该材料最新一条 `valid_from` 的 `price`（元/kg）

| 锚到的酒盒 BOM 材料 | 族 | 演示材料（`MAT-PKG-*`） | 重定后（元/kg） | 采纳的对照 |
|---|---|---|---|---|
| `2.5MM富业灰板` | greyboard | `GREYBOARD`、`BOARD18/20/25`、`GENERAL`、`FUYE25`、`FUYE25GREY`、`FUYE20SLIP`、`FUYE20SLIP2`、`BOARD22` | **3.95** | 0903!N3 与 明细!M3（同为 3950 元/吨） |
| `1.8MM双灰裱225G太阳铜版底PET光银` | greyboard | `BOARD18SUN`、`BOARD-SILVER` | **3.95** | 同上（承印基材是双灰板） |
| `350G玖龙粉灰` | greyboard | `GREY350` | **3.95** | 同上 |
| `225G/255G太阳铜版底PET光银` | face_paper | `FACEPAPER`、`SUN225`、`SUN255`、`FACE225` | **6.30** | 0903!N2（低于 明细!M2=12050） |
| `325G白卡底PET光银`（及 `+3层300G白卡+225G铜版底PET光银`） | white_card | `FACE325`、`WHITE325`、`CARD300`、`LAMINATED`、`LAMINATEDWHITE` | **6.70** | 0903!N4（低于 明细!M4=10200） |
| `235G白卡底PET光银+A9（E坑）130/120` | white_card | `CORRUGATED` | **6.70** | 同上（白卡面纸基材） |
| `38度A级白色EVA` / 磁铁 | eva / magnet | `EVA`、`EVA35` / `MAGNET` | 保持现值 | 两张工作簿都没有可比的 EVA / 磁铁吨价 |
| 无对口（衬纸 / 特种纸 / 纸管） | — | `LINER`、`DEMO-LINER`、`SPECIAL`、`TUBE` | 保持现值 | 酒盒 BOM 里没有对口材料 |

## 3. 写入方式（只追加，不覆盖）

1. **只追加**一条 `kb_material_price`：`valid_from='2026-10-14 12:00:00'`、
   `source_name='packaging_demo_20261014_bom_anchor'`、`unit='kg'`、`currency='CNY'`，
   `evidence` 写清「锚到哪条 BOM 材料、走什么工序、取到哪个族、采纳哪一格对照」。
2. 不覆盖、不删除历史价格行；`(material_code, source_name)` 已存在则**跳过**；
   库里现值已等于目标值时**也跳过**（不重复追加）。
3. 写前必须给 `--backup <新文件>`，先落全库快照；写入在单事务内、带 advisory lock，提交后递增 `kb_version`。
4. `plan()` 默认只输出计划（含锚点 / 工序 / before-after 与留痕），**不连库**。

## 4. 不改

- 不改成本公式 `packaging_cost.FORMULA_CATALOG`、不改 `kb_cost_rate` 费率行、不改 `kb_packaging_*` 权威表。
- 不改任何非包装行业材料，不改 `source_type` 为 `dwg_confirmed` / `workbook` 的记录。
- 不改材料克重身份（`157g 铜版纸` 仍是 157g；灰板仍无 gsm、按厚度×密度推导）。

## 5. 为什么工艺（覆膜 / 热烫 / 啤切）那几档本批不动

覆膜 / 热烫 / 啤切在两张工作簿里只差**要不要机台工时**，共用的数值逐字一致：
膜 `1.7` 元/㎡、膜 `18` g/㎡ × `18.5` 元/kg、辅助杂费 `0.15`、烫金 `8.5` 元/㎡、保底开机费 `200/150/100`。
酒盒 BOM 的工序给的是同一件事的**工艺描述**（如「19um即涂防刮花哑胶(佳泓)」），
没有独立于工作簿的**单价**；KB 里没有任何一个数值高于工作簿共用值，所以没有可取的「低值」。
机台工时参数（换版分钟 / 产能 / 设备+人工费率）只出现在 报价逻辑-0903，「成本测算明细」没有对照，
且被 `tests/test_packaging_cost_engine_red.py` 的 `复膜=1.376611258004827` 逐值锁死：
按第 1 节规则保持，不动公式与费率。（本轮只把每条演示材料的 **BOM 锚点 + 工序**写进留痕。）

## 6. 验收

- 红测全绿（含：锚点 == `score_candidate` 最优、三族重定值、无对口族不动、目标价不高于现值、
  来源/工序/规则留痕、重复执行幂等）。
- `scripts/packaging_demo_kb_relower.py`（不带 `--apply`）打印的计划与第 2 节表格逐字一致。
