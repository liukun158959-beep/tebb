import { useEffect, useId, useRef, type ReactNode } from 'react';
import { usePageActive } from './page-stack';

// The native modal supplies background isolation, focus containment and Escape.
export function WorkspaceDialog({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}){
  const ref=useRef<HTMLDialogElement|null>(null),active=usePageActive(),id=useId();
  useEffect(()=>{const dialog=ref.current;if(!dialog||!active)return;const previous=document.activeElement as HTMLElement|null;
    dialog.showModal();window.dispatchEvent(new CustomEvent('tebb-workspace-dialog',{detail:{id,open:true}}));
    return()=>{dialog.close();window.dispatchEvent(new CustomEvent('tebb-workspace-dialog',{detail:{id,open:false}}));if(previous?.isConnected)previous.focus();};
  },[active]);
  return active?<dialog ref={ref} className="workspace-dialog" aria-label={title} onCancel={event=>{event.preventDefault();onClose();}}>
    <header><h2>{title}</h2><button type="button" className="desk-menu-item" aria-label={`关闭${title}`} onClick={onClose}>关闭</button></header>{children}
  </dialog>:null;
}
