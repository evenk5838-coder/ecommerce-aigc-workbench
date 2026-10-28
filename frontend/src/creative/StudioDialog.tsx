import { useEffect, useRef, type ReactNode } from 'react';
import { X } from 'lucide-react';
export default function StudioDialog({title,onClose,children,open=true}:{title:string;onClose:()=>void;children:ReactNode;open?:boolean}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const el=ref.current,previous=document.activeElement instanceof HTMLElement?document.activeElement:null;if(open)el?.showModal();else el?.close();return()=>{el?.close();previous?.focus({preventScroll:true});};},[open]);
  return <dialog className="director-dialog" ref={ref} aria-label={title} onCancel={e=>{e.preventDefault();onClose();}}><header><h2>{title}</h2><button type="button" aria-label={`关闭${title}`} onClick={onClose}><X size={18}/></button></header><div className="director-dialog-content">{children}</div></dialog>;
}
