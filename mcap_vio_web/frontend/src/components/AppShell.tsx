import type { ReactNode } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { RuntimeSummary } from './RuntimeSummary'

const navItems = [
  { to: '/run', label: '数据运行', index: '01' },
  { to: '/tasks', label: '任务中心', index: '02' },
]

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/run" aria-label="EGO 设备数据标注平台">
          <span className="brand-mark" aria-hidden="true">
            E
          </span>
          <span>
            <b>EGO LAB</b>
            <small>设备数据标注平台</small>
          </span>
        </Link>
        <nav className="main-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? 'nav-link is-active' : 'nav-link')}
            >
              <span aria-hidden="true">{item.index}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <RuntimeSummary />
      </header>
      <main className="workspace">{children}</main>
    </div>
  )
}
