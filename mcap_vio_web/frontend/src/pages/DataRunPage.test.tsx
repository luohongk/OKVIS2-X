import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataRunPage } from './DataRunPage'

const devices = {
  items: [
    { name: '0805', valid_sequence_count: 2 },
    { name: '0804', valid_sequence_count: 1 },
  ],
}
const sequences = {
  items: [
    {
      name: '20260805-113804',
      complete: true,
      duration_sec: 247,
      platform_name: 'ego2',
      info_warning: null,
      sensor_mcap_counts: { cam0: 1, cam1: 1, cam2: 1, cam3: 1, imu: 1 },
    },
    {
      name: '20260805-114500',
      complete: false,
      duration_sec: null,
      platform_name: null,
      info_warning: 'missing .info file',
      sensor_mcap_counts: { cam0: 2, cam1: 2, cam2: 2, cam3: 2, imu: 1 },
    },
  ],
}
const configs = {
  items: [
    { id: 'EGO2/okvis2_eucm.yaml', label: 'EGO2 / okvis2_eucm.yaml' },
    { id: '0805/okvis.yaml', label: '0805 / okvis.yaml' },
  ],
}

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

function installFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    const url = String(input)
    if (url === '/api/v1/devices') return response(devices)
    if (url === '/api/v1/configs') return response(configs)
    if (url === '/api/v1/devices/0805/sequences') return response(sequences)
    if (url === '/api/v1/devices/0804/sequences') {
      return response({ items: [{ ...sequences.items[0], name: '0804-sequence' }] })
    }
    if (url === '/api/v1/task-groups' && init?.method === 'POST') {
      return response({ group_id: 'group-1', task_ids: ['task-1'] }, 201)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={['/run']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/run" element={<DataRunPage />} />
        <Route path="/tasks" element={<h1>任务已提交</h1>} />
      </Routes>
    </MemoryRouter>,
  )
}

let fetchMock: ReturnType<typeof installFetch>

beforeEach(() => {
  fetchMock = installFetch()
})
afterEach(() => fetchMock.mockRestore())

describe('DataRunPage', () => {
  it('loads devices, then displays sequence metadata and completion', async () => {
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('button', { name: /0805.*2 组序列/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /0805.*2 组序列/ }))

    const firstRow = await screen.findByRole('row', { name: /20260805-113804/ })
    expect(within(firstRow).getByText('247 秒')).toBeInTheDocument()
    expect(within(firstRow).getByText('EGO2')).toBeInTheDocument()
    expect(within(firstRow).getByText('完整')).toBeInTheDocument()
    const secondRow = screen.getByRole('row', { name: /20260805-114500/ })
    expect(within(secondRow).getByText('信息缺失')).toBeInTheDocument()
  })

  it('ignores stale sequence responses when devices switch quickly', async () => {
    let resolve0805!: (value: Response) => void
    let resolve0804!: (value: Response) => void
    const request0805 = new Promise<Response>((resolve) => { resolve0805 = resolve })
    const request0804 = new Promise<Response>((resolve) => { resolve0804 = resolve })
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/devices') return response(devices)
      if (url === '/api/v1/configs') return response(configs)
      if (url === '/api/v1/devices/0805/sequences') return request0805
      if (url === '/api/v1/devices/0804/sequences') return request0804
      throw new Error(`Unexpected request: ${url}`)
    })
    const user = userEvent.setup()
    renderPage()

    const first = await screen.findByRole('button', { name: /0805.*2 组序列/ })
    const second = screen.getByRole('button', { name: /0804.*1 组序列/ })
    await user.click(first)
    await user.click(second)
    resolve0804(await response({ items: [{ ...sequences.items[0], name: '0804-sequence' }] }))
    expect(await screen.findByText('0804-sequence')).toBeInTheDocument()
    resolve0805(await response(sequences))

    await waitFor(() => expect(screen.queryByText('20260805-113804')).not.toBeInTheDocument())
    expect(second).toHaveAttribute('aria-pressed', 'true')
    expect(first).toHaveAttribute('aria-pressed', 'false')
  })

  it('switches device and reloads its sequences', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /0804.*1 组序列/ }))

    expect(await screen.findByText('0804-sequence')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/v1/devices/0804/sequences', expect.anything())
  })

  it('searches, selects visible rows, and supports individual selection', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /0805.*2 组序列/ }))
    await screen.findByText('20260805-113804')

    await user.type(screen.getByRole('searchbox', { name: '搜索序列' }), '1145')
    expect(screen.queryByText('20260805-113804')).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: '全选当前结果' }))
    expect(screen.getByRole('checkbox', { name: '选择 20260805-114500' })).toBeChecked()

    await user.clear(screen.getByRole('searchbox', { name: '搜索序列' }))
    await user.click(screen.getByRole('checkbox', { name: '选择 20260805-113804' }))
    expect(screen.getByText('已选择 2 组')).toBeInTheDocument()
  })

  it('disables submission until sequence and config are selected', async () => {
    const user = userEvent.setup()
    renderPage()
    const submit = screen.getByRole('button', { name: '提交位姿任务' })
    expect(submit).toBeDisabled()

    await user.click(await screen.findByRole('button', { name: /0805.*2 组序列/ }))
    await user.click(await screen.findByRole('checkbox', { name: '选择 20260805-113804' }))
    expect(submit).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: '配置文件' }), 'EGO2/okvis2_eucm.yaml')
    expect(submit).toBeEnabled()

    const workers = screen.getByRole('spinbutton', { name: '相机解码线程' })
    expect(workers).toHaveAttribute('min', '1')
    expect(workers).toHaveAttribute('max', '4')
  })

  it('submits the exact request and navigates to task center', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /0805.*2 组序列/ }))
    await user.click(await screen.findByRole('checkbox', { name: '选择 20260805-113804' }))
    await user.selectOptions(screen.getByRole('combobox', { name: '配置文件' }), 'EGO2/okvis2_eucm.yaml')
    await user.clear(screen.getByRole('spinbutton', { name: '相机解码线程' }))
    await user.type(screen.getByRole('spinbutton', { name: '相机解码线程' }), '3')
    await user.click(screen.getByRole('checkbox', { name: '保留抽取图片' }))
    await user.click(screen.getByRole('button', { name: '提交位姿任务' }))

    expect(await screen.findByRole('heading', { name: '任务已提交' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/task-groups',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          device: '0805',
          sequences: ['20260805-113804'],
          config_id: 'EGO2/okvis2_eucm.yaml',
          options: { cam_workers: 3, keep_images: true },
        }),
      }),
    )
  })

  it('does not navigate when task creation returns a non-201 success status', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/devices') return response(devices)
      if (url === '/api/v1/configs') return response(configs)
      if (url === '/api/v1/devices/0805/sequences') return response(sequences)
      if (url === '/api/v1/task-groups') return response({ group_id: 'group', task_ids: [] }, 202)
      throw new Error(`Unexpected request: ${url}`)
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /0805.*2 组序列/ }))
    await user.click(await screen.findByRole('checkbox', { name: '选择 20260805-113804' }))
    await user.selectOptions(screen.getByRole('combobox', { name: '配置文件' }), 'EGO2/okvis2_eucm.yaml')
    await user.click(screen.getByRole('button', { name: '提交位姿任务' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('201')
    expect(screen.queryByRole('heading', { name: '任务已提交' })).not.toBeInTheDocument()
  })

  it('shows API errors and preserves the current selection', async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/devices') return response(devices)
      if (url === '/api/v1/configs') return response(configs)
      if (url === '/api/v1/devices/0805/sequences') return response(sequences)
      if (url === '/api/v1/task-groups') return response({ detail: '磁盘不可写' }, 503)
      throw new Error(`Unexpected request: ${url}`)
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: /0805.*2 组序列/ }))
    await user.click(await screen.findByRole('checkbox', { name: '选择 20260805-113804' }))
    await user.selectOptions(screen.getByRole('combobox', { name: '配置文件' }), 'EGO2/okvis2_eucm.yaml')
    await user.click(screen.getByRole('button', { name: '提交位姿任务' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('磁盘不可写')
    expect(screen.getByRole('checkbox', { name: '选择 20260805-113804' })).toBeChecked()
    expect(screen.getByRole('combobox', { name: '配置文件' })).toHaveValue('EGO2/okvis2_eucm.yaml')
  })

  it('surfaces catalog network failures', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('network down'))
    renderPage()

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('network down'))
  })
})
