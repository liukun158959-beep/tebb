import { useEffect, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { rpc, type BackendInfo } from './api';
import { PET_PALETTES, petSvg, type BuiltinSkin } from '../builtin-pet';

type Preferences = { ok: boolean; skin: string; size?: number; live2d_available: boolean };
export function PetSettings({ info, debug = false }: { info: BackendInfo | null; debug?: boolean }) {
  const [prefs, setPrefs] = useState<Preferences>({ ok: true, skin: 'paper', live2d_available: false });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  useEffect(() => {
    let live = true;
    if (info && !debug) void rpc<Preferences>(info, 'load_pet_preferences').then(value => { if (live) setPrefs(value); }).catch(e => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [info?.port, info?.token, debug]);
  async function choose(skin: string, size = prefs.size || 48) {
    if (busy) return;
    if (debug) { setPrefs(old => ({ ...old, skin, size })); setNotice('演示外观，未修改实际设置。'); return; }
    if (!info) { setError('本地后端尚未连接。'); return; }
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await rpc<Preferences>(info, 'save_pet_preferences', { skin, size });
      if (!value.ok) throw new Error('桌宠皮肤未能保存');
      setPrefs(value); setNotice('已保存，桌宠会自动切换；隐藏时可点击「显示桌宠」。');
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function visibility(visible:boolean){setError('');try{await invoke('set_pet_visible',{visible});setNotice(visible?'桌宠已显示。':'桌宠已隐藏。');}catch(e){setError(String(e));}}
  return <section className="pet-settings mb-6 border border-border bg-card/80 p-5" aria-label="桌宠形象">
    <header><div><h2 className="text-lg font-semibold">桌宠形象</h2><p>圆点 · 单击打开浮动聊天框，拖动调整位置。外观独立于 Agent 人格。</p></div><div><button type="button" className="desk-menu-item" disabled={!isTauri()} onClick={()=>void visibility(true)}>显示桌宠</button><button type="button" className="desk-menu-item" disabled={!isTauri()} onClick={()=>void visibility(false)}>隐藏桌宠</button></div></header>
    <div className="pet-appearance-preview" aria-label="当前桌宠外观预览">{prefs.skin==='kaltsit'?<p>凯尔希 Live2D · 点击“显示桌宠”查看实际动态外观。</p>:<img width={prefs.size||48} height={prefs.size||48} src={`data:image/svg+xml,${encodeURIComponent(petSvg(prefs.skin as BuiltinSkin))}`} alt="当前圆点桌宠"/>}</div>
    <div className="pet-skin-options">{Object.entries(PET_PALETTES).map(([id, palette]) => <button key={id} type="button" aria-pressed={prefs.skin === id} disabled={busy} onClick={() => void choose(id)}>
      <img src={`data:image/svg+xml,${encodeURIComponent(petSvg(id as BuiltinSkin))}`} alt={`圆点 · ${palette.name}`} /><strong>{palette.name}</strong><small>内置 · 离线可用</small>
    </button>)}<button type="button" aria-pressed={prefs.skin === 'kaltsit'} disabled={busy || !prefs.live2d_available} onClick={() => void choose('kaltsit')}><span className="pet-live2d-mark" aria-hidden="true">K.</span><strong>凯尔希</strong><small>{prefs.live2d_available ? '已有 Live2D 形象' : '请先安装 Live2D 素材'}</small></button></div>
    <label className="pet-size-control">圆形大小 <input aria-label="圆形桌宠大小" type="range" min="32" max="64" step="4" disabled={busy || prefs.skin==='kaltsit'} value={prefs.size || 48} onChange={e => void choose(prefs.skin, Number(e.target.value))} /><span>{prefs.size || 48}px</span></label>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}
