# AppImage 形态的转换器入口必须被认出来：34 的 ODA 就叫 `AppRun`

血缘：承接 `dwg-conversion-adapter.md`（provider / driver / argv 分派）、
`dwg-converter-production-rollout.md`（34 的转换器取值）、`dwg-capability-truth-and-audit.md`（能力事实与告警不许自相矛盾）。

状态：Spec + 红测（已实现）
红测：`tests/test_converter_binary_name_recognition_red.py`
依赖：`tech_app/backend/services/cad_converter/adapters/local_cli.py` 的 `driver_of()` / `provider_of_binary()`、
`tech_app/backend/services/cad_converter/service.py` 的 `_resolve_chain()`

## 0. 一句话目标

`driver_of()` / `provider_of_binary()` 只按**文件名**认 provider，认不出就回落显式 provider；
但 34 上真实部署的 ODA 是 AppImage 解包形态，入口文件名 `AppRun` **不含 "oda"** —— 于是每次
DWG 解析都会往用户可见的 `warnings` 里塞一条假告警。

## 1. 现状缺口（34 真机实测，2026-09-22）

`DWG_CONVERTER_PROVIDER=oda`、`DWG_CONVERTER_BINARY=/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun`
（`scripts/deploy_34_bare.sh` 写的就是这个值）。同一份进程里两条事实互相打架：

```
primary: {"provider": "oda", "driver": "oda_file_converter",
          "note": "unknown_converter_binary：无法从 AppRun 的文件名识别转换器类型，argv 形状未经真机验证",
          "argv_verified": true, "available": true}
chain.warnings: ["unknown_converter_binary：…argv 形状未经真机验证"]
```

`argv_verified` 是 **true**（走的是 ODA 那套真机验证过的 argv），`note` 却说"未经真机验证"。
这条 `note` 会被 `_resolve_chain()` 收进 `warnings`，再经 `convert_drawing()` 的 manifest 传到
统一解析服务的响应里 —— 即**用户每次解析 DWG 都会看到一条假告警**，还会把"这台机器到底验没验过"
这件事说反。

根因：`local_cli.driver_of()` 只按 basename 找 `oda` / `teigha` / `dwg2dxf` / `dwgread`，
而 AppImage 的入口名是 `AppRun`，判别信息在**安装目录名**（`oda-file-converter-27.1`）里。

## 2. 契约

- **C1** 入口名没有判别力时（basename ∈ `APPIMAGE_ENTRY_NAMES`，当前为 `apprun`），改看**路径的每一段
  目录名**：某一段**以标记开头**即归该驱动（标记 → 驱动：`teigha` / `oda` → `oda_file_converter`，
  `dwgread` → `libredwg_dwgread`，`libredwg` → `libredwg_dwg2dxf`）。
  "以标记开头"是刻意的：`oda-file-converter-27.1` ✓、`soda` ✗。
- **C2** `provider_of_binary("<…>/oda-file-converter-27.1/squashfs-root/AppRun") == "oda"`；
  `driver_of("oda", 同一路径) == "oda_file_converter"`。
- **C3** 显式 `DWG_CONVERTER_PROVIDER=oda` + 上面的 AppImage 入口时：`_resolve_chain()["primary"]["note"]`
  必须为空，`warnings` 里不许再出现"未经真机验证"，且 `argv_verified` 保持 **true**。
- **C4** 真认不出的二进制（文件名不含标记、路径段也不以标记开头）仍然必须给告警 —— **不许静默**
  （这条是 C1 的边界，防止"把告警删掉换安静"）。
- **C5** 既有文件名判定一字不改：`ODAFileConverter` → oda、`dwg2dxf` → libredwg、`dwgread` → libredwg-cli、
  `TeighaFileConverter` → oda；无标记路径下的 `AppRun` 仍回落显式 provider。

## 3. 边界

只改 `local_cli.driver_of()` 的路径判别与新增两个常量。不动 `DRIVERS` 的 argv 形状与 `argv_verified`
取值、不动 `service.py` 的 note/告警管线、不动版本探测与回退链判据、不动任何错误码。

**不算本批缺口的一条**：显式给了 `DWG_CONVERTER_BINARY` 时，`_resolve_one()` 会保留配置里的
`provider` 字段原值（`auto` 就还是 `auto`），本批不改这个命名口径 —— 要保证的是 **driver 被认出、
`note` 不再误报**。
