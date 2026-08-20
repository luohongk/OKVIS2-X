import { useEffect, useRef, useState } from 'react'
import { openTaskEvents } from '../events'
import type { Task, TaskStatus } from '../types'
import { StatusBadge } from './StatusBadge'

const MAX_LOG_CHARS = 200_000
const TERMINAL_STATUSES = new Set<TaskStatus>(['succeeded', 'failed', 'interrupted'])

export function TaskLog({ task }: { task: Task }) {
  const [status, setStatus] = useState<TaskStatus>(task.status)
  const [log, setLog] = useState('')
  const [truncated, setTruncated] = useState(false)
  const [connectionError, setConnectionError] = useState(false)
  const logBuffer = useRef('')

  useEffect(() => {
    setStatus((current) => {
      if (TERMINAL_STATUSES.has(current) && !TERMINAL_STATUSES.has(task.status)) {
        return current
      }
      return task.status
    })
  }, [task.id, task.status])

  useEffect(() => {
    setStatus(task.status)
    logBuffer.current = ''
    setLog('')
    setTruncated(false)
    setConnectionError(false)
    return openTaskEvents(task.id, {
      snapshot: (snapshot) => setStatus(snapshot.status),
      log: (text, reset) => {
        const combined = reset ? text : logBuffer.current + text
        const next = combined.slice(-MAX_LOG_CHARS)
        logBuffer.current = next
        setTruncated(combined.length > MAX_LOG_CHARS)
        setLog(next)
      },
      status: setStatus,
      terminal: setStatus,
      open: () => setConnectionError(false),
      error: () => setConnectionError(true),
    })
  }, [task.id])

  return (
    <section className="task-console" aria-label={`${task.sequence} 实时日志`}>
      <header>
        <div>
          <span>LIVE TELEMETRY</span>
          <b>{task.sequence}</b>
        </div>
        <StatusBadge status={status} />
      </header>
      {connectionError && <p className="console-warning">日志连接暂时中断，浏览器将自动重连。</p>}
      {truncated && <p className="console-truncated">较早日志已截断，仅保留最近内容。</p>}
      <pre role="log" aria-live="polite">{log || '等待任务日志…'}</pre>
    </section>
  )
}
