import type {
  Artifact,
  ConfigOption,
  Device,
  RuntimeStatus,
  Sequence,
  Task,
  TaskGroupCreated,
  Trajectory,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function apiRequest<T>(
  path: string,
  init?: RequestInit,
  expectedStatus?: number,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    let code: string | undefined
    try {
      const error = (await response.json()) as {
        detail?: string | { code?: string; message?: string }
      }
      if (typeof error.detail === 'string') message = error.detail
      else if (error.detail) {
        if (typeof error.detail.message === 'string') message = error.detail.message
        if (typeof error.detail.code === 'string') code = error.detail.code
      }
    } catch {
      // Keep the status-based message when the server did not return JSON.
    }
    throw new ApiError(message, response.status, code)
  }
  if (expectedStatus !== undefined && response.status !== expectedStatus) {
    throw new Error(`任务创建需要返回 ${expectedStatus}，实际为 ${response.status}`)
  }
  return (await response.json()) as T
}

export const taskApi = {
  detail: (taskId: string, signal?: AbortSignal) =>
    apiRequest<Task>(`/api/v1/tasks/${encodeURIComponent(taskId)}`, { signal }),
  trajectory: (taskId: string, signal?: AbortSignal) =>
    apiRequest<Trajectory>(`/api/v1/tasks/${encodeURIComponent(taskId)}/trajectory`, { signal }),
  artifacts: (taskId: string, signal?: AbortSignal) =>
    apiRequest<{ items: Artifact[] }>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/artifacts`,
      { signal },
    ),
  list: (query: string, signal?: AbortSignal) =>
    apiRequest<{ items: Task[] }>(`/api/v1/tasks?${query}`, { signal }),
  runtime: (signal?: AbortSignal) =>
    apiRequest<RuntimeStatus>('/api/v1/runtime', { signal }),
}

export const catalogApi = {
  devices: (signal?: AbortSignal) =>
    apiRequest<{ items: Device[] }>('/api/v1/devices', { signal }),
  configs: (signal?: AbortSignal) =>
    apiRequest<{ items: ConfigOption[] }>('/api/v1/configs', { signal }),
  sequences: (device: string, signal?: AbortSignal) =>
    apiRequest<{ items: Sequence[] }>(
      `/api/v1/devices/${encodeURIComponent(device)}/sequences`,
      { signal },
    ),
  createTaskGroup: (payload: {
    device: string
    sequences: string[]
    config_id: string
    options: { cam_workers: number; keep_images: boolean }
  }, signal?: AbortSignal) =>
    apiRequest<TaskGroupCreated>(
      '/api/v1/task-groups',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal,
      },
      201,
    ),
}
