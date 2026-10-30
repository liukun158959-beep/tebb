export type GuidePhase = 'model' | 'feishu' | 'terminal';
export type ModelSetupState = {
  hasBase: boolean; hasModel: boolean; hasKey: boolean;
  saved: boolean; tested: boolean; busy: boolean; error: string;
};
export const GUIDE_PHASES: GuidePhase[] = ['model', 'feishu', 'terminal'];
export function initialGuidePhase(configured: boolean, step?: string): GuidePhase {
  return configured && GUIDE_PHASES.includes(step as GuidePhase) ? step as GuidePhase : 'model';
}
export function modelGuideTarget(state: ModelSetupState | null) {
  if (!state || !state.hasBase) return { anchor: 'model-base', title: '先填 API 地址', text: '把服务商的 Base URL 填在这里，通常包含 /v1；不要填聊天网页地址。' };
  if (!state.hasModel) return { anchor: 'model-name', title: '还缺模型 ID', text: '照抄服务商控制台里的模型 ID，大小写与版本保持一致。' };
  if (!state.hasKey) return { anchor: 'model-key', title: '还缺 API Key', text: '在这里粘贴服务商的 API Key。Key 只交给真实设置表单，不进入引导记录。' };
  if (!state.saved) return { anchor: 'model-save', title: '把模型保存下来', text: '信息已填齐，点击这里保存。填完或测试成功都不等于已保存。' };
  return { anchor: 'model-test', title: state.tested ? '模型连接已验证' : '已保存，还没有验证连通', text: state.tested ? '本次填写的模型已保存并通过真实连通测试，可以继续。' : '点击测试连通，检查地址、模型与 Key。测试会发起简短 API 请求，可能产生费用；也可以明确选择稍后测试。' };
}
export type GuideRect = { left: number; top: number; width: number; height: number };
export function guidePlacement(rect: GuideRect | null, width: number, height: number, panelHeight = 260) {
  const panelWidth = Math.min(356, Math.max(1, width - 24));
  const left = Math.max(12, Math.min(width - panelWidth - 12, rect ? rect.left + rect.width - panelWidth : width - panelWidth - 20));
  const below = rect ? rect.top + rect.height + 16 : height - panelHeight - 16;
  const above = rect ? rect.top - panelHeight - 16 : below;
  const top = Math.max(12, Math.min(Math.max(12, height - panelHeight - 12), below + panelHeight <= height - 12 ? below : above));
  return { left, top, width: panelWidth };
}
