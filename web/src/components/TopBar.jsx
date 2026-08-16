const TABS = [
  { key: 'search', label: 'Search' },
  { key: 'batch', label: 'Batch Search' },
  { key: 'quickmatch', label: 'Quick Match' },
  { key: 'entity', label: 'Entity Graph' },
  { key: 'profile', label: 'Merchant Profile' },
  { key: 'reconcile', label: 'Reconcile' },
  { key: 'rules', label: 'Rule Engine' },
  { key: 'report', label: 'Report Builder' },
  { key: 'aliases', label: 'Alias Review' },
  { key: 'quality', label: 'Data Quality' },
]

export default function TopBar({ current, navigate, auth, onLogout }) {
  return (
    <header className="z-20 border-b border-outline-variant bg-background">
      <div className="mx-auto flex w-full max-w-[1440px] items-center justify-between px-8">
        <nav className="hidden items-center gap-6 md:flex">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              onClick={() => navigate(tab.key)}
              className={`pb-2 pt-4 font-plex text-[13px] transition-colors ${
                current === tab.key
                  ? 'border-b-2 border-primary font-bold text-primary'
                  : 'text-on-surface-variant hover:text-primary'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-4 py-3">
          {auth?.enabled && auth?.authenticated && (
            <div className="flex items-center gap-2">
              <span className="rounded-full bg-primary/10 px-2.5 py-1 font-plex text-[10px] font-bold text-primary">
                {auth.user} · {auth.role}
              </span>
              <button
                onClick={onLogout}
                title="Sign out"
                className="flex items-center gap-1 rounded-lg border border-outline-variant px-2 py-1 font-plex text-[10px] font-bold text-on-surface-variant transition-colors hover:border-primary hover:text-primary"
              >
                <span className="msi text-[14px]">logout</span>
                Sign out
              </button>
            </div>
          )}
          <span className="rounded border border-outline-variant bg-surface-container-high px-2 py-1 text-[10px] font-bold tracking-widest text-on-surface-variant">
            PROD
          </span>
          <button className="text-on-surface-variant transition-colors hover:text-primary active:scale-95">
            <span className="msi text-[22px]">notifications</span>
          </button>
          <button className="text-on-surface-variant transition-colors hover:text-primary active:scale-95">
            <span className="msi text-[22px]">settings</span>
          </button>
          <div className="flex h-8 w-8 items-center justify-center overflow-hidden rounded-full border border-outline-variant bg-primary-fixed font-plex text-xs font-bold text-primary">
            DA
          </div>
        </div>
      </div>
    </header>
  )
}
