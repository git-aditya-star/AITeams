import { useEffect, useState } from 'react'

// F-29: a ```mermaid block rendered as an SVG diagram. The library is large, so it is fetched on first use only (a chunk of
// its own), never in the main bundle. `securityLevel: 'strict'` makes it sanitize the diagram text and the SVG it returns.
type MermaidApi = typeof import('mermaid').default
let loaded: Promise<MermaidApi> | null = null
const load = () =>
  (loaded ??= import('mermaid').then((m) => {
    m.default.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'neutral' })
    return m.default
  }))

// One render at a time, and a unique id per render (mermaid keys its temporary element by it).
let queue: Promise<unknown> = Promise.resolve()
let counter = 0
const renderDiagram = (code: string) => {
  const run = async () => {
    const mermaid = await load()
    const id = `mermaid-${++counter}`
    try {
      await mermaid.parse(code)
      return (await mermaid.render(id, code)).svg
    } finally {
      document.getElementById(`d${id}`)?.remove() // a failed render can leave its error graphic in <body>
    }
  }
  const result = queue.then(run, run)
  queue = result.catch(() => {})
  return result
}

export default function Mermaid({ code }: { code: string }) {
  const [state, setState] = useState<{ svg: string } | { error: string } | null>(null)
  useEffect(() => {
    let live = true
    setState(null)
    renderDiagram(code).then(
      (svg) => live && setState({ svg }),
      (e) => live && setState({ error: String(e?.message || e).split('\n')[0] }),
    )
    return () => {
      live = false
    }
  }, [code])

  if (state && 'error' in state)
    return (
      <div data-mermaid-error>
        <pre>{code}</pre>
        <div className="text-caption text-red-600">This diagram has an error and could not be drawn: {state.error}</div>
      </div>
    )
  if (!state) return <div data-mermaid-loading className="my-2 text-caption text-gray-400">Drawing diagram…</div>
  return <div data-mermaid className="my-2 overflow-x-auto [&>svg]:mx-auto [&>svg]:max-w-full" dangerouslySetInnerHTML={{ __html: state.svg }} />
}
