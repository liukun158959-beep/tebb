export function openProjectRuntime(environment = "", operation = "") {
  window.dispatchEvent(new CustomEvent("desk-open-preview", { detail: { kind: "project-runtime", environment, operation } }));
}
