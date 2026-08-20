import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { taskApi } from '../api'
import { StatusBadge } from '../components/StatusBadge'
import { TaskLog } from '../components/TaskLog'
import type { RuntimeStatus, Task, TaskStatus } from '../types'

const TERMINAL = new Set<TaskStatus>(['succeeded', 'failed', 'interrupted'])

export function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([])
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [error, setError] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('limit=100&offset=0')
  const [status, setStatus] = useState('')
  const [device, setDevice] = useState('')
  const [sequence, setSequence] = useState('')
  const [config, setConfig] = useState('')

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [taskResponse, runtimeResponse] = await Promise.all([
        taskApi.list(appliedQuery, signal),
        taskApi.runtime(signal),
      ])
      setTasks(taskResponse.items)
      setRuntime(runtimeResponse)
      setError('')
    } catch (reason) {
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
        setError(reason instanceof Error ? reason.message : '任务加载失败')
      }
    }
  }, [appliedQuery])

  useEffect(() => {
    const controller = new AbortController()
    let disposed = false
    let timer: number | undefined
    const poll = async () => {
      await load(controller.signal)
      if (!disposed) timer = window.setTimeout(() => void poll(), 5000)
    }
    void poll()
    return () => {
      disposed = true
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [load])

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedId) ?? null,
    [selectedId, tasks],
  )

  const applyFilters = (event: FormEvent) => {
    event.preventDefault()
    const query = new URLSearchParams()
    if (status) query.set('status', status)
    if (device.trim()) query.set('device', device.trim())
    if (sequence.trim()) query.set('sequence', sequence.trim())
    if (config.trim()) query.set('config_id', config.trim())
    query.set('limit', '100')
    query.set('offset', '0')
    setAppliedQuery(query.toString())
  }

  return (
    <div className="tasks-page">
      <header className="tasks-heading">
        <div>
          <p className="eyebrow">QUEUE / TELEMETRY / LOGS</p>
          <h1>任务中心</h1>
        </div>
        <section className="queue-readout" aria-label="队列运行摘要">
          <span><b>{runtime?.max_concurrency ?? '—'}</b> 路并发</span>
          <span><b>{runtime?.running ?? '—'}</b> 运行中</span>
          <span><b>{runtime?.queued ?? '—'}</b> 排队</span>
          <i className={runtime?.accepting_tasks ? 'is-online' : ''}>
            {runtime?.accepting_tasks ? 'ACCEPTING' : 'PAUSED'}
          </i>
        </section>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <form className="task-filters panel-block" onSubmit={applyFilters}>
        <label>
          <span>任务状态</span>
          <select aria-label="任务状态" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            <option value="queued">等待中</option>
            <option value="extracting">抽取中</option>
            <option value="vio">VIO 运行中</option>
            <option value="succeeded">运行成功</option>
            <option value="failed">运行失败</option>
            <option value="interrupted">已中断</option>
          </select>
        </label>
        <label><span>设备</span><input aria-label="设备过滤" value={device} onChange={(event) => setDevice(event.target.value)} /></label>
        <label><span>序列</span><input aria-label="序列过滤" value={sequence} onChange={(event) => setSequence(event.target.value)} /></label>
        <label><span>配置</span><input aria-label="配置过滤" value={config} onChange={(event) => setConfig(event.target.value)} /></label>
        <button type="submit">应用过滤</button>
      </form>

      <div className="task-workbench">
        <section className="task-list panel-block" aria-label="任务列表">
          <div className="section-label"><span>01</span>任务队列 <b>{tasks.length}</b></div>
          <div className="task-list__body">
            {tasks.map((task) => (
              <article key={task.id} className={selectedId === task.id ? 'task-entry is-selected' : 'task-entry'}>
                <button
                  type="button"
                  aria-pressed={selectedId === task.id}
                  onClick={() => setSelectedId(task.id)}
                >
                  <div className="task-entry__top">
                    <b>{task.sequence}</b>
                    <StatusBadge status={task.status} />
                  </div>
                  <dl>
                    <div><dt>DEVICE</dt><dd>{task.device}</dd></div>
                    <div><dt>CONFIG</dt><dd>{task.config_rel}</dd></div>
                    <div><dt>CREATED</dt><dd>{formatTime(task.created_at)}</dd></div>
                  </dl>
                  {task.status === 'failed' && (
                    <div className="task-failure">
                      <span>退出码 {task.exit_code ?? '—'}</span>
                      <strong>{task.error_summary ?? task.runner_status ?? '未知错误'}</strong>
                    </div>
                  )}
                </button>
                {task.status === 'succeeded' && (
                  <Link className="result-link" to={`/results/${task.id}`} aria-label={`查看 ${task.sequence} 结果`}>
                    查看轨迹结果 →
                  </Link>
                )}
              </article>
            ))}
            {tasks.length === 0 && !error && <p className="task-empty">暂无符合条件的任务</p>}
          </div>
        </section>

        <section className="task-detail panel-block">
          <div className="section-label"><span>02</span>实时任务观测</div>
          {selectedTask ? (
            <TaskLog task={selectedTask} />
          ) : (
            <div className="console-empty">
              <span>SELECT TASK</span>
              <p>从左侧选择一项任务以连接 SSE 日志流。</p>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}

function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

export function isTaskTerminal(status: TaskStatus) {
  return TERMINAL.has(status)
}
