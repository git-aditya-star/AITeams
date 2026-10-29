// The small amber "stuck" tag on a role or a feature session that has run, or been queued, for a long time (F-38). Used by the team strip
// and the sidebar's feature-session rows.
export default function StuckTag() {
  return (
    <span data-stuck className="rounded bg-amber-100 px-1 font-semibold text-amber-800">
      stuck
    </span>
  )
}
