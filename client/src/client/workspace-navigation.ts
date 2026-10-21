// Active pages can defer navigation while an unsaved form is being resolved.
export function navigateWorkspace(navigate:()=>void){
  const event=new CustomEvent('tebb-before-navigate',{cancelable:true,detail:{navigate}});
  window.dispatchEvent(event);
  if(!event.defaultPrevented)navigate();
}
