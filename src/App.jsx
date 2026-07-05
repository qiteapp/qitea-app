import { useState } from 'react'
import { activities, services, navSections, menuSections } from './data.js'

function Header({ onMenu }) {
  return (
    <header className="app-header">
      <button className="menu-icon" aria-label="Open menu" onClick={onMenu}>☰</button>
      <div className="app-name">TOUR ME</div>
    </header>
  )
}

function SideMenu({ open, section, onNavigate, onClose }) {
  return (
    <>
      <div className={`backdrop ${open ? 'show' : ''}`} onClick={onClose} />
      <nav className={`side-menu ${open ? 'open' : ''}`}>
        <button className="menu-close" aria-label="Close menu" onClick={onClose}>×</button>
        <ul>
          {menuSections.map((s) => (
            <li key={s.id}>
              <button
                className={section === s.id ? 'active' : ''}
                onClick={() => onNavigate(s.id)}
              >
                <span className="menu-emoji">{s.emoji}</span> {s.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </>
  )
}

function BottomNav({ section, onNavigate }) {
  return (
    <nav className="footer-menu">
      {navSections.map((s) => (
        <button
          key={s.id}
          className={section === s.id ? 'active' : ''}
          onClick={() => onNavigate(s.id)}
        >
          <span className="nav-emoji">{s.emoji}</span>
          <span>{s.label}</span>
        </button>
      ))}
    </nav>
  )
}

function ActivityList({ activity }) {
  const [quantities, setQuantities] = useState({})
  return (
    <div className="activity-content">
      <h3>{activity.title}</h3>
      <ul>
        {activity.items.map((item) => (
          <li key={item.name}>
            <p>{item.name}</p>
            <div className="item-actions">
              {item.actions.map((action) => (
                <button key={action} className="pill-btn">{action}</button>
              ))}
              {activity.cart && (
                <input
                  type="number"
                  min="1"
                  value={quantities[item.name] ?? 1}
                  onChange={(e) =>
                    setQuantities({ ...quantities, [item.name]: e.target.value })
                  }
                  aria-label={`Quantity for ${item.name}`}
                />
              )}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Home() {
  const [openActivity, setOpenActivity] = useState(null)
  return (
    <section className="content">
      <h2>Find Activities</h2>
      <div className="activity-grid">
        {activities.map((a) => (
          <button
            key={a.id}
            className={`activity-card ${openActivity === a.id ? 'selected' : ''}`}
            onClick={() => setOpenActivity(openActivity === a.id ? null : a.id)}
          >
            <span className="activity-emoji">{a.emoji}</span>
            <p>{a.label}</p>
          </button>
        ))}
      </div>
      {openActivity && (
        <ActivityList activity={activities.find((a) => a.id === openActivity)} />
      )}
    </section>
  )
}

function MapSection() {
  const [query, setQuery] = useState('')
  return (
    <section className="content">
      <h2>Find a Destination</h2>
      <input
        className="search-input"
        type="text"
        placeholder="Search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="map-container">
        <span>🗺️</span>
        <p>{query ? `Searching for “${query}”…` : 'Map coming soon'}</p>
      </div>
    </section>
  )
}

function Services() {
  return (
    <section className="content">
      <h2>Let Us Serve You</h2>
      <ul className="services-list">
        {services.map((s) => (
          <li key={s.id}>
            <button className="service-row">
              <span className="service-emoji">{s.emoji}</span>
              <p>{s.name}</p>
              <span className="arrow">→</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Account() {
  return (
    <section className="content account">
      <h2>Account</h2>
      <button className="sign-in apple">Sign in with Apple</button>
      <button className="sign-in google">Sign in with Google</button>
    </section>
  )
}

function Settings() {
  return (
    <section className="content">
      <h2>Settings</h2>
      <p className="muted">Settings options will appear here.</p>
    </section>
  )
}

const sections = {
  home: Home,
  map: MapSection,
  services: Services,
  account: Account,
  settings: Settings,
}

export default function App() {
  const [section, setSection] = useState('home')
  const [menuOpen, setMenuOpen] = useState(false)
  const Section = sections[section]

  const navigate = (id) => {
    setSection(id)
    setMenuOpen(false)
  }

  return (
    <div className="app">
      <Header onMenu={() => setMenuOpen(true)} />
      <SideMenu
        open={menuOpen}
        section={section}
        onNavigate={navigate}
        onClose={() => setMenuOpen(false)}
      />
      <main>
        <Section />
      </main>
      <BottomNav section={section} onNavigate={navigate} />
    </div>
  )
}
