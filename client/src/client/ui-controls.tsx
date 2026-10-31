import { Children, cloneElement, useId, useState, type ButtonHTMLAttributes, type ReactElement, type ReactNode } from 'react';
import { LoadingText } from './loading-text';

export function UiButton({ busy = false, busyLabel = '正在处理…', variant = 'default', children, className = '', disabled, ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean; busyLabel?: string; variant?: 'default' | 'primary' | 'danger' }) {
  return <button type="button" {...props} data-ui-control="button" aria-busy={busy || undefined} disabled={disabled || busy}
    className={`desk-btn ${variant === 'primary' ? 'desk-btn-solid' : variant === 'danger' ? 'desk-btn-danger' : ''} ${className}`}>
    {busy ? <LoadingText text={busyLabel} /> : children}
  </button>;
}

export function UiField({ label, help, error, children }: { label: ReactNode; help?: ReactNode; error?: ReactNode; children: ReactElement }) {
  const generated = useId(), control = Children.only(children) as ReactElement<Record<string, unknown>>;
  const id = typeof control.props.id === 'string' ? control.props.id : `field-${generated}`;
  const description = [control.props['aria-describedby'], help ? id + '-help' : '', error ? id + '-error' : ''].filter(Boolean).join(' ');
  return <div className="ui-field" data-ui-control="field"><label htmlFor={id}>{label}</label>
    {cloneElement(control, { id, 'aria-describedby': description || undefined, 'aria-invalid': error ? true : control.props['aria-invalid'] })}
    {help && <p id={id + '-help'} className="ui-field-help">{help}</p>}
    {error && <p id={id + '-error'} className="ui-field-error">{error}</p>}
  </div>;
}

export type FeedbackState = 'loading' | 'empty' | 'no_results' | 'success' | 'error' | 'conflict' | 'unconfirmed';
export function UiFeedback({ state, title, children, className = '' }: { state: FeedbackState; title: string; children?: ReactNode; className?: string }) {
  const alert = state === 'error' || state === 'conflict' || state === 'unconfirmed';
  return <section className={`ui-feedback ${className}`} data-ui-feedback={state} role={alert ? 'alert' : 'status'} aria-busy={state === 'loading' || undefined}>
    <div className="ui-feedback-title">{state === 'loading' ? <LoadingText text={title} /> : title}</div>
    {children && <div className="ui-feedback-detail">{children}</div>}
  </section>;
}

export function UiContractSample() {
  const [pressed, setPressed] = useState(false);
  const states: [FeedbackState, string][] = [['loading', '示例：正在读取'], ['empty', '示例：还没有数据'], ['no_results', '示例：筛选没有结果'],
    ['success', '示例：已完成'], ['error', '示例：确定失败'], ['conflict', '示例：数据已被修改'], ['unconfirmed', '示例：结果未确认']];
  return <details className="ui-contract-sample"><summary>共享控件状态样板</summary>
    <p className="ui-field-help">仅展示控件状态，不执行保存或重试。下方看板与日程使用同一组控件。</p>
    <div className="ui-contract-buttons"><UiButton aria-pressed={pressed} onClick={() => setPressed(p => !p)}>样板选择</UiButton><UiButton variant="primary">主要样板</UiButton><UiButton disabled>不可用样板</UiButton><UiButton busy busyLabel="样板处理中…">忙碌样板</UiButton></div>
    <UiField label="样板字段" help="帮助文字与输入关联。" error="示例错误，保留输入与焦点。"><input readOnly value="保留的输入" /></UiField>
    <div className="ui-contract-feedback">{states.map(([state, title]) => <UiFeedback key={state} state={state} title={title}>{state === 'unconfirmed' ? '先核对原回执；共享控件不会自动重试。' : undefined}</UiFeedback>)}</div>
  </details>;
}
