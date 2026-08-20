export function RuntimeSummary() {
  return (
    <section className="runtime-strip" aria-label="运行状态">
      <div className="runtime-cell">
        <span>并发上限</span>
        <strong>—</strong>
      </div>
      <div className="runtime-cell runtime-cell--active">
        <span>运行中</span>
        <strong>—</strong>
      </div>
      <div className="runtime-cell">
        <span>排队</span>
        <strong>—</strong>
      </div>
      <span className="runtime-pulse">SYSTEM READY</span>
    </section>
  )
}
