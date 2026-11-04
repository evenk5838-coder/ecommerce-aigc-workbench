# Seedream Pro 真实验证 · 2026-10-29

用户批准 call_e4b98f84df7847b5851d89052547f484：总上限¥2、最多1次文生图和1次图生图，2688×1536、单张；文本/视频0，失败不自动重试。

官方价格已重核：[北京价格页](https://console.volcengine.com/ark/region:cn-beijing/docs/model-pricing)，更新2026-10-26。Pro单图大于261万像素0.60元，首张参考图免费；两次按原价合计估算¥1.20，非供应商账单。

## 真实执行

临时数据库和素材目录为 data/acceptance/pro-20261029-approval-e4b98f84。使用实际节点保存→报价→确认→任务→worker→Ark→素材回写链；独立进程临时允许Pro以验证账号，正式模型状态仅在两次真实成功后改为verified。

| 用例 | 实际结果 | 原始像素 | 供应商请求耗时 |
| --- | --- | --- | --- |
| 文生图：虚构白色无把手杯，浅灰背景 | succeeded | 2688×1536 | 31.360秒 |
| 图生图：仅换浅蓝灰背景 | succeeded | 2688×1536 | 70.976秒 |

实际请求2次、每次返回1图，持久额度预留合计¥1.20；文本0、视频0、自动重试0。第二次只引用第一次新生成的合成图片，路径和SHA256均强制检查，不读取/外发既有业务素材。审批初次因参考图授权不明拒绝执行；补上来源断言后复审允许，拒绝阶段未调用模型。

本地Key鉴权成功；未输出或轮换密钥，未通过密钥推断资源ID，不能据此证明具体Key名称对应关系。两次真实调用证明当前本地凭据能访问Pro。

AI目视：首图符合单白杯、无杯碟/把手/标识的测试要求；图生图背景变蓝灰，主体轮廓与数量基本保留，但杯身和底部光影有变化。只是一组虚构商品样例，不承诺严格局部修改、任意真实SKU保真、其他尺寸或商业成片质量。人工复核待用户检查。

## 启用与发布

Pro状态已改verified，保留原默认模型及Mini/Fast未开通状态。56项模型组合/节点执行回归通过，凭据模式扫描和git diff检查通过。初次备份只读URI连接失败，未改业务数据或配置；改用本地SQLite连接后完成备份与核对，保留无效空备份，不用于回退。

8100已备份重启；40表、全部素材和前端哈希一致，模型API确认Pro可提交。回退只恢复 release-preparation.json 中备份的config.py并重启，不恢复或清空业务数据库。尚未新增GitHub存档。

## 证据

- [真实结果](../evidence/v0.11-Seedream-Pro真实验证-20261029/summary.json)、[完整节点结果](../evidence/v0.11-Seedream-Pro真实验证-20261029/results.json)、[安全账本](../evidence/v0.11-Seedream-Pro真实验证-20261029/provider-ledger-safe.json)。
- [文生图](../evidence/v0.11-Seedream-Pro真实验证-20261029/text_to_image.jpg)、[图生图](../evidence/v0.11-Seedream-Pro真实验证-20261029/image_to_image.jpg)、[AI检查](../evidence/v0.11-Seedream-Pro真实验证-20261029/quality-review.json)。
- [免费回归](../evidence/v0.11-Seedream-Pro真实验证-20261029/regression-enabled.txt)、[发布核对](../evidence/v0.11-Seedream-Pro真实验证-20261029/release-verification.json)。
- run.py 默认仅执行离线范围检查；本轮授权次数已全部使用，禁止再次运行 --run 或重置账本。
