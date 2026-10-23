import { describe, expect, it } from 'vitest';
import { fitAsset, makeBatch, moveLayer, realAssets } from './editor';
import type { Asset } from './types';
const asset:Asset={id:'asset-test',kind:'image',mime:'image/png',source:'upload',size:10,width:400,height:200,created_at:0,meta_json:'{}'};
describe('编辑与批次数据边界',()=>{
  it('横图加入竖画布时等比居中，不拉伸商品',()=>{const l=fitAsset(asset,1080,1440);expect([l.w,l.h,l.x,l.y]).toEqual([1080,540,0,450]);});
  it('拖动不会把图层完全移出可操作区域',()=>{const l=fitAsset(asset,1080,1080);const moved=moveLayer(l,50000,-50000,1080,1080);expect(moved.x).toBe(540);expect(moved.y).toBe(-270);});
  it('批次参数是独立快照且幂等ID不重复',()=>{const b=makeBatch({kind:'image',model_id:'m',params:{prompt:'p',seed:8}},2,[[512,512],[384,512]]);expect(b).toHaveLength(4);expect(new Set(b.map(x=>x.client_request_id)).size).toBe(4);b[0].params.prompt='changed';expect(b[1].params.prompt).toBe('p');expect(b[1].params.seed).toBe(9);});
  it('模拟引擎产物不计入真实素材库',()=>{expect(realAssets([asset,{...asset,id:'fake',meta_json:'{"model_id":"fake-image"}'}])).toEqual([asset]);});
});
