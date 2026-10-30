import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { rpc, type BackendInfo } from './api';
import { guideAnchor, useGuideAnchor } from './onboarding-anchor';
import { GUIDE_PHASES, guidePlacement, initialGuidePhase, modelGuideTarget, type GuidePhase, type ModelSetupState } from './onboarding-state';
import './onboarding.css';

export type SetupStatus = {
  ok: boolean; show: boolean; configured: boolean; data_dir: string; assets_dir: string; step?: string;
  checks: { github: boolean; feishu: boolean; knowledge: boolean; pet: boolean; live2d?: boolean };
};
export type GuideDestination = 'settings' | 'feishu' | 'terminal' | 'collaboration';

export function SetupGuide(props: {
  info: BackendInfo; status: SetupStatus; modelState: ModelSetupState | null;
  configured: boolean; feishuConnected: boolean; modelBusy: boolean;
  onClose: (completed?: boolean) => void; onNavigate: (pane: GuideDestination) => void;
}) {
  const [phase, setPhase] = useState<GuidePhase>(() => initialGuidePhase(props.configured, props.status.step));
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight });
  const latest = useRef(props); latest.current = props;
  const panel = useRef<HTMLElement>(null);
  const [panelHeight, setPanelHeight] = useState(260);
  const paused = useRef(false);
  const target = phase === 'model' ? modelGuideTarget(props.modelState) : phase === 'feishu'
    ? { anchor: 'feishu-connect', title: props.feishuConnected ? '飞书已连接' : '飞书可以稍后连接', text: props.feishuConnected ? '当前 tebb 已连接飞书；具体权限以页面实际结果为准。' : '想读取日程、待办或文档时，在这里明确连接。系统已有登录不等于 tebb 已连接；现在也可以跳过。' }
    : { anchor: 'terminal-check', title: '终端按需准备', text: '这里显示沙箱的真实检查结果。环境未就绪时先按提示准备；引导不会安装组件或执行命令，可以稍后再来。' };
  const rect = useGuideAnchor(target.anchor);
  const position = guidePlacement(rect, viewport.width, viewport.height, panelHeight);
  const handAngle = rect ? Math.atan2(rect.top + rect.height / 2 - position.top - 40, rect.left + rect.width / 2 - position.left - 40) * 180 / Math.PI + 90 : -25;
  const navigate = () => latest.current.onNavigate(phase === 'model' ? 'settings' : phase);
  useEffect(() => { navigate(); }, [phase]);
  useEffect(() => {
    const update = () => setViewport({ width: window.visualViewport?.width || window.innerWidth, height: window.visualViewport?.height || window.innerHeight });
    window.addEventListener('resize', update); window.visualViewport?.addEventListener('resize', update); update();
    const observer = new ResizeObserver(() => { if (panel.current) setPanelHeight(panel.current.getBoundingClientRect().height); });
    if (panel.current) observer.observe(panel.current);
    return () => { window.removeEventListener('resize', update); window.visualViewport?.removeEventListener('resize', update); observer.disconnect(); };
  }, []);
  async function pause() {
    if (busy || props.modelBusy || paused.current) return;
    paused.current = true; setBusy(true); setError('');
    try {
      const result = await rpc<{ok:boolean;error?:string}>(props.info, 'save_onboarding_progress', { step: phase });
      if (!result.ok) throw Error(result.error || '引导进度未保存。');
      props.onClose();
    } catch (err) { paused.current = false; setBusy(false); setError(String(err)); }
  }
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); void pause(); } };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  });
  async function next() {
    if (phase === 'model' && !props.modelState?.saved) return;
    setBusy(true); setError('');
    const nextPhase = GUIDE_PHASES[GUIDE_PHASES.indexOf(phase) + 1];
    try {
      const result = await rpc<{ok:boolean;error?:string}>(props.info, nextPhase ? 'save_onboarding_progress' : 'complete_onboarding', nextPhase ? { step: nextPhase } : {});
      if (!result.ok) throw Error(result.error || '引导进度未保存，请重试。');
      if (nextPhase) setPhase(nextPhase);
      else { props.onClose(true); props.onNavigate('collaboration'); }
    } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  }
  function point() {
    navigate();
    const node = guideAnchor(target.anchor);
    node?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
    node?.focus({ preventScroll: true });
  }
  const disabled = busy || props.modelBusy;
  return <div data-onboarding className="setup-guide-layer">
    {rect && <div aria-hidden="true" className="setup-guide-focus" style={{ left: rect.left - 5, top: rect.top - 5, width: rect.width + 10, height: rect.height + 10 }} />}
    <section ref={panel} role="region" aria-label="桌宠使用引导" className="setup-guide-card" style={position} onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); void pause(); } }}>
      <header><div key={target.anchor} className={`setup-guide-pet ${props.modelState?.error && phase === 'model' ? 'setup-guide-pet-error' : ''}`} style={{ '--setup-hand-angle': `${handAngle}deg` } as CSSProperties} aria-hidden="true"><span className="setup-guide-eyes"/><span className="setup-guide-hand"/></div><div><p className="setup-guide-caption">tebb 带你设置 · {GUIDE_PHASES.indexOf(phase) + 1} / 3</p><h2>{target.title}</h2></div></header>
      <p className="setup-guide-message" aria-live="polite" aria-atomic="true">{target.text}</p>
      {phase === 'model' && props.modelState?.error && <p className="setup-guide-error" role="alert">{props.modelState.error} 请在原表单修正后再试。</p>}
      {error && <p className="setup-guide-error" role="alert">{error}</p>}
      <div className="setup-guide-actions"><button type="button" className="desk-btn" disabled={disabled} onClick={point}>{rect ? '指给我看' : '返回配置位置'}</button><button type="button" className="desk-btn" disabled={disabled} onClick={() => void pause()}>稍后继续</button><button type="button" className="desk-btn desk-btn-solid" disabled={disabled || phase === 'model' && !props.modelState?.saved} onClick={() => void next()}>{phase === 'model' ? props.modelState?.tested ? '继续' : '继续，稍后测试' : phase === 'feishu' ? props.feishuConnected ? '继续' : '跳过飞书' : '完成引导'}</button></div>
      {phase !== 'model' && <button type="button" className="setup-guide-back" disabled={disabled} onClick={() => { setError(''); setPhase(GUIDE_PHASES[GUIDE_PHASES.indexOf(phase) - 1]); }}>上一步</button>}
    </section>
  </div>;
}
