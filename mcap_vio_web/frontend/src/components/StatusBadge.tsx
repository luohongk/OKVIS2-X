import type { TaskStatus } from '../types'

const labels: Record<TaskStatus, string> = {
  queued: '等待中',
  extracting: '抽取中',
  vio: 'VIO 运行中',
  succeeded: '运行成功',
  failed: '运行失败',
  interrupted: '已中断',
}

export function StatusBadge({ status }: { status: TaskStatus }) {
  return <span className={`status-badge status-badge--${status}`}>{labels[status]}</span>
}
