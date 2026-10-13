# 一键生成工艺推荐的并发与缓存

## 1. 背景

「一键生成全部工艺推荐」（`runPackagingBusinessProcessBatch`，`tech_app/frontend/app.js`）今天全程串行，且按"零件 × 组成"逐个发 LLM 调用：

- 前端批量是严格串行 `for (const item of ready) { … await … }`：每件走"GET 已有工艺 → POST 起任务 → 每 1.2s 轮询到终态 → 下一件"。
- 后端对每个**组成**顺序发一次模型：`packaging_sections.recommend_process()`（`tech_app/backend/services/packaging_sections.py`）里 `for section in inputs['sections']` 顺序 `recommend(...)`。
- DA 工艺路线桥接（`tech_app/backend/services/da_process_routing.py`）每个组成一次 HTTP，且候选集**无缓存**、批量中反复拉同一份。
- 任务队列 `TASK_WORKERS=4`（`tech_app/backend/services/tasks.py`），前端一次只提 1 个任务，worker 空转。

本批只做**并发与缓存**，不改任何工艺判定、门禁或成本口径。

## 2. 契约

- **C1 前端有界并发**：批量按有界并发提交（默认 **3**，正好吃满 `TASK_WORKERS=4` 里的多数额度；可用 `window.PACKAGING_PROCESS_BATCH_CONCURRENCY` 覆盖，取值夹在 `1..8`）。并发只让"提交/轮询"重叠，**不改变**每件的既有判定：已有有效工艺（有 steps 且非 stale）仍跳过、stale 才重跑、失败仍计入 failures、终态状态（`succeeded`/`partial`/`interrupted`）与日志含义逐字不变。
- **C2 自适应轮询**：轮询起始间隔缩短为 **600ms**，随后按 ×1.5 退避，封顶不超过原来的 **1200ms**。终态判据、1500 次尝试上限、超时文案一律不变。
- **C3 组成内并行**：`recommend_process()` 对 `inputs['sections']` 并行调用 `recommend`（默认 **4** 并发，可用 `PACKAGING_SECTION_WORKERS` 覆盖）；**首个组成**先在调用线程里跑（保证首个调用顺序稳定），其余并行。**确定性**：合并后的 `steps` 顺序、`warnings`、`open_questions`、`sections` 结果与串行逐字一致（按输入 section 顺序重排）。
- **C4 DA 桥接缓存**：`da_process_routing` 对真实 HTTP 读取加**进程内 TTL 缓存**（默认 TTL `300` 秒，可用 `DA_ROUTING_CACHE_TTL_SECONDS` 覆盖；键含全部入参），**只在 `cache_scope()` 内生效** —— 作用域由单个工艺请求的处理期进入（`cache_scope()` 用进程级计数，覆盖组成并行的工作线程）。作用域外直接调用 `for_row`/`_fetch` 一律各自读桥（不许跨调用记忆）。只缓存**成功**结果，失败不缓存；`clear_cache()` 可清空；缓存命中不改变返回内容。
- **C5 可关**：C1/C3 的并发度取正整数；并发度为 1 时行为与串行等价（C1 覆盖为 1 → 逐件串行，峰值并发 1）。

## 3. 不做

- 不新增服务端批量接口（一个 task 遍历全部零件）——留待后续批次。
- 不改 `TASK_WORKERS` 默认值、不改任何模型提示词、不改工艺库/成本库命中判定、不改门禁。
- 前端不做几何或成本求解。

## 4. 验收

- `tests/test_packaging_process_batch_concurrency_red.py` 全绿；
- 既有护栏不回归：`tests/test_packaging_batch_real_execution_red.py`（前端批量语义）、`tests/test_packaging_route_feature_similarity_red.py`、`tests/test_packaging_analogical_process_red.py`、`tests/test_da_process_routing_live_red.py`、`tests/test_packaging_multipart_sections_complete_red.py`。
