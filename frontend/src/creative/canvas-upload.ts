import { ApiError } from '../api';
export interface CanvasUpload { asset_id:string; kind:'image'|'video'; mime:string; width:number; height:number; duration_ms?:number|null }
export async function uploadCanvasFile(file:File):Promise<CanvasUpload> {
  const body=new FormData();body.append('file',file);
  let response:Response;
  try { response=await fetch('/api/assets',{method:'POST',body,signal:AbortSignal.timeout(60000)}); }
  catch { throw new ApiError('UPLOAD_RESULT_UNKNOWN','上传结果暂未确认，请先到素材库查找这份文件。',0); }
  const data=await response.json().catch(()=>null);
  if(!response.ok)throw new ApiError(data?.error?.code??'UPLOAD_FAILED',data?.error?.message??'素材上传失败，请重新选择文件。',response.status);
  if(!data||typeof data.asset_id!=='string'||!['image','video'].includes(data.kind)||typeof data.mime!=='string'||!Number.isFinite(data.width)||!Number.isFinite(data.height))throw new ApiError('UPLOAD_RESULT_UNKNOWN','上传回执暂未确认，请先到素材库查找这份文件。',0);
  return data as CanvasUpload;
}
