import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { rpc, type BackendInfo } from "./api";
import "./project-runtime.css";

type Service = { id: string; state: string; url: string; operation: string; log_id: string };
type Environment = { id: string; repository: string; ref: string; commit: string; state: string; error?: string;
  creation_archive?: { operation: string; request: string; at: number };
  sandbox_id: string; active_operation?: string; prepare_operation?: string; latest_operation?: string; service?: Service | null };
type Observation = { ok: boolean; error?: string; environment: Environment; service_ready: boolean; recovery_required?: boolean;
  archive_status?: { eligible: boolean; operation: string; local_active: boolean; archived: boolean };
  observed_runtime?: { status: string } | null;
  operation?: { id: string; state: string; stage: string; error?: string; cancel_requested?: boolean } | null };
type Log = { ok: boolean; error?: string; base64: string; offset: number; size: number; done: boolean; truncated: boolean };
const stages: Record<string,string> = { admitted: "已登记", network_policy: "核对网络权限", clone: "拉取仓库", install: "安装依赖", publish: "准备本机端口", health_check: "检查服务响应" };

export function ProjectRuntimePane({ info, environment: initial = "", operation: initialLog = "", debug = false }:
  { info: BackendInfo | null; environment?: string; operation?: string; debug?: boolean }) {
  const [environments,setEnvironments] = useState<Environment[]>([]), [selected,setSelected] = useState(initial);
  const [observed,setObserved] = useState<Observation | null>(null), [log,setLog] = useState(initialLog);
  const [busy,setBusy] = useState(""), [error,setError] = useState(""), [notice,setNotice] = useState("");
  const [verifyCandidate,setVerifyCandidate] = useState("");
  const [archiveConfirm,setArchiveConfirm] = useState<{environment:string;operation:string} | null>(null);
  const archiveAttempt=useRef<{environment:string;operation:string;request:string} | null>(null);
  const host = useRef<HTMLDivElement>(null), terminal = useRef<Terminal>(), alive = useRef(true), selectedRef = useRef(selected);
  selectedRef.current = selected;
  const available = !!info && info.scope !== "collaboration" && !debug;
  const current = observed?.environment.id === selected ? observed : null;
  const env = current?.environment || environments.find(value=>value.id===selected);
  const logs = Array.from(new Set([initialLog,env?.service?.operation,env?.active_operation,env?.latest_operation,env?.prepare_operation].filter(Boolean))) as string[];
  async function call<T>(operation: string, payload: Record<string,unknown> = {}, timeoutMs = 40000) {
    if (!available || !info) throw new Error("请在桌面客户端使用项目运行。");
    return rpc<T>(info,"project_environments_ui",{ operation,payload },{ timeoutMs });
  }
  useEffect(()=>{ alive.current = true; return ()=>{alive.current=false;}; },[]);
  useEffect(()=>{setSelected(initial);setLog(initialLog);setObserved(null);},[initial,initialLog]);
  useEffect(()=>{
    if (!available) return;
    let disposed=false, timer:ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const result=await call<{ok:boolean;environments:Environment[]}>("list");
        if (disposed) return;
        setEnvironments(result.environments);
        const id=selectedRef.current || initial || result.environments[0]?.id;
        if (id) {
          if (!selectedRef.current) setSelected(id);
          const state=await call<Observation>("status",{environment:id});
          if (!disposed && selectedRef.current===id) setObserved(state);
        }
      } catch (reason) { if (!disposed) setError(String(reason)); }
      finally { if (!disposed) timer=setTimeout(refresh,1800); }
    }
    void refresh();return ()=>{disposed=true;clearTimeout(timer);};
  },[info?.port,info?.token,available,initial]);
  useEffect(()=>{
    if (!host.current) return;
    const instance=new Terminal({fontFamily:"Cascadia Mono, Consolas, monospace",fontSize:12,disableStdin:true,scrollback:8000});
    const fit=new FitAddon();instance.loadAddon(fit);instance.open(host.current);terminal.current=instance;
    const theme=()=>{const dark=document.documentElement.classList.contains("dark");instance.options.theme={background:dark?"#1e1e1e":"#ffffff",foreground:dark?"#d4d4d4":"#1f1f1f"};fit.fit();};
    const resize=new ResizeObserver(()=>fit.fit());resize.observe(host.current);
    const observer=new MutationObserver(theme);observer.observe(document.documentElement,{attributes:true,attributeFilter:["class"]});theme();
    return ()=>{resize.disconnect();observer.disconnect();instance.dispose();terminal.current=undefined;};
  },[]);
  const activeLog=logs.includes(log)?log:logs[0] || "";
  useEffect(()=>{
    terminal.current?.reset();if (!available || !selected || !activeLog) return;
    let disposed=false, offset=0, timer:ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const result=await call<Log>("log",{environment:selected,operation:activeLog,offset});
        if (disposed) return;
        if (!result.ok) throw new Error(result.error || "读取日志失败。");
        if (result.base64) terminal.current?.write(Uint8Array.from(atob(result.base64),character=>character.charCodeAt(0)));
        offset=result.offset;
        if (result.truncated) setNotice("已保留前4MiB日志，后续输出未保存。");
        if (!result.done || offset<result.size) timer=setTimeout(read,350);
      } catch (reason) { if (!disposed) setError(String(reason)); }
    }
    void read();return ()=>{disposed=true;clearTimeout(timer);};
  },[info?.port,info?.token,available,selected,activeLog]);
  async function action(label:string,work:()=>Promise<void>) {
    setBusy(label);setError("");try {await work();} catch (reason) {if(alive.current)setError(String(reason));}
    finally {if(alive.current)setBusy("");}
  }
  return <section className="project-runtime" aria-label="项目运行">
    <header><strong>项目运行</strong><button disabled={!available || !!busy} onClick={()=>void action("正在检查",async()=>{
      const result=await call<{ok:boolean;error?:string;version?:string}>("probe");
      if (!result.ok) throw new Error(result.error || "Docker Sandboxes控制通道不可用。");
      setNotice(`Docker Sandboxes ${result.version} 控制通道可用，实际环境在运行时校验。`);
    })}>检查连接</button></header>
    {!available ? <p>请在桌面客户端打开项目运行。</p> : <>
      <label className="project-runtime-picker">项目<select value={selected} onChange={event=>{setSelected(event.target.value);setObserved(null);setLog("");setError("");}}>
        {!environments.length && <option value="">暂无项目</option>}
        {environments.map(value=><option key={value.id} value={value.id}>{value.repository.split("/").slice(-2).join("/")} · {value.id.slice(0,6)}{value.creation_archive ? " · 请求已归档" : ""}</option>)}
      </select></label>
      {!env ? <p>为伙伴勾选“项目环境”，然后在聊天中提供公开GitHub仓库地址。</p> : <div className="project-runtime-summary">
        <strong>{env.creation_archive ? "请求已归档；创建结果仍未知" : current?.service_ready ? "可预览" : current?.recovery_required ? "等待核对原环境" : current?.operation ? stages[current.operation.stage] || current.operation.state : current?.observed_runtime?.status==="stopped" ? "已停止" : current?.observed_runtime?.status==="running" ? "环境运行，服务未启动" : "等待运行或核对"}</strong>
        {env.creation_archive && <p>已释放本应用准入，原操作和日志保留。{env.sandbox_id ? `后来收到原实例身份 ${env.sandbox_id}，请按原身份核对。` : "远端实例是否创建仍待核对。"}{current?.observed_runtime?.status==="stopped" ? "已核对原实例当前停止。" : ""}</p>}
        <small>{env.repository}<br />{env.commit ? `提交 ${env.commit.slice(0,12)}` : "尚未核对仓库提交"}</small>
        {(current?.error || env.error || current?.operation?.error) && <p role="status">{current?.error || current?.operation?.error || env.error}</p>}
        <div><button disabled={!current?.service_ready || !env.service?.url} onClick={()=>window.dispatchEvent(new CustomEvent("desk-open-preview",{detail:{kind:"web",url:env.service!.url}}))}>打开预览</button>
          <button disabled={!!busy} onClick={()=>void action("正在停止",async()=>{
            const result=await call<{ok:boolean;error?:string}>("stop",{environment:env.id},75000);
            if(!result.ok)throw new Error(result.error || "停止尚未确认。");
            setObserved(await call<Observation>("status",{environment:env.id}));setNotice("原环境已停止，项目文件和依赖保留。");
          })}>停止环境</button></div>
        {(!env.sandbox_id || current?.recovery_required) && <button disabled={!!busy} onClick={()=>void action("正在观察原环境",async()=>{
          const result=await call<{ok:boolean;error?:string;verification_required?:boolean}>("reconcile",{environment:env.id});
          if(result.verification_required){setVerifyCandidate(env.id);setNotice(result.error || "需验证原候选归属。");}
          else if(!result.ok)throw new Error(result.error || "原环境尚未核对。");
          else setNotice("已核对原ID；旧服务会话未接管，先停止原环境再按需启动。");
        })}>核对原环境</button>}
        {verifyCandidate===env.id && <p>验证可能启动原候选，只读取固定归属标记。<button disabled={!!busy} onClick={()=>void action("正在验证候选归属",async()=>{
          const result=await call<{ok:boolean;error?:string}>("reconcile",{environment:env.id,verify:true},75000);
          if(!result.ok)throw new Error(result.error || "候选归属未确认。");
          setVerifyCandidate("");setObserved(await call<Observation>("status",{environment:env.id}));setNotice("原环境归属已核对；配置依据原文件和日志继续，未重放。");
        })}>启动候选并验证归属</button></p>}
        {current?.archive_status?.eligible && <button disabled={!!busy} onClick={()=>setArchiveConfirm({environment:env.id,operation:current.archive_status!.operation})}>放弃原创建请求</button>}
        {current?.archive_status?.local_active && !env.sandbox_id && <p>原创建或归属验证仍在父后台执行，暂不能归档。</p>}
        {archiveConfirm?.environment===env.id && !env.creation_archive && <p>确认放弃操作 {archiveConfirm.operation.slice(0,12)}？这只释放应用准入，创建结果仍未知；不会停止或删除远端实例。<button disabled={!!busy || !current?.archive_status?.eligible} onClick={()=>void action("正在归档原请求",async()=>{
          const target=archiveConfirm;
          if(!target) return;
          if(archiveAttempt.current?.environment!==target.environment || archiveAttempt.current?.operation!==target.operation)
            archiveAttempt.current={...target,request:crypto.randomUUID()};
          const result=await call<{ok:boolean;error?:string}>("archive_create",{environment:target.environment,expected_operation:target.operation,request_id:archiveAttempt.current.request,confirm:true});
          if(!result.ok)throw new Error(result.error || "原请求尚未归档。");
          const state=await call<Observation>("status",{environment:target.environment});
          if(selectedRef.current===target.environment)setObserved(state);
          setArchiveConfirm(null);setNotice("原请求已归档并释放应用准入；创建结果仍未知，原回执和诊断保留。");
        })}>确认释放应用准入</button><button disabled={!!busy} onClick={()=>setArchiveConfirm(null)}>继续保留原请求</button></p>}
      </div>}
    </>}
    <label className="project-runtime-picker">日志<select value={activeLog} onChange={event=>setLog(event.target.value)}>
      {!logs.length && <option value="">暂无执行记录</option>}
      {logs.map(id=><option key={id} value={id}>{id===env?.service?.operation?"服务日志":id===env?.prepare_operation?"准备环境":"操作记录"} · {id.slice(0,8)}</option>)}
    </select></label>
    <div ref={host} className="project-runtime-terminal" aria-label="项目日志" />
    <footer><button onClick={()=>void navigator.clipboard.writeText(terminal.current?.getSelection() || "").catch(reason=>setError(String(reason)))}>复制选中内容</button><button onClick={()=>terminal.current?.clear()}>清屏</button></footer>
    <p role="status" className="project-runtime-notice">{busy || notice || "项目服务独立运行；应用退出时停止环境并保留文件。"}</p>
    {error && <p role="alert" className="project-runtime-error">{error}</p>}
  </section>;
}
