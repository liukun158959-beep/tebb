import { Component, type ErrorInfo, type ReactNode } from 'react';
import { startupFailed } from './startup';

export type RenderFailure = { code: 'render_failed'; errorType: string };
let failure: RenderFailure | null = null;

// Read-only diagnostics: no chat, configuration, tokens, raw URLs or exception payloads.
export function renderDiagnostics(): { state: 'healthy' | 'failed'; failure: RenderFailure | null } {
  return { state: failure ? 'failed' : 'healthy', failure: failure && { ...failure } };
}

export class RenderBoundary extends Component<{children: ReactNode}, {failed: boolean}> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, _info: ErrorInfo) {
    failure = { code: 'render_failed', errorType: error instanceof TypeError ? 'TypeError' : error instanceof ReferenceError ? 'ReferenceError' : 'Error' };
    startupFailed(new Error('界面渲染失败。请重新加载；仍失败时记录诊断编号 render_failed。'));
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <main role="alert" aria-labelledby="render-failure-title" style={{padding:32,color:'#f4f1ea',background:'#101216',minHeight:'100vh',fontFamily:'Segoe UI, Microsoft YaHei, sans-serif'}}>
      <h1 id="render-failure-title">界面渲染失败</h1>
      <p>本地助手可能仍在工作。可尝试重新加载界面；不会自动重新发送消息或重跑任务。</p>
      <p>诊断编号：<code>render_failed</code></p>
      <button type="button" onClick={()=>window.location.reload()}>重新加载界面</button>
    </main>;
  }
}
