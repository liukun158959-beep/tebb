import { useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import './inspector-disclosure.css';
export function InspectorDisclosure({label,icon,meta,children}:{label:string;icon?:ReactNode;meta?:string;children:ReactNode}){
 const [open,setOpen]=useState(false);
 return <section className="context-disclosure"><button type="button" aria-expanded={open} onClick={()=>setOpen(!open)}>{icon}<span>{label}</span>{meta&&<small>{meta}</small>}<ChevronRight size={15} className={open?'is-open':''}/></button>{open&&<div className="context-disclosure-content">{children}</div>}</section>;
}
