const NAV = [
  { key: 'search', label: 'Search', icon: 'manage_search' },
  { key: 'batch', label: 'Batch Search', icon: 'playlist_add_check' },
  { key: 'quickmatch', label: 'Quick Match', icon: 'bolt' },
  { key: 'copilot', label: 'Copilot', icon: 'auto_awesome' },
  { key: 'entity', label: 'Entity Graph', icon: 'hub' },
  { key: 'profile', label: 'Merchant Profile', icon: 'person_search' },
  { key: 'reconcile', label: 'Reconcile', icon: 'rule' },
  { key: 'rules', label: 'Rule Engine', icon: 'tune' },
  { key: 'report', label: 'Report Builder', icon: 'description' },
  { key: 'aliases', label: 'Alias Review', icon: 'fact_check' },
  { key: 'quality', label: 'Data Quality', icon: 'analytics' },
  { key: 'audit', label: 'Audit Trail', icon: 'history' },
]

export default function Sidebar({ current, navigate, total }) {
  return (
    <aside className="flex h-screen w-64 shrink-0 flex-col border-r border-outline-variant bg-surface-container-low p-4 shadow-sm">
      {/* Brand */}
      <div className="mb-8 px-2">
        <h1 className="text-xl font-black tracking-tight text-primary">
          Merchant Intelligence
        </h1>
        <div className="mt-1 flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-secondary" />
          <span className="font-plex text-xs font-medium text-on-surface-variant">
            {total ? total.toLocaleString() : '—'} records
          </span>
        </div>
        <p className="mt-1 font-plex text-[10px] font-semibold uppercase tracking-widest text-outline">
          2ISW + NNPC Ecosystem
        </p>
      </div>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-0.5">
        {NAV.map((item) => {
          const active = current === item.key
          return (
            <button
              key={item.key}
              onClick={() => navigate(item.key)}
              className={`flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-left font-plex text-[13px] font-medium transition-colors ${
                active
                  ? 'bg-primary-container font-bold text-on-primary shadow-sm'
                  : 'text-on-surface-variant hover:bg-surface-container hover:text-on-surface'
              }`}
            >
              <span className={`msi shrink-0 text-[20px] ${active ? 'fill' : ''}`}>
                {item.icon}
              </span>
              <span>{item.label}</span>
            </button>
          )
        })}
      </nav>

      {/* CTA */}
      <div className="pt-4">
        <button
          onClick={() => navigate('batch')}
          className="mb-4 flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 font-plex text-[13px] font-bold text-on-primary shadow-sm transition-all hover:opacity-90 active:scale-95"
        >
          <span className="msi text-[20px]">add</span>
          <span>New Investigation</span>
        </button>
        <div className="space-y-1 border-t border-outline-variant pt-3">
          <a
            href="#support"
            className="flex items-center gap-3 rounded-lg px-3.5 py-2 text-on-surface-variant font-plex text-[13px] hover:bg-surface-container transition-colors"
          >
            <span className="msi text-[20px]">help</span>
            <span>Support</span>
          </a>
          <a
            href="#api"
            className="flex items-center gap-3 rounded-lg px-3.5 py-2 text-on-surface-variant font-plex text-[13px] hover:bg-surface-container transition-colors"
          >
            <span className="msi text-[20px]">code</span>
            <span>API Docs</span>
          </a>
        </div>
      </div>
    </aside>
  )
}
