export function homeAssistantPayload(baseUrl, token = '') {
  const payload = {base_url: baseUrl.trim()};
  if (token.trim()) payload.token = token.trim();
  return payload;
}

export function homeAssistantStatus(settings) {
  if (!settings) return {label: '尚未读取', connected: false};
  if (settings.connected === true) {
    const count = Array.isArray(settings.devices) ? settings.devices.length : 0;
    return {label: `已连接 · ${count} 台打印机`, connected: true};
  }
  return {label: settings.configured ? '连接待检查' : '未连接', connected: false};
}

export function clearPrinterSelection(storage) {
  try { (storage || globalThis.localStorage)?.removeItem('printlab-selected-printer'); } catch {}
}
