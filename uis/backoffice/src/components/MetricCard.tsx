interface MetricCardProps {
  label: string
  value: string
  trend: string
}

export function MetricCard({ label, value, trend }: MetricCardProps) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className="mt-2 font-display text-3xl font-bold text-slate-900">{value}</p>
      <p className="mt-2 text-sm text-emerald-700">{trend}</p>
    </article>
  )
}
