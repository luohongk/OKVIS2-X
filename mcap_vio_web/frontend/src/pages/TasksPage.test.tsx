import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskLog } from '../components/TaskLog'
import type { Task } from '../types'
import { TasksPage } from './TasksPage'

const tasks = {
  items: [
    {
      id: 'task-running',
      group_id: 'group-1',
      sequence: '20260805-113804',
      status: 'extracting',
      created_at: '2026-08-06T12:00:00Z',
      started_at: '2026-08-06T12:01:00Z',
      finished_at: null,
      exit_code: null,
      runner_status: null,
      error_summary: null,
      device: '0805',
      config_rel: 'EGO2/okvis.yaml',
      cam_workers: 4,
      keep_images: 0,
    },
    {
      id: 'task-success',
      group_id: 'group-2',
      sequence: '20260805-114500',
      status: 'succeeded',
      created_at: '2026-08-06T11:00:00Z',
      started_at: '2026-08-06T11:01:00Z',
      finished_at: '2026-08-06T11:08:00Z',
      exit_code: 0,
      runner_status: 'SUCCESS',
      error_summary: null,
      device: '0805',
      config_rel: 'EGO2/okvis.yaml',
      cam_workers: 4,
      keep_images: 0,
    },
    {
      id: 'task-failed',
      group_id: 'group-3',
      sequence: 'failed-sequence',
      status: 'failed',
      created_at: '2026-08-06T10:00:00Z',
      started_at: '2026-08-06T10:01:00Z',
      finished_at: '2026-08-06T10:02:00Z',
      exit_code: 7,
      runner_status: 'FAIL_OKVIS',
      error_summary: 'optimization failed',
      device: '0804',
      config_rel: '0804/okvis.yaml',
      cam_workers: 2,
      keep_images: 0,
    },
  ],
}

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  closed = false
  listeners = new Map<string, Array<(event: MessageEvent) => void>>()
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListener) {
    const listeners = this.listeners.get(type) ?? []
    listeners.push(listener as (event: MessageEvent) => void)
    this.listeners.set(type, listeners)
  }

  close() {
    this.closed = true
  }

  emit(type: string, payload: unknown) {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) })
    for (const listener of this.listeners.get(type) ?? []) listener(event)
  }
}

function installFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url.startsWith('/api/v1/tasks')) return response(tasks)
    if (url === '/api/v1/runtime') {
      return response({ max_concurrency: 2, running: 1, queued: 3, accepting_tasks: true })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

let fetchMock: ReturnType<typeof installFetch>

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  fetchMock = installFetch()
})

afterEach(() => {
  fetchMock.mockRestore()
  vi.unstubAllGlobals()
})

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <TasksPage />
    </MemoryRouter>,
  )
}

describe('TasksPage', () => {
  it('renders tasks, statuses, runtime summary, result link, and failure details', async () => {
    renderPage()

    const running = await screen.findByRole('button', { name: /20260805-113804/ })
    expect(within(running).getByText('抽取中')).toBeInTheDocument()
    const runtime = screen.getByRole('region', { name: '队列运行摘要' })
    expect(runtime).toHaveTextContent('2 路并发')
    expect(runtime).toHaveTextContent('1 运行中')
    expect(runtime).toHaveTextContent('3 排队')
    expect(screen.getByRole('link', { name: '查看 20260805-114500 结果' })).toHaveAttribute(
      'href',
      '/results/task-success',
    )
    expect(screen.getByText('退出码 7')).toBeInTheDocument()
    expect(screen.getByText('optimization failed')).toBeInTheDocument()
  })

  it('writes combined filters into the task query', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByText('20260805-113804')

    await user.selectOptions(screen.getByRole('combobox', { name: '任务状态' }), 'failed')
    await user.type(screen.getByRole('textbox', { name: '设备过滤' }), '0804')
    await user.type(screen.getByRole('textbox', { name: '序列过滤' }), 'failed-sequence')
    await user.type(screen.getByRole('textbox', { name: '配置过滤' }), '0804/okvis.yaml')
    await user.click(screen.getByRole('button', { name: '应用过滤' }))

    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/tasks?status=failed&device=0804&sequence=failed-sequence&config_id=0804%2Fokvis.yaml&limit=100&offset=0',
      expect.anything(),
    )
  })

  it('opens one event stream and applies snapshot, log, status, and terminal events', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /20260805-113804/ }))

    expect(FakeEventSource.instances).toHaveLength(1)
    const stream = FakeEventSource.instances[0]
    expect(stream.url).toBe('/api/v1/tasks/task-running/events')
    act(() => {
      stream.emit('snapshot', { task: { ...tasks.items[0], status: 'extracting' }, offset: 0 })
      stream.emit('log', { offset: 18, text: '[EXTRACT] start\n' })
      stream.emit('status', { status: 'vio' })
    })

    const console = screen.getByRole('region', { name: '20260805-113804 实时日志' })
    expect(await within(console).findByText('[EXTRACT] start')).toBeInTheDocument()
    expect(within(console).getByText('VIO 运行中')).toBeInTheDocument()
    act(() => stream.emit('terminal', { status: 'succeeded', offset: 18 }))
    await waitFor(() => expect(stream.closed).toBe(true))
    expect(within(console).getByText('运行成功')).toBeInTheDocument()
  })

  it('closes the previous stream when switching task and on unmount', async () => {
    const user = userEvent.setup()
    const rendered = renderPage()
    await user.click(await screen.findByRole('button', { name: /20260805-113804/ }))
    const first = FakeEventSource.instances[0]
    const firstButton = screen.getByRole('button', { name: /20260805-113804/ })
    expect(firstButton).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: /failed-sequence/ }))
    expect(first.closed).toBe(true)
    expect(firstButton).toHaveAttribute('aria-pressed', 'false')
    expect(FakeEventSource.instances).toHaveLength(2)
    const second = FakeEventSource.instances[1]
    act(() => first.emit('log', { offset: 3, text: 'stale log' }))
    expect(screen.queryByText('stale log')).not.toBeInTheDocument()
    rendered.unmount()
    expect(second.closed).toBe(true)
  })

  it('keeps one stream and preserves bounded logs when task status props refresh', () => {
    const task = tasks.items[0] as Task
    const rendered = render(<TaskLog task={task} />)
    const stream = FakeEventSource.instances[0]
    act(() => stream.emit('log', { offset: 12, text: 'first line\n' }))

    rendered.rerender(<TaskLog task={{ ...task, status: 'vio' }} />)

    expect(FakeEventSource.instances).toHaveLength(1)
    expect(screen.getByRole('log')).toHaveTextContent('first line')
    act(() => stream.emit('log', { offset: 300_000, text: 'x'.repeat(210_000) }))
    expect(screen.getByRole('log').textContent?.length).toBeLessThanOrEqual(200_100)
    expect(screen.getByText(/较早日志已截断/)).toBeInTheDocument()
  })

  it('does not regress an SSE terminal status when stale polling props arrive', () => {
    const task = tasks.items[0] as Task
    const rendered = render(<TaskLog task={task} />)
    const stream = FakeEventSource.instances[0]
    act(() => stream.emit('terminal', { status: 'succeeded', offset: 0 }))
    expect(screen.getByText('运行成功')).toBeInTheDocument()

    rendered.rerender(<TaskLog task={{ ...task, status: 'vio' }} />)

    expect(screen.getByText('运行成功')).toBeInTheDocument()
    expect(screen.queryByText('抽取中')).not.toBeInTheDocument()
  })

  it('clears the connection warning after EventSource reconnects', () => {
    render(<TaskLog task={tasks.items[0] as Task} />)
    const stream = FakeEventSource.instances[0]
    act(() => stream.onerror?.())
    expect(screen.getByText(/日志连接暂时中断/)).toBeInTheDocument()
    act(() => stream.onopen?.())
    expect(screen.queryByText(/日志连接暂时中断/)).not.toBeInTheDocument()
  })

  it('shows task loading failures', async () => {
    fetchMock.mockImplementation((input) => {
      if (String(input) === '/api/v1/runtime') {
        return response({ max_concurrency: 2, running: 0, queued: 0, accepting_tasks: true })
      }
      return response({ detail: '任务数据库不可用' }, 503)
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('任务数据库不可用')
  })
})
