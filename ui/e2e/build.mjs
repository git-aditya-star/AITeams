// F-11: the build step of `npm run e2e`. Two roles' e2e can run at once, and both sandboxes serve aiteams/static.
// A plain `vite build` empties that folder first, which breaks the other role's run mid-test. So: build into a temp
// dir, then move each file into aiteams/static with an atomic rename (assets first, index.html last), never deleting.
// Old hashed assets stay behind; that's harmless (`npm run build` and `aiteams upgrade` still do a clean build).
import { copyFileSync, mkdirSync, mkdtempSync, readdirSync, renameSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'vite'

const UI = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const STATIC = resolve(UI, '../aiteams/static')
const tmp = mkdtempSync(join(tmpdir(), 'aiteams-ui-'))

const files = (dir) => readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? files(join(dir, e.name)) : [join(dir, e.name)]))

try {
  await build({ root: UI, logLevel: 'warn', build: { outDir: tmp, emptyOutDir: true } })
  const all = files(tmp)
  const last = all.filter((f) => relative(tmp, f) === 'index.html')
  for (const f of [...all.filter((f) => !last.includes(f)), ...last]) {
    const dest = join(STATIC, relative(tmp, f))
    mkdirSync(dirname(dest), { recursive: true })
    const part = `${dest}.part-${process.pid}`
    copyFileSync(f, part)
    renameSync(part, dest)
  }
  console.log(`built ${all.length} files into aiteams/static`)
} finally {
  rmSync(tmp, { recursive: true, force: true })
}
