# Spec（批 10）：材料克重的单位与纸种口径 —— 报价侧匹配输入

- 状态：Spec + 红测（已实现）（见 changelog ## 252）
- 覆盖：逆向快速报价「DWG/DXF → 匹配字段」里 `material_notes` → `face_paper_gsm` /
  `grey_board_gsm` 这一段（批 5 的通路，批 9 已收口尺寸侧）。
红测：`tests/test_quick_quote_material_gsm_red.py`
- 前序：`docs/specs/quick-quote-5-file-parsing.md` §2.4 规则 3（「`material_notes` 里能读出的
  克重（`200g` / `200 克` / `1200gsm`）→ `face_paper_gsm` / `grey_board_gsm`」）、
  `docs/specs/quick-quote-9-parse-field-alignment.md`。

## 0. 为什么有这批（`## 251.1` 在 34 上真跑出来的，不是推断）

真样本 `裕同包装项目-待开发/酒盒.dwg` 的材料标注：

```
235g白卡底PET光银裱A9 E坑
名称：左盖面纸\n材料：225G铜版底PET光银
底板面纸：225G铜版底PET光银
350g粉灰
```

34 上跑报价侧客户端（同 `## 251.1` 的复验命令）：

```
inputs: {"v_groove": true}
missing: [ …, grey_board_gsm, face_paper_gsm, … ]
warnings: [ …, "材料标注里读出了克重，但分不清是面纸还是灰板：请人工确认" ]
```

图纸上写得清清楚楚的 235g / 225G 一个都没进来。三条独立原因：

1. **单位只认小写 `g`**：`cpq_quick_quote_file.py:71` 的 `_GSM_RE` 写的是
   `(?:g/m²|g/m2|gsm|g|克)`，中文图纸普遍写「225G」「300G」→ 直接不匹配。**技术工艺侧
   的同名正则早就是 `re.IGNORECASE`**（`tech_app/backend/services/packaging_semantics/fields.py:37`），
   是报价侧漏了，不是口径分歧。
2. **纸种词表缺真图用词**：现表只有「灰板 / 纸板 / greyboard / grey / gray」与
   「面纸 / 面 / face / cover」，真图写的是「白卡」「铜版」「单粉」「双灰」→ 即使数字读出来
   也归不了桶，只能整条丢掉。
3. **取值与纸种不配对**：`_GSM_RE.search()` 只取**第一个**数字，再用「这条标注里出现过哪个
   纸种词」判桶（`if … elif …`）。`衬纸250g白卡裱1200g双灰` 这种一条标注两个纸种的写法，
   250 会被判成灰板克重（真样本 `圆盘盒.dwg` 里就有这一条，今天没出错只是靠标注先后顺序
   侥幸命中了另一条）。

后果不是"读数不准"而是**口径不可信**：报价首页/工作台看到的 `face_paper_gsm` 可能是
「随便第一个数字」，而 `## 251.1` 之后 `missing` 里的 `face_paper_gsm` 又会被当成"图纸没写"
让人工补 —— 等于让销售替图纸背锅。

## 1. 契约

### C1 克重单位识别

`_GSM_RE` 必须在**忽略大小写**下识别：`235g` / `225G` / `300 G` / `1200gsm` / `1200GSM` /
`200克` / `200 g/m²` / `200G/M2`。

**不得**匹配厚度：`2mm` / `1.8mm` / `2.5MM` / `35mm` / `11层=22mm` 一律不是克重
（真样本 `酒盒.dwg` 的灰板全用 mm 写厚度，误判会把 `2.5mm` 读成克重）。

### C2 纸种词表（唯一事实源 = 模块常量）

- 面纸桶（`_FACE_WORDS`）：保留 `面纸 / 面 / face / cover`，新增 `白卡 / 铜版 / 单粉`；
- 灰板桶（`_GREY_WORDS`）：保留 `灰板 / 纸板 / greyboard / grey / gray`，新增 `双灰 / 全灰 / 灰卡`；
- **`粉灰` 故意不登记**：真样本 `酒盒.dwg` 的「350g粉灰」既可读作便宜的面纸
  （单粉灰底），也可读作灰板系的粉灰板，两种口径都讲得通。词表不是唯一事实源之外的猜测
  场所 —— 要登记得先由业务签字，本批按 C4 走"不猜 + warning"。
- 词表允许扩充，但不许出现单字「纸」「卡」这类会把任意文字判成纸种的词条。

### C3 逐值配对（不许"第一个数字 + 命中任意词"）

一条标注里可能有多个克重、多个纸种。归属规则按**每个克重值**各自判定：

1. 先取**紧跟在该值之后**（`start >= value_end`）最近的纸种词；
2. 其后没有纸种词，再取**紧挨在该值之前**（`end <= value_start`）最近的纸种词；
3. 两条都没有 → 该值**不可归属**。

配套：同一个桶里**先到先得**（沿用批 5 的 `setdefault` 口径，不改成最大值 / 众数 —— 那是
另一件事，要另立 Spec）。

真样本对表（`圆盘盒.dwg`）：

```
衬纸250g白卡裱1200g双灰      → face_paper_gsm=250、grey_board_gsm=1200   （今天读不出 / 误判）
底座灰板围板:1200g双灰（2mm） → grey_board_gsm=1200                        （今天侥幸正确）
10PC圆盒 内托面卡：350g单粉   → face_paper_gsm=350                        （今天靠「面」侥幸正确）
```

### C4 不猜：不可归属的值一个键都不写，并记 warning

- 不可归属的克重**不得**写进 `face_paper_gsm` / `grey_board_gsm`；
- 存在不可归属的克重时，`warnings` 里必须有**逐字**这一条：

  ```
  材料标注里读出了克重，但分不清是面纸还是灰板：请人工确认
  ```

  （沿用批 5 `cpq_quick_quote_file.py:_gsm_from_notes()` 现有文案，不改措辞、不改语序。）
- 键写不进去就照批 2 / 批 5 的老口径进 `missing`，继续由销售手填，**不填默认值**。

### C5 真样本金标（本机有 DWG 转换器时跑；没有则 skip）

`裕同包装项目-待开发/酒盒.dwg`：

| 键 | 值 | 依据 |
| --- | --- | --- |
| `face_paper_gsm` | `235.0` | 第一条标注「235g白卡底PET光银裱A9 E坑」= 面纸白卡 235g（C3 后置词命中「白卡」） |
| `grey_board_gsm` | 不出现 | 该图灰板只写了 mm 厚度；唯一带克重的「350g粉灰」按 C2 不登记 → 不可归属 |
| `warnings` | **不含** C4 文案 | 进到 `material_notes` 的每条克重都配上了纸种（不许无端报歧义，见 §5） |

`裕同包装项目-待开发/圆盘盒.dwg`：`face_paper_gsm=300.0`（`面卡，300G白卡/哑PP` 先到先得）、
`grey_board_gsm=1200.0`（`底座灰板围板:1200g双灰（2mm）`）。

> 变化说明：`圆盘盒.dwg` 的 `face_paper_gsm` 由今天的 `350.0`（`内托面卡：350g单粉` 靠
> 「面」命中）变成 `300.0`（`面卡，300G白卡/哑PP`）。两者都是图上真值，改的是**取值口径**：
> 今天靠"任一词命中 + 第一个数字"，改后是"逐值与紧邻纸种配对 + 先到先得"。这条变化必须写进
> changelog，不许静默。

### C6 不回归

- 批 5 的 `test_e7_material_notes_parsed_to_gsm`（`面纸 250g 铜版纸` / `灰板 1200gsm`）
  必须仍然绿；
- `to_match_inputs()` 的出参形状（`inputs` / `missing` / `sources` / `warnings` / `units_factor` /
  `match_input_keys`）与键闭集（`cpq_quick_quote_match.QUICK_MATCH_INPUT_KEYS`）**一字不动**；
- 不新增依赖、不联网、不连库、不真转图纸（除 C5 的本机真实样本）。

## 2. 允许修改范围（只这 3 处）

1. `cpq_quick_quote_file.py`：`_GSM_RE` / `_GREY_WORDS` / `_FACE_WORDS` / `_gsm_from_notes()`，
   以及为 C3 新增的私有辅助（词条落位、逐值归属）。
2. `tests/test_quick_quote_material_gsm_red.py`（本批红测，先写先跑红）。
3. `changelog/changelog_9_21_25.md` 追加本批条目。

## 3. 禁止事项

- 不许改 `tech_app/backend/services/packaging_semantics/fields.py`（技术工艺侧口径另算，
  本批只对齐报价侧客户端）；
- 不许改 `cpq_quick_quote_match.py` 的键闭集、权重、门槛；
- 不许把「粉灰」等有歧义的词条偷偷登记进词表来把 C5 的金标改成"有灰板克重"；
- 不许用 `outline_size`（图纸幅面）或标注尺寸反推克重；
- 不许改批 5 / 批 9 的任何断言，不许删断言 / 加 skip 来换绿；
- 不许改 `to_match_inputs()` 出参形状；不许给它加第二套"猜测结果"字段。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quick_quote_material_gsm_red -v
./open-claude/.venv/bin/python -m unittest \
  tests.test_quick_quote_file_parsing_red \
  tests.test_quick_quote_parse_field_alignment_red \
  tests.test_quick_quote_field_workspace_red
```

C5 / C6 之后照 `## 251.1` 的口径在 34 上用真样本复跑一遍（客户端 → 统一解析服务），
把 `inputs` 前后对照贴进 changelog。

## 5. 本批**不**做、但要记下来的（服务侧关键词表）

真样本 `酒盒.dwg` 的图纸文字里有「350g粉灰」，但它**没进** `material_notes`：批 7 服务
`unified_parse._is_material_note()` 只保留含 `MATERIAL_KEYWORDS`（灰板 / 纸板 / 铜版 / 白卡 /
单粉 / 牛皮 / 瓦楞 / 克重 / g-m2 / gsm）的行，「粉灰」不在表里被整行挡掉。

也就是说这条克重连报价侧都没看到 —— 与 C2「不登记 粉灰」是两件不同的事：

- 报告侧的 `material_notes` 归属（服务侧）→ 本批不碰；
- 归属后的纸种分桶（报价侧）→ 本批按 C2 走「不猜 + warning」。

要收口得先由业务对「粉灰到底算面纸还是灰板」签字，再决定是扩服务侧关键词表、还是扩 C2 词表；
两条都不许由实现方自行拍板。
