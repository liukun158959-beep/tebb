/// <reference types="vite/client" />
// 宠物窗：内置矢量小伴 / 可选 Live2D、拖动、任务气泡和右键菜单。
// 穿透由 Rust 轮询命中区域。拖动期间锁住穿透，避免松手事件被吃掉。
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { PetFeedback, type FeedbackProgress, type FeedbackRun } from './pet-feedback';
import {
  BUBBLE_MS,
  type Bubble,
  type Gesture,
  IDLE,
  bubbleDue,
  dismissBubble,
  emptyBubble,
  errorBubble,
  pointerDown,
  pointerMove,
  pointerUp,
  tapBubble,
} from "./pet-gesture";
import { currentMotion, modelBounds, playMotion, startPet, startBuiltinPet, setPetWorking } from "./pet-render";
import { backendInfo, rpc, type BackendInfo } from "./client/api";
import type { SetupStatus } from "./client/onboarding";
import { loadCubismCore, petErrorText } from "./pet-assets";
import { isBuiltinSkin } from "./builtin-pet";
import { installPetMenu, syncPetMenuRegion } from "./pet-menu";

let petAvailable = false;
const bubbleEl = document.getElementById("bubble") as HTMLDivElement;
const menuEl = document.getElementById("menu") as HTMLDivElement;

let gesture: Gesture = IDLE;
let bubble: Bubble = emptyBubble();
const feedback = new PetFeedback();
let feedbackBubble = false, chatVisible = false, hasFeedbackSnapshot = false;
let lastProgress:FeedbackProgress = {enabled:true};
let lastRuns:FeedbackRun[] = [];
function refreshFeedback() {
  if(!hasFeedbackSnapshot)return;
  const value=feedback.update(lastProgress,lastRuns,chatVisible);
  if(value){
    // Asset/runtime errors have their own explicit recovery instructions.
    if(bubble.open && bubble.error && !feedbackBubble)return;
    feedbackBubble=true;
    bubble={open:true,text:value.text,error:value.error,until:0,index:-1};renderBubble();
  } else if(feedbackBubble){feedbackBubble=false;bubble=dismissBubble(bubble);renderBubble();}
}
let gliding = false;
let menuControls: ReturnType<typeof installPetMenu> | null = null;

function placeBubble(): void {
  const height = bubbleEl.offsetHeight;
  const width = bubbleEl.offsetWidth;
  const bounds = modelBounds();
  let top = 24;
  let left = window.innerWidth / 2 - width / 2;
  if (bounds) {
    top = bounds.y - height - 10;
    if (top < 8) top = 8;
    left = bounds.x + bounds.w / 2 - width / 2;
  }
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  bubbleEl.style.left = `${left}px`;
  bubbleEl.style.top = `${top}px`;
}

function renderBubble(): void {
  bubbleEl.classList.toggle("hidden", !bubble.open);
  bubbleEl.dataset.kind = bubble.error ? "error" : "line";
  bubbleEl.textContent = bubble.text;
  if (bubble.open) placeBubble();
  menuControls?.refresh();
  void reportHitRegions();
}

function renderMenu(open: boolean, x = 0, y = 0): void {
  menuEl.classList.toggle("hidden", !open);
  menuControls?.refresh();
  if (!open) {
    void syncPetMenuRegion(menuEl).catch((err) => showError(String(err)));
    void reportHitRegions();
    return;
  }
  const width = menuEl.offsetWidth || 140;
  const height = menuEl.offsetHeight || 120;
  const left = Math.max(8, Math.min(x, window.innerWidth - width - 8));
  const top = Math.max(8, Math.min(y, window.innerHeight - height - 8));
  menuEl.style.left = `${left}px`;
  menuEl.style.top = `${top}px`;
  void syncPetMenuRegion(menuEl).catch((err) => showError(String(err)));
  void reportHitRegions();
}

function showError(message: string): void {
  feedbackBubble = false;
  bubble = errorBubble(message);
  renderBubble();
}

async function reportHitRegions(): Promise<void> {
  const rects: { x: number; y: number; w: number; h: number }[] = [];
  const bounds = modelBounds();
  if (bounds) rects.push(bounds);
  for (const el of [bubbleEl, menuEl]) {
    if (el.classList.contains("hidden")) continue;
    const rect = el.getBoundingClientRect();
    rects.push({ x: rect.x, y: rect.y, w: rect.width, h: rect.height });
  }
  if (!isTauri()) return;
  await invoke("set_hit_regions", { rects });
}

async function rust(command: string, args?: Record<string, unknown>): Promise<void> {
  if (!isTauri()) return;
  await invoke(command, args);
}

function nudge(dx: number, dy: number): void {
  void rust("nudge_pet", { dx, dy }).catch((err: unknown) => {
    showError(`拖动失败：${String(err)}。恢复：重启客户端。`);
  });
}

function glide(vx: number, vy: number): void {
  const speed = Math.hypot(vx, vy);
  if (speed < 0.02) return;
  const cap = 1.2;
  if (speed > cap) {
    vx = (vx / speed) * cap;
    vy = (vy / speed) * cap;
  }
  gliding = true;
  let last = performance.now();
  const frame = (now: number) => {
    if (!gliding) return;
    const dt = Math.min(32, now - last);
    last = now;
    const decay = Math.pow(0.9, dt / 16);
    vx *= decay;
    vy *= decay;
    if (Math.hypot(vx, vy) < 0.02) {
      gliding = false;
      return;
    }
    nudge(vx * dt, vy * dt);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function beginDragLock(): void {
  void rust("set_drag_lock", { locked: true }).catch((err: unknown) => {
    showError(`拖动失败：${String(err)}。恢复：重启客户端。`);
  });
}

function endDragLock(): void {
  void rust("set_drag_lock", { locked: false }).catch((err: unknown) => {
    showError(`拖动结束失败：${String(err)}。恢复：重启客户端。`);
  });
}

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0) return;
  const target = event.target as HTMLElement | null;
  if (target?.closest("#menu, #bubble, button")) return;
  gliding = false;
  gesture = pointerDown(event.screenX, event.screenY, performance.now());
  beginDragLock();
}

function onPointerMove(event: PointerEvent): void {
  const step = pointerMove(gesture, event.screenX, event.screenY, performance.now());
  gesture = step.gesture;
  if (step.startedDrag) {
    bubble = dismissBubble(bubble);
    renderBubble();
    renderMenu(false);
  }
  if (step.dx !== 0 || step.dy !== 0) nudge(step.dx, step.dy);
}

function onPointerUp(): void {
  if (gesture.kind !== "down") return;
  const result = pointerUp(gesture);
  gesture = IDLE;
  endDragLock();
  if (!result.moved) {
    if (!petAvailable) return;
    renderMenu(false);
    void rust("toggle_pet_chat").catch(err=>showError(String(err)));
    void playMotion("Tap").catch((err: unknown) => showError(String(err)));
    return;
  }
  glide(result.vx, result.vy);
}

function openMenu(x: number, y: number): void {
  renderMenu(true, x, y);
}

function runMenu(action: string): void {
  renderMenu(false);
  const jobs: Record<string, { label: string; run: () => Promise<void> }> = {
    today: {
      label: "今天的安排",
      run: async () => {
        await rust("show_main");
        await rust("open_today");
      },
    },
    hide: { label: "隐藏桌宠", run: () => rust("set_pet_visible", { visible: false }) },
    close_bubble: { label: "关闭提示", run: async () => { if(feedbackBubble)feedback.dismiss();feedbackBubble=false;bubble = dismissBubble(bubble); renderBubble(); } },
    main: { label: "打开主窗口", run: () => rust("show_main") },
    quit: { label: "退出", run: () => rust("quit_app") },
  };
  const job = jobs[action];
  if (!job) {
    showError(`没有这个菜单项：${action}。恢复：重启客户端。`);
    return;
  }
  void job.run().catch((err: unknown) => {
    showError(`${job.label}失败：${String(err)}。恢复：重启客户端。`);
  });
}

function expire(now: number): void {
  if (!bubbleDue(bubble, now)) return;
  bubble = dismissBubble(bubble);
  renderBubble();
}

function installDebug(): void {
  if (!import.meta.env.DEV) return;
  const pet = window as Window & {
    __petDebug?: {
      bubbleOpen: () => boolean;
      bubbleText: () => string;
      bubbleKind: () => string;
      bubbleMs: number;
      menuOpen: () => boolean;
      motion: () => string;
      bounds: () => { x: number; y: number; w: number; h: number } | null;
      tap: () => void;
      dragBy: (dx: number, dy: number) => void;
      dismissIfDue: (now: number) => void;
      openMenu: () => void;
      feedback: (progress:FeedbackProgress,runs:FeedbackRun[]) => void;
    };
  };
  pet.__petDebug = {
    bubbleOpen: () => bubble.open,
    bubbleText: () => bubble.text,
    bubbleKind: () => bubbleEl.dataset.kind || "",
    bubbleMs: BUBBLE_MS,
    menuOpen: () => !menuEl.classList.contains("hidden"),
    motion: () => currentMotion(),
    bounds: () => modelBounds(),
    tap: () => {
      bubble = tapBubble(bubble, Date.now());
      renderBubble();
      void playMotion("Tap").catch((err: unknown) => showError(String(err)));
    },
    dragBy: (dx: number, dy: number) => {
      gesture = pointerDown(0, 0, 0);
      const step = pointerMove(gesture, dx, dy, 16);
      gesture = step.gesture;
      if (step.startedDrag) {
        bubble = dismissBubble(bubble);
        renderBubble();
      }
      gesture = IDLE;
    },
    dismissIfDue: (now: number) => expire(now),
    openMenu: () => openMenu(24, 24),
    feedback: (progress,runs) => {lastProgress=progress;lastRuns=runs;hasFeedbackSnapshot=true;refreshFeedback();},
  };
}

async function main(): Promise<void> {
  installDebug();
  if(isTauri()){
    try {
      await listen<boolean>('pet-chat-visible',event=>{chatVisible=event.payload;refreshFeedback();});
      const state=await invoke<{visible:boolean}>('get_pet_chat_state');chatVisible=state.visible;
    } catch { /* Native visibility is retried by the next user toggle. */ }
  }
  bubbleEl.addEventListener('click',event=>{
    if(!feedbackBubble||window.getSelection()?.toString().trim())return;
    event.stopPropagation();feedback.dismiss();
    feedbackBubble=false;bubble=dismissBubble(bubble);renderBubble();
  });
  window.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);
  window.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    if ((event.target as Element | null)?.closest("#menu")) return;
    if (gesture.kind === "down") return;
    openMenu(event.clientX, event.clientY);
  });
  menuControls = installPetMenu({
    menu: menuEl, bubble: bubbleEl, close: () => renderMenu(false), run: runMenu,
    lockSelection: (locked) => { if (locked) beginDragLock(); else endDragLock(); },
    errorText: () => bubble.open && bubble.error ? bubble.text : "",
  });
  setInterval(() => expire(Date.now()), 200);
  setInterval(() => void reportHitRegions(), 500);

  const canvas = document.getElementById("stage") as HTMLCanvasElement;
  // Render immediately, including first run/offline installs without Live2D assets.
  startBuiltinPet(canvas, 'paper'); petAvailable = true;
  let backend: BackendInfo | null = null;
  let lastSkin = 'paper';
  let lastSize = 48;
  let changing = false;
  let reported = false;
  let renderedSkin = 'paper';
  async function refreshSkin() {
    if (!backend || changing) return;
    changing = true;
    try {
      const preferences = await rpc<{ skin: string; size: number }>(backend, 'load_pet_preferences');
      if (preferences.skin !== lastSkin || preferences.size !== lastSize) {
        lastSkin = preferences.skin; lastSize = preferences.size; reported = false;
        bubble = dismissBubble(bubble); renderBubble(); renderMenu(false);
        petAvailable = false;
        try {
          if (isBuiltinSkin(lastSkin)) { startBuiltinPet(canvas, lastSkin, lastSize); renderedSkin = lastSkin; }
          else {
            const setup = await rpc<SetupStatus>(backend, 'load_onboarding');
            const base = `http://127.0.0.1:${backend.port}/pet-assets/${backend.token}`;
            if (!(window as Window & { Live2DCubismCore?: unknown }).Live2DCubismCore) await loadCubismCore(`${base}/Core/live2dcubismcore.js`, `${setup.assets_dir}/Core/live2dcubismcore.js`);
            await startPet(canvas, `${base}/skins/kaltsit/kaltsit.model3.json`); renderedSkin = 'kaltsit';
          }
        } catch (err) {
          startBuiltinPet(canvas, 'paper'); renderedSkin = 'paper';
          showError(`已恢复内置小伴。${petErrorText(err)}`);
        }
        petAvailable = true;
        await reportHitRegions();
      }
      if (!reported) { await rpc(backend, 'report_pet_status', { ready: true, message: `桌宠形象已就绪：${renderedSkin}` }); reported = true; }
    } catch { /* Keep the bundled companion while the backend reconnects. */ }
    finally { changing = false; }
  }
  try {
    if (isTauri()) backend = await backendInfo();
    await refreshSkin();
  } catch { /* Built-in pet works independently of the backend. */ }
  await reportHitRegions();
  if (backend) {
    const info = backend;
    let polling = false;
    setInterval(async () => {
      void refreshSkin();
      if (polling) return;
      polling = true;
      try {
        const [progress, collaboration] = await Promise.all([
          rpc<FeedbackProgress>(info, "load_task_progress"),
          rpc<{ state: { runs: FeedbackRun[] } }>(info, "load_collaboration"),
        ]);
        lastProgress=progress;lastRuns=collaboration.state.runs;hasFeedbackSnapshot=true;refreshFeedback();
        setPetWorking(!!progress.enabled && ((!!progress.task && ['running','queued','cancelling'].includes(progress.task.state)) || collaboration.state.runs.some(run=>['running','queued','waiting','pending','cancelling'].includes(run.state))));
      } catch { /* 接口重连期间保留现有形象，不反复弹出错误。 */ }
      finally { polling = false; }
    }, 2000);
  }
}

void main();
