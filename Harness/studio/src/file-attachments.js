import {agentUploadFile, agentUploadImage} from './agent-client.js';
import {safeAttachmentUrl, uniqueReferenceFiles, validateReferenceFiles} from './image-attachments.js';

export const MAX_PROJECT_FILE_BYTES = 100 * 1024 * 1024;
const imageTypes = new Set(['image/png', 'image/jpeg', 'image/webp']);

export function attachmentKind(file) {
  return imageTypes.has(file.type) ? 'image' : 'file';
}

export function validateProjectAttachments(existing, files, {imagesOnly = false} = {}) {
  const incoming = uniqueReferenceFiles(existing, files);
  for (const file of incoming) {
    if (imagesOnly || attachmentKind(file) === 'image') validateReferenceFiles([file]);
    else {
      if (!Number.isFinite(file.size) || file.size <= 0) throw Error('这个附件是空的，请重新选择。');
      if (file.size > MAX_PROJECT_FILE_BYTES) throw Error('单个附件不能超过 100 MB。');
    }
  }
  return incoming;
}

export function safeProjectFileUrl(value, projectId) {
  const prefix = `/api/projects/${encodeURIComponent(projectId)}/files/`;
  return typeof value === 'string' && value.startsWith(prefix) && /^[a-f0-9]{32}$/.test(value.slice(prefix.length)) ? value : null;
}

export function attachmentSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return '大小未知';
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function attachmentProjectName(items) {
  return (items[0]?.file.name || '').replace(/\.[^.]+$/, '').trim().slice(0, 120) || '附件项目';
}

export function defaultAttachmentMessage(items) {
  if (!items.length) return '';
  return items.some(item => attachmentKind(item.file) === 'file')
    ? '请先查看这些附件，整理其中与作品有关的模型、需求和尺寸，说明还需要补充什么，再继续当前项目。'
    : '请结合这些参考图片，先整理这件作品的外观、用途和需要补充的尺寸，再继续当前项目。';
}

export async function uploadProjectAttachments(projectId, items, {signal, uploadFile = agentUploadFile, uploadImage = agentUploadImage, onUploaded = () => {}, onProgress = () => {}} = {}) {
  const result = {images: [], files: []};
  for (const [index, item] of items.entries()) {
    if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError');
    const kind = attachmentKind(item.file), name = item.file.name || (kind === 'image' ? '参考图片' : '附件');
    const progress = {localId: item.localId, index: index + 1, total: items.length, name, kind};
    if (!item.uploaded) onProgress({...progress, status: 'uploading'});
    try {
      const uploaded = item.uploaded || await (kind === 'image' ? uploadImage : uploadFile)(projectId, item.file, {signal});
      if (signal?.aborted) throw new DOMException('上传已取消', 'AbortError');
      const safeUrl = kind === 'image' ? safeAttachmentUrl : safeProjectFileUrl;
      if (!/^[a-f0-9]{32}$/.test(uploaded?.id || '') || !safeUrl(uploaded.url, projectId)) throw Error('附件上传结果不完整，请重试。');
      result[kind === 'image' ? 'images' : 'files'].push(uploaded);
      onUploaded(item.localId, uploaded);
      onProgress({...progress, status: 'uploaded'});
    } catch (problem) {
      if (problem.name === 'AbortError') throw problem;
      onProgress({...progress, status: 'failed'});
      const error = new Error(`“${name}”上传失败：${problem.message || '请重试。'}`, {cause: problem});
      error.status = problem.status;
      error.fileName = name;
      throw error;
    }
  }
  return result;
}
