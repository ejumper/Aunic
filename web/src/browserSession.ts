const INSTANCE_ID_KEY = "aunic:browserInstanceId";
const CURRENT_PAGE_ID = createBrowserPageId();

export function createBrowserPageId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function getOrCreateBrowserInstanceId(): string {
  const existing = window.sessionStorage.getItem(INSTANCE_ID_KEY);
  if (existing) {
    return existing;
  }
  const next = createBrowserPageId();
  window.sessionStorage.setItem(INSTANCE_ID_KEY, next);
  return next;
}

export function rotateBrowserInstanceId(): string {
  const next = createBrowserPageId();
  window.sessionStorage.setItem(INSTANCE_ID_KEY, next);
  return next;
}

export function getCurrentBrowserPageId(): string {
  return CURRENT_PAGE_ID;
}

export function getLastOpenFileStorageKey(instanceId: string): string {
  return `aunic:lastOpenFile:${instanceId}`;
}
