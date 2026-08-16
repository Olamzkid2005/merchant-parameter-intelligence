import { useEffect, useState } from 'react'
import Sidebar from './components/Sidebar'
import TopBar from './components/TopBar'
import SearchPage from './pages/SearchPage'
import BatchPage from './pages/BatchPage'
import EntityGraphPage from './pages/EntityGraphPage'
import ProfilePage from './pages/ProfilePage'
import QuickMatchPage from './pages/QuickMatchPage'
import ReconcilePage from './pages/ReconcilePage'
import RuleEnginePage from './pages/RuleEnginePage'
import QualityPage from './pages/QualityPage'
import AliasReviewPage from './pages/AliasReviewPage'
import ReportBuilderPage from './pages/ReportBuilderPage'
import AuditPage from './pages/AuditPage'
import { api } from './api'

const PAGES = [
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

function currentPage() {
  const p = new URLSearchParams(window.location.search).get('page')
  return PAGES.some((x) => x.key === p) ? p : 'search'
}

export default function App() {
  const [page, setPage] = useState(currentPage)
  const [total, setTotal] = useState(0)

  useEffect(() => {
    api.stats().then((d) => setTotal(d.total_records || 0)).catch(() => {})
  }, [])

  const navigate = (key, extra = {}) => {
    setPage(key)
    const url = new URL(window.location.href)
    url.searchParams.set('page', key)
    Object.entries(extra).forEach(([k, v]) => {
      if (v) url.searchParams.set(k, v)
      else url.searchParams.delete(k)
    })
    window.history.pushState({}, '', url)
  }

  useEffect(() => {
    const onPop = () => setPage(currentPage())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar current={page} navigate={navigate} total={total} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <TopBar current={page} navigate={navigate} />
        <main className="flex-1 overflow-y-auto px-8 py-6">
          <div className="mx-auto max-w-[1440px]">
            {page === 'search' && <SearchPage onOpenProfile={(name) => navigate('profile', { q: name })} />}
            {page === 'batch' && <BatchPage onOpenProfile={(name) => navigate('profile', { q: name })} />}
            {page === 'quickmatch' && <QuickMatchPage onOpenProfile={(name) => navigate('profile', { q: name })} />}
            {page === 'entity' && <EntityGraphPage />}
            {page === 'profile' && (
              <ProfilePage onOpenGraph={(name) => navigate('entity', { q: name })} />
            )}
            {page === 'reconcile' && <ReconcilePage />}
            {page === 'rules' && <RuleEnginePage />}
            {page === 'report' && <ReportBuilderPage />}
            {page === 'aliases' && <AliasReviewPage />}
            {page === 'quality' && <QualityPage />}
            {page === 'audit' && <AuditPage />}
          </div>
        </main>
      </div>
    </div>
  )
}
