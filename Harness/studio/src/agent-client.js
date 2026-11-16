const terminalStates = new Set(['succeeded', 'failed', 'interrupted', 'cancelled']);

export function agentPath(projectId, runId) {
  const base = `/api/projects/${encodeURIComponent(projectId)}/agent`;
  return runId ? `${base}/runs/${encodeURIComponent(runId)}` : base;
}

export function isActiveRun(run) {
  return Boolean(run?.id) && !terminalStates.has(run.status);
}

export function mergeAgentEvents(previous = [], incoming = []) {
  const bySequence = new Map();
  for (const event of [...previous, ...incoming]) {
    if (Number.isInteger(event?.seq) && event.seq >= 0) bySequence.set(event.seq, event);
  }
  return [...bySequence.values()].sort((a, b) => a.seq - b.seq);
}

export function mergeAgentRun(previous, incoming) {
  if (!incoming || !previous) return incoming || previous;
  if (incoming.id !== previous.id || (previous.project_id && incoming.project_id && incoming.project_id !== previous.project_id)) return previous;
  if (Number.isFinite(previous.last_seq) && Number.isFinite(incoming.last_seq) && incoming.last_seq < previous.last_seq) return previous;
  return {...previous, ...incoming};
}

export function assistantPreview(events = [], result) {
  if (typeof result === 'string' && result.trim()) return result;
  const items = new Map();
  for (const event of events) {
    if (typeof event.text !== 'string') continue;
    const id = event.item_id || 'assistant';
    if (event.type === 'assistant_delta') items.set(id, (items.get(id) || '') + event.text);
    else if (event.type === 'assistant_message') items.set(id, event.text);
  }
  return [...items.values()].filter(Boolean).join('\n\n');
}

export function createAgentRequestId(cryptoSource = globalThis.crypto) {
  if (cryptoSource?.randomUUID) return cryptoSource.randomUUID();
  if (!cryptoSource?.getRandomValues) throw Error('浏览器暂不支持安全提交，请换用较新的浏览器。');
  const bytes = cryptoSource.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export async function agentRead(url, {signal, fetcher = globalThis.fetch} = {}) {
  let response;
  try { response = await fetcher(url, {cache: 'no-store', credentials: 'same-origin', signal}); }
  catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error('暂时无法连接项目服务，请稍后核对任务状态。');
  }
  return readResponse(response);
}

async function readResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : typeof data.message === 'string' ? data.message : `请求未完成（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function agentWrite(url, body = {}, {signal, fetcher = globalThis.fetch, method = 'POST'} = {}) {
  if (!['POST', 'DELETE'].includes(method)) throw Error('不支持此项目操作。');
  const session = await agentRead('/api/prepared/session', {signal, fetcher});
  if (typeof session.csrf_token !== 'string' || !session.csrf_token) throw Error('工作台会话暂不可用，请刷新后再试。');
  let response;
  try {
    response = await fetcher(url, {
      method, credentials: 'same-origin', signal,
      headers: {'Content-Type': 'application/json', 'X-PrintLab-CSRF': session.csrf_token},
      body: JSON.stringify(body),
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error('提交结果待核对，请先刷新任务状态。');
  }
  return readResponse(response);
}

export async function agentUploadImage(projectId, file, {signal, fetcher = globalThis.fetch} = {}) {
  return uploadProjectFile(projectId, file, 'images', 'reference.png', '图片上传未完成，请重试。', {signal, fetcher});
}

export async function agentUploadFile(projectId, file, {signal, fetcher = globalThis.fetch} = {}) {
  return uploadProjectFile(projectId, file, 'files', 'attachment', '附件上传未完成，请重试。', {signal, fetcher});
}

async function uploadProjectFile(projectId, file, collection, fallbackName, failureMessage, {signal, fetcher}) {
  const session = await agentRead('/api/prepared/session', {signal, fetcher});
  if (typeof session.csrf_token !== 'string' || !session.csrf_token) throw Error('工作台会话暂不可用，请刷新后再试。');
  let response;
  try {
    response = await fetcher(`/api/projects/${encodeURIComponent(projectId)}/${collection}?filename=${encodeURIComponent(file.name || fallbackName)}`, {
      method: 'POST', credentials: 'same-origin', signal,
      headers: {'Content-Type': file.type || 'application/octet-stream', 'X-PrintLab-CSRF': session.csrf_token},
      body: file,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw Error(failureMessage);
  }
  return readResponse(response);
}
