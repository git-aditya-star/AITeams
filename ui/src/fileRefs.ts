// F-50: the files a text names, for the inline previews. A reference is a token that ends in .html, .png, .jpg, .jpeg or .svg: a
// docs path (.aiteams/features/F-n/x.png), a bare name (x.png), an absolute or ~ path, or a relative one. It counts as plain text,
// inside a code span or as a link target. The trailing check keeps "shot.png." (the end of a sentence) and refuses "x.html.bak".
// The daemon decides what a reference resolves to (aiteams/previews.py); this only finds the tokens. Kept free of React so a spec
// can import it.
const PATH_RUN = /[\w.~/@+%-]+/g
const FILE_TYPE = /[^./]\.(?:html|png|jpe?g|svg)$/i // a stem first: ".png" on its own is a word, not a file

export function fileRefs(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const m of text.matchAll(PATH_RUN)) {
    // a URL is not a file of ours: "https://x.test/a.png" is a run behind a ":"
    if (m.index > 0 && text[m.index - 1] === ':') continue
    const run = m[0].replace(/\.+$/, '')
    if (run.startsWith('//') || !FILE_TYPE.test(run) || seen.has(run)) continue
    seen.add(run)
    out.push(run)
  }
  return out
}

// What a surface shows for a reference: its file name.
export const refName = (ref: string) => ref.slice(ref.lastIndexOf('/') + 1)

// A feature's docs file can be spelled as its docs path or as a bare name (an approval's `doc`, next to a summary that links the
// path): one file, so it is shown once. Keeps the first spelling of each.
const DOCS_REF = /^(?:\.\/)?\.aiteams\/features\/F-(\d+)\/([^/]+)$/
export function distinctRefs(refs: string[], featureId: number | null): string[] {
  const seen = new Set<string>()
  return refs.filter((ref) => {
    const m = DOCS_REF.exec(ref)
    const key = m && featureId != null && Number(m[1]) === featureId ? m[2] : ref
    return !seen.has(key) && !!seen.add(key)
  })
}
