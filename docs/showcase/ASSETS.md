# 演示附件来源与边界

本目录的参考图、生成结果和 Bad Case 对照全部来自项目既有真实模型测试。此次只复制原文件，没有新增模型调用，没有重新生成、裁切、擦除水印或美化结果。产品操作演示另见操作演示目录，不能把操作示意或静态原型当成真实生成实测。

## 原始复验材料

- [无水印四模式真实复验](../evidence/v0.11-AiHubMix无水印复验-20261030/当前结果.md)：白杯文生图、换蓝背景的图生图，以及修正前的视频。
- [视频构图 v2 真实复验](../evidence/v0.11-视频构图v2真实复验-20261030/当前结果.md)：用户指定复用的现有材料，包含新版原片、前后对照、预算和已知限制。

这两份报告记录的是对应测试批次的历史状态，其中“菜单尚未实施”等后续项应结合仓库最新项目状态查看，不作为当前版本功能清单。

## 文件清单

所有文件均为字节一致的公开展示副本；完整来源路径、字节大小和 SHA-256 同时保存在 [媒体指纹清单](media-manifest.json)。

| 文件 | 模式与来源 | 证据标签 | SHA-256 |
| --- | --- | --- | --- |
| [reference-results/reference.jpg](reference-results/reference.jpg) | 文生图 / 后续图生图与图生视频的参考原图 · Seedream Flash | 真实生成，虚构无品牌白杯 | `1d74cefb5d1ea8a97cfb072444869359a0bb95306b25641de846a22eb0aaf163` |
| [reference-results/image-result.jpg](reference-results/image-result.jpg) | 图生图 · Seedream Flash | 真实生成，换浅蓝背景 | `47a36eeeb6e15c7eb7a59b0320be381b9364ac7435b0aaa77e7858428d828d37` |
| [reference-results/video-first-frame-v2.png](reference-results/video-first-frame-v2.png) | 图生视频实际发送首帧 · 本地边缘延展 | 同一白杯图，生成前预处理，非成片后处理 | `41b80b6767ca1a8b090241b135ece32e638f5301f49a71ba208637354457b7b4` |
| [reference-results/text-to-video.mp4](reference-results/text-to-video.mp4) | 文生视频 v2 · Seedance Fast | 真实生成原片，5.04秒、864×496、无音轨 | `cfd061d8845cf0452fb8f92f8ef78acf34de56178336abc48fa9701dcb952ff9` |
| [reference-results/image-to-video.mp4](reference-results/image-to-video.mp4) | 图生视频 v2 · Seedance Fast | 真实生成原片，5.04秒、864×496、无音轨 | `2d1f2d14ed7f8bb3685f7ea67c0b362f3ed3344d5a02a0f1b4b52e4ce688209e` |
| [reference-results/text-to-video-contact-sheet.jpg](reference-results/text-to-video-contact-sheet.jpg) | 文生视频 v2 抽帧 · Seedance Fast | 既有逐帧核查表 | `e16a465efb4751a4e41ce3cae5703d94dc66e335acb44970fc333d991cf0dcc5` |
| [reference-results/image-to-video-contact-sheet.jpg](reference-results/image-to-video-contact-sheet.jpg) | 图生视频 v2 抽帧 · Seedance Fast | 既有逐帧核查表 | `07bdcba13d3c23cc2a8fb1c7140b72982f220b60a7b243fbd03c9ae7a7c1dbdc` |
| [bad-cases/text-to-video-before.mp4](bad-cases/text-to-video-before.mp4) | 文生视频修正前 · Seedance Fast | 真实历史失败样例，后半段杯底裁切 | `d9ea5dbbed668c6ec53b675fef008d848c60ad31dbdbe8ecfc6f91447bdadd43` |
| [bad-cases/image-to-video-before.mp4](bad-cases/image-to-video-before.mp4) | 图生视频修正前 · Seedance Fast | 真实历史失败样例，补边方框接缝明显 | `c7a70bf6c9ab6b7a2e733ff8cf47e3d42bf78abc95601f28490439b574cc4b72` |
| [bad-cases/text-to-video-before-after.jpg](bad-cases/text-to-video-before-after.jpg) | 文生视频末段前后对比 · Seedance Fast | 直接复用既有对照图，上旧下新 | `a0825296833dcbd93a118eff3c450ca2200eeb22b37302b609d05b9e71746b12` |
| [bad-cases/image-to-video-before-after.jpg](bad-cases/image-to-video-before-after.jpg) | 图生视频补边前后对比 · Seedance Fast | 直接复用既有对照图，上旧下新 | `da170c7ee04f48161f4feeae3605422528fb98d7266b32212c19a370cab62b79` |
| [bad-cases/image-to-video-v2-last-frame.png](bad-cases/image-to-video-v2-last-frame.png) | 图生视频 v2 实际末帧 · Seedance Fast | 保留左下阴影局部接痕证据 | `e9a0190cea38c3da66e6e57eabefd0ab7b1993db57fd1c203ea82673f31cbecc` |

## 阅读边界

- 白杯是测试时生成的虚构无品牌物体。参考原图是一次文生图结果；图生图和图生视频以该图为输入。video-first-frame-v2.png 是实际发给视频模型的首帧，经过生成前的本地边缘延展，和原始 1024×1024 参考图分开展示。
- Bad Case 的上下对照使用既有比较图，上方为旧版失败样例，下方为新版复验样例。两次生成属于不同随机输出，不是相同随机种子的严格实验。
- 新版样例完整展示白杯，检查时未发现可见水印；这不代表所有商品、所有模型或全部画面都保证无水印。
- 文生视频末段仍接近画面下沿；图生视频左下阴影仍有局部短竖接痕。保留真实末帧与原片，不将“有所改善”写成“所有画质问题解决”。
- 供应商费用中心账单仍须独立核对。原始报告中的保守预算预留、任务返回 usage 与实际扣费含义不同。
- 源码开源许可与模型输出、第三方服务条款分别适用；此处提供样例溯源，不承诺生成素材的独占权或所有用途均获许可。
