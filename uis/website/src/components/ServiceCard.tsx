interface ServiceCardProps {
  title: string
  summary: string
  stat: string
}

export function ServiceCard({ title, summary, stat }: ServiceCardProps) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white/80 p-6 shadow-sm backdrop-blur-sm transition hover:-translate-y-1 hover:shadow-lg">
      <p className="text-xs font-semibold uppercase tracking-[0.17em] text-cyan-700">
        {stat}
      </p>
      <h3 className="mt-3 font-display text-2xl font-semibold text-slate-900">
        {title}
      </h3>
      <p className="mt-3 text-slate-600">{summary}</p>
    </article>
  )
}
