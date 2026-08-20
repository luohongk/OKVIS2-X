import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { catalogApi } from '../api'
import type { ConfigOption, Device, Sequence } from '../types'

function isAbortError(reason: unknown) {
  return reason instanceof DOMException && reason.name === 'AbortError'
}

export function DataRunPage() {
  const navigate = useNavigate()
  const [devices, setDevices] = useState<Device[]>([])
  const [configs, setConfigs] = useState<ConfigOption[]>([])
  const [device, setDevice] = useState('')
  const [sequences, setSequences] = useState<Sequence[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [configId, setConfigId] = useState('')
  const [search, setSearch] = useState('')
  const [camWorkersInput, setCamWorkersInput] = useState('4')
  const [keepImages, setKeepImages] = useState(false)
  const [loadingSequences, setLoadingSequences] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const mounted = useRef(true)
  const sequenceRequest = useRef(0)
  const sequenceController = useRef<AbortController | null>(null)
  const submitController = useRef<AbortController | null>(null)

  useEffect(() => {
    const catalogController = new AbortController()
    mounted.current = true
    Promise.all([
      catalogApi.devices(catalogController.signal),
      catalogApi.configs(catalogController.signal),
    ])
      .then(([deviceResponse, configResponse]) => {
        if (!mounted.current) return
        setDevices(deviceResponse.items)
        setConfigs(configResponse.items)
      })
      .catch((reason: unknown) => {
        if (mounted.current && !isAbortError(reason)) {
          setError(reason instanceof Error ? reason.message : '目录加载失败')
        }
      })
    return () => {
      mounted.current = false
      catalogController.abort()
      sequenceController.current?.abort()
      submitController.current?.abort()
    }
  }, [])

  const chooseDevice = async (nextDevice: string) => {
    sequenceController.current?.abort()
    const controller = new AbortController()
    sequenceController.current = controller
    const requestId = ++sequenceRequest.current
    setDevice(nextDevice)
    setSequences([])
    setSelected(new Set())
    setSearch('')
    setError('')
    setLoadingSequences(true)
    try {
      const response = await catalogApi.sequences(nextDevice, controller.signal)
      if (mounted.current && requestId === sequenceRequest.current) {
        setSequences(response.items)
      }
    } catch (reason) {
      if (mounted.current && requestId === sequenceRequest.current && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : '序列加载失败')
      }
    } finally {
      if (mounted.current && requestId === sequenceRequest.current) {
        setLoadingSequences(false)
      }
    }
  }

  const filteredSequences = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return sequences
    return sequences.filter((sequence) => sequence.name.toLowerCase().includes(term))
  }, [search, sequences])

  const toggleSequence = (name: string) => {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const toggleVisible = () => {
    const visibleNames = filteredSequences.map((sequence) => sequence.name)
    const allSelected = visibleNames.length > 0 && visibleNames.every((name) => selected.has(name))
    setSelected((current) => {
      const next = new Set(current)
      for (const name of visibleNames) {
        if (allSelected) next.delete(name)
        else next.add(name)
      }
      return next
    })
  }

  const camWorkers = Number(camWorkersInput)
  const workersValid = Number.isInteger(camWorkers) && camWorkers >= 1 && camWorkers <= 4

  const submit = async () => {
    if (!device || !configId || selected.size === 0 || !workersValid) return
    submitController.current?.abort()
    const controller = new AbortController()
    submitController.current = controller
    setSubmitting(true)
    setError('')
    try {
      await catalogApi.createTaskGroup(
        {
          device,
          sequences: [...selected],
          config_id: configId,
          options: { cam_workers: camWorkers, keep_images: keepImages },
        },
        controller.signal,
      )
      if (mounted.current) navigate('/tasks')
    } catch (reason) {
      if (mounted.current && !isAbortError(reason)) {
        setError(reason instanceof Error ? reason.message : '任务提交失败')
      }
    } finally {
      if (mounted.current) setSubmitting(false)
    }
  }

  const allVisibleSelected =
    filteredSequences.length > 0 &&
    filteredSequences.every((sequence) => selected.has(sequence.name))

  return (
    <div className="run-page">
      <header className="run-heading">
        <div>
          <p className="eyebrow">ACQUISITION / VIO PIPELINE</p>
          <h1>数据运行</h1>
        </div>
        <div className="run-heading__readout">
          <span>INPUT ROOT</span>
          <b>/home/conanluo/okvis_data</b>
        </div>
      </header>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <section className="run-grid">
        <aside className="device-panel panel-block">
          <div className="section-label"><span>01</span>选择设备</div>
          <div className="device-list">
            {devices.map((item) => (
              <button
                type="button"
                key={item.name}
                className={device === item.name ? 'device-card is-selected' : 'device-card'}
                aria-pressed={device === item.name}
                onClick={() => void chooseDevice(item.name)}
              >
                <span className="device-card__signal" />
                <b>{item.name}</b>
                <small>{item.valid_sequence_count} 组序列</small>
              </button>
            ))}
            {devices.length === 0 && !error && <p className="empty-copy">正在扫描数据根目录…</p>}
          </div>
        </aside>

        <section className="sequence-panel panel-block">
          <div className="section-label"><span>02</span>选择序列</div>
          <div className="sequence-tools">
            <input
              type="search"
              aria-label="搜索序列"
              placeholder="SEARCH SEQUENCE"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              disabled={!device}
            />
            <label className="select-visible">
              <input
                type="checkbox"
                aria-label="全选当前结果"
                checked={allVisibleSelected}
                onChange={toggleVisible}
                disabled={filteredSequences.length === 0}
              />
              全选当前结果
            </label>
            <strong>已选择 {selected.size} 组</strong>
          </div>
          <div className="sequence-table-wrap">
            <table className="sequence-table">
              <thead>
                <tr>
                  <th>选择</th><th>序列</th><th>时长</th><th>平台</th><th>数据状态</th><th>MCAP</th>
                </tr>
              </thead>
              <tbody>
                {filteredSequences.map((sequence) => (
                  <tr key={sequence.name} className={selected.has(sequence.name) ? 'is-selected' : ''}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`选择 ${sequence.name}`}
                        checked={selected.has(sequence.name)}
                        onChange={() => toggleSequence(sequence.name)}
                      />
                    </td>
                    <td><b>{sequence.name}</b></td>
                    <td>{sequence.duration_sec === null ? '—' : `${sequence.duration_sec} 秒`}</td>
                    <td>{sequence.platform_name?.toUpperCase() ?? '—'}</td>
                    <td>
                      <span className={sequence.complete ? 'data-state data-state--ok' : 'data-state data-state--warn'}>
                        {sequence.complete ? '完整' : '信息缺失'}
                      </span>
                    </td>
                    <td>{Object.values(sequence.sensor_mcap_counts).reduce((sum, count) => sum + count, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!device && <div className="table-empty">先从左侧选择设备</div>}
            {loadingSequences && <div className="table-empty">正在读取 MCAP 索引…</div>}
            {device && !loadingSequences && filteredSequences.length === 0 && (
              <div className="table-empty">没有匹配的有效序列</div>
            )}
          </div>
        </section>
      </section>

      <section className="launch-panel panel-block">
        <div className="section-label"><span>03</span>运行配置</div>
        <div className="launch-controls">
          <label className="field-control field-control--wide">
            <span>配置文件</span>
            <select aria-label="配置文件" value={configId} onChange={(event) => setConfigId(event.target.value)}>
              <option value="">选择 YAML 标定配置</option>
              {configs.map((config) => <option key={config.id} value={config.id}>{config.label}</option>)}
            </select>
          </label>
          <label className="field-control">
            <span>相机解码线程</span>
            <input
              type="number"
              aria-label="相机解码线程"
              min={1}
              max={4}
              value={camWorkersInput}
              onChange={(event) => setCamWorkersInput(event.target.value)}
            />
          </label>
          <label className="keep-images">
            <input
              type="checkbox"
              aria-label="保留抽取图片"
              checked={keepImages}
              onChange={(event) => setKeepImages(event.target.checked)}
            />
            <span>保留抽取图片</span>
            <small>将显著增加磁盘占用</small>
          </label>
          <div className="launch-summary">
            <span>DEVICE <b>{device || '—'}</b></span>
            <span>SEQUENCES <b>{selected.size}</b></span>
            <span>WORKERS <b>{workersValid ? camWorkers : '—'}</b></span>
          </div>
          <button
            className="launch-button"
            type="button"
            disabled={!device || selected.size === 0 || !configId || !workersValid || submitting}
            onClick={() => void submit()}
          >
            {submitting ? '正在提交…' : '提交位姿任务'}
          </button>
        </div>
      </section>
    </div>
  )
}
