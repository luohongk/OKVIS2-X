import { act, render, screen, waitFor } from '@testing-library/react'
import {
  createMemoryRouter,
  MemoryRouter,
  Route,
  RouterProvider,
  Routes,
} from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ResultPage } from './ResultPage'

const succeededTask = {
  id: 'task-42',
  group_id: 'group-1',
  sequence: '20260805-113804',
  status: 'succeeded',
  created_at: '2026-08-06T12:00:00Z',
  started_at: '2026-08-06T12:01:00Z',
  finished_at: '2026-08-06T12:08:00Z',
  exit_code: 0,
  runner_status: 'SUCCESS',
  error_summary: null,
  device: '0805',
  config_rel: 'EGO2/okvis.yaml',
  config_sha256: 'abc',
  cam_workers: 4,
  keep_images: 0,
}
const trajectory = {
  timestamp_start: 1000000000,
  timestamp_end: 3000000000,
  duration_sec: 2,
  point_count: 3,
  points: [[0, 0, 0], [3, 0, 0], [3, 4, 0]],
  bbox: { min: [0, 0, 0], max: [3, 4, 0] },
  path_length_m: 7,
  displacement_m: 5,
}
const artifacts = {
  items: [
    { path: 'runner.log', name: 'runner.log', size: 120 },
    {
      path: 'output/20260805-113804/results/okvis2-slam-calib-final_trajectory.csv',
      name: 'okvis2-slam-calib-final_trajectory.csv',
      size: 4096,
    },
  ],
}

vi.mock('react-plotly.js', () => ({
  default: ({ data }: { data: Array<{ x: number[]; y: number[]; z: number[] }> }) => (
    <div
      role="img"
      aria-label="3D 位姿轨迹"
      data-x={JSON.stringify(data[0].x)}
      data-y={JSON.stringify(data[0].y)}
      data-z={JSON.stringify(data[0].z)}
    />
  ),
}))

function response(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response)
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={['/results/task-42']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/results/:taskId" element={<ResultPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

let fetchMock: ReturnType<typeof installFetch>
function installFetch() {
  return vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/v1/tasks/task-42') return response(succeededTask)
    if (url === '/api/v1/tasks/task-42/trajectory') return response(trajectory)
    if (url === '/api/v1/tasks/task-42/artifacts') return response(artifacts)
    throw new Error(`Unexpected request: ${url}`)
  })
}

beforeEach(() => { fetchMock = installFetch() })
afterEach(() => fetchMock.mockRestore())

describe('ResultPage', () => {
  it('loads a successful task and maps trajectory points to Plotly axes', async () => {
    renderPage()

    const plot = await screen.findByRole('img', { name: '3D 位姿轨迹' })
    expect(plot).toHaveAttribute('data-x', '[0,3,3]')
    expect(plot).toHaveAttribute('data-y', '[0,0,4]')
    expect(plot).toHaveAttribute('data-z', '[0,0,0]')
    expect(screen.getByRole('heading', { name: '20260805-113804' })).toBeInTheDocument()
  })

  it('renders trajectory statistics and task metadata', async () => {
    renderPage()

    expect(await screen.findByText('3 点')).toBeInTheDocument()
    expect(screen.getByText('2.00 秒')).toBeInTheDocument()
    expect(screen.getByText('7.00 m')).toBeInTheDocument()
    expect(screen.getByText('5.00 m')).toBeInTheDocument()
    expect(screen.getByText('X 0.00 → 3.00')).toBeInTheDocument()
    expect(screen.getByText('0805')).toBeInTheDocument()
    expect(screen.getByText('EGO2/okvis.yaml')).toBeInTheDocument()
  })

  it('renders artifact download links through the backend API', async () => {
    renderPage()

    const trajectoryLink = await screen.findByRole('link', {
      name: '下载 okvis2-slam-calib-final_trajectory.csv',
    })
    expect(trajectoryLink).toHaveAttribute(
      'href',
      '/api/v1/tasks/task-42/artifacts/output/20260805-113804/results/okvis2-slam-calib-final_trajectory.csv',
    )
    expect(screen.getByText('4.0 KB')).toBeInTheDocument()
  })

  it('does not request trajectory for a non-successful task', async () => {
    fetchMock.mockImplementation((input) => {
      if (String(input) === '/api/v1/tasks/task-42') {
        return response({ ...succeededTask, status: 'vio', finished_at: null })
      }
      throw new Error(`Unexpected request: ${String(input)}`)
    })
    renderPage()

    expect(await screen.findByText('任务尚未完成')).toBeInTheDocument()
    expect(screen.getAllByText('VIO 运行中')).toHaveLength(2)
    expect(fetch).not.toHaveBeenCalledWith('/api/v1/tasks/task-42/trajectory', expect.anything())
    expect(screen.queryByRole('img', { name: '3D 位姿轨迹' })).not.toBeInTheDocument()
  })

  it('shows an incomplete artifact state when trajectory is missing', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/tasks/task-42') return response(succeededTask)
      if (url === '/api/v1/tasks/task-42/trajectory') {
        return response({ detail: { code: 'trajectory_missing', message: 'trajectory file is missing' } }, 404)
      }
      if (url === '/api/v1/tasks/task-42/artifacts') return response(artifacts)
      throw new Error(`Unexpected request: ${url}`)
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('产物不完整')
    expect(screen.queryByRole('img', { name: '3D 位姿轨迹' })).not.toBeInTheDocument()
  })

  it('ignores stale successful results after navigating to another task', async () => {
    let resolveTrajectory!: (value: Response) => void
    const pendingTrajectory = new Promise<Response>((resolve) => { resolveTrajectory = resolve })
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/tasks/task-42') return response(succeededTask)
      if (url === '/api/v1/tasks/task-42/trajectory') return pendingTrajectory
      if (url === '/api/v1/tasks/task-42/artifacts') return response(artifacts)
      if (url === '/api/v1/tasks/task-99') {
        return response({ ...succeededTask, id: 'task-99', sequence: 'new-sequence', status: 'vio' })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    const router = createMemoryRouter(
      [{ path: '/results/:taskId', element: <ResultPage /> }],
      { initialEntries: ['/results/task-42'], future: { v7_relativeSplatPath: true } },
    )
    render(<RouterProvider router={router} future={{ v7_startTransition: true }} />)
    await screen.findByRole('heading', { name: '20260805-113804' })
    await act(() => router.navigate('/results/task-99'))
    resolveTrajectory(await response(trajectory))

    expect(await screen.findByRole('heading', { name: 'new-sequence' })).toBeInTheDocument()
    expect(screen.queryByRole('img', { name: '3D 位姿轨迹' })).not.toBeInTheDocument()
  })

  it('loads diagnostic artifacts for failed tasks without requesting trajectory', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/tasks/task-42') {
        return response({ ...succeededTask, status: 'failed', exit_code: 7, error_summary: 'failed' })
      }
      if (url === '/api/v1/tasks/task-42/artifacts') return response(artifacts)
      throw new Error(`Unexpected request: ${url}`)
    })
    renderPage()

    expect(await screen.findByText('任务运行失败')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载 runner.log' })).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalledWith('/api/v1/tasks/task-42/trajectory', expect.anything())
  })

  it('rejects unsafe artifact path segments instead of rendering a download link', async () => {
    fetchMock.mockImplementation((input) => {
      const url = String(input)
      if (url === '/api/v1/tasks/task-42') return response(succeededTask)
      if (url === '/api/v1/tasks/task-42/trajectory') return response(trajectory)
      if (url === '/api/v1/tasks/task-42/artifacts') {
        return response({ items: [{ path: 'results/../secret', name: 'secret', size: 1 }] })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    renderPage()

    await screen.findByRole('img', { name: '3D 位姿轨迹' })
    expect(screen.queryByRole('link', { name: '下载 secret' })).not.toBeInTheDocument()
  })

  it('shows task loading errors without requesting result data', async () => {
    fetchMock.mockImplementation((input) => {
      if (String(input) === '/api/v1/tasks/task-42') return response({ detail: '任务不存在' }, 404)
      throw new Error(`Unexpected request: ${String(input)}`)
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('任务不存在')
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1))
  })
})
