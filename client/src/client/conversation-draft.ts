import { useState } from 'react';
import type { DemoAction, DemoConversation } from './chat-prototype-state';

export type FirstMessage=Extract<DemoAction,{type:'send'}>;
export type ConversationDraft={id:string;agent_id:string;project_id:string;attempt?:FirstMessage;failed?:boolean};

// One unsent conversation per window. The existing composer owns text/materials
// under this stable ID; navigation hides it without creating a server record.
export function useConversationDraft(){
  const [draft,setDraft]=useState<ConversationDraft|null>(null);
  const [active,setActive]=useState(false);
  return {
    draft,active,
    begin(project_id:string,agent_id?:string){
      setDraft(old=>old ? old.attempt||!agent_id ? old : {...old,agent_id} :
        {id:'dm-'+crypto.randomUUID().replace(/-/g,''),agent_id:agent_id||'tebb',project_id});
      setActive(true);
    },
    change(values:Partial<Pick<ConversationDraft,'agent_id'|'project_id'>>){setDraft(old=>old&&!old.attempt?{...old,...values}:old);},
    hide(){setActive(false);},
    freeze(action:FirstMessage){setDraft(old=>old?{...old,attempt:old.attempt||structuredClone(action),failed:false}:old);},
    failed(id:string){setDraft(old=>old?.id===id?{...old,failed:true}:old);},
    finish(){setDraft(null);setActive(false);}
  };
}

export function draftConversation(draft:ConversationDraft):DemoConversation{
  return {id:draft.id,title:'新对话',kind:'dm',members:[draft.agent_id],coordinator:draft.agent_id,goal:'',project_id:draft.project_id};
}
