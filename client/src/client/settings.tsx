import { useEffect, useRef, useState } from "react";

export const BG_STORAGE_KEY = "desk-companion-bg";
const BG_MAX = 2_000_000;

export type ModelPublic = {
  ok: boolean;
  error?: string;
  base_url: string;
  model: string;
  has_key: boolean;
  input_cny_per_mtok?: number | null;
  output_cny_per_mtok?: number | null;
  message?: string;
};

export type ModelEntry = {
  id: string;
  base_url: string;
  model: string;
  has_key: boolean;
};

export type ModelList = {
  ok: boolean;
  error?: string;
  message?: string;
  active: string;
  items: ModelEntry[];
};

export const MODELS_FIXTURE: ModelList = {
  ok: true,
  active: "m1",
  items: [
    { id: "m1", base_url: "https://api.example.com/v1", model: "example-model", has_key: true },
    { id: "m2", base_url: "https://api.other.example/v1", model: "example-model-2", has_key: true },
  ],
};

export const SETTINGS_FIXTURE: ModelPublic = {
  ok: true,
  base_url: "https://api.example.com/v1",
  model: "example-model",
  has_key: true,
};

export type PersonaPublic = {
  ok: boolean;
  error?: string;
  message?: string;
  persona: string;
  max_steps: number;
  nudge_enabled: boolean;
};

export const PERSONA_FIXTURE: PersonaPublic = {
  ok: true,
  persona: "你是 tebb，可靠、灵活的个人 Agent 工作伙伴。用中文直接回答，依据真实工具结果汇报。",
  max_steps: 8,
  nudge_enabled: true,
};

export function isStoredBackground(value: string): boolean {
  return /^data:image\/(png|jpeg|webp);base64,[a-z0-9+/=\r\n]+$/i.test(value);
}

export function readStoredBackground(): { url: string; error: string } {
  let raw = "";
  try {
    raw = localStorage.getItem(BG_STORAGE_KEY) || "";
  } catch (err) {
    return { url: "", error: `读不到已保存的背景。${String(err)} 恢复：在设置里再选一张图。` };
  }
  if (!raw) return { url: "", error: "" };
  if (!isStoredBackground(raw)) {
    localStorage.removeItem(BG_STORAGE_KEY);
    return { url: "", error: "保存的背景打不开。恢复：在设置里再选一张 png、jpg 或 webp。" };
  }
  return { url: raw, error: "" };
}

export function storeBackground(url: string): { url: string; error: string } {
  if (!isStoredBackground(url)) {
    return { url: "", error: "这张图打不开。恢复：换一张 png、jpg 或 webp。" };
  }
  if (url.length > BG_MAX) {
    return { url: "", error: "这张图太大，放不进本机记录。恢复：换一张更小的图。" };
  }
  try {
    localStorage.setItem(BG_STORAGE_KEY, url);
  } catch (err) {
    return { url: "", error: `这张图太大，放不进本机记录。${String(err)} 恢复：换一张更小的图。` };
  }
  return { url, error: "" };
}

export function clearStoredBackground(): void {
  localStorage.removeItem(BG_STORAGE_KEY);
}

function acceptedImage(file: File): boolean {
  const type = file.type.toLowerCase();
  if (type === "image/png" || type === "image/jpeg" || type === "image/webp") return true;
  const name = file.name.toLowerCase();
  return name.endsWith(".png") || name.endsWith(".jpg") || name.endsWith(".jpeg") || name.endsWith(".webp");
}

export function coverPlacement(imgW: number, imgH: number, frameW: number, frameH: number) {
  const scale = Math.max(frameW / imgW, frameH / imgH);
  const displayW = imgW * scale;
  const displayH = imgH * scale;
  return {
    scale,
    displayW,
    displayH,
    minX: frameW - displayW,
    minY: frameH - displayH,
    x: (frameW - displayW) / 2,
    y: (frameH - displayH) / 2,
  };
}

export function clampCropOffset(x: number, y: number, minX: number, minY: number) {
  return {
    x: Math.min(0, Math.max(minX, x)),
    y: Math.min(0, Math.max(minY, y)),
  };
}

export function cropCoverImage(
  image: CanvasImageSource & { naturalWidth?: number },
  frameW: number,
  frameH: number,
  offsetX: number,
  offsetY: number,
  scale: number,
): string {
  const canvas = document.createElement("canvas");
  const longEdge = Math.max(frameW, frameH);
  const ratio = longEdge > 1280 ? 1280 / longEdge : 1;
  canvas.width = Math.max(1, Math.round(frameW * ratio));
  canvas.height = Math.max(1, Math.round(frameH * ratio));
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("截取失败。恢复：再选一次这张图。");
  }
  const sx = -offsetX / scale;
  const sy = -offsetY / scale;
  const sw = frameW / scale;
  const sh = frameH / scale;
  ctx.drawImage(image, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}

export function readBackgroundFile(file: File): Promise<{ url: string; error: string }> {
  if (!acceptedImage(file)) {
    return Promise.resolve({ url: "", error: "只能用 png、jpg 或 webp。" });
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ url: String(reader.result || ""), error: "" });
    reader.onerror = () => resolve({ url: "", error: "这张图打不开。恢复：换一张 png、jpg 或 webp。" });
    reader.readAsDataURL(file);
  });
}

export function SettingsPane(props: {
  section?: 'assistant'|'appearance';
  model: ModelPublic | null;
  models: ModelEntry[];
  editingId: string;
  modelError: string;
  notice: string;
  persona: string;
  personaError: string;
  personaNotice: string;
  busy: boolean;
  keyEpoch: number;
  dark: boolean;
  photo: boolean;
  bgError: string;
  onToggleTheme: () => void;
  onPickFile: (file: File) => void;
  onClearBackground: () => void;
  onSave: (draft: { id: string; base_url: string; model: string; api_key: string }) => void;
  onTest: (draft: { base_url: string; model: string; api_key: string }) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
  onSavePersona: (persona: string) => void;
  onOpenGuide: () => void;
}) {
  const [baseUrl, setBaseUrl] = useState(props.model?.base_url || "");
  const [modelName, setModelName] = useState(props.model?.model || "");
  const [apiKey, setApiKey] = useState("");
  const [persona, setPersona] = useState(props.persona);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setBaseUrl(props.model?.base_url || "");
    setModelName(props.model?.model || "");
  }, [props.model?.base_url, props.model?.model]);

  useEffect(() => {
    setApiKey("");
  }, [props.keyEpoch]);

  useEffect(() => {
    setPersona(props.persona);
  }, [props.persona]);

  return (
    <div data-settings className="mx-auto flex max-w-xl flex-col gap-8">
      <section className="rounded-lg border border-border p-4 text-sm">
        <p className="mb-3 text-muted-foreground">首次使用或不确定配置位置时，可以重新打开引导，查看模型、账号和可选功能的准备步骤。</p>
        <button type="button" className="desk-btn desk-btn-lg" onClick={props.onOpenGuide}>打开使用引导</button>
      </section>
      <section hidden={props.section==='appearance'} className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-primary">模型</h2>
        <div className="flex flex-col gap-1">
          {props.models.length === 0 ? (
            <p className="text-sm text-muted-foreground">还没有模型。在下面填一条再添加。</p>
          ) : null}
          {props.models.map((item) => (
            <button
              key={item.id}
              type="button"
              data-model-entry={item.id}
              data-selected={props.editingId === item.id ? "1" : "0"}
              className={`rounded-[10px] border px-3 py-2 text-left text-sm ${
                props.editingId === item.id ? "border-primary bg-primary/15 text-primary" : "border-border"
              }`}
              onClick={() => props.onEdit(item.id)}
            >
              {item.model}
              <span className="ml-2 text-xs text-muted-foreground">{item.base_url}</span>
            </button>
          ))}
        </div>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">API 地址</span>
          <input
            data-model-base
            placeholder="https://api.example.com/v1"
            value={baseUrl}
            onChange={(ev) => setBaseUrl(ev.target.value)}
            className="border border-border bg-background px-3 py-2"
            autoComplete="off"
          />
        </label>
        <p className="text-xs text-muted-foreground">使用服务商提供的 Base URL，通常包含 /v1。不要填写聊天网页地址或 /chat/completions。</p>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">模型名</span>
          <input
            data-model-name
            placeholder="控制台中的准确模型 ID"
            value={modelName}
            onChange={(ev) => setModelName(ev.target.value)}
            className="border border-border bg-background px-3 py-2"
            autoComplete="off"
          />
        </label>
        <p className="text-xs text-muted-foreground">照抄服务商控制台的模型 ID；调用工具还需模型支持 tool calling。</p>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted-foreground">API Key</span>
          <input
            data-model-key
            data-model-key-filled={apiKey.trim() ? "1" : "0"}
            data-model-has-key={props.model?.has_key ? "1" : "0"}
            type="password"
            value={apiKey}
            onChange={(ev) => setApiKey(ev.target.value)}
            placeholder={props.model?.has_key ? "已保存，留空则不改" : "必填"}
            className="border border-border bg-background px-3 py-2"
            autoComplete="off"
          />
        </label>
        <p className="text-xs text-muted-foreground">
          {props.editingId && props.model?.has_key
            ? "Key 留空时，保存这条会保持原来的 Key，添加为新模型会沿用这把 Key。页面不显示原文。"
            : "填写 Key。页面不显示原文。"}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            data-model-save
            disabled={props.busy}
            className="desk-btn desk-btn-lg desk-btn-solid"
            onClick={() =>
              props.onSave({ id: props.editingId, base_url: baseUrl, model: modelName, api_key: apiKey })
            }
          >
            {props.editingId ? "保存这条" : "添加模型"}
          </button>
          <button
            type="button"
            data-model-create
            disabled={props.busy}
            className="desk-btn desk-btn-lg"
            onClick={() => props.onSave({ id: "", base_url: baseUrl, model: modelName, api_key: apiKey })}
          >
            添加为新模型
          </button>
          <button
            type="button"
            data-model-delete
            disabled={props.busy || !props.editingId}
            className="desk-btn desk-btn-lg desk-btn-danger"
            onClick={() => props.onDelete(props.editingId)}
          >
            删除这条
          </button>
          <button
            type="button"
            data-model-test
            disabled={props.busy}
            className="desk-btn desk-btn-lg"
            onClick={() => props.onTest({ base_url: baseUrl, model: modelName, api_key: apiKey })}
          >
            测试连通
          </button>
        </div>
        <p className="text-xs text-muted-foreground">测试连通会向你填写的服务商发送一条简短请求，可能产生 API 费用。Key 保存在本机配置文件中。</p>
        {props.notice ? (
          <p data-settings-notice className="text-sm text-primary">
            {props.notice}
          </p>
        ) : null}
        {props.modelError ? (
          <p data-settings-error className="text-sm text-destructive">
            {props.modelError}
          </p>
        ) : null}
      </section>
      <section hidden={props.section==='appearance'} className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-primary">系统提示词</h2>
        <p className="text-xs text-muted-foreground">保存后立刻生效。工具用法仍由程序接在这段后面。</p>
        <textarea
          data-persona
          value={persona}
          onChange={(ev) => setPersona(ev.target.value)}
          rows={8}
          className="border border-border bg-background px-3 py-2 text-sm"
        />
        <button
          type="button"
          data-persona-save
          disabled={props.busy}
          className="w-fit desk-btn desk-btn-lg desk-btn-solid"
          onClick={() => props.onSavePersona(persona)}
        >
          保存提示词
        </button>
        {props.personaNotice ? (
          <p data-persona-notice className="text-sm text-primary">
            {props.personaNotice}
          </p>
        ) : null}
        {props.personaError ? (
          <p data-persona-error className="text-sm text-destructive">
            {props.personaError}
          </p>
        ) : null}
      </section>
      <section hidden={props.section==='assistant'} className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-primary">外观</h2>
        <div className="flex flex-wrap gap-2">
          <button type="button" data-theme className="desk-btn desk-btn-lg desk-btn-solid" onClick={props.onToggleTheme}>
            {props.dark ? "浅色" : "深色"}
          </button>
          <button
            type="button"
            data-bg-pick
            className="desk-btn desk-btn-lg"
            onClick={() => fileRef.current?.click()}
          >
            背景
          </button>
          {props.photo ? (
            <button
              type="button"
              data-bg-clear
              className="desk-btn desk-btn-lg"
              onClick={props.onClearBackground}
            >
              恢复网格
            </button>
          ) : null}
        </div>
        <input
          ref={fileRef}
          data-bg-file
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(ev) => {
            const file = ev.target.files?.[0];
            ev.target.value = "";
            if (file) props.onPickFile(file);
          }}
        />
        {props.bgError ? (
          <p data-bg-error className="text-sm text-destructive">
            {props.bgError}
          </p>
        ) : null}
      </section>
    </div>
  );
}

export function BackgroundCrop(props: {
  src: string;
  error: string;
  onConfirm: (url: string) => void;
  onCancel: () => void;
  onBroken: () => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const dragRef = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [natural, setNatural] = useState({ w: 0, h: 0 });
  const [frame, setFrame] = useState({ w: 0, h: 0 });
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const node = frameRef.current;
    if (!node) return;
    const measure = () => setFrame({ w: node.clientWidth, h: node.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const place = natural.w > 0 && frame.w > 0 && frame.h > 0
    ? coverPlacement(natural.w, natural.h, frame.w, frame.h)
    : null;

  useEffect(() => {
    if (!place) return;
    setOffset({ x: place.x, y: place.y });
  }, [place?.x, place?.y, natural.w, natural.h, frame.w, frame.h]);

  function move(x: number, y: number) {
    if (!place) return;
    setOffset(clampCropOffset(x, y, place.minX, place.minY));
  }

  return (
    <div
      ref={frameRef}
      data-bg-crop=""
      data-bg-crop-x={offset.x.toFixed(1)}
      data-bg-crop-y={offset.y.toFixed(1)}
      className="absolute inset-0 z-30 cursor-grab overflow-hidden bg-black"
      onPointerDown={(ev) => {
        if ((ev.target as HTMLElement).closest("[data-bg-crop-actions]")) return;
        dragRef.current = { x: ev.clientX, y: ev.clientY, ox: offset.x, oy: offset.y };
        ev.currentTarget.setPointerCapture(ev.pointerId);
      }}
      onPointerMove={(ev) => {
        const drag = dragRef.current;
        if (!drag) return;
        move(drag.ox + ev.clientX - drag.x, drag.oy + ev.clientY - drag.y);
      }}
      onPointerUp={() => {
        dragRef.current = null;
      }}
    >
      <img
        ref={imageRef}
        src={props.src}
        alt=""
        draggable={false}
        className="absolute max-w-none select-none"
        style={place ? { width: place.displayW, height: place.displayH, left: offset.x, top: offset.y } : { visibility: "hidden" }}
        onLoad={(ev) => setNatural({ w: ev.currentTarget.naturalWidth, h: ev.currentTarget.naturalHeight })}
        onError={props.onBroken}
      />
      <div data-bg-crop-actions className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-2 bg-background/90 px-3 py-2 text-sm">
        <span className="text-muted-foreground">拖动图片，把要留下的区域放进窗口。</span>
        <button
          type="button"
          data-bg-crop-ok
          className="desk-btn desk-btn-lg desk-btn-solid"
          onClick={() => {
            const image = imageRef.current;
            if (!image || !place || !image.naturalWidth) return;
            props.onConfirm(cropCoverImage(image, frame.w, frame.h, offset.x, offset.y, place.scale));
          }}
        >
          确认截取
        </button>
        <button type="button" data-bg-crop-cancel className="desk-btn desk-btn-lg" onClick={props.onCancel}>
          取消
        </button>
      </div>
      {props.error ? (
        <p data-bg-crop-error className="absolute left-1/2 top-4 -translate-x-1/2 bg-background/90 px-3 py-2 text-sm text-destructive">
          {props.error}
        </p>
      ) : null}
    </div>
  );
}
