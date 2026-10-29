export type ChoiceQuestion={id:string;question:string;options:{id:string;label:string;value:string}[]};
export type ClarificationForm={version:string;questions:ChoiceQuestion[]};
export type ChoiceAnswer={option_id:string}|{custom:string};
export type ClarificationAction={type:'clarification_options';id:string;conversation:string;version:string}|{type:'clarification_answer';id:string;conversation:string;version:string;answers:Record<string,ChoiceAnswer>}|{type:'cancel';id:string;conversation:string};
export function pendingClarifications<T extends {id:string;state:string;parent?:string}>(runs:T[]){return runs.filter(run=>run.state==='needs_input'&&!run.parent);}
