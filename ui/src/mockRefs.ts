// F-29: the mocks a chat message names. A path counts as plain text, inside a code span or as a link target. The trailing
// check keeps "shot.png." (the end of a sentence) and refuses "mock.html.bak". Kept free of React so a spec can import it.
const MOCK_PATH = /\.aiteams\/features\/F-(\d+)\/([\w.-]+\.(?:html|png|jpe?g|svg))(?!\.?[\w-])/gi
const MAX_PER_MESSAGE = 4

export function mockRefs(text: string): { fid: number; name: string }[] {
  const seen = new Set<string>()
  const out: { fid: number; name: string }[] = []
  for (const m of text.matchAll(MOCK_PATH)) {
    const k = `${m[1]}/${m[2]}`
    if (seen.has(k)) continue
    seen.add(k)
    out.push({ fid: Number(m[1]), name: m[2] })
    if (out.length === MAX_PER_MESSAGE) break
  }
  return out
}

