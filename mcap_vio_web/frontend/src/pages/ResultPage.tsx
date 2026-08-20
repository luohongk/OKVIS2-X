import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, taskApi } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import type { Artifact, Task, Trajectory } from '../types'

const Plot = lazy(() => import('react-plotly.js'))
const MAX_PLOT_POINTS = 50_000

export function ResultPage() {
  const { taskId = '' } = useParams()
  const [task, setTask] = useState<Task | null>(null)
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null)
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [error, setError] = useState('')
  const [artifactError, setArtifactError] = useState('')
  const [incomplete, setIncomplete] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    setTask(null)
    setTrajectory(null)
    setArtifacts([])
    setError('')
    setArtifactError('')
    setIncomplete(false)

    const loadArtifacts = async () => {
      try {
        const result = await taskApi.artifacts(taskId, controller.signal)
        if (active) setArtifacts(result.items.filter((artifact) => isSafeArtifactPath(artifact.path)))
      } catch (reason) {
        if (active && !isAbort(reason)) {
          setArtifactError(errorMessage(reason, '产物列表加载失败'))
        }
      }
    }

    const load = async () => {
      try {
        const taskData = await taskApi.detail(taskId, controller.signal)
        if (!active) return
        setTask(taskData)
        void loadArtifacts()
        if (taskData.status !== 'succeeded') return
        try {
          const result = await taskApi.trajectory(taskId, controller.signal)
          if (active) setTrajectory(result)
        } catch (reason) {
          if (!active || isAbort(reason)) return
          if (
            reason instanceof ApiError &&
            (reason.code === 'trajectory_missing' ||
              reason.code === 'trajectory_invalid' ||
              reason.status === 404 ||
              reason.status === 422)
          ) {
            setIncomplete(true)
          } else {
            setError(errorMessage(reason, '轨迹加载失败'))
          }
        }
      } catch (reason) {
        if (active && !isAbort(reason)) setError(errorMessage(reason, '任务加载失败'))
      }
    }
    void load()
    return () => {
      active = false
      controller.abort()
    }
  }, [taskId])

  const terminalFailure = task?.status === 'failed' || task?.status === 'interrupted'

  return (
    <div className="result-page">
      <header className="result-heading">
        <div>
          <p className="eyebrow">TRAJECTORY / ARTIFACTS</p>
          <h1>{task?.sequence ?? '结果详情'}</h1>
          <p className="task-reference">TASK · {taskId}</p>
        </div>
        {task && <StatusBadge status={task.status} />}
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}
      {incomplete && (
        <div className="artifact-warning" role="alert">
          <b>产物不完整</b>
          <span>任务标记为成功，但最终轨迹文件缺失或无法解析。</span>
        </div>
      )}

      {task && task.status !== 'succeeded' && (
        <section className="result-pending panel-block">
          <span>{terminalFailure ? 'TASK TERMINATED' : 'RESULT NOT READY'}</span>
          <h2>{pendingTitle(task)}</h2>
          <StatusBadge status={task.status} />
          <p>
            {terminalFailure
              ? task.error_summary ?? '任务未产生可用的最终轨迹，可下载日志诊断问题。'
              : '任务完成后，此页面将提供 3D 位姿轨迹、统计摘要与结果文件。'}
          </p>
          <Link to="/tasks">返回任务中心</Link>
        </section>
      )}

      {task && <TaskMetadata task={task} />}

      {task?.status === 'succeeded' && trajectory && (
        <>
          <section className="trajectory-stats">
            <StatCard label="轨迹点" value={`${trajectory.point_count} 点`} />
            <StatCard label="采样时长" value={`${trajectory.duration_sec.toFixed(2)} 秒`} />
            <StatCard label="路径长度" value={`${trajectory.path_length_m.toFixed(2)} m`} />
            <StatCard label="首尾位移" value={`${trajectory.displacement_m.toFixed(2)} m`} />
          </section>
          <section className="trajectory-workbench">
            <TrajectoryFigure trajectory={trajectory} />
            <aside className="bbox-panel panel-block">
              <div className="section-label"><span>02</span>空间范围</div>
              <div className="bbox-readout">
                <p>X {trajectory.bbox.min[0].toFixed(2)} → {trajectory.bbox.max[0].toFixed(2)}</p>
                <p>Y {trajectory.bbox.min[1].toFixed(2)} → {trajectory.bbox.max[1].toFixed(2)}</p>
                <p>Z {trajectory.bbox.min[2].toFixed(2)} → {trajectory.bbox.max[2].toFixed(2)}</p>
              </div>
            </aside>
          </section>
        </>
      )}

      {task && (
        <section className="artifact-panel panel-block">
          <div className="section-label"><span>03</span>结果产物 <b>{artifacts.length}</b></div>
          {artifactError ? (
            <p className="artifact-error" role="alert">{artifactError}</p>
          ) : (
            <div className="artifact-list">
              {artifacts.map((artifact) => (
                <a
                  key={artifact.path}
                  href={artifactDownloadUrl(task.id, artifact.path)}
                  aria-label={`下载 ${artifact.name}`}
                >
                  <span>{artifact.name}</span>
                  <small>{formatBytes(artifact.size)}</small>
                  <b>DOWNLOAD ↓</b>
                </a>
              ))}
              {artifacts.length === 0 && <p>未发现可下载产物</p>}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function TrajectoryFigure({ trajectory }: { trajectory: Trajectory }) {
  const sampledPoints = useMemo(() => samplePoints(trajectory.points), [trajectory.points])
  const data = useMemo(
    () => [
      {
        type: 'scatter3d' as const,
        mode: 'lines' as const,
        name: 'VIO trajectory',
        x: sampledPoints.map((point) => point[0]),
        y: sampledPoints.map((point) => point[1]),
        z: sampledPoints.map((point) => point[2]),
        line: { color: '#69f5b2', width: 5 },
      },
    ],
    [sampledPoints],
  )
  const layout = useMemo(
    () => ({
      autosize: true,
      paper_bgcolor: '#07100d',
      plot_bgcolor: '#07100d',
      font: { color: '#8fa89c', family: 'Chivo Mono' },
      margin: { l: 0, r: 0, t: 10, b: 0 },
      scene: {
        bgcolor: '#07100d',
        aspectmode: 'data' as const,
        xaxis: axisStyle('X / m'),
        yaxis: axisStyle('Y / m'),
        zaxis: axisStyle('Z / m'),
      },
    }),
    [],
  )

  return (
    <figure className="plot-panel panel-block" aria-label="3D 位姿轨迹">
      <figcaption className="section-label"><span>01</span>三维位姿轨迹</figcaption>
      <Suspense fallback={<div className="plot-loading">正在加载 3D 渲染器…</div>}>
        <Plot
          data={data}
          layout={layout}
          config={{ responsive: true, displaylogo: false }}
          useResizeHandler
          style={{ width: '100%', height: '100%' }}
        />
      </Suspense>
      <p className="sr-only">
        三维轨迹包含 {trajectory.point_count} 个点，路径长度 {trajectory.path_length_m.toFixed(2)} 米。
      </p>
    </figure>
  )
}

function TaskMetadata({ task }: { task: Task }) {
  return (
    <section className="result-meta panel-block">
      <div><span>DEVICE</span><b>{task.device}</b></div>
      <div><span>SEQUENCE</span><b>{task.sequence}</b></div>
      <div><span>CONFIG</span><b>{task.config_rel}</b></div>
      <div><span>WORKERS</span><b>{task.cam_workers}</b></div>
      <div><span>FINISHED</span><b>{task.finished_at ? formatDate(task.finished_at) : '—'}</b></div>
    </section>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return <div className="stat-card"><span>{label}</span><b>{value}</b></div>
}

function pendingTitle(task: Task) {
  if (task.status === 'failed') return '任务运行失败'
  if (task.status === 'interrupted') return '任务已中断'
  return '任务尚未完成'
}

function samplePoints(points: Trajectory['points']) {
  if (points.length <= MAX_PLOT_POINTS) return points
  const step = Math.ceil(points.length / MAX_PLOT_POINTS)
  const sampled = points.filter((_, index) => index % step === 0)
  if (sampled.at(-1) !== points.at(-1)) sampled.push(points[points.length - 1])
  return sampled
}

function axisStyle(title: string) {
  return {
    title,
    color: '#658077',
    gridcolor: '#1d352d',
    zerolinecolor: '#315347',
    showbackground: true,
    backgroundcolor: '#08120f',
  }
}

function isSafeArtifactPath(path: string) {
  const parts = path.split('/')
  return parts.every((part) => part !== '' && part !== '.' && part !== '..')
}

function artifactDownloadUrl(taskId: string, path: string) {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}/artifacts/${path
    .split('/')
    .map(encodeURIComponent)
    .join('/')}`
}

function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function isAbort(reason: unknown) {
  return reason instanceof DOMException && reason.name === 'AbortError'
}

function errorMessage(reason: unknown, fallback: string) {
  return reason instanceof Error ? reason.message : fallback
}
