# 34新建报价与行业预填部署记录（2026-10-29）

- 应用版本：`e92ecaca187958ff62c676d3714b10c789d6a5bf`，分支ytbz。GitLab及GitHub双推并独立回读一致。
- 环境：http://172.16.10.34:8010/；时间：2026-10-29T11:20:54.306629+08:00。服务器从ca8eeef仅fast-forward到目标提交。
- 备份：`/home/wugefei/CPQ/deploy_backups/ytbz-e92ecaca1879-20261029-112049`。停写期间备份持久化目录、设置及原版本信息，未操作业务数据。
- 本地223项相关自动化测试通过；Python/JavaScript语法及diff检查通过。
- 服务器本机首页和健康检查通过，status=ok、build.commit精确一致。外网访问两页面200且SHA256与提交一致。
- 四行业已部署服务端目录各含六个第1步分区及推荐内容，只读检查通过。HTTP行业接口匿名访问401，未进行登录后接口或浏览器完整业务验收。
- 原设置及密钥文件指纹未变，持久化文件无缺失。部署前后文件数分别保持：cpq_history246、xbom_history62、rule_history15、tech_app/tech_data3147、product_images20、tech_app/data34、cpq_data1。
- 父服务PID2369026、子服务PID2369100，沿用原环境及启动命令；ODA可用，文本模型deepseek-v4-flash。
- 未创建MR、tag或Release。后续部署记录为纯文档提交，不改变运行应用版本。
