import type { Asset, Draft, Model, SystemStatus, Task, TaskEstimate, TaskInput } from './types';

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number, public detail?: unknown) { super(message); }
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, { ...init, signal: AbortSignal.timeout(25000) });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.error?.code || 'HTTP_ERROR', body.error?.message || `请求失败（${response.status}）`, response.status, body.error?.detail);
  return body as T;
}
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const api = {
  models: () => request<{ items: Model[] }>('/models'),
  system: () => request<SystemStatus>('/system/status'),
  assets: () => request<{ items: Asset[] }>('/assets?limit=200'),
  tasks: () => request<{ items: Task[] }>('/tasks?limit=200'),
  task: (id: string) => request<Task>(`/tasks/${encodeURIComponent(id)}`),
  estimateTask: (body: TaskInput) => request<TaskEstimate>('/tasks/estimate', json('POST', body)),
  createTask: (body: TaskInput) => request<{ task_id: string }>('/tasks', json('POST', body)),
  createBatch: (name: string, items: TaskInput[]) => request<{ batch_id: string; task_ids: string[] }>('/batches', json('POST', { name, items })),
  cancelTask: (id: string) => request<{ result: string; message?: string; cloud_canceled?: boolean }>(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  usage: () => request<{ budget_cny: number; spent_today_cny: number }>('/usage'),
  retryTask: (id: string) => request<{ task_id: string; resumed?: boolean }>(`/tasks/${encodeURIComponent(id)}/retry`, json('POST', {})),
  upload: (file: File) => { const body = new FormData(); body.append('file', file); return request<{ asset_id: string }>('/assets', { method: 'POST', body }); },
  draft: (id: string) => request<Draft>(`/drafts/${encodeURIComponent(id)}`),
  drafts: () => request<{ items: Draft[] }>('/drafts?limit=20'),
  createDraft: (name: string, content: Draft['content']) => request<{ draft_id: string; version: number }>('/drafts', json('POST', { name, content })),
  saveDraft: (id: string, version: number, content: Draft['content']) => request<{ draft_id: string; version: number }>(`/drafts/${encodeURIComponent(id)}`, json('PATCH', { content, base_version: version })),
};
export const mediaUrl = (id: string) => `/api/assets/${encodeURIComponent(id)}/file`;
