import {agentUploadImage} from './agent-client.js';

export const MAX_REFERENCE_IMAGE_BYTES = 12 * 1024 * 1024;
const supportedTypes = new Set(['image/png', 'image/jpeg', 'image/webp']);

export function validateReferenceFiles(files) {
  const incoming = Array.from(files || []);
  for (const file of incoming) {
    if (!supportedTypes.has(file.type)) throw Error('请使用 PNG、JPG 或 WebP 图片。');
    if (!Number.isFinite(file.size) || file.size <= 0) throw Error('这张图片是空的，请重新选择。');
    if (file.size > MAX_REFERENCE_IMAGE_BYTES) throw Error('单张图片不能超过 12 MB。');
  }
  return incoming;
}

export function referenceFileKey(file) {
  return JSON.stringify([file.name, file.size, file.lastModified]);
}

export function uniqueReferenceFiles(existing, incoming) {
  const known = new Set(existing.map(item => referenceFileKey(item.file)));
  return Array.from(incoming || []).filter(file => {
    const key = referenceFileKey(file);
    if (known.has(key)) return false;
    known.add(key);
    return true;
  });
}

export function safeAttachmentUrl(value, projectId) {
  const prefix = `/api/projects/${encodeURIComponent(projectId)}/images/`;
  return typeof value === 'string' && value.startsWith(prefix) && /^[a-f0-9]{32}$/.test(value.slice(prefix.length)) ? value : null;
}

export async function uploadReferenceImages(projectId, items, {signal, upload = agentUploadImage, onUploaded = () => {}, onProgress = () => {}} = {}) {
  const uploaded = [];
  for (const [index, item] of items.entries()) {
    if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError');
    const progress = {localId: item.localId, index: index + 1, total: items.length, name: item.file.name || '参考图片'};
    if (!item.uploaded) onProgress({...progress, status: 'uploading'});
    try {
      const image = item.uploaded || await upload(projectId, item.file, {signal});
      if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError');
      if (!/^[a-f0-9]{32}$/.test(image?.id || '') || !safeAttachmentUrl(image.url, projectId)) throw Error('图片上传结果不完整，请重试。');
      uploaded.push(image);
      onUploaded(item.localId, image);
      onProgress({...progress, status: 'uploaded'});
    } catch (problem) {
      if (problem.name === 'AbortError') throw problem;
      onProgress({...progress, status: 'failed'});
      const error = new Error(`“${progress.name}”上传失败：${problem.message || '请重试。'}`, {cause: problem});
      error.status = problem.status;
      error.fileName = progress.name;
      throw error;
    }
  }
  return uploaded;
}
