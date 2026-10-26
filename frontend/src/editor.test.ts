import { describe, expect, it } from 'vitest';
import { fitAsset, makeBatch, moveLayer, realAssets, restoreGenerationSettings } from './editor';
import type { Asset, GenerationSettings } from './types';
const asset:Asset={id:'asset-test',kind:'image',mime:'image/png',source:'upload',size:10,width:400,height:200,created_at:0,meta_json:'{}'};
describe('编辑与批次数据边界',()=>{
  it('横图加入竖画布时等比居中，不拉伸商品',()=>{const l=fitAsset(asset,1080,1440);expect([l.w,l.h,l.x,l.y]).toEqual([1080,540,0,450]);});
  it('拖动不会把图层完全移出可操作区域',()=>{const l=fitAsset(asset,1080,1080);const moved=moveLayer(l,50000,-50000,1080,1080);expect(moved.x).toBe(540);expect(moved.y).toBe(-270);});
  it('批次参数是独立快照且幂等ID不重复',()=>{const b=makeBatch({kind:'image',model_id:'m',params:{prompt:'p',seed:8}},2,[[512,512],[384,512]]);expect(b).toHaveLength(4);expect(new Set(b.map(x=>x.client_request_id)).size).toBe(4);b[0].params.prompt='changed';expect(b[1].params.prompt).toBe('p');expect(b[1].params.seed).toBe(9);});
  it('模拟引擎产物不计入真实素材库',()=>{expect(realAssets([asset,{...asset,id:'fake',meta_json:'{"model_id":"fake-image"}'}])).toEqual([asset]);});
});

describe('四模式草稿恢复',()=>{
  it('旧视频草稿始终保持首帧模式，保留原图和用户描述',()=>{
    const original={kind:'video' as const,videoReference:'asset_original',videoPrompt:'只转动瓶身',duration:10};
    const restored=restoreGenerationSettings(original);
    expect(restored.videoMode).toBe('image_to_video');
    expect(restored.videoReference).toBe('asset_original');
    expect(restored.videoPrompt).toBe('只转动瓶身');
    expect(restored.duration).toBe(10);
    expect(original).not.toHaveProperty('videoMode');
  });
  it('旧视频缺参考图时仍保持图生视频，交由提交校验要求补图',()=>{
    const restored=restoreGenerationSettings({kind:'video'});
    expect(restored.videoMode).toBe('image_to_video');
    expect(restored.videoReference).toBeNull();
  });
  it.each(['text_to_video','image_to_video'] as const)('新草稿保留明确的 %s 以及两个类型各自的参考图',videoMode=>{
    const restored=restoreGenerationSettings({kind:'video',videoMode,videoReference:'video-ref',imageReference:'image-ref',imageMode:'image_to_image'});
    expect(restored.videoMode).toBe(videoMode);
    expect(restored.videoReference).toBe('video-ref');
    expect(restored.imageReference).toBe('image-ref');
    expect(restored.imageMode).toBe('image_to_image');
  });
  it('损坏模式不把旧首帧视频悄悄转为纯文字视频',()=>{
    const restored=restoreGenerationSettings({kind:'video',videoMode:'unknown',videoReference:123} as unknown as GenerationSettings);
    expect(restored.videoMode).toBe('image_to_video');
    expect(restored.videoReference).toBeNull();
  });
});
