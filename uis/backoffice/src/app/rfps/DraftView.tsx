import { parseDraft } from '../../utils/rfps'

function Inline({ text }: { text: string }) {
  // Solo **negrita**: el borrador es texto generado y nunca se inyecta como HTML.
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**')
    ? <strong key={index}>{part.slice(2, -2)}</strong> : <span key={index}>{part}</span>)}</>
}

export function DraftView({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-2 text-sm text-slate-800">
      {parseDraft(markdown).map((block, index) => {
        if (block.kind === 'heading') {
          return block.level <= 2
            ? <h4 key={index} className="text-base font-semibold text-slate-900">{block.text}</h4>
            : <h5 key={index} className="pt-2 font-semibold text-slate-900">{block.text}</h5>
        }
        if (block.kind === 'list') return <ul key={index} className="list-disc pl-5">{block.items.map((item) => <li key={item}><Inline text={item} /></li>)}</ul>
        if (block.kind === 'table') {
          return (
            <div key={index} className="overflow-x-auto">
              <table className="w-full border border-slate-200 text-left text-xs">
                <thead className="bg-slate-50"><tr>{block.header.map((cell) => <th key={cell} className="border border-slate-200 px-2 py-1">{cell}</th>)}</tr></thead>
                <tbody>{block.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="border border-slate-200 px-2 py-1">{cell}</td>)}</tr>)}</tbody>
              </table>
            </div>
          )
        }
        return <p key={index}><Inline text={block.text} /></p>
      })}
    </div>
  )
}
