import { useEffect,useState } from 'react';
import { BookOpen,RefreshCw } from 'lucide-react';
import { listSingleTemplates } from './single-templates';
import type { SingleTemplate,SingleTemplateSelection } from './single-templates';
import './SingleTemplatePanel.css';
export default function SingleTemplatePanel({kind,modelId,value,onChange,onSelect}:{kind:'image'|'video';modelId:string;value:SingleTemplateSelection|null;onChange:(v:SingleTemplateSelection|null)=>void;onSelect:(v:SingleTemplate)=>void}){
 const [items,setItems]=useState<SingleTemplate[]>([]),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[loading,setLoading]=useState(true);
 useEffect(()=>{const c=new AbortController();setLoading(true);setError('');listSingleTemplates(kind,c.signal).then(x=>{if(!c.signal.aborted)setItems(x);}).catch(e=>{if(!c.signal.aborted)setError(e instanceof Error?e.message:'模板不可用');}).finally(()=>{if(!c.signal.aborted)setLoading(false);});return()=>c.abort();},[kind,refresh]);
 const active=value?.template.kind===kind?value:null;
 return <details className="single-template-panel"><summary><BookOpen size={15}/><span>{active?'模板：'+active.template.name:'工作与主题模板'}</span><small>{active?'v'+active.template.latest_version:'可选'}</small></summary>
 <div className="single-template-body">{loading&&<p role="status">正在读取模板…</p>}{error&&<p role="alert">{error}<button type="button" onClick={()=>setRefresh(x=>x+1)}><RefreshCw size={12}/>重新读取</button></p>}
 {!loading&&!error&&<label>选择模板<select aria-label="单次创作模板" value={active?.template.template_id??''} onChange={e=>{const t=items.find(x=>x.template_id===e.target.value);if(t)onSelect(t);else onChange(null);}}><option value="">手动创作</option>{items.map(t=><option key={t.template_id} value={t.template_id} disabled={!t.compatible_model_ids.includes(modelId)}>{t.category==='hot'?'主题 · ':''}{t.name} · v{t.latest_version}{!t.compatible_model_ids.includes(modelId)?'（模型不兼容）':''}</option>)}</select></label>}
 {active&&<><p>{active.template.description}</p>{active.template.fields.filter(f=>!f.param_key).map(f=><label key={f.key}>{f.label}{f.required?' *':''}{f.type==='select'?<select value={active.fields[f.key]??''} onChange={e=>onChange({...active,fields:{...active.fields,[f.key]:e.target.value}})}><option value="">请选择</option>{f.options?.map(v=><option key={v} value={String(v)}>{v}</option>)}</select>:f.type==='textarea'?<textarea rows={2} value={active.fields[f.key]??''} onChange={e=>onChange({...active,fields:{...active.fields,[f.key]:e.target.value}})} placeholder={f.placeholder}/>:<input value={active.fields[f.key]??''} onChange={e=>onChange({...active,fields:{...active.fields,[f.key]:e.target.value}})} placeholder={f.placeholder}/>}{f.custom_key&&active.fields[f.key]==='自定义'&&<input aria-label={f.label+'自定义'} value={active.fields[f.custom_key]??''} onChange={e=>onChange({...active,fields:{...active.fields,[f.custom_key as string]:e.target.value}})}/>}</label>)}
 {active.template.template_id==='tpl_season_theme'&&<label className="template-concept"><input type="checkbox" checked={active.conceptAcknowledged} onChange={e=>onChange({...active,conceptAcknowledged:e.target.checked})}/>未提供原图时，我确认按创意概念图生成，不代表真实商品。</label>}
 <p>描述留空时使用模板提示词；填写描述会完整替换模板提示词。具体内容和费用会在生成前显示。</p><button type="button" onClick={()=>onChange(null)}>退出模板，继续手动创作</button></>}
 </div></details>;
}
