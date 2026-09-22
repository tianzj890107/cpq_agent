# 转换缓存必须带「引擎身份」：不然改了代码，旧 manifest 会把旧话接着说一遍

血缘：承接 `dwg-controlled-conversion-adapter.md`（`cache_key` 与幂等复用 §4.2）、
`dwg-conversion-quality-repair.md`（生效转换器身份段 §7.3）、
`converter-binary-name-recognition.md`（同一批：告警说反话）。

状态：Spec + 红测（已实现）
红测：`tests/test_converter_cache_engine_identity_red.py`
依赖：`tech_app/backend/services/cad_converter/service.py` 的 `_chain_fingerprint()` / `convert_drawing()` / `_cached_manifest()`

## 0. 一句话目标

`cache_key` 的生效转换器身份段必须**同时**含「转换器身份」与「我们这侧转换链引擎的身份」——
否则**只改我们自己代码**（不改配置、不换二进制）时 `cache_key` 不变，旧 manifest 连同里面的
`warnings` 被原样复用：代码已经改对了，用户看到的还是旧话。

## 1. 现状缺口（34 真机实测，2026-09-22）

`## 297` 把「AppImage 形态入口认不出 → 假告警」修掉并部署（`22b0979`），但 34 上解析真实
`酒盒.dwg` 仍然回：

```
warnings: ["unknown_converter_binary：无法从 AppRun 的文件名识别转换器类型，argv 形状未经真机验证"]
```

同一条命令下（同一个 venv、同一份配置）新起进程直调却是干净的：

```
$ ./open-claude/.venv/bin/python -c "unified_parse.parse_payload(...)"
parse_payload warnings: []
```

原因不是代码没生效，而是**持久化的转换 manifest 被幂等复用了**：
`tech_app/tech_data/cpq-unified-parse/conversions/manifests.json` 里躺着 09-21 23:13 / 23:55 写下的两条，
`warnings` 就是当时那条假告警；`cache_key` 只由「源文件 sha256 + 主/回退 provider/version/二进制 +
options」决定，**代码变了它不变** → `_cached_manifest()` 直接返回旧 manifest（连 `warnings` 一起）。

## 2. 契约

- **C1** `cad_converter.service` 导出常量 `CHAIN_ENGINE_VERSION`（非空字符串，形如
  `cad-converter-chain/<n>`）；本文件的**解析/告警语义**一变，就必须 bump 它。
- **C2** `_chain_fingerprint()` 的首段必须是 `CHAIN_ENGINE_VERSION`，其后才是既有的
  主 provider / converter_version / 二进制 sha256（+ 回退同三项）。因此 `cache_key` 与
  manifest 的 `conversion_options["converter_chain"]` 都带上引擎身份（可审计）。
- **C3** 引擎身份变化必须**导致重跑**：同源文件、同转换器、同 options，把
  `CHAIN_ENGINE_VERSION` 换成另一个值再转一次 → `cache_key` 不同、适配器**被真的再调一次**
  （不许复用旧 manifest）。
- **C4** 引擎身份不变时幂等**一点都不能少**：同源文件同 options 连转两次 → 适配器只被调一次，
  `conversion_id` 与产物目录不变。
- **C5** `conversion_id`（产物目录名）**不受**引擎身份影响：换引擎重跑仍写回同一个
  `conversion_id`，不许因为改了句话就在项目里堆出第二份产物目录。

## 3. 边界

只改 `_chain_fingerprint()` 与新增一个常量。不动 `identity_digest`（`conversion_id` 的来源）、
不动 options 其它字段、不动错误码、不动回退链判据、不动 manifest 的键集（不新增字段——
引擎身份只出现在 `converter_chain` 字符串里）。
