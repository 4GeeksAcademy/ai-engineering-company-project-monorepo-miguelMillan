interface SectionTitleProps {
  eyebrow: string
  title: string
  description: string
}

/** Reusable heading block for content sections. */
export function SectionTitle({ eyebrow, title, description }: SectionTitleProps) {
  return (
    <header className="mx-auto max-w-3xl text-center">
      <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-700">
        {eyebrow}
      </p>
      <h2 className="mt-3 font-display text-3xl font-bold text-slate-900 md:text-4xl">
        {title}
      </h2>
      <p className="mt-4 text-base text-slate-600 md:text-lg">{description}</p>
    </header>
  )
}
