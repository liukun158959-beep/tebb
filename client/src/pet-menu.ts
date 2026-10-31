import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export function petActionError(label: string, error: unknown): string {
  const message = (error instanceof Error ? error.message : String(error)).trim();
  const ending = /[。！？.!?]$/.test(message) ? "" : "。";
  const recovery = message.includes("恢复：") ? "" : "恢复：重启客户端。";
  return `${label}失败：${message}${ending}${recovery}`;
}

export function selectedPetText(selection: Selection | null, menu: HTMLElement, bubble: HTMLElement): string {
  if (!selection || selection.isCollapsed || !selection.anchorNode || !selection.focusNode) return "";
  const inside = (node: Node) => menu.contains(node) || bubble.contains(node);
  return inside(selection.anchorNode) && inside(selection.focusNode) ? selection.toString() : "";
}

export async function copyPetText(text: string): Promise<void> {
  if (!text) return;
  try { if (navigator.clipboard) { await navigator.clipboard.writeText(text); return; } } catch { /* 旧 WebView 使用选择复制。 */ }
  const area = document.createElement("textarea");
  const focused = document.activeElement as HTMLElement | null;
  const selection = window.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange()) : [];
  area.value = text;
  Object.assign(area.style, { position: "fixed", opacity: "0", left: "0", top: "0" });
  document.body.appendChild(area);
  try {
    area.select();
    if (!document.execCommand("copy")) throw new Error("复制失败，请选中文字后按 Ctrl+C。");
  } finally {
    area.remove(); focused?.focus();
    if (selection) { selection.removeAllRanges(); ranges.forEach((range) => selection.addRange(range)); }
  }
}

/** DOM 处理窗口内点击，Rust 处理穿透区域与窗口外的点击。 */
export function installPetMenu(options: {
  menu: HTMLElement; bubble: HTMLElement; close: () => void; run: (action: string) => void;
  lockSelection: (locked: boolean) => void; errorText: () => string;
}) {
  const { menu, bubble } = options;
  const copyButton = menu.querySelector<HTMLButtonElement>('[data-action="copy"]')!;
  const hint = menu.querySelector<HTMLElement>("[data-menu-hint]")!;
  let selecting = false;
  let textOnPress = "";
  function updateCopy() {
    const text = selectedPetText(window.getSelection(), menu, bubble);
    copyButton.disabled = !text && !textOnPress && !options.errorText();
    copyButton.textContent = text || !options.errorText() ? "复制选中文字" : "复制错误信息";
  }
  function unlock() { if (selecting) { selecting = false; options.lockSelection(false); } }
  function close() { unlock(); options.close(); }
  function pointerDown(event: PointerEvent) {
    if (menu.classList.contains("hidden")) return;
    const target = event.target as Element | null;
    if (!target || !menu.contains(target)) { close(); return; }
    if (event.button !== 0) return;
    if (target.closest('[data-action="copy"]')) {
      textOnPress = selectedPetText(window.getSelection(), menu, bubble) || options.errorText();
      event.preventDefault(); // 保留复制按钮按下前选中的文字。
      return;
    }
    selecting = true; options.lockSelection(true);
  }
  function click(event: MouseEvent) {
    const button = (event.target as Element | null)?.closest<HTMLButtonElement>("button[data-action]");
    if (!button || button.disabled) return;
    if (button.dataset.action === "copy") {
      const text = textOnPress || selectedPetText(window.getSelection(), menu, bubble) || options.errorText();
      textOnPress = "";
      updateCopy();
      void copyPetText(text).then(() => { hint.textContent = "已复制"; }).catch((err) => { hint.textContent = err instanceof Error ? err.message : String(err); });
      return;
    }
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed && selection.anchorNode && selection.focusNode
      && menu.contains(selection.anchorNode) && menu.contains(selection.focusNode)) {
      event.preventDefault(); return; // 拖选菜单文字不执行其动作。
    }
    options.run(button.dataset.action!);
  }
  function keyDown(event: KeyboardEvent) { if (event.key === "Escape") close(); }
  window.addEventListener("pointerdown", pointerDown, true);
  window.addEventListener("pointerup", unlock);
  window.addEventListener("pointercancel", unlock);
  window.addEventListener("blur", close);
  window.addEventListener("keydown", keyDown);
  menu.addEventListener("click", click);
  document.addEventListener("selectionchange", updateCopy);
  const nativeListeners = isTauri()
    ? Promise.all([listen("dismiss-pet-menu", close), listen("pet-pointer-released", unlock)])
    : Promise.resolve([]);
  return {
    refresh: updateCopy,
    dispose() {
      unlock(); window.removeEventListener("pointerdown", pointerDown, true);
      window.removeEventListener("pointerup", unlock); window.removeEventListener("pointercancel", unlock);
      window.removeEventListener("blur", close); window.removeEventListener("keydown", keyDown);
      menu.removeEventListener("click", click); document.removeEventListener("selectionchange", updateCopy);
      void nativeListeners.then((listeners) => listeners.forEach((unlisten) => unlisten()));
    },
  };
}

export async function syncPetMenuRegion(menu: HTMLElement): Promise<void> {
  if (!isTauri()) return;
  const bounds = menu.getBoundingClientRect();
  const rect = menu.classList.contains("hidden") ? null : { x: bounds.x, y: bounds.y, w: bounds.width, h: bounds.height };
  await invoke("set_pet_menu_region", { rect });
}
